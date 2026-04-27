"""Pydantic data contracts for structured PDF parsing."""

import json
import math
from typing import Annotated, Any, Final, Literal, Self, TypeAlias

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

ElementType: TypeAlias = Literal[
    "title",
    "heading",
    "paragraph",
    "list",
    "table",
    "figure",
    "caption",
    "equation",
    "code",
    "reference",
    "page_header",
    "page_footer",
    "unknown",
]

ExtractionMethod: TypeAlias = Literal[
    "native_text",
    "ocr",
    "layout_model",
    "llm_parser",
    "legacy",
]

PassageType: TypeAlias = Literal[
    "abstract",
    "introduction",
    "method",
    "result",
    "discussion",
    "limitation",
    "appendix",
    "body",
]


def _validate_bbox(value: list[float]) -> list[float]:
    """Validate PDF-style bounding box coordinates."""
    if any(not math.isfinite(coordinate) for coordinate in value):
        raise ValueError("bbox coordinates must be finite numbers")

    x0, y0, x1, y1 = value
    if x0 > x1:
        raise ValueError("bbox x0 must be less than or equal to x1")
    if y0 > y1:
        raise ValueError("bbox y0 must be less than or equal to y1")
    return value


BBox: TypeAlias = Annotated[
    list[float],
    Field(min_length=4, max_length=4),
    AfterValidator(_validate_bbox),
]

NonEmptyString: TypeAlias = Annotated[str, Field(min_length=1)]

ELEMENT_TYPES: Final[tuple[ElementType, ...]] = (
    "title",
    "heading",
    "paragraph",
    "list",
    "table",
    "figure",
    "caption",
    "equation",
    "code",
    "reference",
    "page_header",
    "page_footer",
    "unknown",
)

EXTRACTION_METHODS: Final[tuple[ExtractionMethod, ...]] = (
    "native_text",
    "ocr",
    "layout_model",
    "llm_parser",
    "legacy",
)

PASSAGE_TYPES: Final[tuple[PassageType, ...]] = (
    "abstract",
    "introduction",
    "method",
    "result",
    "discussion",
    "limitation",
    "appendix",
    "body",
)


class _ParserContractModel(BaseModel):
    """Shared configuration for parser contract models."""

    model_config = ConfigDict(extra="forbid", strict=True)


def _reject_duplicate_ids(collection_name: str, ids: list[str]) -> None:
    """Reject duplicate IDs while preserving the first duplicate in the error."""
    seen: set[str] = set()
    for id_value in ids:
        if id_value in seen:
            raise ValueError(f"duplicate {collection_name} id {id_value}")
        seen.add(id_value)


class PdfQualityReport(_ParserContractModel):
    """Quality signals gathered before or during PDF parsing."""

    page_count: int = Field(default=0, ge=0)
    native_text_pages: int = Field(default=0, ge=0)
    image_only_pages: int = Field(default=0, ge=0)
    estimated_table_pages: int = Field(default=0, ge=0)
    estimated_two_column_pages: int = Field(default=0, ge=0)
    needs_ocr: bool = False
    needs_layout_model: bool = False
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseElement(_ParserContractModel):
    """A single structured text or layout element extracted from a PDF."""

    id: NonEmptyString
    element_index: int = Field(ge=0)
    element_type: ElementType
    text: str = ""
    page_number: int = Field(default=0, ge=0)
    bbox: BBox | None = None
    heading_path: list[str] = Field(default_factory=list)
    extraction_method: ExtractionMethod
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseTable(_ParserContractModel):
    """A normalized table extracted from a parsed document."""

    id: NonEmptyString
    element_id: str | None = None
    table_index: int = Field(default=0, ge=0)
    page_number: int = Field(default=0, ge=0)
    caption: str = ""
    cells: list[list[str]] = Field(default_factory=list)
    bbox: BBox | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseAsset(_ParserContractModel):
    """A non-text asset extracted from a parsed document."""

    id: NonEmptyString
    element_id: str | None = None
    asset_type: str
    page_number: int = Field(default=0, ge=0)
    uri: str = ""
    bbox: BBox | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseDocument(_ParserContractModel):
    """Complete structured parse output for a paper."""

    paper_id: NonEmptyString
    space_id: NonEmptyString
    backend: str
    extraction_method: ExtractionMethod
    quality: PdfQualityReport
    elements: list[ParseElement] = Field(default_factory=list)
    tables: list[ParseTable] = Field(default_factory=list)
    assets: list[ParseAsset] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_element_references(self) -> Self:
        """Validate table and asset references against document elements."""
        _reject_duplicate_ids("element", [element.id for element in self.elements])
        _reject_duplicate_ids("table", [table.id for table in self.tables])
        _reject_duplicate_ids("asset", [asset.id for asset in self.assets])

        element_ids = {element.id for element in self.elements}

        for table in self.tables:
            if table.element_id is not None and table.element_id not in element_ids:
                raise ValueError(
                    f"table {table.id} references unknown element_id {table.element_id}"
                )

        for asset in self.assets:
            if asset.element_id is not None and asset.element_id not in element_ids:
                raise ValueError(
                    f"asset {asset.id} references unknown element_id {asset.element_id}"
                )

        return self


