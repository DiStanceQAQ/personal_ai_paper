"""Heading-aware chunking for structured PDF parse documents."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from typing import Any, cast

from pdf_models import (
    BBox,
    ParseDocument,
    ParseElement,
    ParseTable,
    PassageRecord,
    PassageType,
)

_SEARCHABLE_ELEMENT_TYPES = {
    "title",
    "paragraph",
    "list",
    "caption",
    "equation",
    "code",
    "unknown",
}
_IGNORED_ELEMENT_TYPES = {"heading", "page_header", "page_footer", "reference"}
_WORD_RE = re.compile(r"\S+")
_WHITESPACE_RE = re.compile(r"\s+")


def chunk_parse_document(
    doc: ParseDocument,
    max_tokens: int = 900,
    soft_tokens: int = 700,
    overlap_tokens: int = 100,
) -> list[PassageRecord]:
    """Build retrieval passages from structured parse elements."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if soft_tokens <= 0:
        raise ValueError("soft_tokens must be positive")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens must be non-negative")

    estimator = _TokenEstimator()
    tables_by_element_id = {
        table.element_id: table for table in doc.tables if table.element_id is not None
    }
    passages: list[PassageRecord] = []
    current_elements: list[ParseElement] = []
    current_heading: list[str] | None = None
    previous_by_heading: dict[tuple[str, ...], list[ParseElement]] = {}

    def flush_current() -> None:
        nonlocal current_elements, current_heading
        if not current_elements:
            return
        assert current_heading is not None
        passages.extend(
            _build_element_passages(
                doc=doc,
                elements=current_elements,
                heading_path=current_heading,
                paragraph_index_start=len(passages),
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
                estimator=estimator,
                previous_elements=previous_by_heading.get(tuple(current_heading), []),
            )
        )
        previous_by_heading[tuple(current_heading)] = list(current_elements)
        current_elements = []
        current_heading = None

    for element in sorted(doc.elements, key=lambda item: item.element_index):
        if element.element_type == "table":
            flush_current()
            table = tables_by_element_id.get(element.id)
            passages.extend(
                _build_table_passages(
                    doc=doc,
                    element=element,
                    table=table,
                    paragraph_index_start=len(passages),
                    max_tokens=max_tokens,
                    estimator=estimator,
                )
            )
            continue

        if not _is_searchable_body_element(element):
            continue

        heading_path = list(element.heading_path)
        if current_heading is not None and heading_path != current_heading:
            flush_current()
        current_heading = heading_path

        candidate = current_elements + [element]
        candidate_text = _join_element_text(candidate)
        candidate_tokens = estimator.count(candidate_text)
        if (
            current_elements
            and (candidate_tokens > max_tokens or candidate_tokens > soft_tokens)
        ):
            flush_current()
            current_heading = heading_path

        current_elements.append(element)

    flush_current()
    return [
        passage.model_copy(update={"paragraph_index": index})
        for index, passage in enumerate(passages)
    ]


class _TokenEstimator:
    """Token counter with tiktoken when available and deterministic fallback."""

    def __init__(self) -> None:
        self._encoder: Any | None = None
        try:
            tiktoken_module = importlib.import_module("tiktoken")
            self._encoder = tiktoken_module.get_encoding("cl100k_base")
        except Exception:
            self._encoder = None

    def count(self, text: str) -> int:
        normalized = _normalize_text(text)
        if not normalized:
            return 0
        if self._encoder is not None:
            encoded = self._encoder.encode(normalized)
            return len(cast(Sequence[Any], encoded))
        words = _WORD_RE.findall(normalized)
        if not words:
            return 0
        char_estimate = math.ceil(len(normalized) / 4)
        return max(len(words), char_estimate)


_DEFAULT_TOKEN_ESTIMATOR = _TokenEstimator()


def count_text_tokens(text: str) -> int:
    """Count text tokens with the same estimator used for PDF chunking."""
    return _DEFAULT_TOKEN_ESTIMATOR.count(text)


def _is_searchable_body_element(element: ParseElement) -> bool:
    if element.element_type in _IGNORED_ELEMENT_TYPES:
        return False
    if element.element_type not in _SEARCHABLE_ELEMENT_TYPES:
        return False
    if _is_references_heading(element.heading_path):
        return False
    return bool(_normalize_text(element.text))


def _is_references_heading(heading_path: Sequence[str]) -> bool:
    return any(_normalize_text(part).lower() == "references" for part in heading_path)


