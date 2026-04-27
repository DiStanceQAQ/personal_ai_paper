"""PyMuPDF4LLM-backed PDF parser implementation."""

from __future__ import annotations

import importlib
import importlib.util
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, cast

from pdf_backend_base import ParserBackendError, ParserBackendUnavailable
from pdf_models import (
    ElementType,
    ExtractionMethod,
    ParseAsset,
    ParseDocument,
    ParseElement,
    ParseTable,
    PdfQualityReport,
)


_BACKEND_NAME = "pymupdf4llm"
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$")


def _load_pymupdf4llm() -> Any:
    """Import PyMuPDF4LLM lazily so availability is reported cleanly."""
    if importlib.util.find_spec("pymupdf4llm") is None:
        raise ParserBackendUnavailable(_BACKEND_NAME, "pymupdf4llm is not installed")
    return importlib.import_module("pymupdf4llm")


class PyMuPDF4LLMBackend:
    """Parse PDF files through PyMuPDF4LLM page chunks."""

    name = _BACKEND_NAME

    def is_available(self) -> bool:
        """Return whether PyMuPDF4LLM can be imported."""
        return importlib.util.find_spec("pymupdf4llm") is not None

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        """Parse a PDF into the normalized parser contract."""
        if not self.is_available():
            raise ParserBackendUnavailable(self.name, "pymupdf4llm is not installed")

        try:
            pymupdf4llm = _load_pymupdf4llm()
            chunks = pymupdf4llm.to_markdown(
                str(file_path),
                page_chunks=True,
                use_ocr=True,
                write_images=False,
                embed_images=False,
            )
        except ParserBackendUnavailable:
            raise
        except Exception as exc:
            raise ParserBackendError(self.name, "failed to parse PDF", cause=exc) from exc

        try:
            return _chunks_to_document(
                chunks,
                paper_id=paper_id,
                space_id=space_id,
                quality_report=quality_report,
            )
        except Exception as exc:
            raise ParserBackendError(
                self.name,
                "failed to normalize PyMuPDF4LLM output",
                cause=exc,
            ) from exc


