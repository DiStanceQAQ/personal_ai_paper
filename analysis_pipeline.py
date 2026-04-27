"""Multi-stage source-grounded AI paper analysis pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, TypeAlias

from pydantic import BaseModel, ValidationError

from analysis_models import PaperMetadataExtraction
from analysis_prompts import build_metadata_extraction_prompt
from llm_client import LLMStructuredOutputError, call_llm_schema


PipelineInput: TypeAlias = Mapping[str, Any] | BaseModel

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
ARXIV_RE = re.compile(
    r"(?i)(?:arxiv\s*:?\s*|arxiv\.org/(?:abs|pdf)/)"
    r"([a-z-]+(?:\.[a-z]{2})?/\d{7}(?:v\d+)?|\d{4}\.\d{4,5}(?:v\d+)?)"
)
YEAR_RE = re.compile(r"\b(18|19|20)\d{2}\b")


async def extract_metadata_stage(
    paper_id: str,
    passages: Sequence[PipelineInput],
    elements: Sequence[PipelineInput],
    grobid_metadata: Mapping[str, Any] | None,
) -> PaperMetadataExtraction:
    """Extract scholarly metadata with deterministic sources before LLM fallback."""
    grobid = _metadata_from_grobid(grobid_metadata or {})
    llm = await _llm_metadata(passages, grobid_metadata or {})

    first_page_title = _first_page_title(elements)
    doi_hit = _first_doi_hit(passages, elements)
    arxiv_hit = _first_arxiv_hit(passages, elements)
    abstract_hit = _first_abstract_hit(passages)

    source_passage_ids = _dedupe_strings(
        [
            *llm.source_passage_ids,
            doi_hit.source_id,
            arxiv_hit.source_id,
            abstract_hit.source_id,
        ]
    )

    metadata = {
        **llm.metadata,
        "paper_id": paper_id,
        "metadata_stage": {
            "title": first_page_title.source_name
            if first_page_title.value
            else _source_name(grobid.title, "grobid.title")
            or _source_name(llm.title, "llm.title"),
            "authors": _source_name(grobid.authors, "grobid.authors")
            or _source_name(llm.authors, "llm.authors"),
            "year": _source_name(grobid.year, "grobid.year")
            or _source_name(llm.year, "llm.year"),
            "doi": _source_name(grobid.doi, "grobid.doi")
            or doi_hit.source_name
            or _source_name(llm.doi, "llm.doi"),
            "arxiv_id": _source_name(grobid.arxiv_id, "grobid.arxiv_id")
            or arxiv_hit.source_name
            or _source_name(llm.arxiv_id, "llm.arxiv_id"),
            "venue": _source_name(grobid.venue, "grobid.venue")
            or _source_name(llm.venue, "llm.venue"),
            "abstract": _source_name(grobid.abstract, "grobid.abstract")
            or abstract_hit.source_name
            or _source_name(llm.abstract, "llm.abstract"),
        },
    }
    if llm.metadata.get("llm_error"):
        metadata["llm_error"] = llm.metadata["llm_error"]

    return PaperMetadataExtraction(
        title=first_page_title.value or grobid.title or llm.title,
        authors=grobid.authors or llm.authors,
        year=grobid.year if grobid.year is not None else llm.year,
        venue=grobid.venue or llm.venue,
        doi=grobid.doi or doi_hit.value or _normalize_doi(llm.doi),
        arxiv_id=grobid.arxiv_id or arxiv_hit.value or _normalize_arxiv_id(llm.arxiv_id),
        abstract=grobid.abstract or abstract_hit.value or llm.abstract,
        source_passage_ids=source_passage_ids,
        confidence=llm.confidence,
        metadata=metadata,
    )


class _SourceValue(BaseModel):
    """A normalized value and where it came from."""

    value: str = ""
    source_id: str = ""
    source_name: str = ""


def _metadata_from_grobid(metadata: Mapping[str, Any]) -> PaperMetadataExtraction:
    return PaperMetadataExtraction(
        title=_clean_title(_optional_string(metadata, "title")),
        authors=_author_list(metadata.get("authors")),
        year=_year_value(metadata.get("year")),
        venue=_clean_text(_optional_string(metadata, "venue", "journal", "conference")),
        doi=_normalize_doi(_optional_string(metadata, "doi")),
        arxiv_id=_normalize_arxiv_id(
            _optional_string(metadata, "arxiv_id", "arxiv", "arxivId")
        ),
        abstract=_clean_abstract(_optional_string(metadata, "abstract")),
        metadata={"source": "grobid"} if metadata else {},
    )


async def _llm_metadata(
    passages: Sequence[PipelineInput],
    grobid_metadata: Mapping[str, Any],
) -> PaperMetadataExtraction:
    try:
        prompt = build_metadata_extraction_prompt(
            passages,
            grobid_metadata=grobid_metadata,
        )
        response = await call_llm_schema(
            prompt.system_prompt,
            prompt.user_prompt,
            prompt.schema_name,
            prompt.schema,
        )
        return PaperMetadataExtraction.model_validate(response)
    except (LLMStructuredOutputError, ValidationError, ValueError) as exc:
        return PaperMetadataExtraction(metadata={"llm_error": str(exc)})


def _first_page_title(elements: Sequence[PipelineInput]) -> _SourceValue:
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

    first_page_candidates = [
        candidate for candidate in candidates if candidate[0] in {0, 1}
    ]
    selected_pool = first_page_candidates or candidates
    if not selected_pool:
        return _SourceValue()

    page_number, _element_index, title, element_id = min(
        selected_pool,
        key=lambda candidate: (candidate[0], candidate[1]),
    )
    source_suffix = f":{element_id}" if element_id else f":page-{page_number}"
    return _SourceValue(
        value=title,
        source_name=f"element.title{source_suffix}",
    )


def _first_doi_hit(
    passages: Sequence[PipelineInput],
    elements: Sequence[PipelineInput],
) -> _SourceValue:
    for data in _source_texts(passages, elements):
        match = DOI_RE.search(data["text"])
        if match is not None:
            value = _normalize_doi(match.group(0))
            if value:
                source_id = data["source_id"]
                return _SourceValue(
                    value=value,
                    source_id=source_id if data["kind"] == "passage" else "",
                    source_name=f"{data['kind']}:{source_id}",
                )
    return _SourceValue()


def _first_arxiv_hit(
    passages: Sequence[PipelineInput],
    elements: Sequence[PipelineInput],
) -> _SourceValue:
    for data in _source_texts(passages, elements):
        match = ARXIV_RE.search(data["text"])
        if match is not None:
            value = _normalize_arxiv_id(match.group(1))
            if value:
                source_id = data["source_id"]
                return _SourceValue(
                    value=value,
                    source_id=source_id if data["kind"] == "passage" else "",
                    source_name=f"{data['kind']}:{source_id}",
                )
    return _SourceValue()


def _first_abstract_hit(passages: Sequence[PipelineInput]) -> _SourceValue:
    for passage in passages:
        data = _object_to_mapping(passage)
        section_values = [
            _optional_string(data, "section"),
            _optional_string(data, "passage_type"),
            " ".join(_string_list(data.get("heading_path"))),
        ]
        if any(_is_abstract_label(value) for value in section_values):
            text = _clean_abstract(_optional_string(data, "original_text", "text"))
            if text:
                passage_id = _optional_string(data, "id", "source_id")
                return _SourceValue(
                    value=text,
                    source_id=passage_id,
                    source_name=f"passage:{passage_id}",
                )
    return _SourceValue()


def _source_texts(
    passages: Sequence[PipelineInput],
    elements: Sequence[PipelineInput],
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


def _object_to_mapping(value: PipelineInput) -> Mapping[str, Any]:
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


def _int_value(value: Any, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _author_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _dedupe_strings(re.split(r"\s*(?:;|\band\b)\s*", value))
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        authors: list[str] = []
        for item in value:
            author = _author_name(item)
            if author:
                authors.append(author)
        return _dedupe_strings(authors)
    return []


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


def _year_value(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if value is None:
        return None
    match = YEAR_RE.search(str(value))
    if match is None:
        return None
    return int(match.group(0))


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


def _clean_title(value: str) -> str:
    return re.sub(r"(?i)^title\s*[:.\-]?\s*", "", _clean_text(value))


def _clean_abstract(value: str) -> str:
    return re.sub(r"(?i)^abstract\s*[:.\-]?\s*", "", _clean_text(value))


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _is_abstract_label(value: str) -> bool:
    return _clean_text(value).lower() == "abstract"


def _source_name(value: object, name: str) -> str:
    if isinstance(value, list):
        return name if value else ""
    return name if value else ""


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


__all__ = ["extract_metadata_stage"]