def _join_element_text(elements: Sequence[ParseElement]) -> str:
    return "\n\n".join(
        text
        for text in (_normalize_text(element.text) for element in elements)
        if text
    )


def _build_element_passages(
    *,
    doc: ParseDocument,
    elements: Sequence[ParseElement],
    heading_path: Sequence[str],
    paragraph_index_start: int,
    max_tokens: int,
    overlap_tokens: int,
    estimator: _TokenEstimator,
    previous_elements: Sequence[ParseElement],
) -> list[PassageRecord]:
    chunks: list[list[ParseElement]] = []
    current: list[ParseElement] = []
    for element in elements:
        if not current:
            current = [element]
            continue
        candidate = current + [element]
        if estimator.count(_join_element_text(candidate)) <= max_tokens:
            current.append(element)
        else:
            chunks.append(current)
            current = [element]
    if current:
        chunks.append(current)

    passages: list[PassageRecord] = []
    previous_chunk_elements = list(previous_elements)
    for index, chunk_elements in enumerate(chunks):
        text = _join_element_text(chunk_elements)
        if estimator.count(text) > max_tokens:
            split_texts = _split_text_for_budget(text, max_tokens, estimator)
            for split_index, split_text in enumerate(split_texts):
                metadata = _overlap_metadata(
                    previous_chunk_elements,
                    overlap_tokens,
                    estimator,
                )
                passages.append(
                    _make_passage(
                        doc=doc,
                        text=split_text,
                        element_ids=[chunk_elements[0].id],
                        heading_path=heading_path,
                        page_numbers=[chunk_elements[0].page_number],
                        bbox=chunk_elements[0].bbox,
                        paragraph_index=paragraph_index_start
                        + len(passages),
                        estimator=estimator,
                        passage_type=_passage_type_for_heading(heading_path),
                        quality_flags=[],
                        metadata={
                            **metadata,
                            "split_source": "element_text",
                            "split_index": split_index,
                        },
                    )
                )
            previous_chunk_elements = list(chunk_elements)
            continue

        metadata = (
            _overlap_metadata(previous_chunk_elements, overlap_tokens, estimator)
            if index > 0 or previous_elements
            else {}
        )
        passages.append(
            _make_passage(
                doc=doc,
                text=text,
                element_ids=[element.id for element in chunk_elements],
                heading_path=heading_path,
                page_numbers=[element.page_number for element in chunk_elements],
                bbox=_union_bboxes(element.bbox for element in chunk_elements),
                paragraph_index=paragraph_index_start + len(passages),
                estimator=estimator,
                passage_type=_passage_type_for_heading(heading_path),
                quality_flags=[],
                metadata=metadata,
            )
        )
        previous_chunk_elements = list(chunk_elements)
    return passages