class _DocumentBuilder:
    def __init__(
        self,
        *,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
        chunks: list[Mapping[str, Any]],
    ) -> None:
        self.paper_id = paper_id
        self.space_id = space_id
        self.quality_report = quality_report
        self.chunks = chunks
        self.extraction_method = _extraction_method(quality_report)
        self.elements: list[ParseElement] = []
        self.tables: list[ParseTable] = []
        self.assets: list[ParseAsset] = []
        self._heading_path: list[str] = []
        self._saw_title = False

    def build(self) -> ParseDocument:
        repeated_text = _repeated_margin_text(self.chunks)

        for chunk_index, chunk in enumerate(self.chunks):
            page_number = _page_number(chunk, chunk_index)
            text = str(chunk.get("text") or "")
            toc_items = _as_list(chunk.get("toc_items"))
            self._add_page_boxes(
                page_number=page_number,
                text=text,
                boxes=_as_list(chunk.get("page_boxes")),
                repeated_text=repeated_text,
                toc_items=toc_items,
            )
            self._add_toc_items(page_number, toc_items)
            self._add_tables(page_number, _as_list(chunk.get("tables")))
            self._add_assets(page_number, _as_list(chunk.get("images")), "image")
            self._add_assets(page_number, _as_list(chunk.get("graphics")), "graphic")

            if not _as_list(chunk.get("page_boxes")):
                self._add_markdown_blocks(page_number, text, toc_items)

        return ParseDocument(
            paper_id=self.paper_id,
            space_id=self.space_id,
            backend=_BACKEND_NAME,
            extraction_method=self.extraction_method,
            quality=self.quality_report,
            elements=self.elements,
            tables=self.tables,
            assets=self.assets,
            metadata={
                "chunk_count": len(self.chunks),
                "parser": "pymupdf4llm.to_markdown",
            },
        )

    def _add_page_boxes(
        self,
        *,
        page_number: int,
        text: str,
        boxes: list[Any],
        repeated_text: set[str],
        toc_items: list[Any],
    ) -> None:
        for box_index, raw_box in enumerate(boxes):
            if not isinstance(raw_box, Mapping):
                continue

            box_class = str(raw_box.get("class") or "unknown")
            bbox = _bbox(raw_box.get("bbox"))
            box_text = _box_text(text, raw_box)
            if box_class == "table":
                box_text = _markdown_table_around(text, raw_box) or box_text

            cleaned_text = _clean_markdown_text(box_text)
            element_type = _element_type_for_box(
                box_class=box_class,
                text=cleaned_text,
                bbox=bbox,
                repeated_text=repeated_text,
            )
            filtered = element_type in {"page_header", "page_footer"}

            if not cleaned_text and element_type not in {"figure", "table"}:
                continue

            if element_type == "heading":
                heading_text = cleaned_text
                self._heading_path = [heading_text]
            elif element_type == "title":
                self._saw_title = True
            elif (
                element_type == "paragraph"
                and not self._saw_title
                and not filtered
                and _looks_like_title(cleaned_text)
            ):
                element_type = "title"
                self._saw_title = True

            metadata: dict[str, Any] = {
                "source": "page_box",
                "box_class": box_class,
                "box_index": box_index,
            }
            if filtered:
                metadata["filtered"] = True
            matching_toc = _matching_toc_items(cleaned_text, toc_items)
            if matching_toc:
                metadata["toc_items"] = matching_toc

            element = self._add_element(
                element_type=element_type,
                text=cleaned_text,
                page_number=page_number,
                bbox=bbox,
                metadata=metadata,
            )

            if element.element_type == "table":
                cells = _markdown_table_cells(box_text)
                self._add_table(
                    page_number=page_number,
                    element_id=element.id,
                    cells=cells,
                    bbox=bbox,
                    metadata={"source": "page_box", "box_class": box_class},
                )

    def _add_markdown_blocks(
        self,
        page_number: int,
        text: str,
        toc_items: list[Any],
    ) -> None:
        for block in _markdown_blocks(text):
            element_type = _element_type_for_markdown(block)
            cleaned_text = _clean_markdown_text(block)
            if not cleaned_text:
                continue
            if element_type == "heading":
                self._heading_path = [cleaned_text]
            elif (
                element_type == "paragraph"
                and not self._saw_title
                and _looks_like_title(cleaned_text)
            ):
                element_type = "title"
                self._saw_title = True

            element = self._add_element(
                element_type=element_type,
                text=cleaned_text,
                page_number=page_number,
                bbox=None,
                metadata={
                    "source": "markdown",
                    "toc_items": _matching_toc_items(cleaned_text, toc_items),
                },
            )
            if element.element_type == "table":
                self._add_table(
                    page_number=page_number,
                    element_id=element.id,
                    cells=_markdown_table_cells(block),
                    bbox=None,
                    metadata={"source": "markdown"},
                )

    def _add_toc_items(self, page_number: int, toc_items: list[Any]) -> None:
        existing_headings = {
            (element.page_number, element.text.casefold())
            for element in self.elements
            if element.element_type == "heading"
        }
        for toc_index, raw_item in enumerate(toc_items):
            item = _toc_item(raw_item)
            title = str(item.get("title") or "").strip()
            if not title or (page_number, title.casefold()) in existing_headings:
                continue
            self._heading_path = [title]
            item_page = _int_or_none(item.get("page")) or page_number
            self._add_element(
                element_type="heading",
                text=title,
                page_number=item_page,
                bbox=None,
                metadata={"source": "toc_item", "toc_index": toc_index, "toc_item": item},
            )

    def _add_tables(self, page_number: int, tables: list[Any]) -> None:
        for raw_table in tables:
            if not isinstance(raw_table, Mapping):
                continue
            bbox = _bbox(raw_table.get("bbox"))
            cells = _table_cells(raw_table)
            caption = str(raw_table.get("caption") or "")
            text = caption or _cells_to_markdown(cells) or "Table"
            element = self._add_element(
                element_type="table",
                text=text,
                page_number=page_number,
                bbox=bbox,
                metadata={"source": "tables"},
            )
            self._add_table(
                page_number=page_number,
                element_id=element.id,
                cells=cells,
                bbox=bbox,
                caption=caption,
                metadata={"source": "tables"},
            )

    def _add_assets(self, page_number: int, assets: list[Any], asset_type: str) -> None:
        for raw_asset in assets:
            if not isinstance(raw_asset, Mapping):
                continue
            bbox = _bbox(raw_asset.get("bbox"))
            element = self._add_element(
                element_type="figure",
                text=str(raw_asset.get("alt") or raw_asset.get("caption") or ""),
                page_number=page_number,
                bbox=bbox,
                metadata={"source": f"{asset_type}s", "asset_type": asset_type},
            )
            asset_id = f"asset-{len(self.assets):04d}"
            self.assets.append(
                ParseAsset(
                    id=asset_id,
                    element_id=element.id,
                    asset_type=asset_type,
                    page_number=page_number,
                    uri=str(raw_asset.get("uri") or raw_asset.get("path") or ""),
                    bbox=bbox,
                    metadata={
                        "source": f"{asset_type}s",
                        "raw": _json_safe(raw_asset),
                    },
                )
            )

    def _add_element(
        self,
        *,
        element_type: str,
        text: str,
        page_number: int,
        bbox: list[float] | None,
        metadata: dict[str, Any],
    ) -> ParseElement:
        element = ParseElement(
            id=f"p{page_number:04d}-e{len(self.elements):04d}",
            element_index=len(self.elements),
            element_type=cast(ElementType, element_type),
            text=text,
            page_number=page_number,
            bbox=bbox,
            heading_path=(
                [] if element_type in {"title", "heading"} else self._heading_path
            ),
            extraction_method=self.extraction_method,
            metadata=metadata,
        )
        self.elements.append(element)
        return element

    def _add_table(
        self,
        *,
        page_number: int,
        element_id: str,
        cells: list[list[str]],
        bbox: list[float] | None,
        metadata: dict[str, Any],
        caption: str = "",
    ) -> None:
        table_index = len(self.tables)
        self.tables.append(
            ParseTable(
                id=f"table-{table_index:04d}",
                element_id=element_id,
                table_index=table_index,
                page_number=page_number,
                caption=caption,
                cells=cells,
                bbox=bbox,
                metadata=metadata,
            )
        )


