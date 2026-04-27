"""Tests for PDF parser data contract models."""

from typing import Any

import pytest
from pydantic import ValidationError

from pdf_models import (
    ChunkCandidate,
    ELEMENT_TYPES,
    EXTRACTION_METHODS,
    ParseAsset,
    ParseDocument,
    ParseElement,
    ParseTable,
    PassageRecord,
    PdfQualityReport,
)


def test_allowed_vocabularies_are_exported_for_parser_callers() -> None:
    """Parser callers should be able to reuse the controlled vocabularies."""
    assert ELEMENT_TYPES == (
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
    assert EXTRACTION_METHODS == (
        "native_text",
        "ocr",
        "layout_model",
        "llm_parser",
        "legacy",
    )


def test_parse_element_accepts_controlled_element_and_extraction_types() -> None:
    """ParseElement should accept documented parser vocabularies."""
    element = ParseElement(
        id="element-1",
        element_index=0,
        element_type="paragraph",
        text="The model converged.",
        page_number=3,
        extraction_method="native_text",
    )

    assert element.element_type == "paragraph"
    assert element.extraction_method == "native_text"
    assert element.heading_path == []
    assert element.metadata == {}


@pytest.mark.parametrize("element_type", ["body", "section", "image"])
def test_parse_element_rejects_unknown_element_types(element_type: str) -> None:
    """ParseElement should reject values outside the parser element vocabulary."""
    with pytest.raises(ValidationError):
        ParseElement(
            id="element-1",
            element_index=0,
            element_type=element_type,
            extraction_method="native_text",
        )


@pytest.mark.parametrize("extraction_method", ["pdfminer", "vision", "manual"])
def test_parse_element_rejects_unknown_extraction_methods(
    extraction_method: str,
) -> None:
    """ParseElement should reject values outside the extraction method vocabulary."""
    with pytest.raises(ValidationError):
        ParseElement(
            id="element-1",
            element_index=0,
            element_type="paragraph",
            extraction_method=extraction_method,
        )


def test_model_defaults_do_not_share_mutable_state() -> None:
    """List and dict defaults should be independent for each model instance."""
    first = PdfQualityReport()
    second = PdfQualityReport()

    first.warnings.append("low text coverage")
    first.metadata["backend"] = "fitz"

    assert second.warnings == []
    assert second.metadata == {}


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (PdfQualityReport, {"page_count": -1}),
        (
            ParseElement,
            {
                "id": "element-1",
                "element_index": -1,
                "element_type": "paragraph",
                "extraction_method": "native_text",
            },
        ),
        (ParseTable, {"id": "table-1", "table_index": -1}),
        (ParseAsset, {"id": "asset-1", "asset_type": "figure", "page_number": -1}),
        (
            ChunkCandidate,
            {"id": "chunk-1", "element_ids": [], "text": "text", "token_count": -1},
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "text",
                "page_number": -1,
            },
        ),
    ],
)
def test_models_reject_negative_counts_indexes_and_pages(
    model_cls: type[Any],
    kwargs: dict[str, Any],
) -> None:
    """Parser data models should reject negative numeric positions and counts."""
    with pytest.raises(ValidationError):
        model_cls(**kwargs)


def test_parse_document_nests_quality_elements_tables_and_assets() -> None:
    """ParseDocument should contain structured parse artifacts."""
    quality = PdfQualityReport(page_count=4, native_text_pages=3, needs_ocr=True)
    element = ParseElement(
        id="element-1",
        element_index=0,
        element_type="table",
        page_number=2,
        extraction_method="layout_model",
    )
    table = ParseTable(
        id="table-1",
        element_id="element-1",
        table_index=0,
        page_number=2,
        caption="Ablation results",
        cells=[["Model", "Score"], ["Baseline", "0.71"]],
    )
    asset = ParseAsset(
        id="asset-1",
        element_id="element-1",
        asset_type="figure",
        page_number=2,
        uri="assets/figure-1.png",
    )

    document = ParseDocument(
        paper_id="paper-1",
        space_id="space-1",
        backend="structured-parser",
        extraction_method="layout_model",
        quality=quality,
        elements=[element],
        tables=[table],
        assets=[asset],
    )

    assert document.quality.needs_ocr is True
    assert document.elements[0].id == "element-1"
    assert document.tables[0].cells[1][1] == "0.71"
    assert document.assets[0].uri == "assets/figure-1.png"


def test_passage_record_matches_passages_table_and_provenance_fields() -> None:
    """PassageRecord should expose legacy passage columns plus parse provenance."""
    passage = PassageRecord(
        id="passage-1",
        paper_id="paper-1",
        space_id="space-1",
        section="method",
        page_number=5,
        paragraph_index=2,
        original_text="We train with contrastive loss.",
        parse_confidence=0.92,
        passage_type="method",
        parse_run_id="run-1",
        element_ids=["element-1", "element-2"],
        heading_path=["Method", "Training"],
        bbox=[12.0, 24.0, 300.0, 420.0],
        token_count=42,
        char_count=128,
        content_hash="hash-1",
        parser_backend="structured-parser",
        extraction_method="llm_parser",
        quality_flags=["requires-review"],
        metadata={"source": "chunker"},
    )

    assert passage.section == "method"
    assert passage.element_ids == ["element-1", "element-2"]
    assert passage.heading_path == ["Method", "Training"]
    assert passage.extraction_method == "llm_parser"
    assert passage.metadata == {"source": "chunker"}
