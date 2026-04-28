"""Pydantic contracts for source-grounded AI paper analysis."""

from typing import Annotated, Any, Final, Literal, Self, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

CardType: TypeAlias = Literal[
    "Problem",
    "Claim",
    "Evidence",
    "Method",
    "Object",
    "Variable",
    "Metric",
    "Result",
    "Failure Mode",
    "Interpretation",
    "Limitation",
    "Practical Tip",
]

CARD_TYPES: Final[tuple[CardType, ...]] = (
    "Problem",
    "Claim",
    "Evidence",
    "Method",
    "Object",
    "Variable",
    "Metric",
    "Result",
    "Failure Mode",
    "Interpretation",
    "Limitation",
    "Practical Tip",
)

NonBlankString: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
StrippedString: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True),
]


def _reject_duplicate_strings(collection_name: str, values: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {collection_name} value {value}")
        seen.add(value)


def _validate_source_passage_ids(values: list[str]) -> list[str]:
    _reject_duplicate_strings("source_passage_ids", values)
    return values


SourcePassageIds: TypeAlias = Annotated[
    list[NonBlankString],
    AfterValidator(_validate_source_passage_ids),
]


class _AnalysisContractModel(BaseModel):
    """Shared configuration for AI analysis contract models."""

    model_config = ConfigDict(extra="forbid", strict=True)


class PaperMetadataExtraction(_AnalysisContractModel):
    """Structured scholarly metadata extracted from source-grounded evidence."""

    title: StrippedString = ""
    authors: list[NonBlankString] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=0)
    venue: StrippedString = ""
    doi: StrippedString = ""
    arxiv_id: StrippedString = ""
    abstract: StrippedString = ""
    source_passage_ids: SourcePassageIds = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CardExtraction(_AnalysisContractModel):
    """A strict, source-grounded card proposed by the AI extractor."""

    card_type: CardType
    summary: NonBlankString
    source_passage_ids: SourcePassageIds = Field(min_length=1)
    evidence_quote: NonBlankString
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: NonBlankString
    quality_flags: list[NonBlankString] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CardExtractionBatch(_AnalysisContractModel):
    """A batch of AI cards extracted from a bounded source passage set."""

    paper_id: NonBlankString
    space_id: NonBlankString
    batch_index: int = Field(default=0, ge=0)
    source_passage_ids: SourcePassageIds = Field(min_length=1)
    cards: list[CardExtraction] = Field(default_factory=list)
    warnings: list[NonBlankString] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_card_sources_are_in_batch(self) -> Self:
        batch_sources = set(self.source_passage_ids)
        for card in self.cards:
            missing_sources = [
                source_id
                for source_id in card.source_passage_ids
                if source_id not in batch_sources
            ]
            if missing_sources:
                raise ValueError(
                    "card source_passage_ids must be included in batch sources: "
                    + ", ".join(missing_sources)
                )
        return self


class AnalysisQualityReport(_AnalysisContractModel):
    """Diagnostics for an AI analysis run."""

    accepted_card_count: int = Field(default=0, ge=0)
    rejected_card_count: int = Field(default=0, ge=0)
    source_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[NonBlankString] = Field(default_factory=list)
    validation_errors: list[NonBlankString] = Field(default_factory=list)
    quality_flags: list[NonBlankString] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class MergedAnalysisResult(_AnalysisContractModel):
    """Final merged AI analysis output for one paper."""

    paper_id: NonBlankString
    space_id: NonBlankString
    metadata: PaperMetadataExtraction = Field(default_factory=PaperMetadataExtraction)
    cards: list[CardExtraction] = Field(default_factory=list)
    quality: AnalysisQualityReport = Field(default_factory=AnalysisQualityReport)
    model: StrippedString = ""
    provider: StrippedString = ""
    extractor_version: StrippedString = ""
    metadata_extra: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "CARD_TYPES",
    "CardType",
    "NonBlankString",
    "SourcePassageIds",
    "StrippedString",
    "AnalysisQualityReport",
    "CardExtraction",
    "CardExtractionBatch",
    "MergedAnalysisResult",
    "PaperMetadataExtraction",
]
