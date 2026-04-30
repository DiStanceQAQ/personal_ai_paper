"""Multi-stage source-grounded AI paper analysis pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
import json
import re
import sqlite3
import time
import uuid
from typing import Any, TypeAlias

from pydantic import BaseModel, ValidationError

from paper_engine.analysis.models import (
    AnalysisQualityReport,
    CardExtraction,
    CardExtractionBatch,
    MergedAnalysisResult,
    PaperMetadataExtraction,
    PaperUnderstandingExtraction,
)
from paper_engine.analysis.jobs import (
    AnalysisRunCancelled,
    is_analysis_run_cancelled,
)
from paper_engine.analysis.prompts import (
    build_card_batch_extraction_prompt,
    build_metadata_extraction_prompt,
    build_paper_understanding_prompt,
)
from paper_engine.analysis.verifier import (
    RejectedCardDiagnostic,
    SourceVerificationResult,
    verify_extraction_batch_sources,
)
from paper_engine.papers.metadata import (
    extract_core_metadata_candidates,
    merge_metadata_candidates,
    metadata_candidates_from_ai,
    promote_core_metadata_from_ai,
)
from paper_engine.storage.database import get_connection
from paper_engine.agent.llm_client import (
    LLMRequestError,
    LLMStructuredOutputError,
    call_llm_schema,
)
from paper_engine.pdf.chunking import count_text_tokens


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
DEFAULT_ANALYSIS_BATCH_TOKEN_BUDGET = 8000
DEFAULT_FINAL_AI_CARD_LIMIT = 20
DEFAULT_CARD_EXTRACTION_CONCURRENCY = 3
METADATA_LLM_MAX_PASSAGES = 10
METADATA_LLM_FRONTMATTER_PAGES = 3
UNDERSTANDING_LLM_MAX_PASSAGES = 24
UNDERSTANDING_LLM_TOKEN_BUDGET = 12_000
SUMMARY_DUPLICATE_SIMILARITY_THRESHOLD = 0.75
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
KEY_RANKING_SECTIONS = {"method", "result", "limitation"}
SUMMARY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}
SUMMARY_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")
SUMMARY_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


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
class CardRankingResult:
    """Final ranked AI cards plus diagnostics for analysis run persistence."""

    cards: list[CardExtraction]
    rejected_cards: list[RejectedCardDiagnostic]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class PaperAnalysisRunResult:
    """Persisted output from a complete multi-stage paper analysis run."""

    analysis_run_id: str
    result: MergedAnalysisResult


@dataclass(frozen=True)
class _CardBatchResult:
    batch: AnalysisPassageBatch
    accepted_cards: list[CardExtraction]
    rejected_cards: list[RejectedCardDiagnostic]
    progress: dict[str, Any]


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


@dataclass(frozen=True)
class _RankedCardCandidate:
    card: CardExtraction
    original_index: int
    normalized_summary: str
    summary_tokens: frozenset[str]
    source_ids: frozenset[str]
    section_score: int


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

    return _merge_low_priority_body_batches(
        batches,
        max_batch_tokens=max_batch_tokens,
    )


def _merge_low_priority_body_batches(
    batches: Sequence[AnalysisPassageBatch],
    *,
    max_batch_tokens: int,
) -> list[AnalysisPassageBatch]:
    merged: list[AnalysisPassageBatch] = []
    current: AnalysisPassageBatch | None = None

    for batch in batches:
        if (
            current is None
            or current.passage_type != "body"
            or batch.passage_type != "body"
            or _merged_batch_token_count(current, batch) > max_batch_tokens
        ):
            if current is not None:
                merged.append(current)
            current = batch
            continue

        current = AnalysisPassageBatch(
            batch_index=current.batch_index,
            group_key=_analysis_group_key("body", ()),
            heading_path=(),
            passage_type="body",
            passages=(*current.passages, *batch.passages),
            source_passage_ids=tuple(
                _dedupe_strings([*current.source_passage_ids, *batch.source_passage_ids])
            ),
            token_count=current.token_count + batch.token_count,
        )

    if current is not None:
        merged.append(current)

    return [
        batch if batch.batch_index == index else AnalysisPassageBatch(
            batch_index=index,
            group_key=batch.group_key,
            heading_path=batch.heading_path,
            passage_type=batch.passage_type,
            passages=batch.passages,
            source_passage_ids=batch.source_passage_ids,
            token_count=batch.token_count,
        )
        for index, batch in enumerate(merged)
    ]


def _merged_batch_token_count(
    left: AnalysisPassageBatch,
    right: AnalysisPassageBatch,
) -> int:
    payload = "\n\n".join(
        _analysis_passage_payload(
            source_id=_optional_string(_object_to_mapping(passage), "id", "source_id"),
            page_number=_int_value(
                _object_to_mapping(passage).get("page_number"),
                default=0,
            ),
            heading_path=tuple(
                _heading_path_for_analysis(
                    _object_to_mapping(passage),
                    _optional_string(_object_to_mapping(passage), "section"),
                )
            ),
            passage_type=_analysis_passage_type(
                _optional_string(_object_to_mapping(passage), "passage_type", "type"),
                _optional_string(_object_to_mapping(passage), "section"),
                tuple(
                    _heading_path_for_analysis(
                        _object_to_mapping(passage),
                        _optional_string(_object_to_mapping(passage), "section"),
                    )
                ),
            ),
            text=_optional_string(_object_to_mapping(passage), "original_text", "text"),
        )
        for passage in (*left.passages, *right.passages)
    )
    return count_text_tokens(payload)


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


def _select_metadata_llm_passages(
    passages: Sequence[PipelineInput],
    *,
    max_passages: int = METADATA_LLM_MAX_PASSAGES,
) -> list[PipelineInput]:
    """Keep metadata enrichment prompts focused on frontmatter evidence."""
    if len(passages) <= max_passages:
        return list(passages)

    ranked: list[tuple[int, int, int, PipelineInput]] = []
    for position, passage in enumerate(passages):
        data = _object_to_mapping(passage)
        page_number = _int_value(data.get("page_number"), default=999)
        section = _optional_string(data, "section")
        heading_path = _heading_path_for_analysis(data, section)
        passage_type = _analysis_passage_type(
            _optional_string(data, "passage_type", "type"),
            section,
            heading_path,
        )
        labels = [section, passage_type, *heading_path]
        normalized_labels = " ".join(_normalized_section_label(label) for label in labels)
        compact_labels = normalized_labels.replace(" ", "")
        text_prefix = _clean_text(
            _optional_string(data, "text", "original_text")[:240]
        ).lower()
        compact_text_prefix = text_prefix.replace(" ", "")

        rank = 100
        if page_number <= METADATA_LLM_FRONTMATTER_PAGES:
            rank = min(rank, 10 + page_number)
        if "abstract" in compact_labels or "abstract" in compact_text_prefix:
            rank = min(rank, 0)
        if any(term in normalized_labels for term in ("article info", "keywords")):
            rank = min(rank, 1)
        if page_number <= 1:
            rank = min(rank, 2)
        if passage_type == "introduction" or "introduction" in normalized_labels:
            rank = min(rank, 30)
        if _is_reference_passage(
            _optional_string(data, "passage_type", "type"),
            section,
            heading_path,
        ):
            rank = 200

        ranked.append((rank, page_number, position, passage))

    selected = [
        passage
        for rank, _page_number, _position, passage in sorted(ranked)
        if rank < 200
    ][:max_passages]
    return selected or list(passages[:max_passages])


def _select_understanding_llm_passages(
    passages: Sequence[PipelineInput],
    *,
    max_passages: int = UNDERSTANDING_LLM_MAX_PASSAGES,
    token_budget: int = UNDERSTANDING_LLM_TOKEN_BUDGET,
) -> list[PipelineInput]:
    """Choose representative passages that help summarize the whole paper."""
    if max_passages <= 0 or token_budget <= 0:
        return []

    ranked: list[tuple[int, int, int, PipelineInput]] = []
    for position, passage in enumerate(passages):
        data = _object_to_mapping(passage)
        section = _optional_string(data, "section")
        heading_path = _heading_path_for_analysis(data, section)
        raw_passage_type = _optional_string(data, "passage_type", "type")
        if _is_reference_passage(raw_passage_type, section, heading_path):
            continue

        passage_type = _analysis_passage_type(raw_passage_type, section, heading_path)
        page_number = _int_value(data.get("page_number"), default=999)
        rank = {
            "abstract": 0,
            "introduction": 10,
            "method": 20,
            "result": 30,
            "discussion": 40,
            "limitation": 50,
            "body": 70,
            "appendix": 90,
        }.get(passage_type, 80)

        labels = " ".join([section, passage_type, *heading_path]).lower()
        if any(term in labels for term in ("conclusion", "summary")):
            rank = min(rank, 35)
        if any(term in labels for term in ("limitation", "future work")):
            rank = min(rank, 45)

        ranked.append((rank, page_number, position, passage))

    selected: list[PipelineInput] = []
    selected_source_ids: set[str] = set()
    used_tokens = 0
    for _rank, _page_number, _position, passage in sorted(ranked):
        data = _object_to_mapping(passage)
        source_id = _optional_string(data, "id", "source_id")
        if not source_id or source_id in selected_source_ids:
            continue
        text = _optional_string(data, "original_text", "text")
        token_count = count_text_tokens(text)
        if selected and used_tokens + token_count > token_budget:
            continue

        selected.append(passage)
        selected_source_ids.add(source_id)
        used_tokens += token_count
        if len(selected) >= max_passages:
            break

    return selected


async def extract_metadata_stage(
    paper_id: str,
    passages: Sequence[PipelineInput],
    elements: Sequence[PipelineInput],
) -> PaperMetadataExtraction:
    """Extract scholarly metadata with deterministic sources before LLM fallback."""
    rule_candidates = extract_core_metadata_candidates(
        passages=passages,
        elements=elements,
    )
    llm = await _llm_metadata(
        _select_metadata_llm_passages(passages),
    )
    merged_candidates = merge_metadata_candidates(
        rule_candidates,
        metadata_candidates_from_ai(llm),
    )

    source_passage_ids = _dedupe_strings(
        [
            *llm.source_passage_ids,
            *[
                candidate.source_id
                for candidate in merged_candidates.values()
                if candidate.source_id
            ],
        ]
    )

    metadata = {
        **llm.metadata,
        "paper_id": paper_id,
        "metadata_stage": {
            field: candidate.source
            for field, candidate in merged_candidates.items()
        },
    }
    if llm.metadata.get("llm_error"):
        metadata["llm_error"] = llm.metadata["llm_error"]

    authors_value = merged_candidates.get("authors")
    authors = (
        [str(author) for author in authors_value.value]
        if authors_value is not None and isinstance(authors_value.value, list)
        else []
    )
    year_value = merged_candidates.get("year")
    year: int | None = None
    if year_value is not None and year_value.value is not None:
        try:
            year = int(str(year_value.value))
        except (TypeError, ValueError):
            year = None
    title_value = merged_candidates.get("title")
    venue_value = merged_candidates.get("venue")
    doi_value = merged_candidates.get("doi")
    arxiv_id_value = merged_candidates.get("arxiv_id")
    abstract_value = merged_candidates.get("abstract")

    return PaperMetadataExtraction(
        title=str(title_value.value) if title_value is not None else "",
        authors=authors,
        year=year,
        venue=str(venue_value.value) if venue_value is not None else "",
        doi=str(doi_value.value) if doi_value is not None else "",
        arxiv_id=str(arxiv_id_value.value) if arxiv_id_value is not None else "",
        abstract=str(abstract_value.value) if abstract_value is not None else "",
        source_passage_ids=source_passage_ids,
        confidence=llm.confidence,
        metadata=metadata,
    )


async def extract_paper_understanding_stage(
    passages: Sequence[PipelineInput],
) -> PaperUnderstandingExtraction | None:
    """Synthesize a Chinese whole-paper understanding from representative evidence."""
    selected_passages = _select_understanding_llm_passages(passages)
    if not selected_passages:
        return None
    return await _llm_paper_understanding(selected_passages)


async def extract_card_batches_stage(
    paper_id: str,
    space_id: str,
    batches: Sequence[AnalysisPassageBatch],
    *,
    analysis_run_id: str | None = None,
    paper_understanding: PaperUnderstandingExtraction | None = None,
) -> SourceVerificationResult:
    """Extract and source-verify AI cards for selected passage batches."""
    batch_progress: list[dict[str, Any]] = []
    completed_results: dict[int, _CardBatchResult] = {}
    concurrency = _card_extraction_concurrency()
    semaphore = asyncio.Semaphore(concurrency)
    _record_analysis_progress(
        analysis_run_id,
        stage="card_extraction",
        total_batches=len(batches),
        completed_batches=0,
        current_batch_index=batches[0].batch_index if batches else None,
        accepted_card_count=0,
        rejected_card_count=0,
        batch_progress=batch_progress,
    )

    async def run_batch(batch: AnalysisPassageBatch) -> _CardBatchResult:
        async with semaphore:
            return await _extract_and_verify_card_batch(
                paper_id=paper_id,
                space_id=space_id,
                batch=batch,
                paper_understanding=paper_understanding,
            )

    tasks: dict[asyncio.Task[_CardBatchResult], AnalysisPassageBatch] = {
        asyncio.create_task(run_batch(batch)): batch for batch in batches
    }

    try:
        while tasks:
            _raise_if_analysis_cancelled(analysis_run_id)
            pending_batches = sorted(
                batch.batch_index
                for task, batch in tasks.items()
                if not task.done()
            )
            running_batches = set(pending_batches[:concurrency])
            _record_analysis_progress(
                analysis_run_id,
                stage="card_extraction",
                total_batches=len(batches),
                completed_batches=len(batch_progress),
                current_batch_index=min(running_batches) if running_batches else None,
                accepted_card_count=sum(
                    len(result.accepted_cards)
                    for result in completed_results.values()
                ),
                rejected_card_count=sum(
                    len(result.rejected_cards)
                    for result in completed_results.values()
                ),
                batch_progress=batch_progress,
            )

            done, _pending = await asyncio.wait(
                tasks.keys(),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                batch = tasks.pop(task)
                result = task.result()
                completed_results[batch.batch_index] = result
                batch_progress.append(result.progress)
                batch_progress.sort(key=_batch_progress_sort_key)
                _record_analysis_progress(
                    analysis_run_id,
                    stage="card_extraction",
                    total_batches=len(batches),
                    completed_batches=len(batch_progress),
                    current_batch_index=None,
                    accepted_card_count=sum(
                        len(item.accepted_cards)
                        for item in completed_results.values()
                    ),
                    rejected_card_count=sum(
                        len(item.rejected_cards)
                        for item in completed_results.values()
                    ),
                    batch_progress=batch_progress,
                )
    except Exception:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks.keys(), return_exceptions=True)
        raise

    accepted_cards: list[CardExtraction] = []
    rejected_cards: list[RejectedCardDiagnostic] = []
    for batch in batches:
        batch_result = completed_results.get(batch.batch_index)
        if batch_result is None:
            continue
        accepted_cards.extend(batch_result.accepted_cards)
        rejected_cards.extend(batch_result.rejected_cards)

    return SourceVerificationResult(
        accepted_cards=accepted_cards,
        rejected_cards=rejected_cards,
    )


async def _extract_and_verify_card_batch(
    *,
    paper_id: str,
    space_id: str,
    batch: AnalysisPassageBatch,
    paper_understanding: PaperUnderstandingExtraction | None = None,
) -> _CardBatchResult:
    accepted_cards: list[CardExtraction] = []
    rejected_cards: list[RejectedCardDiagnostic] = []
    try:
        extraction_batch = await _call_card_batch_extraction(
            paper_id=paper_id,
            space_id=space_id,
            batch=batch,
            paper_understanding=paper_understanding,
        )
    except (LLMRequestError, LLMStructuredOutputError, ValidationError, ValueError) as exc:
        diagnostic = _card_batch_failure_diagnostic(
            batch,
            stage="initial",
            exc=exc,
        )
        rejected_cards.append(diagnostic)
        return _CardBatchResult(
            batch=batch,
            accepted_cards=accepted_cards,
            rejected_cards=rejected_cards,
            progress=_analysis_batch_progress_payload(
                batch,
                status="failed",
                accepted_card_count=0,
                rejected_card_count=1,
                error=str(exc),
            ),
        )

    verification = verify_extraction_batch_sources(
        extraction_batch,
        batch.passages,
    )
    accepted_cards.extend(verification.accepted_cards)
    if not verification.rejected_cards:
        return _CardBatchResult(
            batch=batch,
            accepted_cards=accepted_cards,
            rejected_cards=rejected_cards,
            progress=_analysis_batch_progress_payload(
                batch,
                status="completed",
                accepted_card_count=len(verification.accepted_cards),
                rejected_card_count=0,
            ),
        )

    try:
        repaired_batch = await _call_card_batch_extraction(
            paper_id=paper_id,
            space_id=space_id,
            batch=batch,
            paper_understanding=paper_understanding,
            repair_diagnostics=verification.rejected_cards,
        )
    except (LLMRequestError, LLMStructuredOutputError, ValidationError, ValueError) as exc:
        repair_diagnostics = _diagnostics_with_repair_error(
            verification.rejected_cards,
            exc,
        )
        rejected_cards.extend(repair_diagnostics)
        return _CardBatchResult(
            batch=batch,
            accepted_cards=accepted_cards,
            rejected_cards=rejected_cards,
            progress=_analysis_batch_progress_payload(
                batch,
                status="repair_failed",
                accepted_card_count=len(verification.accepted_cards),
                rejected_card_count=len(repair_diagnostics),
                repair_attempted=True,
                error=str(exc),
            ),
        )

    repaired_verification = verify_extraction_batch_sources(
        repaired_batch,
        batch.passages,
    )
    accepted_cards.extend(repaired_verification.accepted_cards)
    rejected_cards.extend(repaired_verification.rejected_cards)
    return _CardBatchResult(
        batch=batch,
        accepted_cards=accepted_cards,
        rejected_cards=rejected_cards,
        progress=_analysis_batch_progress_payload(
            batch,
            status="repaired"
            if not repaired_verification.rejected_cards
            else "partially_repaired",
            accepted_card_count=(
                len(verification.accepted_cards)
                + len(repaired_verification.accepted_cards)
            ),
            rejected_card_count=len(repaired_verification.rejected_cards),
            repair_attempted=True,
        ),
    )


def _card_extraction_concurrency() -> int:
    raw_value = os.getenv("PAPER_ENGINE_CARD_EXTRACTION_CONCURRENCY", "")
    try:
        value = int(raw_value)
    except ValueError:
        value = DEFAULT_CARD_EXTRACTION_CONCURRENCY
    return min(8, max(1, value))


def _batch_progress_sort_key(progress: Mapping[str, Any]) -> int:
    try:
        return int(progress.get("batch_index", 0))
    except (TypeError, ValueError):
        return 0


async def _extract_card_batches_stage_serial(
    paper_id: str,
    space_id: str,
    batches: Sequence[AnalysisPassageBatch],
    *,
    analysis_run_id: str | None = None,
    paper_understanding: PaperUnderstandingExtraction | None = None,
) -> SourceVerificationResult:
    """Legacy serial implementation kept for targeted debugging."""
    accepted_cards: list[CardExtraction] = []
    rejected_cards: list[RejectedCardDiagnostic] = []
    batch_progress: list[dict[str, Any]] = []

    for batch in batches:
        _raise_if_analysis_cancelled(analysis_run_id)
        _record_analysis_progress(
            analysis_run_id,
            stage="card_extraction",
            total_batches=len(batches),
            completed_batches=len(batch_progress),
            current_batch_index=batch.batch_index,
            accepted_card_count=len(accepted_cards),
            rejected_card_count=len(rejected_cards),
            batch_progress=batch_progress,
        )
        result = await _extract_and_verify_card_batch(
            paper_id=paper_id,
            space_id=space_id,
            batch=batch,
            paper_understanding=paper_understanding,
        )
        accepted_cards.extend(result.accepted_cards)
        rejected_cards.extend(result.rejected_cards)
        batch_progress.append(result.progress)
        _record_analysis_progress(
            analysis_run_id,
            stage="card_extraction",
            total_batches=len(batches),
            completed_batches=len(batch_progress),
            current_batch_index=None,
            accepted_card_count=len(accepted_cards),
            rejected_card_count=len(rejected_cards),
            batch_progress=batch_progress,
        )

    return SourceVerificationResult(
        accepted_cards=accepted_cards,
        rejected_cards=rejected_cards,
    )


def deduplicate_and_rank_cards_stage(
    cards: Sequence[CardExtraction],
    *,
    rejected_cards: Sequence[RejectedCardDiagnostic] = (),
    batches: Sequence[AnalysisPassageBatch] = (),
    max_cards: int = DEFAULT_FINAL_AI_CARD_LIMIT,
) -> CardRankingResult:
    """Collapse duplicate AI cards and return the final ranked card set."""
    if max_cards <= 0:
        raise ValueError("max_cards must be positive")

    source_section_scores = _source_section_scores(batches)
    candidates = [
        _ranked_card_candidate(
            card,
            original_index=index,
            source_section_scores=source_section_scores,
        )
        for index, card in enumerate(cards)
    ]
    ranked_candidates = sorted(
        candidates,
        key=_card_rank_key,
        reverse=True,
    )

    deduped_candidates: list[_RankedCardCandidate] = []
    duplicate_diagnostics: list[dict[str, Any]] = []
    for candidate in ranked_candidates:
        duplicate_of = _matching_duplicate(candidate, deduped_candidates)
        if duplicate_of is None:
            deduped_candidates.append(candidate)
            continue

        duplicate_diagnostics.append(
            {
                "reason": "duplicate",
                "similarity": _summary_similarity(
                    candidate.summary_tokens,
                    duplicate_of.summary_tokens,
                ),
                "kept": _card_candidate_diagnostic(duplicate_of),
                "dropped": _card_candidate_diagnostic(candidate),
            }
        )

    final_candidates = deduped_candidates[:max_cards]
    overflow_candidates = deduped_candidates[max_cards:]
    overflow_diagnostics = [
        {
            "reason": "overflow",
            "rank": max_cards + overflow_index + 1,
            "dropped": _card_candidate_diagnostic(candidate),
        }
        for overflow_index, candidate in enumerate(overflow_candidates)
    ]

    diagnostics: dict[str, Any] = {
        "final_card_limit": max_cards,
        "input_card_count": len(cards),
        "ranked_card_count": len(final_candidates),
        "duplicate_card_count": len(duplicate_diagnostics),
        "overflow_card_count": len(overflow_diagnostics),
        "rejected_card_count": len(rejected_cards),
        "duplicate_cards": duplicate_diagnostics,
        "overflow_cards": overflow_diagnostics,
        "rejected_cards": [
            _diagnostic_payload(diagnostic) for diagnostic in rejected_cards
        ],
    }

    return CardRankingResult(
        cards=[candidate.card for candidate in final_candidates],
        rejected_cards=list(rejected_cards),
        diagnostics=diagnostics,
    )


async def run_paper_analysis(
    paper_id: str,
    space_id: str,
    *,
    analysis_run_id: str | None = None,
) -> PaperAnalysisRunResult:
    """Run metadata extraction, card extraction, ranking, and persistence."""
    timings: dict[str, float] = {}
    load_started = time.perf_counter()
    conn = get_connection()
    try:
        _ensure_analysis_paper_exists(conn, paper_id, space_id)
        passages = _load_analysis_passages(conn, paper_id, space_id)
        if not passages:
            raise ValueError("No passages found. Please parse PDF first.")
        elements = _load_analysis_elements(conn, paper_id, space_id)
        provider, model = _load_llm_identity(conn)
    finally:
        conn.close()
    timings["load_inputs_seconds"] = time.perf_counter() - load_started

    _record_analysis_progress(
        analysis_run_id,
        stage="metadata",
        total_batches=0,
        completed_batches=0,
        current_batch_index=None,
        accepted_card_count=0,
        rejected_card_count=0,
    )
    _raise_if_analysis_cancelled(analysis_run_id)
    metadata_started = time.perf_counter()
    metadata = await extract_metadata_stage(
        paper_id,
        passages,
        elements,
    )
    timings["metadata_seconds"] = time.perf_counter() - metadata_started
    _raise_if_analysis_cancelled(analysis_run_id)

    _record_analysis_progress(
        analysis_run_id,
        stage="understanding",
        total_batches=0,
        completed_batches=0,
        current_batch_index=None,
        accepted_card_count=0,
        rejected_card_count=0,
    )
    understanding_started = time.perf_counter()
    paper_understanding = await extract_paper_understanding_stage(passages)
    timings["understanding_seconds"] = time.perf_counter() - understanding_started
    _raise_if_analysis_cancelled(analysis_run_id)

    batch_select_started = time.perf_counter()
    batches = select_analysis_passage_batches(passages)
    timings["batch_selection_seconds"] = time.perf_counter() - batch_select_started
    _record_analysis_progress(
        analysis_run_id,
        stage="card_extraction",
        total_batches=len(batches),
        completed_batches=0,
        current_batch_index=batches[0].batch_index if batches else None,
        accepted_card_count=0,
        rejected_card_count=0,
        batch_progress=[],
    )
    card_extraction_started = time.perf_counter()
    card_result = await extract_card_batches_stage(
        paper_id,
        space_id,
        batches,
        analysis_run_id=analysis_run_id,
        paper_understanding=paper_understanding,
    )
    timings["card_extraction_seconds"] = (
        time.perf_counter() - card_extraction_started
    )
    _raise_if_analysis_cancelled(analysis_run_id)
    _record_analysis_progress(
        analysis_run_id,
        stage="ranking",
        total_batches=len(batches),
        completed_batches=len(batches),
        current_batch_index=None,
        accepted_card_count=len(card_result.accepted_cards),
        rejected_card_count=len(card_result.rejected_cards),
    )
    ranking_started = time.perf_counter()
    ranked_cards = deduplicate_and_rank_cards_stage(
        card_result.accepted_cards,
        rejected_cards=card_result.rejected_cards,
        batches=batches,
    )
    timings["ranking_seconds"] = time.perf_counter() - ranking_started
    result = _merged_analysis_result(
        paper_id=paper_id,
        space_id=space_id,
        metadata=metadata,
        understanding=paper_understanding,
        passages=passages,
        batches=batches,
        ranked_cards=ranked_cards,
        provider=provider,
        model=model,
    )

    persist_started = time.perf_counter()
    conn = get_connection()
    try:
        timings["persist_seconds"] = 0.0
        timings["total_seconds"] = sum(timings.values())
        result = result.model_copy(
            update={
                "metadata_extra": {
                    **result.metadata_extra,
                    "timings": {
                        key: round(value, 4)
                        for key, value in timings.items()
                    },
                }
            }
        )
        if analysis_run_id is not None:
            conn.execute("BEGIN IMMEDIATE")
            _raise_if_analysis_cancelled_with_conn(conn, analysis_run_id)
        _update_paper_metadata(conn, result)
        persisted_analysis_run_id = persist_analysis_result(
            conn,
            result,
            analysis_run_id=analysis_run_id,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    timings["persist_seconds"] = time.perf_counter() - persist_started
    timings["total_seconds"] = sum(
        value for key, value in timings.items() if key != "total_seconds"
    )
    result = result.model_copy(
        update={
            "metadata_extra": {
                **result.metadata_extra,
                "timings": {
                    key: round(value, 4)
                    for key, value in timings.items()
                },
            }
        }
    )

    return PaperAnalysisRunResult(
        analysis_run_id=persisted_analysis_run_id,
        result=result,
    )


def _raise_if_analysis_cancelled(analysis_run_id: str | None) -> None:
    if analysis_run_id is None:
        return

    conn = get_connection()
    try:
        _raise_if_analysis_cancelled_with_conn(conn, analysis_run_id)
    finally:
        conn.close()


def _raise_if_analysis_cancelled_with_conn(
    conn: sqlite3.Connection,
    analysis_run_id: str,
) -> None:
    if is_analysis_run_cancelled(conn, analysis_run_id):
        raise AnalysisRunCancelled(f"analysis run {analysis_run_id} was cancelled")


def _record_analysis_progress(
    analysis_run_id: str | None,
    *,
    stage: str,
    total_batches: int,
    completed_batches: int,
    current_batch_index: int | None,
    accepted_card_count: int,
    rejected_card_count: int,
    batch_progress: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    if analysis_run_id is None:
        return

    total = max(total_batches, 0)
    completed = min(max(completed_batches, 0), total) if total else 0
    progress: dict[str, Any] = {
        "stage": stage,
        "total_batches": total,
        "completed_batches": completed,
        "current_batch_index": current_batch_index,
        "accepted_card_count": max(accepted_card_count, 0),
        "rejected_card_count": max(rejected_card_count, 0),
    }

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT status, diagnostics_json
            FROM analysis_runs
            WHERE id = ?
            """,
            (analysis_run_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return
        if row["status"] == "cancelled":
            raise AnalysisRunCancelled(f"analysis run {analysis_run_id} was cancelled")
        if row["status"] not in {"queued", "running"}:
            conn.rollback()
            return

        diagnostics = _json_object_from_db(row["diagnostics_json"])
        previous_progress = diagnostics.get("progress")
        if batch_progress is not None:
            progress["batches"] = [dict(item) for item in batch_progress]
        elif isinstance(previous_progress, Mapping) and isinstance(
            previous_progress.get("batches"),
            list,
        ):
            progress["batches"] = previous_progress["batches"]

        diagnostics["analysis_batch_count"] = total
        diagnostics["progress"] = progress
        conn.execute(
            """
            UPDATE analysis_runs
            SET accepted_card_count = ?,
                rejected_card_count = ?,
                diagnostics_json = ?,
                heartbeat_at = datetime('now')
            WHERE id = ?
              AND status IN ('queued', 'running')
            """,
            (
                progress["accepted_card_count"],
                progress["rejected_card_count"],
                _analysis_json(diagnostics),
                analysis_run_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _analysis_batch_progress_payload(
    batch: AnalysisPassageBatch,
    *,
    status: str,
    accepted_card_count: int,
    rejected_card_count: int,
    repair_attempted: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "batch_index": batch.batch_index,
        "status": status,
        "group_key": batch.group_key,
        "passage_type": batch.passage_type,
        "source_passage_count": len(batch.source_passage_ids),
        "token_count": batch.token_count,
        "accepted_card_count": max(accepted_card_count, 0),
        "rejected_card_count": max(rejected_card_count, 0),
        "repair_attempted": repair_attempted,
    }
    if error:
        payload["error"] = error[:500]
    return payload


def _ensure_analysis_paper_exists(
    conn: sqlite3.Connection,
    paper_id: str,
    space_id: str,
) -> None:
    row = conn.execute(
        "SELECT 1 FROM papers WHERE id = ? AND space_id = ?",
        (paper_id, space_id),
    ).fetchone()
    if row is None:
        raise ValueError("Paper not found")


def _load_analysis_passages(
    conn: sqlite3.Connection,
    paper_id: str,
    space_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, paper_id, space_id, section, page_number, paragraph_index,
               original_text, parse_confidence, passage_type, parse_run_id,
               element_ids_json, heading_path_json, bbox_json, token_count,
               char_count, content_hash, parser_backend, extraction_method,
               quality_flags_json
        FROM passages
        WHERE paper_id = ? AND space_id = ?
        ORDER BY page_number, paragraph_index, id
        """,
        (paper_id, space_id),
    ).fetchall()
    return [_analysis_passage_from_row(row) for row in rows]


def _analysis_passage_from_row(row: sqlite3.Row) -> dict[str, Any]:
    data = _row_dict(row)
    data["heading_path"] = _json_list_from_db(data.pop("heading_path_json", "[]"))
    data["element_ids"] = _json_list_from_db(data.pop("element_ids_json", "[]"))
    data["quality_flags"] = _json_list_from_db(data.pop("quality_flags_json", "[]"))
    data["bbox"] = _json_value_from_db(data.pop("bbox_json", None))
    return data


def _load_analysis_elements(
    conn: sqlite3.Connection,
    paper_id: str,
    space_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, parse_run_id, paper_id, space_id, element_index, element_type,
               text, page_number, bbox_json, heading_path_json, metadata_json
        FROM document_elements
        WHERE paper_id = ? AND space_id = ?
        ORDER BY element_index, id
        """,
        (paper_id, space_id),
    ).fetchall()
    return [_analysis_element_from_row(row) for row in rows]


def _analysis_element_from_row(row: sqlite3.Row) -> dict[str, Any]:
    data = _row_dict(row)
    data["heading_path"] = _json_list_from_db(data.pop("heading_path_json", "[]"))
    data["metadata"] = _json_object_from_db(data.pop("metadata_json", "{}"))
    data["bbox"] = _json_value_from_db(data.pop("bbox_json", None))
    return data


def _load_llm_identity(conn: sqlite3.Connection) -> tuple[str, str]:
    rows = conn.execute(
        "SELECT key, value FROM app_state WHERE key IN (?, ?)",
        ("llm_provider", "llm_model"),
    ).fetchall()
    config = {str(row["key"]): str(row["value"]) for row in rows}
    provider = config.get("llm_provider", "openai").strip() or "openai"
    model = config.get("llm_model", "gpt-4o").strip() or "gpt-4o"
    return provider, model


def _merged_analysis_result(
    *,
    paper_id: str,
    space_id: str,
    metadata: PaperMetadataExtraction,
    understanding: PaperUnderstandingExtraction | None,
    passages: Sequence[PipelineInput],
    batches: Sequence[AnalysisPassageBatch],
    ranked_cards: CardRankingResult,
    provider: str,
    model: str,
) -> MergedAnalysisResult:
    diagnostics = {
        **ranked_cards.diagnostics,
        "analysis_batch_count": len(batches),
        "source_passage_count": len(passages),
    }
    warnings: list[str] = []
    if not batches:
        warnings.append("no_analysis_batches_selected")
    if ranked_cards.rejected_cards:
        warnings.append(f"{len(ranked_cards.rejected_cards)} rejected cards omitted")

    return MergedAnalysisResult(
        paper_id=paper_id,
        space_id=space_id,
        metadata=metadata,
        understanding=understanding,
        cards=ranked_cards.cards,
        quality=AnalysisQualityReport(
            accepted_card_count=len(ranked_cards.cards),
            rejected_card_count=len(ranked_cards.rejected_cards),
            source_coverage=_analysis_source_coverage(ranked_cards.cards, passages),
            warnings=warnings,
            diagnostics=diagnostics,
        ),
        model=model,
        provider=provider,
        extractor_version="analysis-v2",
        metadata_extra={
            "analysis_batch_count": len(batches),
            "source_passage_count": len(passages),
            **(
                {"paper_understanding_zh": understanding.model_dump()}
                if understanding is not None
                else {}
            ),
        },
    )


def _analysis_source_coverage(
    cards: Sequence[CardExtraction],
    passages: Sequence[PipelineInput],
) -> float | None:
    available_source_ids = {
        _optional_string(_object_to_mapping(passage), "id", "source_id")
        for passage in passages
    }
    available_source_ids.discard("")
    if not available_source_ids:
        return None

    cited_source_ids = {
        source_id
        for card in cards
        for source_id in card.source_passage_ids
        if source_id in available_source_ids
    }
    return len(cited_source_ids) / len(available_source_ids)


def _update_paper_metadata(
    conn: sqlite3.Connection,
    result: MergedAnalysisResult,
) -> None:
    promote_core_metadata_from_ai(
        conn,
        paper_id=result.paper_id,
        space_id=result.space_id,
        metadata=result.metadata,
    )


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _json_value_from_db(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _json_object_from_db(value: Any) -> dict[str, Any]:
    parsed = _json_value_from_db(value)
    if not isinstance(parsed, Mapping):
        return {}
    return {str(key): item for key, item in parsed.items()}


def _json_list_from_db(value: Any) -> list[str]:
    parsed = _json_value_from_db(value)
    if not isinstance(parsed, Sequence) or isinstance(parsed, (bytes, bytearray, str)):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def persist_analysis_result(
    conn: sqlite3.Connection,
    result: MergedAnalysisResult,
    *,
    analysis_run_id: str | None = None,
) -> str:
    """Persist one AI analysis run and replace only prior unedited AI cards."""
    storage_analysis_run_id = analysis_run_id or f"analysis-run-{uuid.uuid4()}"
    savepoint = f"persist_analysis_result_{uuid.uuid4().hex}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        if analysis_run_id is None:
            _insert_analysis_run(conn, storage_analysis_run_id, result)
        else:
            _complete_existing_analysis_run(conn, storage_analysis_run_id, result)
        conn.execute(
            """
            DELETE FROM knowledge_cards
            WHERE paper_id = ?
              AND space_id = ?
              AND created_by = 'ai'
              AND user_edited != 1
            """,
            (result.paper_id, result.space_id),
        )
        for card in result.cards:
            card_id = f"ai-card-{uuid.uuid4()}"
            _insert_ai_card(conn, storage_analysis_run_id, card_id, result, card)
            _insert_ai_card_sources(conn, storage_analysis_run_id, card_id, result, card)

        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise

    return storage_analysis_run_id


def _analysis_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _insert_analysis_run(
    conn: sqlite3.Connection,
    analysis_run_id: str,
    result: MergedAnalysisResult,
) -> None:
    accepted_card_count = result.quality.accepted_card_count
    if accepted_card_count == 0 and result.cards:
        accepted_card_count = len(result.cards)
    conn.execute(
        """
        INSERT INTO analysis_runs (
            id, paper_id, space_id, status, model, provider, extractor_version,
            accepted_card_count, rejected_card_count, metadata_json,
            warnings_json, diagnostics_json, completed_at
        )
        VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            analysis_run_id,
            result.paper_id,
            result.space_id,
            result.model,
            result.provider,
            result.extractor_version,
            accepted_card_count,
            result.quality.rejected_card_count,
            _analysis_json(
                {
                    "paper_metadata": result.metadata.model_dump(),
                    "paper_understanding_zh": result.understanding.model_dump()
                    if result.understanding is not None
                    else None,
                    "metadata_extra": result.metadata_extra,
                    "quality": {
                        "source_coverage": result.quality.source_coverage,
                        "validation_errors": result.quality.validation_errors,
                        "quality_flags": result.quality.quality_flags,
                    },
                }
            ),
            _analysis_json(result.quality.warnings),
            _analysis_json(result.quality.diagnostics),
        ),
    )


def _complete_existing_analysis_run(
    conn: sqlite3.Connection,
    analysis_run_id: str,
    result: MergedAnalysisResult,
) -> None:
    accepted_card_count = result.quality.accepted_card_count
    if accepted_card_count == 0 and result.cards:
        accepted_card_count = len(result.cards)
    update = conn.execute(
        """
        UPDATE analysis_runs
        SET status = 'completed',
            model = ?,
            provider = ?,
            extractor_version = ?,
            accepted_card_count = ?,
            rejected_card_count = ?,
            metadata_json = ?,
            warnings_json = ?,
            diagnostics_json = ?,
            completed_at = datetime('now'),
            heartbeat_at = datetime('now'),
            worker_id = NULL,
            last_error = NULL
        WHERE id = ?
          AND paper_id = ?
          AND space_id = ?
          AND status IN ('queued', 'running')
        """,
        (
            result.model,
            result.provider,
            result.extractor_version,
            accepted_card_count,
            result.quality.rejected_card_count,
            _analysis_json(
                {
                    "paper_metadata": result.metadata.model_dump(),
                    "paper_understanding_zh": result.understanding.model_dump()
                    if result.understanding is not None
                    else None,
                    "metadata_extra": result.metadata_extra,
                    "quality": {
                        "source_coverage": result.quality.source_coverage,
                        "validation_errors": result.quality.validation_errors,
                        "quality_flags": result.quality.quality_flags,
                    },
                }
            ),
            _analysis_json(result.quality.warnings),
            _analysis_json(result.quality.diagnostics),
            analysis_run_id,
            result.paper_id,
            result.space_id,
        ),
    )
    if update.rowcount != 1:
        raise RuntimeError(f"analysis run {analysis_run_id} is not queued or running")


def _insert_ai_card(
    conn: sqlite3.Connection,
    analysis_run_id: str,
    card_id: str,
    result: MergedAnalysisResult,
    card: CardExtraction,
) -> None:
    evidence_payload = {
        "source_passage_ids": list(card.source_passage_ids),
        "evidence_quote": card.evidence_quote,
        "reasoning_summary": card.reasoning_summary,
        "metadata": card.metadata,
    }
    conn.execute(
        """
        INSERT INTO knowledge_cards (
            id, space_id, paper_id, source_passage_id, card_type, summary,
            confidence, user_edited, created_by, extractor_version,
            analysis_run_id, evidence_json, quality_flags_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'ai', ?, ?, ?, ?)
        """,
        (
            card_id,
            result.space_id,
            result.paper_id,
            card.source_passage_ids[0],
            card.card_type,
            card.summary,
            card.confidence,
            result.extractor_version,
            analysis_run_id,
            _analysis_json(evidence_payload),
            _analysis_json(card.quality_flags),
        ),
    )


def _insert_ai_card_sources(
    conn: sqlite3.Connection,
    analysis_run_id: str,
    card_id: str,
    result: MergedAnalysisResult,
    card: CardExtraction,
) -> None:
    for source_index, passage_id in enumerate(card.source_passage_ids):
        conn.execute(
            """
            INSERT INTO knowledge_card_sources (
                id, card_id, passage_id, paper_id, space_id, analysis_run_id,
                evidence_quote, confidence, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"card-source-{uuid.uuid4()}",
                card_id,
                passage_id,
                result.paper_id,
                result.space_id,
                analysis_run_id,
                card.evidence_quote,
                card.confidence,
                _analysis_json(
                    {
                        "source_index": source_index,
                        "card_metadata": card.metadata,
                    }
                ),
            ),
        )


async def _call_card_batch_extraction(
    *,
    paper_id: str,
    space_id: str,
    batch: AnalysisPassageBatch,
    paper_understanding: PaperUnderstandingExtraction | None = None,
    repair_diagnostics: Sequence[RejectedCardDiagnostic] | None = None,
) -> CardExtractionBatch:
    prompt = build_card_batch_extraction_prompt(
        paper_id=paper_id,
        space_id=space_id,
        batch_index=batch.batch_index,
        passages=batch.passages,
        paper_understanding=paper_understanding,
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


def _ranked_card_candidate(
    card: CardExtraction,
    *,
    original_index: int,
    source_section_scores: Mapping[str, int],
) -> _RankedCardCandidate:
    source_ids = frozenset(card.source_passage_ids)
    section_score = 0
    for source_id in source_ids:
        section_score = max(section_score, source_section_scores.get(source_id, 0))
    return _RankedCardCandidate(
        card=card,
        original_index=original_index,
        normalized_summary=_normalized_summary(card.summary),
        summary_tokens=frozenset(_summary_tokens(card.summary)),
        source_ids=source_ids,
        section_score=section_score,
    )


def _source_section_scores(
    batches: Sequence[AnalysisPassageBatch],
) -> dict[str, int]:
    scores: dict[str, int] = {}
    for batch in batches:
        batch_score = _section_ranking_score(batch.passage_type)
        for source_id in batch.source_passage_ids:
            scores[source_id] = max(scores.get(source_id, 0), batch_score)

        for passage in batch.passages:
            data = _object_to_mapping(passage)
            source_id = _optional_string(data, "id", "source_id")
            if not source_id:
                continue
            section = _optional_string(data, "section")
            heading_path = tuple(_heading_path_for_analysis(data, section))
            passage_type = _analysis_passage_type(
                _optional_string(data, "passage_type", "type"),
                section,
                heading_path,
            )
            passage_score = _section_ranking_score(passage_type)
            scores[source_id] = max(scores.get(source_id, 0), passage_score)
    return scores


def _section_ranking_score(passage_type: str) -> int:
    normalized_type = _analysis_passage_type(passage_type, "", ())
    if normalized_type in KEY_RANKING_SECTIONS:
        return 3
    if normalized_type == "discussion":
        return 2
    if normalized_type in {"abstract", "introduction"}:
        return 1
    return 0


def _card_rank_key(
    candidate: _RankedCardCandidate,
) -> tuple[float, int, int, int]:
    return (
        round(float(candidate.card.confidence), 6),
        len(candidate.source_ids),
        candidate.section_score,
        -candidate.original_index,
    )


def _matching_duplicate(
    candidate: _RankedCardCandidate,
    kept_candidates: Sequence[_RankedCardCandidate],
) -> _RankedCardCandidate | None:
    for kept in kept_candidates:
        if _cards_are_duplicates(candidate, kept):
            return kept
    return None


def _cards_are_duplicates(
    candidate: _RankedCardCandidate,
    kept: _RankedCardCandidate,
) -> bool:
    if candidate.card.card_type != kept.card.card_type:
        return False
    if not candidate.source_ids.intersection(kept.source_ids):
        return False
    return (
        _summary_similarity(candidate.summary_tokens, kept.summary_tokens)
        >= SUMMARY_DUPLICATE_SIMILARITY_THRESHOLD
    )


def _summary_similarity(
    left_tokens: frozenset[str],
    right_tokens: frozenset[str],
) -> float:
    if not left_tokens or not right_tokens:
        return 0.0
    intersection_count = len(left_tokens.intersection(right_tokens))
    union_count = len(left_tokens.union(right_tokens))
    if union_count == 0:
        return 0.0
    return intersection_count / union_count


def _normalized_summary(value: str) -> str:
    cleaned = _clean_text(value).casefold()
    return re.sub(r"[^a-z0-9'\u4e00-\u9fff]+", " ", cleaned).strip()


def _summary_tokens(value: str) -> list[str]:
    normalized = _normalized_summary(value)
    latin_tokens = [
        token
        for token in SUMMARY_TOKEN_RE.findall(normalized)
        if token not in SUMMARY_STOPWORDS
    ]
    cjk_tokens = SUMMARY_CJK_RE.findall(normalized)
    cjk_bigrams = [
        "".join(cjk_tokens[index : index + 2])
        for index in range(max(0, len(cjk_tokens) - 1))
    ]
    if len(cjk_tokens) == 1:
        cjk_bigrams = cjk_tokens
    return [*latin_tokens, *cjk_bigrams]


def _card_candidate_diagnostic(
    candidate: _RankedCardCandidate,
) -> dict[str, Any]:
    return {
        "card_index": candidate.original_index,
        "card_type": candidate.card.card_type,
        "summary": candidate.card.summary,
        "source_passage_ids": list(candidate.card.source_passage_ids),
        "confidence": candidate.card.confidence,
        "source_coverage": len(candidate.source_ids),
        "section_score": candidate.section_score,
        "normalized_summary": candidate.normalized_summary,
    }


class _SourceValue(BaseModel):
    """A normalized value and where it came from."""

    value: str = ""
    source_id: str = ""
    source_name: str = ""


async def _llm_metadata(
    passages: Sequence[PipelineInput],
) -> PaperMetadataExtraction:
    try:
        prompt = build_metadata_extraction_prompt(passages)
        response = await call_llm_schema(
            prompt.system_prompt,
            prompt.user_prompt,
            prompt.schema_name,
            prompt.schema,
        )
        return PaperMetadataExtraction.model_validate(response)
    except (LLMRequestError, LLMStructuredOutputError, ValidationError, ValueError) as exc:
        return PaperMetadataExtraction(metadata={"llm_error": str(exc)})


async def _llm_paper_understanding(
    passages: Sequence[PipelineInput],
) -> PaperUnderstandingExtraction | None:
    try:
        prompt = build_paper_understanding_prompt(passages)
        response = await call_llm_schema(
            prompt.system_prompt,
            prompt.user_prompt,
            prompt.schema_name,
            prompt.schema,
        )
        return PaperUnderstandingExtraction.model_validate(response)
    except (LLMRequestError, LLMStructuredOutputError, ValidationError, ValueError):
        return None


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
    "CardRankingResult",
    "PaperAnalysisRunResult",
    "deduplicate_and_rank_cards_stage",
    "extract_card_batches_stage",
    "extract_metadata_stage",
    "persist_analysis_result",
    "run_paper_analysis",
    "select_analysis_passage_batches",
]
