"""Prompt builders for source-grounded AI paper analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from paper_engine.analysis.models import (
    CARD_TYPES,
    CardExtraction,
    CardExtractionBatch,
    PaperMetadataExtraction,
)


NonBlankString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

MAX_SOURCE_TEXT_CHARS = 3_000

GROUNDING_RULES = (
    "Only use facts supported by source_id values in the source passages. "
    "Every factual claim must cite source_passage_ids copied exactly from source_id. "
    "If a fact is unsupported or ambiguous, leave it out or record it as a warning; "
    "do not guess, infer beyond the supplied passages, or cite unavailable sources."
)

BASE_SYSTEM_PROMPT = (
    "You are a precise academic paper analysis assistant. "
    "Use the page-aware source catalog as the only evidence. "
    f"{GROUNDING_RULES} Return only valid JSON for the requested schema."
)


@dataclass(frozen=True)
class AnalysisPrompt:
    """System/user prompt pair plus the structured output schema to request."""

    system_prompt: str
    user_prompt: str
    schema_name: str
    schema: dict[str, Any]


class SourcePassageInput(BaseModel):
    """Minimal evidence payload exposed to prompt builders."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: NonBlankString
    page_number: int = Field(ge=0)
    text: NonBlankString
    section: str = ""
    heading_path: list[str] = Field(default_factory=list)


SourcePassageLike = SourcePassageInput | Mapping[str, Any] | BaseModel


SECTION_SUMMARY_SCHEMA: dict[str, Any] = {
    "title": "SectionSummaryExtraction",
    "type": "object",
    "properties": {
        "section_name": {"type": "string"},
        "summary": {"type": "string"},
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
        },
        "source_passage_ids": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "uniqueItems": True,
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "section_name",
        "summary",
        "key_points",
        "source_passage_ids",
        "warnings",
    ],
    "additionalProperties": False,
}

CARD_MERGE_DEDUP_SCHEMA: dict[str, Any] = {
    "title": "CardMergeDedup",
    "type": "object",
    "properties": {
        "paper_id": {"type": "string"},
        "space_id": {"type": "string"},
        "merged_cards": {
            "type": "array",
            "items": CardExtraction.model_json_schema(),
        },
        "dropped_candidate_indices": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "paper_id",
        "space_id",
        "merged_cards",
        "dropped_candidate_indices",
        "warnings",
    ],
    "additionalProperties": False,
}


def build_metadata_extraction_prompt(
    passages: Sequence[SourcePassageLike],
) -> AnalysisPrompt:
    """Build a source-grounded prompt for scholarly metadata extraction."""
    source_passages = _coerce_source_passages(passages)
    user_prompt = "\n".join(
        [
            "Task: Extract paper metadata from the source passages.",
            GROUNDING_RULES,
            "Return empty strings or arrays for metadata not directly supported.",
            "Source passages (JSONL):",
            _render_source_passages(source_passages),
        ]
    )
    return AnalysisPrompt(
        system_prompt=BASE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema_name="paper_metadata_extraction",
        schema=PaperMetadataExtraction.model_json_schema(),
    )


def build_section_summary_prompt(
    section_name: str,
    passages: Sequence[SourcePassageLike],
) -> AnalysisPrompt:
    """Build a source-grounded prompt for one section summary."""
    source_passages = _coerce_source_passages(passages)
    user_prompt = "\n".join(
        [
            f"Task: Summarize the section named {section_name!r}.",
            GROUNDING_RULES,
            "Keep only section-level claims directly supported by cited sources.",
            _context_json({"section_name": section_name}),
            "Source passages (JSONL):",
            _render_source_passages(source_passages),
        ]
    )
    return AnalysisPrompt(
        system_prompt=BASE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema_name="section_summary_extraction",
        schema=SECTION_SUMMARY_SCHEMA,
    )


def build_card_batch_extraction_prompt(
    *,
    paper_id: str,
    space_id: str,
    batch_index: int,
    passages: Sequence[SourcePassageLike],
) -> AnalysisPrompt:
    """Build a source-grounded prompt for extracting one batch of cards."""
    source_passages = _coerce_source_passages(passages)
    system_prompt = (
        f"{BASE_SYSTEM_PROMPT} Allowed card_type values: "
        f"{', '.join(CARD_TYPES)}."
    )
    user_prompt = "\n".join(
        [
            "Task: Extract high-value knowledge cards from this passage batch.",
            GROUNDING_RULES,
            "Create cards only when a concise, useful claim is directly supported.",
            "Each card must include evidence_quote copied from a cited source passage.",
            _context_json(
                {
                    "paper_id": paper_id,
                    "space_id": space_id,
                    "batch_index": batch_index,
                    "source_passage_ids": [
                        passage.id for passage in source_passages
                    ],
                }
            ),
            "Source passages (JSONL):",
            _render_source_passages(source_passages),
        ]
    )
    return AnalysisPrompt(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema_name="card_extraction_batch",
        schema=CardExtractionBatch.model_json_schema(),
    )


