"""Multi-stage source-grounded AI paper analysis pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import re
from typing import Any, TypeAlias

from pydantic import BaseModel, ValidationError

from analysis_models import CardExtraction, CardExtractionBatch, PaperMetadataExtraction
from analysis_prompts import (
    build_card_batch_extraction_prompt,
    build_metadata_extraction_prompt,
)
from analysis_verifier import (
    RejectedCardDiagnostic,
    SourceVerificationResult,
    verify_extraction_batch_sources,
)
from llm_client import LLMStructuredOutputError, call_llm_schema
from pdf_chunker import count_text_tokens


PipelineInput: TypeAlias = Mapping[str, Any] | BaseModel

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
ARXIV_RE = re.compile(
    r"(?i)(?:arxiv\s*:?\s*|arxiv\.org/(?:abs|pdf)/)"
    r"([a-z-]+(?:\.[a-z]{2})?/\d{7}(?:v\d+)?|\d{4}\.\d{4,5}(?:v\d+)?)"
)
YEAR_RE = re.compile(r"\b(18|19|20)\d{2}\b")
SECTION_NUMBER_RE = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*)|(?:[ivxlcdm]+))(?:[\).:\-]\s*|\s+)",
    re.IGNORECASE,
)
DEFAULT_ANALYSIS_BATCH_TOKEN_BUDGET = 3500
SECTION_PRIORITIES: dict[str, int] = {
    "abstract": 0,
    "introduction": 1,
    "method": 2,
    "result": 3,
    "discussion": 4,
    "limitation": 5,
    "body": 6,
    "appendix": 7,
    "reference": 8,
}
REFERENCE_LABELS = {
    "reference",
    "references",
    "bibliography",
    "works cited",
    "literature cited",
}


@dataclass(frozen=True)
class AnalysisPassageBatch:
    """A token-bounded, source-preserving passage batch for one LLM request."""

    batch_index: int
    group_key: str
    heading_path: tuple[str, ...]
    passage_type: str
    passages: tuple[PipelineInput, ...]
    source_passage_ids: tuple[str, ...]
    token_count: int


@dataclass(frozen=True)
class _PreparedAnalysisPassage:
    passage: PipelineInput
    source_id: str
    text: str
    page_number: int
    heading_path: tuple[str, ...]
    passage_type: str
    priority: int
    position: int
    token_count: int


def select_analysis_passage_batches(
    passages: Sequence[PipelineInput],
    *,
    max_batch_tokens: int = DEFAULT_ANALYSIS_BATCH_TOKEN_BUDGET,
    include_references: bool = False,
) -> list[AnalysisPassageBatch]:
    """Select full-paper passages and group them into token-bounded LLM batches."""
    if max_batch_tokens <= 0:
        raise ValueError("max_batch_tokens must be positive")

    grouped: dict[tuple[str, tuple[str, ...]], list[_PreparedAnalysisPassage]] = {}
    group_order: dict[tuple[str, tuple[str, ...]], tuple[int, int]] = {}

    for position, passage in enumerate(passages):
        prepared_passages = _prepare_analysis_passages(
            passage,
            position=position,
            max_batch_tokens=max_batch_tokens,
            include_references=include_references,
        )
        for prepared in prepared_passages:
            group_key = (prepared.passage_type, prepared.heading_path)
            grouped.setdefault(group_key, []).append(prepared)
            current_order = group_order.get(group_key)
            candidate_order = (prepared.priority, prepared.position)
            if current_order is None or candidate_order < current_order:
                group_order[group_key] = candidate_order

    ordered_groups = sorted(
        grouped.items(),
        key=lambda item: (
            group_order[item[0]][0],
            group_order[item[0]][1],
            item[0][0],
            item[0][1],
        ),
    )

    batches: list[AnalysisPassageBatch] = []
    for (passage_type, heading_path), group_passages in ordered_groups:
        current: list[_PreparedAnalysisPassage] = []

        def flush_current() -> None:
            if not current:
                return
            batches.append(
                _make_analysis_batch(
                    batch_index=len(batches),
                    passage_type=passage_type,
                    heading_path=heading_path,
                    passages=current,
                )
            )
            current.clear()

        for prepared in group_passages:
            candidate = [*current, prepared]
            candidate_tokens = _analysis_batch_token_count(candidate)
            if current and candidate_tokens > max_batch_tokens:
                flush_current()
                current.append(prepared)
            else:
                current = candidate

        flush_current()

    return batches


def _prepare_analysis_passages(
    passage: PipelineInput,
    *,
    position: int,
    max_batch_tokens: int,
    include_references: bool,
) -> list[_PreparedAnalysisPassage]:
    data = _object_to_mapping(passage)
    source_id = _optional_string(data, "id", "source_id")
    text = _optional_string(data, "original_text", "text")
    if not source_id or not text:
        return []

    raw_passage_type = _optional_string(data, "passage_type", "type")
    section = _optional_string(data, "section")
    heading_path = tuple(_heading_path_for_analysis(data, section))
    if not include_references and _is_reference_passage(
        raw_passage_type,
        section,
        heading_path,
    ):
        return []

    passage_type = _analysis_passage_type(raw_passage_type, section, heading_path)
    page_number = _int_value(data.get("page_number", data.get("page_start")), default=0)
    priority = _section_priority(passage_type, section, heading_path)
    fragments = _split_passage_for_analysis_budget(
        passage,
        source_id=source_id,
        text=text,
        page_number=page_number,
        heading_path=heading_path,
        passage_type=passage_type,
        max_batch_tokens=max_batch_tokens,
    )

    prepared: list[_PreparedAnalysisPassage] = []
    for fragment_passage, fragment_text in fragments:
        token_count = count_text_tokens(
            _analysis_passage_payload(
                source_id=source_id,
                page_number=page_number,
                heading_path=heading_path,
                passage_type=passage_type,
                text=fragment_text,
            )
        )
        prepared.append(
            _PreparedAnalysisPassage(
                passage=fragment_passage,
                source_id=source_id,
                text=fragment_text,
                page_number=page_number,
                heading_path=heading_path,
                passage_type=passage_type,
                priority=priority,
                position=position,
                token_count=token_count,
            )
        )
    return prepared


def _make_analysis_batch(
    *,
    batch_index: int,
    passage_type: str,
    heading_path: tuple[str, ...],
    passages: Sequence[_PreparedAnalysisPassage],
) -> AnalysisPassageBatch:
    return AnalysisPassageBatch(
        batch_index=batch_index,
        group_key=_analysis_group_key(passage_type, heading_path),
        heading_path=heading_path,
        passage_type=passage_type,
        passages=tuple(passage.passage for passage in passages),
        source_passage_ids=tuple(
            _dedupe_strings([passage.source_id for passage in passages])
        ),
        token_count=_analysis_batch_token_count(passages),
    )


def _analysis_batch_token_count(
    passages: Sequence[_PreparedAnalysisPassage],
) -> int:
    payload = "\n\n".join(
        _analysis_passage_payload(
            source_id=passage.source_id,
            page_number=passage.page_number,
            heading_path=passage.heading_path,
            passage_type=passage.passage_type,
            text=passage.text,
        )
        for passage in passages
    )
    return count_text_tokens(payload)


def _split_passage_for_analysis_budget(
    passage: PipelineInput,
    *,
    source_id: str,
    text: str,
    page_number: int,
    heading_path: tuple[str, ...],
    passage_type: str,
    max_batch_tokens: int,
) -> list[tuple[PipelineInput, str]]:
    if (
        count_text_tokens(
            _analysis_passage_payload(
                source_id=source_id,
                page_number=page_number,
                heading_path=heading_path,
                passage_type=passage_type,
                text=text,
            )
        )
        <= max_batch_tokens
    ):
        return [(passage, text)]

    overhead = count_text_tokens(
        _analysis_passage_payload(
            source_id=source_id,
            page_number=page_number,
            heading_path=heading_path,
            passage_type=passage_type,
            text="",
        )
    )
    text_budget = max(1, max_batch_tokens - overhead)
    text_fragments = _split_text_to_token_budget(text, text_budget)
    fragment_count = len(text_fragments)
    return [
        (
            _analysis_fragment_passage(
                passage,
                text=fragment_text,
                fragment_index=fragment_index,
                fragment_count=fragment_count,
            ),
            fragment_text,
        )
        for fragment_index, fragment_text in enumerate(text_fragments)
    ]


def _split_text_to_token_budget(text: str, max_tokens: int) -> list[str]:
    normalized = _clean_text(text)
    if not normalized:
        return []
    if count_text_tokens(normalized) <= max_tokens:
        return [normalized]

    fragments: list[str] = []
    current_words: list[str] = []
    for word in normalized.split():
        candidate_words = [*current_words, word]
        candidate_text = " ".join(candidate_words)
        if current_words and count_text_tokens(candidate_text) > max_tokens:
            fragments.append(" ".join(current_words))
            current_words = [word]
        else:
            current_words = candidate_words

        current_text = " ".join(current_words)
        if current_text and count_text_tokens(current_text) > max_tokens:
            fragments.extend(_split_long_text_to_token_budget(current_text, max_tokens))
            current_words = []

    if current_words:
        fragments.append(" ".join(current_words))
    return [fragment for fragment in fragments if fragment]


def _split_long_text_to_token_budget(text: str, max_tokens: int) -> list[str]:
    fragments: list[str] = []
    current = ""
    for char in text:
        candidate = f"{current}{char}"
        if current and count_text_tokens(candidate) > max_tokens:
            fragments.append(current.rstrip())
            current = char.lstrip()
        else:
            current = candidate
    if current:
        fragments.append(current.rstrip())
    return [fragment for fragment in fragments if fragment]


def _analysis_fragment_passage(
    passage: PipelineInput,
    *,
    text: str,
    fragment_index: int,
    fragment_count: int,
) -> dict[str, Any]:
    data = dict(_object_to_mapping(passage))
    data["original_text"] = text
    data["text"] = text
    metadata_value = data.get("metadata")
    metadata = dict(metadata_value) if isinstance(metadata_value, Mapping) else {}
    metadata["analysis_fragment_index"] = fragment_index
    metadata["analysis_fragment_count"] = fragment_count
    data["metadata"] = metadata
    return data


def _analysis_passage_payload(
    *,
    source_id: str,
    page_number: int,
    heading_path: Sequence[str],
    passage_type: str,
    text: str,
) -> str:
    return json.dumps(
        {
            "source_id": source_id,
            "page_number": page_number,
            "passage_type": passage_type,
            "heading_path": list(heading_path),
            "text": text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _analysis_group_key(passage_type: str, heading_path: Sequence[str]) -> str:
    heading = " > ".join(heading_path)
    return f"{passage_type}:{heading}" if heading else passage_type


def _heading_path_for_analysis(
    data: Mapping[str, Any],
    section: str,
) -> list[str]:
    heading_path = _string_list(data.get("heading_path"))
    if heading_path:
        return heading_path
    return [section] if section else []


def _is_reference_passage(
    raw_passage_type: str,
    section: str,
    heading_path: Sequence[str],
) -> bool:
    labels = [raw_passage_type, section, *heading_path]
    return any(_normalized_section_label(label) in REFERENCE_LABELS for label in labels)


def _analysis_passage_type(
    raw_passage_type: str,
    section: str,
    heading_path: Sequence[str],
) -> str:
    raw_label = _normalized_section_label(raw_passage_type)
    if raw_label in SECTION_PRIORITIES and raw_label != "body":
        return raw_label

    labels = [section, *heading_path]
    normalized = " ".join(_normalized_section_label(label) for label in labels)
    if "abstract" in normalized:
        return "abstract"
    if "introduction" in normalized:
        return "introduction"
    if any(term in normalized for term in ("method", "approach", "experiment")):
        return "method"
    if any(term in normalized for term in ("result", "evaluation", "finding")):
        return "result"
    if "discussion" in normalized:
        return "discussion"
    if any(term in normalized for term in ("limitation", "future work")):
        return "limitation"
    if "appendix" in normalized:
        return "appendix"
    if any(_normalized_section_label(label) in REFERENCE_LABELS for label in labels):
        return "reference"
    return "body"


def _section_priority(
    passage_type: str,
    section: str,
    heading_path: Sequence[str],
) -> int:
    inferred_type = _analysis_passage_type(passage_type, section, heading_path)
    return SECTION_PRIORITIES.get(inferred_type, SECTION_PRIORITIES["body"])


def _normalized_section_label(value: str) -> str:
    text = _clean_text(value).lower()
    text = SECTION_NUMBER_RE.sub("", text)
    return text.strip(" .:-)\t")


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


async def extract_card_batches_stage(
    paper_id: str,
    space_id: str,
    batches: Sequence[AnalysisPassageBatch],
) -> SourceVerificationResult:
    """Extract and source-verify AI cards for selected passage batches."""
    accepted_cards: list[CardExtraction] = []
    rejected_cards: list[RejectedCardDiagnostic] = []

    for batch in batches:
        try:
            extraction_batch = await _call_card_batch_extraction(
                paper_id=paper_id,
                space_id=space_id,
                batch=batch,
            )
        except (LLMStructuredOutputError, ValidationError, ValueError) as exc:
            rejected_cards.append(
                _card_batch_failure_diagnostic(
                    batch,
                    stage="initial",
                    exc=exc,
                )
            )
            continue

        verification = verify_extraction_batch_sources(
            extraction_batch,
            batch.passages,
        )
        accepted_cards.extend(verification.accepted_cards)
        if not verification.rejected_cards:
            continue

        try:
            repaired_batch = await _call_card_batch_extraction(
                paper_id=paper_id,
                space_id=space_id,
                batch=batch,
                repair_diagnostics=verification.rejected_cards,
            )
        except (LLMStructuredOutputError, ValidationError, ValueError) as exc:
            rejected_cards.extend(
                _diagnostics_with_repair_error(verification.rejected_cards, exc)
            )
            continue

        repaired_verification = verify_extraction_batch_sources(
            repaired_batch,
            batch.passages,
        )
        accepted_cards.extend(repaired_verification.accepted_cards)
        rejected_cards.extend(repaired_verification.rejected_cards)

    return SourceVerificationResult(
        accepted_cards=accepted_cards,
        rejected_cards=rejected_cards,
    )


async def _call_card_batch_extraction(
    *,
    paper_id: str,
    space_id: str,
    batch: AnalysisPassageBatch,
    repair_diagnostics: Sequence[RejectedCardDiagnostic] | None = None,
) -> CardExtractionBatch:
    prompt = build_card_batch_extraction_prompt(
        paper_id=paper_id,
        space_id=space_id,
        batch_index=batch.batch_index,
        passages=batch.passages,
    )
    user_prompt = prompt.user_prompt
    if repair_diagnostics:
        user_prompt = _card_batch_repair_prompt(user_prompt, repair_diagnostics)

    response = await call_llm_schema(
        prompt.system_prompt,
        user_prompt,
        prompt.schema_name,
        prompt.schema,
    )
    return CardExtractionBatch.model_validate(response)


def _card_batch_repair_prompt(
    original_user_prompt: str,
    diagnostics: Sequence[RejectedCardDiagnostic],
) -> str:
    return "\n".join(
        [
            original_user_prompt,
            (
                "Repair request: The previous CardExtractionBatch passed schema "
                "validation, but source verification rejected the following cards."
            ),
            (
                "Return a CardExtractionBatch containing only corrected replacements "
                "for rejected cards. Do not repeat accepted cards. Drop any card that "
                "cannot be repaired from the provided source passages."
            ),
            "Rejected diagnostics (JSON):",
            _diagnostics_json(diagnostics),
        ]
    )


def _diagnostics_json(diagnostics: Sequence[RejectedCardDiagnostic]) -> str:
    return json.dumps(
        [_diagnostic_payload(diagnostic) for diagnostic in diagnostics],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _diagnostic_payload(
    diagnostic: RejectedCardDiagnostic,
) -> dict[str, Any]:
    return {
        "card_index": diagnostic.card_index,
        "reason": diagnostic.reason,
        "message": diagnostic.message,
        "source_passage_ids": diagnostic.source_passage_ids,
        "evidence_quote": diagnostic.evidence_quote,
        "batch_index": diagnostic.batch_index,
        "metadata": diagnostic.metadata,
    }


def _card_batch_failure_diagnostic(
    batch: AnalysisPassageBatch,
    *,
    stage: str,
    exc: Exception,
) -> RejectedCardDiagnostic:
    return RejectedCardDiagnostic(
        card_index=-1,
        reason="invalid_batch",
        message=f"Card batch extraction failed during {stage}: {exc}",
        source_passage_ids=list(batch.source_passage_ids),
        batch_index=batch.batch_index,
        metadata={
            "stage": stage,
            "group_key": batch.group_key,
            "error": str(exc),
        },
    )


def _diagnostics_with_repair_error(
    diagnostics: Sequence[RejectedCardDiagnostic],
    exc: Exception,
) -> list[RejectedCardDiagnostic]:
    return [
        RejectedCardDiagnostic(
            card_index=diagnostic.card_index,
            reason=diagnostic.reason,
            message=diagnostic.message,
            source_passage_ids=list(diagnostic.source_passage_ids),
            evidence_quote=diagnostic.evidence_quote,
            batch_index=diagnostic.batch_index,
            metadata={
                **diagnostic.metadata,
                "repair_error": str(exc),
            },
        )
        for diagnostic in diagnostics
    ]


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


__all__ = [
    "AnalysisPassageBatch",
    "extract_card_batches_stage",
    "extract_metadata_stage",
    "select_analysis_passage_batches",
]