def _build_table_passages(
    *,
    doc: ParseDocument,
    element: ParseElement,
    table: ParseTable | None,
    paragraph_index_start: int,
    max_tokens: int,
    estimator: _TokenEstimator,
) -> list[PassageRecord]:
    if table is None:
        text = _normalize_text(element.text)
        if not text:
            return []
        return _make_budgeted_text_passages(
            doc=doc,
            text=text,
            element_ids=[element.id],
            heading_path=element.heading_path,
            page_numbers=[element.page_number],
            bbox=element.bbox,
            paragraph_index_start=paragraph_index_start,
            max_tokens=max_tokens,
            estimator=estimator,
            passage_type=_passage_type_for_heading(element.heading_path),
            quality_flags=[],
            metadata={"table_element_id": element.id},
        )

    table_text = _render_table(table)
    if not table_text:
        return []
    table_metadata = _table_metadata(table)
    if estimator.count(table_text) <= max_tokens:
        return [
            _make_passage(
                doc=doc,
                text=table_text,
                element_ids=[element.id],
                heading_path=element.heading_path,
                page_numbers=[table.page_number or element.page_number],
                bbox=table.bbox or element.bbox,
                paragraph_index=paragraph_index_start,
                estimator=estimator,
                passage_type=_passage_type_for_heading(element.heading_path),
                quality_flags=[],
                metadata=table_metadata,
            )
        ]

    passages: list[PassageRecord] = []
    header_rows = _header_row_count(table)
    headers = table.cells[:header_rows]
    data_rows = table.cells[header_rows:] if header_rows else table.cells

    if not data_rows:
        return _make_budgeted_text_passages(
            doc=doc,
            text=table_text,
            element_ids=[element.id],
            heading_path=element.heading_path,
            page_numbers=[table.page_number or element.page_number],
            bbox=table.bbox or element.bbox,
            paragraph_index_start=paragraph_index_start,
            max_tokens=max_tokens,
            estimator=estimator,
            passage_type=_passage_type_for_heading(element.heading_path),
            quality_flags=[],
            metadata={**table_metadata, "split_source": "table_text"},
        )

    current_rows: list[list[str]] = []
    current_start = 0

    def append_rows(row_end: int) -> None:
        nonlocal current_rows, current_start
        if not current_rows:
            return
        if len(current_rows) == 1:
            split_passages = _make_single_row_table_passages(
                doc=doc,
                table=table,
                element=element,
                headers=headers,
                row=current_rows[0],
                row_start=current_start,
                row_end=row_end,
                paragraph_index_start=paragraph_index_start + len(passages),
                max_tokens=max_tokens,
                estimator=estimator,
                table_metadata=table_metadata,
            )
            if split_passages:
                passages.extend(split_passages)
                current_rows = []
                current_start = row_end
                return

        text_with_caption = _render_table(
            table,
            header_rows=headers,
            data_rows=current_rows,
        )
        text = (
            text_with_caption
            if estimator.count(text_with_caption) <= max_tokens
            else _render_table(
                table,
                header_rows=headers,
                data_rows=current_rows,
                include_caption=False,
            )
        )
        metadata = {
            **table_metadata,
            "row_start": current_start,
            "row_end": row_end,
            "split_source": "table_rows",
        }
        passages.extend(
            _make_budgeted_text_passages(
                doc=doc,
                text=text,
                element_ids=[element.id],
                heading_path=element.heading_path,
                page_numbers=[table.page_number or element.page_number],
                bbox=table.bbox or element.bbox,
                paragraph_index_start=paragraph_index_start + len(passages),
                max_tokens=max_tokens,
                estimator=estimator,
                passage_type=_passage_type_for_heading(element.heading_path),
                quality_flags=[],
                metadata=metadata,
            )
        )
        current_rows = []
        current_start = row_end

    for data_index, row in enumerate(data_rows):
        candidate_rows = current_rows + [row]
        candidate_text = _render_table(
            table,
            header_rows=headers,
            data_rows=candidate_rows,
        )
        if current_rows and estimator.count(candidate_text) > max_tokens:
            append_rows(data_index)
        if not current_rows:
            current_start = data_index
        current_rows.append(row)

        single_row_text = _render_table(
            table,
            header_rows=headers,
            data_rows=current_rows,
            include_caption=False,
        )
        if estimator.count(single_row_text) > max_tokens:
            append_rows(data_index + 1)

    append_rows(len(data_rows))
    return passages


def _make_single_row_table_passages(
    *,
    doc: ParseDocument,
    table: ParseTable,
    element: ParseElement,
    headers: Sequence[Sequence[str]],
    row: Sequence[str],
    row_start: int,
    row_end: int,
    paragraph_index_start: int,
    max_tokens: int,
    estimator: _TokenEstimator,
    table_metadata: dict[str, Any],
) -> list[PassageRecord]:
    header_text = _render_table(
        table,
        header_rows=headers,
        data_rows=[],
        include_caption=False,
    )
    row_text = _render_row(row)
    row_with_header = "\n".join(part for part in (header_text, row_text) if part)
    if estimator.count(row_with_header) <= max_tokens:
        return [
            _make_passage(
                doc=doc,
                text=row_with_header,
                element_ids=[element.id],
                heading_path=element.heading_path,
                page_numbers=[table.page_number or element.page_number],
                bbox=table.bbox or element.bbox,
                paragraph_index=paragraph_index_start,
                estimator=estimator,
                passage_type=_passage_type_for_heading(element.heading_path),
                quality_flags=[],
                metadata={
                    **table_metadata,
                    "row_start": row_start,
                    "row_end": row_end,
                    "split_source": "table_rows",
                },
            )
        ]

    if header_text and estimator.count(header_text) < max_tokens:
        row_fragments = _split_row_text_with_header_budget(
            header_text,
            row_text,
            max_tokens,
            estimator,
        )
        if row_fragments:
            passages: list[PassageRecord] = []
            for split_index, row_fragment in enumerate(row_fragments):
                text = "\n".join((header_text, row_fragment))
                passages.append(
                    _make_passage(
                        doc=doc,
                        text=text,
                        element_ids=[element.id],
                        heading_path=element.heading_path,
                        page_numbers=[table.page_number or element.page_number],
                        bbox=table.bbox or element.bbox,
                        paragraph_index=paragraph_index_start + len(passages),
                        estimator=estimator,
                        passage_type=_passage_type_for_heading(element.heading_path),
                        quality_flags=[],
                        metadata={
                            **table_metadata,
                            "row_start": row_start,
                            "row_end": row_end,
                            "split_source": "table_rows",
                            "split_index": split_index,
                            "split_count": len(row_fragments),
                        },
                    )
                )
            return passages

    return _make_budgeted_text_passages(
        doc=doc,
        text=row_with_header,
        element_ids=[element.id],
        heading_path=element.heading_path,
        page_numbers=[table.page_number or element.page_number],
        bbox=table.bbox or element.bbox,
        paragraph_index_start=paragraph_index_start,
        max_tokens=max_tokens,
        estimator=estimator,
        passage_type=_passage_type_for_heading(element.heading_path),
        quality_flags=[],
        metadata={
            **table_metadata,
            "row_start": row_start,
            "row_end": row_end,
            "split_source": "table_rows",
        },
    )