class ChunkCandidate(_ParserContractModel):
    """Candidate passage chunk assembled from one or more parse elements."""

    id: NonEmptyString
    element_ids: list[NonEmptyString] = Field(min_length=1)
    text: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    page_start: int = Field(default=0, ge=0)
    page_end: int = Field(default=0, ge=0)
    token_count: int = Field(default=0, ge=0)
    char_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_page_range(self) -> Self:
        """Validate that the candidate page range is ordered."""
        if self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class PassageRecord(_ParserContractModel):
    """Storage-ready passage row with structured parse provenance."""

    id: NonEmptyString
    paper_id: NonEmptyString
    space_id: NonEmptyString
    section: str = ""
    page_number: int = Field(default=0, ge=0)
    paragraph_index: int = Field(default=0, ge=0)
    original_text: NonEmptyString
    parse_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    passage_type: PassageType = "body"
    parse_run_id: str | None = None
    element_ids: list[NonEmptyString] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)
    bbox: BBox | None = None
    token_count: int | None = Field(default=None, ge=0)
    char_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    parser_backend: str = ""
    extraction_method: ExtractionMethod | None = None
    quality_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source_grounding(self) -> Self:
        """Validate structured provenance has source element IDs."""
        if self.parse_run_id is not None and not self.element_ids:
            raise ValueError("element_ids must be set when parse_run_id is set")
        return self

    def to_passage_row(self) -> dict[str, Any]:
        """Return a dict aligned with the migrated passages table columns."""
        return {
            "id": self.id,
            "paper_id": self.paper_id,
            "space_id": self.space_id,
            "section": self.section,
            "page_number": self.page_number,
            "paragraph_index": self.paragraph_index,
            "original_text": self.original_text,
            "parse_confidence": self.parse_confidence,
            "passage_type": self.passage_type,
            "parse_run_id": self.parse_run_id,
            "element_ids_json": json.dumps(
                self.element_ids,
                ensure_ascii=False,
            ),
            "heading_path_json": json.dumps(
                self.heading_path,
                ensure_ascii=False,
            ),
            "bbox_json": (
                json.dumps(self.bbox, ensure_ascii=False)
                if self.bbox is not None
                else None
            ),
            "token_count": self.token_count,
            "char_count": self.char_count,
            "content_hash": self.content_hash,
            "parser_backend": self.parser_backend,
            "extraction_method": self.extraction_method or "",
            "quality_flags_json": json.dumps(
                self.quality_flags,
                ensure_ascii=False,
            ),
        }


__all__ = [
    "BBox",
    "ELEMENT_TYPES",
    "EXTRACTION_METHODS",
    "NonEmptyString",
    "PASSAGE_TYPES",
    "PassageType",
    "ChunkCandidate",
    "ElementType",
    "ExtractionMethod",
    "ParseAsset",
    "ParseDocument",
    "ParseElement",
    "ParseTable",
    "PassageRecord",
    "PdfQualityReport",
]
