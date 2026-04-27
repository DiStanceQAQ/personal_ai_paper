"""Pydantic data contracts for structured PDF parsing."""

from typing import Any, Final, Literal, TypeAlias

from pydantic import BaseModel, Field

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


class PdfQualityReport(BaseModel):
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


class ParseElement(BaseModel):
    """A single structured text or layout element extracted from a PDF."""

    id: str
    element_index: int = Field(ge=0)
    element_type: ElementType
    text: str = ""
    page_number: int = Field(default=0, ge=0)
    bbox: list[float] | None = None
    heading_path: list[str] = Field(default_factory=list)
    extraction_method: ExtractionMethod
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseTable(BaseModel):
    """A normalized table extracted from a parsed document."""

    id: str
    element_id: str | None = None
    table_index: int = Field(default=0, ge=0)
    page_number: int = Field(default=0, ge=0)
    caption: str = ""
    cells: list[list[str]] = Field(default_factory=list)
    bbox: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseAsset(BaseModel):
    """A non-text asset extracted from a parsed document."""

    id: str
    element_id: str | None = None
    asset_type: str
    page_number: int = Field(default=0, ge=0)
    uri: str = ""
    bbox: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseDocument(BaseModel):
    """Complete structured parse output for a paper."""

    paper_id: str
    space_id: str
    backend: str
    extraction_method: ExtractionMethod
    quality: PdfQualityReport
    elements: list[ParseElement] = Field(default_factory=list)
    tables: list[ParseTable] = Field(default_factory=list)
    assets: list[ParseAsset] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkCandidate(BaseModel):
    """Candidate passage chunk assembled from one or more parse elements."""

    id: str
    element_ids: list[str]
    text: str
    heading_path: list[str] = Field(default_factory=list)
    page_start: int = Field(default=0, ge=0)
    page_end: int = Field(default=0, ge=0)
    token_count: int = Field(default=0, ge=0)
    char_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PassageRecord(BaseModel):
    """Storage-ready passage row with structured parse provenance."""

    id: str
    paper_id: str
    space_id: str
    section: str = ""
    page_number: int = Field(default=0, ge=0)
    paragraph_index: int = Field(default=0, ge=0)
    original_text: str = ""
    parse_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    passage_type: str = "body"
    parse_run_id: str | None = None
    element_ids: list[str] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)
    bbox: list[float] | None = None
    token_count: int | None = Field(default=None, ge=0)
    char_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    parser_backend: str = ""
    extraction_method: ExtractionMethod | None = None
    quality_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ELEMENT_TYPES",
    "EXTRACTION_METHODS",
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