def _split_row_text_with_header_budget(
    header_text: str,
    row_text: str,
    max_tokens: int,
    estimator: _TokenEstimator,
) -> list[str]:
    fragments: list[str] = []
    current = ""
    for char in row_text:
        candidate = f"{current}{char}"
        candidate_text = "\n".join((header_text, candidate))
        if current and estimator.count(candidate_text) > max_tokens:
            fragments.append(current)
            current = char
        else:
            current = candidate
    if current:
        fragments.append(current)
    return fragments


def _make_budgeted_text_passages(
    *,
    doc: ParseDocument,
    text: str,
    element_ids: Sequence[str],
    heading_path: Sequence[str],
    page_numbers: Sequence[int],
    bbox: BBox | None,
    paragraph_index_start: int,
    max_tokens: int,
    estimator: _TokenEstimator,
    passage_type: PassageType,
    quality_flags: Sequence[str],
    metadata: dict[str, Any],
) -> list[PassageRecord]:
    split_texts = _split_text_for_budget(text, max_tokens, estimator)
    passages: list[PassageRecord] = []
    for split_index, split_text in enumerate(split_texts):
        if not split_text:
            continue
        split_metadata = dict(metadata)
        if len(split_texts) > 1:
            split_metadata["split_index"] = split_index
            split_metadata["split_count"] = len(split_texts)
        passages.append(
            _make_passage(
                doc=doc,
                text=split_text,
                element_ids=element_ids,
                heading_path=heading_path,
                page_numbers=page_numbers,
                bbox=bbox,
                paragraph_index=paragraph_index_start + len(passages),
                estimator=estimator,
                passage_type=passage_type,
                quality_flags=quality_flags,
                metadata=split_metadata,
            )
        )
    return passages


def _render_row(row: Sequence[str]) -> str:
    return " | ".join(_normalize_text(cell) for cell in row)


def _render_table(
    table: ParseTable,
    *,
    header_rows: Sequence[Sequence[str]] | None = None,
    data_rows: Sequence[Sequence[str]] | None = None,
    include_caption: bool = True,
) -> str:
    headers = (
        list(header_rows)
        if header_rows is not None
        else table.cells[: _header_row_count(table)]
    )
    rows = (
        list(data_rows)
        if data_rows is not None
        else table.cells[_header_row_count(table) :]
    )
    lines: list[str] = []
    if include_caption and table.caption:
        lines.append(_normalize_text(table.caption))
    for row in [*headers, *rows]:
        row_text = _render_row(row)
        if row_text:
            lines.append(row_text)
    return "\n".join(line for line in lines if line)


def _table_metadata(table: ParseTable) -> dict[str, Any]:
    metadata = {
        "table_id": table.id,
        "table_index": table.table_index,
        "caption": table.caption,
        "header_rows": _header_row_count(table),
    }
    return metadata


def _header_row_count(table: ParseTable) -> int:
    raw = table.metadata.get("header_rows", 1 if table.cells else 0)
    if isinstance(raw, int):
        return max(0, min(raw, len(table.cells)))
    return 1 if table.cells else 0