def _chunks_to_document(
    chunks: Any,
    *,
    paper_id: str,
    space_id: str,
    quality_report: PdfQualityReport,
) -> ParseDocument:
    if isinstance(chunks, Mapping):
        chunk_list = [chunks]
    else:
        chunk_list = [chunk for chunk in chunks if isinstance(chunk, Mapping)]

    return _DocumentBuilder(
        paper_id=paper_id,
        space_id=space_id,
        quality_report=quality_report,
        chunks=chunk_list,
    ).build()


def _extraction_method(quality_report: PdfQualityReport) -> ExtractionMethod:
    if quality_report.needs_layout_model:
        return "layout_model"
    if quality_report.needs_ocr:
        return "ocr"
    return "native_text"


def _page_number(chunk: Mapping[str, Any], chunk_index: int) -> int:
    metadata = chunk.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("page", "page_number", "page_index"):
            value = _int_or_none(metadata.get(key))
            if value is not None:
                return value + 1 if key == "page_index" else max(value, 1)
    return chunk_index + 1


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = [float(coordinate) for coordinate in value]
    except (TypeError, ValueError):
        return None
    if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
        return None
    return bbox


def _box_text(page_text: str, box: Mapping[str, Any]) -> str:
    pos = box.get("pos")
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        start = _int_or_none(pos[0])
        end = _int_or_none(pos[1])
        if start is not None and end is not None and 0 <= start <= end <= len(page_text):
            return page_text[start:end]
    return str(box.get("text") or "")


def _markdown_table_around(page_text: str, box: Mapping[str, Any]) -> str:
    pos = box.get("pos")
    if not isinstance(pos, (list, tuple)) or len(pos) != 2:
        return ""
    start = _int_or_none(pos[0])
    if start is None:
        return ""

    line_spans: list[tuple[int, int, str]] = []
    cursor = 0
    for line in page_text.splitlines(keepends=True):
        line_spans.append((cursor, cursor + len(line), line.rstrip("\n")))
        cursor += len(line)

    line_index = next(
        (index for index, (line_start, line_end, _) in enumerate(line_spans) if line_start <= start < line_end),
        None,
    )
    if line_index is None:
        return ""

    first = line_index
    while first > 0 and _looks_like_table_line(line_spans[first - 1][2]):
        first -= 1
    last = line_index
    while last + 1 < len(line_spans) and _looks_like_table_line(line_spans[last + 1][2]):
        last += 1

    lines = [line for _, _, line in line_spans[first : last + 1] if _looks_like_table_line(line)]
    return "\n".join(lines)


