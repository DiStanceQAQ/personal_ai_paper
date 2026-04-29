"""Core paper metadata extraction and promotion helpers."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

CORE_METADATA_FIELDS = (
    "title",
    "authors",
    "year",
    "doi",
    "arxiv_id",
    "venue",
    "abstract",
)

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
ARXIV_RE = re.compile(
    r"(?i)(?:arxiv\s*:?\s*|arxiv\.org/(?:abs|pdf)/)"
    r"([a-z-]+(?:\.[a-z]{2})?/\d{7}(?:v\d+)?|\d{4}\.\d{4,5}(?:v\d+)?)"
)
YEAR_RE = re.compile(r"\b(18|19|20)\d{2}\b")


@dataclass(frozen=True)
class MetadataCandidate:
    """A normalized metadata value and its provenance."""

    value: str | int | list[str] | None
    source: str
    confidence: float
    source_id: str = ""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_value(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _json_object(value: Any) -> dict[str, Any]:
    parsed = _json_value(value)
    if not isinstance(parsed, Mapping):
        return {}
    return {str(key): item for key, item in parsed.items()}


def _json_list(value: Any) -> list[str]:
    parsed = _json_value(value)
    if not isinstance(parsed, Sequence) or isinstance(parsed, (bytes, bytearray, str)):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _object_to_mapping(value: Mapping[str, Any] | BaseModel) -> Mapping[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump()
    return value


def _optional_string(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return ""


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _clean_title(value: str) -> str:
    return re.sub(r"(?i)^title\s*[:.\-]?\s*", "", _clean_text(value))


def _clean_abstract(value: str) -> str:
    return re.sub(r"(?i)^abstract\s*[:.\-]\s*", "", _clean_text(value))


def _normalize_doi(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = re.sub(r"(?i)^doi\s*:\s*", "", text)
    text = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", text)
    match = DOI_RE.search(text)
    if match is not None:
        text = match.group(0)
    return text.rstrip(".,;")


def _normalize_arxiv_id(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = re.sub(r"(?i)^arxiv\s*:?\s*", "", text)
    text = re.sub(r"(?i)^https?://arxiv\.org/(?:abs|pdf)/", "", text)
    return text.rstrip(".,;")


def _year_value(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if value is None:
        return None
    match = YEAR_RE.search(str(value))
    if match is None:
        return None
    return int(match.group(0))


def _author_name(value: Any) -> str:
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, Mapping):
        for key in ("full_name", "name", "author"):
            text = _clean_text(str(value.get(key, "")))
            if text:
                return text
        first = _clean_text(str(value.get("first", value.get("forename", ""))))
        last = _clean_text(str(value.get("last", value.get("surname", ""))))
        return _clean_text(f"{first} {last}")
    return ""


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


def _author_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _dedupe_strings(re.split(r"\s*(?:;|\band\b)\s*", value))
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return _dedupe_strings([name for item in value if (name := _author_name(item))])
    return []


def _candidate_has_value(candidate: MetadataCandidate) -> bool:
    return _has_value(candidate.value)


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _candidate_value_for_db(candidate: MetadataCandidate) -> str | int | None:
    value = candidate.value
    if isinstance(value, list):
        return ", ".join(value)
    return value


def _source_priority(source: str) -> int:
    if source == "user.edit":
        return 1000
    if source.startswith("regex."):
        return 900
    if source.startswith("document."):
        return 700
    if source.startswith("llm."):
        return 600
    if source == "filename.fallback":
        return 100
    if source:
        return 50
    return 0


def _source_texts(
    passages: Sequence[Mapping[str, Any] | BaseModel],
    elements: Sequence[Mapping[str, Any] | BaseModel],
) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for passage in passages:
        data = _object_to_mapping(passage)
        values.append(
            {
                "kind": "passage",
                "source_id": _optional_string(data, "id", "source_id"),
                "text": _optional_string(data, "original_text", "text"),
            }
        )
    for element in elements:
        data = _object_to_mapping(element)
        values.append(
            {
                "kind": "element",
                "source_id": _optional_string(data, "id", "element_id"),
                "text": _optional_string(data, "text"),
            }
        )
    return values


def _first_page_title(
    elements: Sequence[Mapping[str, Any] | BaseModel],
) -> MetadataCandidate | None:
    candidates: list[tuple[int, int, str, str]] = []
    for element in elements:
        data = _object_to_mapping(element)
        if _optional_string(data, "element_type", "type") != "title":
            continue
        title = _clean_title(_optional_string(data, "text"))
        if not title:
            continue
        page_number = _int_value(data.get("page_number"), default=0)
        element_index = _int_value(data.get("element_index"), default=0)
        element_id = _optional_string(data, "id")
        candidates.append((page_number, element_index, title, element_id))

    selected_pool = [candidate for candidate in candidates if candidate[0] in {0, 1}]
    selected_pool = selected_pool or candidates
    if not selected_pool:
        return None

    page_number, _element_index, title, element_id = min(
        selected_pool,
        key=lambda candidate: (candidate[0], candidate[1]),
    )
    source_id = element_id or f"page-{page_number}"
    return MetadataCandidate(
        title,
        "document.title",
        0.75,
        source_id=source_id,
    )


def _first_doi_hit(
    passages: Sequence[Mapping[str, Any] | BaseModel],
    elements: Sequence[Mapping[str, Any] | BaseModel],
) -> MetadataCandidate | None:
    for data in _source_texts(passages, elements):
        match = DOI_RE.search(data["text"])
        if match is None:
            continue
        value = _normalize_doi(match.group(0))
        if value:
            source_id = data["source_id"] if data["kind"] == "passage" else ""
            return MetadataCandidate(value, "regex.doi", 0.98, source_id=source_id)
    return None


def _first_arxiv_hit(
    passages: Sequence[Mapping[str, Any] | BaseModel],
    elements: Sequence[Mapping[str, Any] | BaseModel],
) -> MetadataCandidate | None:
    for data in _source_texts(passages, elements):
        match = ARXIV_RE.search(data["text"])
        if match is None:
            continue
        value = _normalize_arxiv_id(match.group(1))
        if value:
            source_id = data["source_id"] if data["kind"] == "passage" else ""
            return MetadataCandidate(value, "regex.arxiv", 0.98, source_id=source_id)
    return None


def _first_abstract_hit(
    passages: Sequence[Mapping[str, Any] | BaseModel],
) -> MetadataCandidate | None:
    for passage in passages:
        data = _object_to_mapping(passage)
        labels = [
            _optional_string(data, "section"),
            _optional_string(data, "passage_type"),
            " ".join(_json_list(data.get("heading_path_json"))),
        ]
        labels.extend(_string_list(data.get("heading_path")))
        if not any(_is_abstract_label(label) for label in labels):
            continue
        text = _clean_abstract(_optional_string(data, "original_text", "text"))
        if text:
            passage_id = _optional_string(data, "id", "source_id")
            return MetadataCandidate(
                text,
                "document.abstract",
                0.75,
                source_id=passage_id,
            )
    return None


def _int_value(value: Any, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _is_abstract_label(value: str) -> bool:
    return _clean_text(value).lower() == "abstract"


def _add_candidate(
    candidates: dict[str, MetadataCandidate],
    field: str,
    candidate: MetadataCandidate | None,
) -> None:
    if candidate is None or not _candidate_has_value(candidate):
        return
    existing = candidates.get(field)
    if existing is None or _source_priority(candidate.source) > _source_priority(existing.source):
        candidates[field] = candidate


def extract_core_metadata_candidates(
    *,
    passages: Sequence[Mapping[str, Any] | BaseModel],
    elements: Sequence[Mapping[str, Any] | BaseModel],
    parse_metadata: Mapping[str, Any] | None = None,
    file_path: str | Path | None = None,
) -> dict[str, MetadataCandidate]:
    """Extract deterministic core metadata candidates from parse outputs."""
    candidates: dict[str, MetadataCandidate] = {}
    _add_candidate(candidates, "title", _first_page_title(elements))
    _add_candidate(candidates, "doi", _first_doi_hit(passages, elements))
    _add_candidate(candidates, "arxiv_id", _first_arxiv_hit(passages, elements))
    _add_candidate(candidates, "abstract", _first_abstract_hit(passages))

    fallback_title = filename_fallback_title(file_path)
    _add_candidate(
        candidates,
        "title",
        MetadataCandidate(fallback_title, "filename.fallback", 0.1),
    )
    return candidates


def filename_fallback_title(file_path: str | Path | None) -> str:
    if file_path is None:
        return ""
    stem = Path(str(file_path)).stem
    return _clean_title(stem.replace("_", " ").replace("-", " "))


def metadata_candidates_from_ai(metadata: Any) -> dict[str, MetadataCandidate]:
    """Return low-priority metadata candidates from an AI extraction object."""
    if hasattr(metadata, "model_dump"):
        data = metadata.model_dump()
    elif isinstance(metadata, Mapping):
        data = dict(metadata)
    else:
        return {}

    confidence = data.get("confidence")
    try:
        candidate_confidence = float(confidence) if confidence is not None else 0.6
    except (TypeError, ValueError):
        candidate_confidence = 0.6

    candidates: dict[str, MetadataCandidate] = {}
    _add_candidate(
        candidates,
        "title",
        MetadataCandidate(_clean_title(str(data.get("title") or "")), "llm.metadata", candidate_confidence),
    )
    _add_candidate(
        candidates,
        "authors",
        MetadataCandidate(_author_list(data.get("authors")), "llm.metadata", candidate_confidence),
    )
    _add_candidate(
        candidates,
        "year",
        MetadataCandidate(_year_value(data.get("year")), "llm.metadata", candidate_confidence),
    )
    _add_candidate(
        candidates,
        "doi",
        MetadataCandidate(_normalize_doi(str(data.get("doi") or "")), "llm.metadata", candidate_confidence),
    )
    _add_candidate(
        candidates,
        "arxiv_id",
        MetadataCandidate(
            _normalize_arxiv_id(str(data.get("arxiv_id") or "")),
            "llm.metadata",
            candidate_confidence,
        ),
    )
    _add_candidate(
        candidates,
        "venue",
        MetadataCandidate(_clean_text(str(data.get("venue") or "")), "llm.metadata", candidate_confidence),
    )
    _add_candidate(
        candidates,
        "abstract",
        MetadataCandidate(
            _clean_abstract(str(data.get("abstract") or "")),
            "llm.metadata",
            candidate_confidence,
        ),
    )
    return candidates


def merge_metadata_candidates(
    *candidate_groups: Mapping[str, MetadataCandidate],
) -> dict[str, MetadataCandidate]:
    merged: dict[str, MetadataCandidate] = {}
    for candidates in candidate_groups:
        for field, candidate in candidates.items():
            _add_candidate(merged, field, candidate)
    return merged


def _infer_metadata_status(
    sources: Mapping[str, Any],
    user_fields: Sequence[str],
) -> str:
    if user_fields:
        return "user_edited"
    source_values = [str(value) for value in sources.values()]
    if any(source.startswith("llm.") for source in source_values):
        return "enriched"
    if any(source and source != "filename.fallback" for source in source_values):
        return "extracted"
    return "empty"


def promote_metadata_candidates(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    space_id: str,
    candidates: Mapping[str, MetadataCandidate],
) -> dict[str, MetadataCandidate]:
    """Promote candidates to papers while preserving user-edited fields."""
    row = conn.execute(
        """
        SELECT *
        FROM papers
        WHERE id = ? AND space_id = ?
        """,
        (paper_id, space_id),
    ).fetchone()
    if row is None:
        raise ValueError("Paper not found")

    sources = _json_object(row["metadata_sources_json"])
    confidence = _json_object(row["metadata_confidence_json"])
    user_fields = set(_json_list(row["user_edited_fields_json"]))

    updates: list[str] = []
    params: list[Any] = []
    promoted: dict[str, MetadataCandidate] = {}

    for field in CORE_METADATA_FIELDS:
        candidate = candidates.get(field)
        if candidate is None or not _candidate_has_value(candidate):
            continue
        if field in user_fields:
            continue

        existing_value = row[field]
        existing_source = str(sources.get(field) or "")
        existing_has_value = _has_value(existing_value)
        if existing_has_value and not existing_source:
            existing_source = "user.edit"
        if existing_has_value and _source_priority(existing_source) >= _source_priority(candidate.source):
            continue

        updates.append(f"{field} = ?")
        params.append(_candidate_value_for_db(candidate))
        sources[field] = candidate.source
        confidence[field] = candidate.confidence
        promoted[field] = candidate

    if not updates:
        next_status = _infer_metadata_status(sources, sorted(user_fields))
        if next_status != row["metadata_status"]:
            conn.execute(
                """
                UPDATE papers
                SET metadata_status = ?
                WHERE id = ? AND space_id = ?
                """,
                (next_status, paper_id, space_id),
            )
        return promoted

    next_status = _infer_metadata_status(sources, sorted(user_fields))
    updates.extend(
        [
            "metadata_status = ?",
            "metadata_sources_json = ?",
            "metadata_confidence_json = ?",
        ]
    )
    params.extend([next_status, _json(sources), _json(confidence), paper_id, space_id])
    conn.execute(
        f"UPDATE papers SET {', '.join(updates)} WHERE id = ? AND space_id = ?",
        params,
    )
    return promoted


def mark_user_edited_metadata_fields(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    space_id: str,
    fields: Sequence[str],
) -> None:
    """Record manually edited core metadata fields."""
    edited_fields = [field for field in fields if field in CORE_METADATA_FIELDS]
    if not edited_fields:
        return

    row = conn.execute(
        """
        SELECT metadata_sources_json, metadata_confidence_json, user_edited_fields_json
        FROM papers
        WHERE id = ? AND space_id = ?
        """,
        (paper_id, space_id),
    ).fetchone()
    if row is None:
        raise ValueError("Paper not found")

    sources = _json_object(row["metadata_sources_json"])
    confidence = _json_object(row["metadata_confidence_json"])
    user_fields = set(_json_list(row["user_edited_fields_json"]))
    for field in edited_fields:
        user_fields.add(field)
        sources[field] = "user.edit"
        confidence[field] = 1.0

    conn.execute(
        """
        UPDATE papers
        SET metadata_status = 'user_edited',
            metadata_sources_json = ?,
            metadata_confidence_json = ?,
            user_edited_fields_json = ?
        WHERE id = ? AND space_id = ?
        """,
        (
            _json(sources),
            _json(confidence),
            _json(sorted(user_fields)),
            paper_id,
            space_id,
        ),
    )


def promote_core_metadata_from_parse(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    space_id: str,
    parse_run_id: str,
) -> dict[str, MetadataCandidate]:
    """Load persisted parse outputs and promote deterministic core metadata."""
    paper = conn.execute(
        "SELECT file_path FROM papers WHERE id = ? AND space_id = ?",
        (paper_id, space_id),
    ).fetchone()
    if paper is None:
        raise ValueError("Paper not found")

    parse_run = conn.execute(
        """
        SELECT metadata_json
        FROM parse_runs
        WHERE id = ? AND paper_id = ? AND space_id = ?
        """,
        (parse_run_id, paper_id, space_id),
    ).fetchone()
    parse_metadata = _json_object(parse_run["metadata_json"]) if parse_run else {}

    passage_rows = conn.execute(
        """
        SELECT id, original_text, section, passage_type, heading_path_json
        FROM passages
        WHERE paper_id = ? AND space_id = ? AND parse_run_id = ?
        ORDER BY page_number, paragraph_index, id
        """,
        (paper_id, space_id, parse_run_id),
    ).fetchall()
    element_rows = conn.execute(
        """
        SELECT id, element_index, element_type, text, page_number, heading_path_json
        FROM document_elements
        WHERE paper_id = ? AND space_id = ? AND parse_run_id = ?
        ORDER BY element_index, id
        """,
        (paper_id, space_id, parse_run_id),
    ).fetchall()

    candidates = extract_core_metadata_candidates(
        passages=[dict(row) for row in passage_rows],
        elements=[dict(row) for row in element_rows],
        parse_metadata=parse_metadata,
        file_path=str(paper["file_path"]),
    )
    return promote_metadata_candidates(
        conn,
        paper_id=paper_id,
        space_id=space_id,
        candidates=candidates,
    )


def promote_core_metadata_from_ai(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    space_id: str,
    metadata: Any,
) -> dict[str, MetadataCandidate]:
    candidates = metadata_candidates_from_ai(metadata)
    return promote_metadata_candidates(
        conn,
        paper_id=paper_id,
        space_id=space_id,
        candidates=candidates,
    )


__all__ = [
    "CORE_METADATA_FIELDS",
    "MetadataCandidate",
    "extract_core_metadata_candidates",
    "filename_fallback_title",
    "mark_user_edited_metadata_fields",
    "merge_metadata_candidates",
    "metadata_candidates_from_ai",
    "promote_core_metadata_from_ai",
    "promote_core_metadata_from_parse",
    "promote_metadata_candidates",
]
