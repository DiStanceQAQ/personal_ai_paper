"""Source-grounding verification for AI-generated paper analysis cards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ValidationError

from paper_engine.analysis.models import CardExtraction, CardExtractionBatch


CardInput: TypeAlias = CardExtraction | Mapping[str, Any]
PassageInput: TypeAlias = Mapping[str, Any] | BaseModel
BatchInput: TypeAlias = CardExtractionBatch | Mapping[str, Any]
RejectReason: TypeAlias = Literal[
    "invalid_card",
    "invalid_batch",
    "missing_source",
    "evidence_mismatch",
]

DEFAULT_EVIDENCE_OVERLAP_THRESHOLD = 0.8
MIN_OVERLAP_TOKENS = 3
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")
_WHITESPACE_RE = re.compile(r"\s+")
_STOPWORDS = {
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


@dataclass(frozen=True)
class RejectedCardDiagnostic:
    """Reason a candidate AI card failed source verification."""

    card_index: int
    reason: RejectReason
    message: str
    source_passage_ids: list[str] = field(default_factory=list)
    evidence_quote: str = ""
    batch_index: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceVerificationResult:
    """Accepted cards plus diagnostics for rejected card candidates."""

    accepted_cards: list[CardExtraction]
    rejected_cards: list[RejectedCardDiagnostic]


@dataclass(frozen=True)
class _SourcePassage:
    id: str
    text: str
    paper_id: str
    space_id: str


def verify_extraction_batch_sources(
    batch: BatchInput,
    passages: Sequence[PassageInput],
    *,
    evidence_overlap_threshold: float = DEFAULT_EVIDENCE_OVERLAP_THRESHOLD,
) -> SourceVerificationResult:
    """Verify all cards in a structured extraction batch against source passages."""
    try:
        extraction_batch = _coerce_batch(batch)
    except ValidationError as exc:
        batch_data = _optional_mapping(batch)
        return SourceVerificationResult(
            accepted_cards=[],
            rejected_cards=[
                RejectedCardDiagnostic(
                    card_index=-1,
                    reason="invalid_batch",
                    message=f"Batch validation failed: {exc}",
                    batch_index=_optional_int(batch_data.get("batch_index")),
                    metadata={"validation_error": str(exc)},
                ),
            ],
        )

    return verify_card_sources(
        extraction_batch.cards,
        passages,
        paper_id=extraction_batch.paper_id,
        space_id=extraction_batch.space_id,
        batch_index=extraction_batch.batch_index,
        evidence_overlap_threshold=evidence_overlap_threshold,
    )


def verify_card_sources(
    cards: Sequence[CardInput],
    passages: Sequence[PassageInput],
    *,
    paper_id: str | None = None,
    space_id: str | None = None,
    batch_index: int | None = None,
    evidence_overlap_threshold: float = DEFAULT_EVIDENCE_OVERLAP_THRESHOLD,
) -> SourceVerificationResult:
    """Verify card schema, paper-local source IDs, and evidence quote support."""
    if not 0.0 <= evidence_overlap_threshold <= 1.0:
        raise ValueError("evidence_overlap_threshold must be between 0 and 1")

    source_catalog = _source_catalog(passages, paper_id=paper_id, space_id=space_id)
    accepted_cards: list[CardExtraction] = []
    rejected_cards: list[RejectedCardDiagnostic] = []

    for card_index, candidate in enumerate(cards):
        try:
            card = _coerce_card(candidate)
        except ValidationError as exc:
            rejected_cards.append(
                RejectedCardDiagnostic(
                    card_index=card_index,
                    reason="invalid_card",
                    message=f"Card validation failed: {exc}",
                    source_passage_ids=_raw_source_passage_ids(candidate),
                    evidence_quote=_raw_string(candidate, "evidence_quote"),
                    batch_index=batch_index,
                    metadata={"validation_error": str(exc)},
                )
            )
            continue

        missing_source_ids = [
            source_id
            for source_id in card.source_passage_ids
            if source_id not in source_catalog
        ]
        if missing_source_ids:
            rejected_cards.append(
                RejectedCardDiagnostic(
                    card_index=card_index,
                    reason="missing_source",
                    message=(
                        "Card cites source_passage_ids that do not exist for "
                        "the target paper"
                    ),
                    source_passage_ids=missing_source_ids,
                    evidence_quote=card.evidence_quote,
                    batch_index=batch_index,
                )
            )
            continue

        cited_passages = [source_catalog[source_id] for source_id in card.source_passage_ids]
        if not any(
            evidence_quote_is_supported(
                card.evidence_quote,
                passage.text,
                overlap_threshold=evidence_overlap_threshold,
            )
            for passage in cited_passages
        ):
            rejected_cards.append(
                RejectedCardDiagnostic(
                    card_index=card_index,
                    reason="evidence_mismatch",
                    message=(
                        "Card evidence_quote is not supported by any cited "
                        "source passage"
                    ),
                    source_passage_ids=list(card.source_passage_ids),
                    evidence_quote=card.evidence_quote,
                    batch_index=batch_index,
                    metadata={
                        "overlap_threshold": evidence_overlap_threshold,
                    },
                )
            )
            continue

        accepted_cards.append(card)

    return SourceVerificationResult(
        accepted_cards=accepted_cards,
        rejected_cards=rejected_cards,
    )


def evidence_quote_is_supported(
    evidence_quote: str,
    source_text: str,
    *,
    overlap_threshold: float = DEFAULT_EVIDENCE_OVERLAP_THRESHOLD,
) -> bool:
    """Return whether evidence appears in or strongly overlaps a source passage."""
    normalized_quote = _normalize_for_substring(evidence_quote)
    normalized_source = _normalize_for_substring(source_text)
    if not normalized_quote or not normalized_source:
        return False
    if normalized_quote in normalized_source:
        return True

    quote_tokens = _content_tokens(evidence_quote)
    if len(quote_tokens) < MIN_OVERLAP_TOKENS:
        return False

    source_tokens = set(_content_tokens(source_text))
    matched_token_count = sum(1 for token in quote_tokens if token in source_tokens)
    return (
        matched_token_count >= MIN_OVERLAP_TOKENS
        and matched_token_count / len(quote_tokens) >= overlap_threshold
    )


def _coerce_batch(batch: BatchInput) -> CardExtractionBatch:
    if isinstance(batch, CardExtractionBatch):
        return batch
    return CardExtractionBatch.model_validate(batch)


def _coerce_card(card: CardInput) -> CardExtraction:
    if isinstance(card, CardExtraction):
        return card
    return CardExtraction.model_validate(card)


def _source_catalog(
    passages: Sequence[PassageInput],
    *,
    paper_id: str | None,
    space_id: str | None,
) -> dict[str, _SourcePassage]:
    catalog: dict[str, _SourcePassage] = {}
    for passage in passages:
        data = _object_to_mapping(passage)
        source_id = _optional_string(data, "id", "source_id")
        text = _optional_string(data, "original_text", "text")
        if not source_id or not text:
            continue

        passage_paper_id = _optional_string(data, "paper_id")
        passage_space_id = _optional_string(data, "space_id")
        if paper_id is not None and passage_paper_id and passage_paper_id != paper_id:
            continue
        if space_id is not None and passage_space_id and passage_space_id != space_id:
            continue

        catalog.setdefault(
            source_id,
            _SourcePassage(
                id=source_id,
                text=text,
                paper_id=passage_paper_id,
                space_id=passage_space_id,
            ),
        )
    return catalog


def _object_to_mapping(value: PassageInput) -> Mapping[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump()
    return value


def _optional_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, Mapping):
        return value
    return {}


def _optional_string(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return ""


def _raw_string(candidate: object, key: str) -> str:
    data = _optional_mapping(candidate)
    value = data.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _raw_source_passage_ids(candidate: object) -> list[str]:
    data = _optional_mapping(candidate)
    value = data.get("source_passage_ids")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, (str, bytes, bytearray)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_for_substring(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = normalized.replace("\u00ad", "")
    normalized = normalized.replace("\u2010", "-")
    normalized = normalized.replace("\u2011", "-")
    normalized = normalized.replace("\u2012", "-")
    normalized = normalized.replace("\u2013", "-")
    normalized = normalized.replace("\u2014", "-")
    normalized = normalized.replace("\u2212", "-")
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def _tokens(text: str) -> list[str]:
    normalized = _normalize_for_substring(text)
    return _TOKEN_RE.findall(normalized)


def _content_tokens(text: str) -> list[str]:
    return [token for token in _tokens(text) if token not in _STOPWORDS]


__all__ = [
    "DEFAULT_EVIDENCE_OVERLAP_THRESHOLD",
    "RejectedCardDiagnostic",
    "SourceVerificationResult",
    "evidence_quote_is_supported",
    "verify_card_sources",
    "verify_extraction_batch_sources",
]