def _markdown_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


def _element_type_for_box(
    *,
    box_class: str,
    text: str,
    bbox: list[float] | None,
    repeated_text: set[str],
) -> str:
    normalized_class = box_class.replace("_", "-").casefold()
    normalized_text = _normalize_repeated_text(text)

    if normalized_class == "page-header":
        return "page_header"
    if normalized_class == "page-footer":
        return "page_footer"
    if normalized_text in repeated_text and bbox is not None:
        if bbox[3] <= 110:
            return "page_header"
        if bbox[1] >= 700:
            return "page_footer"
    if normalized_class in {"section-header", "heading", "title"} or _HEADING_RE.match(text):
        return "heading"
    if normalized_class == "table" or _markdown_table_cells(text):
        return "table"
    if normalized_class in {"image", "figure", "graphic"}:
        return "figure"
    if normalized_class in {"list", "caption", "equation", "code", "reference"}:
        return normalized_class
    if normalized_class == "text":
        return "paragraph"
    return "unknown"


def _element_type_for_markdown(block: str) -> str:
    if _HEADING_RE.match(block.strip()):
        return "heading"
    if _markdown_table_cells(block):
        return "table"
    return "paragraph"


def _clean_markdown_text(text: str) -> str:
    stripped = text.strip()
    heading = _HEADING_RE.match(stripped)
    if heading:
        return heading.group(2).strip()
    return stripped


def _looks_like_title(text: str) -> bool:
    if not text or "\n" in text:
        return False
    words = text.split()
    if len(words) > 16:
        return False
    lowered = text.casefold()
    return lowered not in {"abstract", "introduction", "method", "methods", "results"}


def _looks_like_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _markdown_table_cells(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not _looks_like_table_line(line):
            continue
        stripped = line.strip().strip("|")
        if _TABLE_SEPARATOR_RE.match(line.strip()):
            continue
        rows.append([cell.strip() for cell in stripped.split("|")])
    return rows if len(rows) >= 1 else []


def _table_cells(raw_table: Mapping[str, Any]) -> list[list[str]]:
    for key in ("cells", "rows", "data"):
        value = raw_table.get(key)
        if isinstance(value, list):
            rows: list[list[str]] = []
            for row in value:
                if isinstance(row, list):
                    rows.append([str(cell).strip() for cell in row])
                elif isinstance(row, tuple):
                    rows.append([str(cell).strip() for cell in row])
            if rows:
                return rows

    markdown = str(raw_table.get("markdown") or raw_table.get("text") or "")
    return _markdown_table_cells(markdown)


def _cells_to_markdown(cells: list[list[str]]) -> str:
    return "\n".join("|" + "|".join(row) + "|" for row in cells)


def _repeated_margin_text(chunks: list[Mapping[str, Any]]) -> set[str]:
    candidates: Counter[str] = Counter()
    for chunk in chunks:
        page_text = str(chunk.get("text") or "")
        for raw_box in _as_list(chunk.get("page_boxes")):
            if not isinstance(raw_box, Mapping):
                continue
            bbox = _bbox(raw_box.get("bbox"))
            if bbox is None or not (bbox[3] <= 110 or bbox[1] >= 700):
                continue
            text = _normalize_repeated_text(_clean_markdown_text(_box_text(page_text, raw_box)))
            if text:
                candidates[text] += 1
    return {text for text, count in candidates.items() if count >= 2}


def _normalize_repeated_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def _matching_toc_items(text: str, toc_items: list[Any]) -> list[dict[str, Any]]:
    normalized_text = text.casefold()
    matches = []
    for raw_item in toc_items:
        item = _toc_item(raw_item)
        title = str(item.get("title") or "").strip()
        if title and title.casefold() == normalized_text:
            matches.append(item)
    return matches


def _toc_item(raw_item: Any) -> dict[str, Any]:
    if isinstance(raw_item, Mapping):
        return {str(key): _json_safe(value) for key, value in raw_item.items()}
    if isinstance(raw_item, (list, tuple)) and len(raw_item) >= 2:
        return {"level": _json_safe(raw_item[0]), "title": str(raw_item[1])}
    return {"title": str(raw_item)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["PyMuPDF4LLMBackend"]