def _split_text_for_budget(
    text: str,
    max_tokens: int,
    estimator: _TokenEstimator,
) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    if estimator.count(normalized) <= max_tokens:
        return [normalized]

    chunks: list[str] = []
    current = ""
    for char in normalized:
        candidate = f"{current}{char}"
        if current and estimator.count(candidate) > max_tokens:
            chunks.append(current.rstrip())
            current = char.lstrip()
        else:
            current = candidate
    if current:
        chunks.append(current.rstrip())
    return [chunk for chunk in chunks if chunk]


def _overlap_metadata(
    previous_elements: Sequence[ParseElement],
    overlap_tokens: int,
    estimator: _TokenEstimator,
) -> dict[str, Any]:
    if overlap_tokens <= 0 or not previous_elements:
        return {}
    overlap_ids: list[str] = []
    token_total = 0
    for element in reversed(previous_elements):
        element_tokens = estimator.count(element.text)
        if overlap_ids and token_total + element_tokens > overlap_tokens:
            break
        overlap_ids.append(element.id)
        token_total += element_tokens
        if token_total >= overlap_tokens:
            break
    if not overlap_ids:
        overlap_ids = [previous_elements[-1].id]
    overlap_ids.reverse()
    return {"overlap_element_ids": overlap_ids}


def _make_passage(
    *,
    doc: ParseDocument,
    text: str,
    element_ids: Sequence[str],
    heading_path: Sequence[str],
    page_numbers: Sequence[int],
    bbox: BBox | None,
    paragraph_index: int,
    estimator: _TokenEstimator,
    passage_type: PassageType,
    quality_flags: Sequence[str],
    metadata: dict[str, Any],
) -> PassageRecord:
    normalized_text = _normalize_text(text)
    pages = [page for page in page_numbers if page > 0]
    page_number = min(pages) if pages else 0
    content_hash = _content_hash(
        text=normalized_text,
        heading_path=heading_path,
        page_numbers=pages or [0],
        element_ids=element_ids,
    )
    passage_id = f"passage-{content_hash[:16]}-{paragraph_index:04d}"
    return PassageRecord(
        id=passage_id,
        paper_id=doc.paper_id,
        space_id=doc.space_id,
        section=heading_path[-1] if heading_path else "",
        page_number=page_number,
        paragraph_index=paragraph_index,
        original_text=normalized_text,
        parse_confidence=1.0,
        passage_type=passage_type,
        parse_run_id=_parse_run_id(doc),
        element_ids=list(element_ids),
        heading_path=list(heading_path),
        bbox=bbox,
        token_count=estimator.count(normalized_text),
        char_count=len(normalized_text),
        content_hash=content_hash,
        parser_backend=doc.backend,
        extraction_method=doc.extraction_method,
        quality_flags=list(quality_flags),
        metadata=metadata,
    )


def _content_hash(
    *,
    text: str,
    heading_path: Sequence[str],
    page_numbers: Sequence[int],
    element_ids: Sequence[str],
) -> str:
    page_range = [min(page_numbers), max(page_numbers)]
    payload = {
        "text": _normalize_text(text),
        "heading_path": [_normalize_text(part) for part in heading_path],
        "page_range": page_range,
        "element_ids": list(element_ids),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _union_bboxes(bboxes: Iterable[BBox | None]) -> BBox | None:
    concrete = [bbox for bbox in bboxes if bbox is not None]
    if not concrete:
        return None
    return cast(
        BBox,
        [
            min(bbox[0] for bbox in concrete),
            min(bbox[1] for bbox in concrete),
            max(bbox[2] for bbox in concrete),
            max(bbox[3] for bbox in concrete),
        ],
    )


def _parse_run_id(doc: ParseDocument) -> str | None:
    parse_run_id = doc.metadata.get("parse_run_id")
    return parse_run_id if isinstance(parse_run_id, str) and parse_run_id else None


def _passage_type_for_heading(heading_path: Sequence[str]) -> PassageType:
    section = " ".join(heading_path).lower()
    if "abstract" in section:
        return "abstract"
    if "introduction" in section:
        return "introduction"
    if any(term in section for term in ("method", "approach", "experiment")):
        return "method"
    if any(term in section for term in ("result", "evaluation")):
        return "result"
    if "discussion" in section:
        return "discussion"
    if any(term in section for term in ("limitation", "future work")):
        return "limitation"
    if "appendix" in section:
        return "appendix"
    return "body"


__all__ = ["chunk_parse_document", "count_text_tokens"]