def build_merge_dedup_prompt(
    *,
    paper_id: str,
    space_id: str,
    cards: Sequence[CardExtraction | Mapping[str, Any]],
    passages: Sequence[SourcePassageLike],
    max_cards: int,
) -> AnalysisPrompt:
    """Build a prompt for merging duplicate source-grounded card candidates."""
    source_passages = _coerce_source_passages(passages)
    card_candidates = [
        _render_card_candidate(index, card) for index, card in enumerate(cards)
    ]
    user_prompt = "\n".join(
        [
            "Task: Merge and deduplicate candidate knowledge cards.",
            GROUNDING_RULES,
            "Keep the strongest card when candidates repeat the same supported fact.",
            "Do not add new facts; merged cards must cite only available source_id values.",
            _context_json(
                {
                    "paper_id": paper_id,
                    "space_id": space_id,
                    "max_cards": max_cards,
                }
            ),
            "Source catalog (JSONL):",
            _render_source_passages(source_passages),
            "Candidate cards (JSONL):",
            "\n".join(card_candidates),
        ]
    )
    return AnalysisPrompt(
        system_prompt=BASE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema_name="card_merge_dedup",
        schema=CARD_MERGE_DEDUP_SCHEMA,
    )


def _coerce_source_passages(
    passages: Sequence[SourcePassageLike],
) -> list[SourcePassageInput]:
    return [_coerce_source_passage(passage) for passage in passages]


def _coerce_source_passage(passage: SourcePassageLike) -> SourcePassageInput:
    if isinstance(passage, SourcePassageInput):
        return passage

    data = _object_to_mapping(passage)
    passage_id = _string_value(data, "id", "source_id")
    page_number = _int_value(data, "page_number", "page_start")
    text = _string_value(data, "text", "original_text")
    section = _optional_string_value(data, "section", "passage_type")
    heading_path = _heading_path_value(data)

    return SourcePassageInput(
        id=passage_id,
        page_number=page_number,
        text=_truncate_text(text),
        section=section,
        heading_path=heading_path,
    )


def _object_to_mapping(value: SourcePassageLike) -> Mapping[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, Mapping):
        return value
    raise TypeError(f"unsupported source passage input: {type(value).__name__}")


def _string_value(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    raise ValueError(f"source passage is missing required field {keys[0]}")


def _optional_string_value(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _int_value(data: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return int(value)
    return 0


def _heading_path_value(data: Mapping[str, Any]) -> list[str]:
    value = data.get("heading_path")
    if value is None:
        value = _json_list_value(data.get("heading_path_json"))
    if isinstance(value, str):
        parsed = _json_list_value(value)
        if parsed:
            value = parsed
        else:
            value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _json_list_value(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _truncate_text(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= MAX_SOURCE_TEXT_CHARS:
        return normalized
    return f"{normalized[:MAX_SOURCE_TEXT_CHARS].rstrip()}... [truncated]"


def _context_json(payload: Mapping[str, Any]) -> str:
    return "Context: " + _compact_json(payload)


def _render_source_passages(passages: Sequence[SourcePassageInput]) -> str:
    return "\n".join(
        _compact_json(_source_passage_payload(passage)) for passage in passages
    )


def _source_passage_payload(passage: SourcePassageInput) -> dict[str, Any]:
    return {
        "source_id": passage.id,
        "page_number": passage.page_number,
        "section": passage.section,
        "heading_path": passage.heading_path,
        "text": passage.text,
    }


def _render_card_candidate(
    candidate_index: int,
    card: CardExtraction | Mapping[str, Any],
) -> str:
    extraction = (
        card if isinstance(card, CardExtraction) else CardExtraction.model_validate(card)
    )
    return _compact_json(
        {
            "candidate_index": candidate_index,
            "card_type": extraction.card_type,
            "summary": extraction.summary,
            "source_passage_ids": extraction.source_passage_ids,
            "evidence_quote": extraction.evidence_quote,
            "confidence": extraction.confidence,
            "reasoning_summary": extraction.reasoning_summary,
            "quality_flags": extraction.quality_flags,
        }
    )


def _compact_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "AnalysisPrompt",
    "SourcePassageInput",
    "build_card_batch_extraction_prompt",
    "build_merge_dedup_prompt",
    "build_metadata_extraction_prompt",
    "build_section_summary_prompt",
]
