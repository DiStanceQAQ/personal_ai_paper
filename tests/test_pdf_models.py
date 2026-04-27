"""Tests for PDF parser data contract models."""

import json
import math
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pdf_models import (
    ChunkCandidate,
    ELEMENT_TYPES,
    EXTRACTION_METHODS,
    PASSAGE_TYPES,
    ParseAsset,
    ParseDocument,
    ParseElement,
    ParseTable,
    PassageRecord,
    PdfQualityReport,
)


def test_pdf_models_is_in_packaged_runtime_modules() -> None:
    """The runtime parser models module should be included in packaged builds."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert "pdf_models" in pyproject["tool"]["setuptools"]["py-modules"]


def test_pdf_models_declares_direct_pydantic_v2_runtime_dependency() -> None:
    """Packaged parser models should declare their Pydantic runtime dependency."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert "pydantic>=2,<3" in pyproject["project"]["dependencies"]


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
    assert PASSAGE_TYPES == (
        "abstract",
        "introduction",
        "method",
        "result",
        "discussion",
        "limitation",
        "appendix",
        "body",
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


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (
            ParseElement,
            {
                "id": "element-1",
                "element_index": 0,
                "element_type": "paragraph",
                "extraction_method": "native_text",
                "backend_confidence": 0.92,
            },
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "text",
                "backend_confidence": 0.92,
            },
        ),
    ],
)
def test_models_reject_unknown_extra_fields(
    model_cls: type[Any],
    kwargs: dict[str, Any],
) -> None:
    """Backend-specific fields should be rejected unless stored in metadata."""
    with pytest.raises(ValidationError):
        model_cls(**kwargs)


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (
            ParseElement,
            {
                "id": "",
                "element_index": 0,
                "element_type": "paragraph",
                "extraction_method": "native_text",
            },
        ),
        (ParseTable, {"id": ""}),
        (ParseAsset, {"id": "", "asset_type": "figure"}),
        (
            ParseDocument,
            {
                "paper_id": "",
                "space_id": "space-1",
                "backend": "structured-parser",
                "extraction_method": "native_text",
                "quality": PdfQualityReport(),
            },
        ),
        (
            ParseDocument,
            {
                "paper_id": "paper-1",
                "space_id": "",
                "backend": "structured-parser",
                "extraction_method": "native_text",
                "quality": PdfQualityReport(),
            },
        ),
        (ChunkCandidate, {"id": "", "element_ids": ["element-1"], "text": "text"}),
        (ChunkCandidate, {"id": "chunk-1", "element_ids": [""], "text": "text"}),
        (
            PassageRecord,
            {
                "id": "",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "text",
            },
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "",
                "space_id": "space-1",
                "original_text": "text",
            },
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "",
                "original_text": "text",
            },
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "text",
                "parse_run_id": "run-1",
                "element_ids": [""],
            },
        ),
    ],
)
def test_models_reject_empty_required_identity_fields(
    model_cls: type[Any],
    kwargs: dict[str, Any],
) -> None:
    """Required IDs and identity references should not accept empty strings."""
    with pytest.raises(ValidationError):
        model_cls(**kwargs)


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (
            ParseElement,
            {
                "id": "   ",
                "element_index": 0,
                "element_type": "paragraph",
                "extraction_method": "native_text",
            },
        ),
        (ParseTable, {"id": "\t"}),
        (ParseAsset, {"id": "   ", "asset_type": "figure"}),
        (
            ParseDocument,
            {
                "paper_id": "   ",
                "space_id": "space-1",
                "backend": "structured-parser",
                "extraction_method": "native_text",
                "quality": PdfQualityReport(),
            },
        ),
        (
            ParseDocument,
            {
                "paper_id": "paper-1",
                "space_id": "   ",
                "backend": "structured-parser",
                "extraction_method": "native_text",
                "quality": PdfQualityReport(),
            },
        ),
        (ChunkCandidate, {"id": "   ", "element_ids": ["element-1"], "text": "text"}),
        (ChunkCandidate, {"id": "chunk-1", "element_ids": ["   "], "text": "text"}),
        (ChunkCandidate, {"id": "chunk-1", "element_ids": ["element-1"], "text": "   "}),
        (
            PassageRecord,
            {
                "id": "   ",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "text",
            },
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "   ",
                "space_id": "space-1",
                "original_text": "text",
            },
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "   ",
                "original_text": "text",
            },
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "   ",
            },
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "text",
                "parse_run_id": "run-1",
                "element_ids": ["   "],
            },
        ),
    ],
)
def test_models_reject_blank_required_identity_and_text_fields(
    model_cls: type[Any],
    kwargs: dict[str, Any],
) -> None:
    """Required IDs, source IDs, and required text should reject blank strings."""
    with pytest.raises(ValidationError):
        model_cls(**kwargs)


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (ParseTable, {"id": "table-1", "element_id": ""}),
        (ParseAsset, {"id": "asset-1", "asset_type": "figure", "element_id": "   "}),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "text",
                "parse_run_id": "",
                "element_ids": ["element-1"],
            },
        ),
    ],
)
def test_models_reject_blank_optional_provenance_references(
    model_cls: type[Any],
    kwargs: dict[str, Any],
) -> None:
    """Optional provenance references should be None or non-blank strings."""
    with pytest.raises(ValidationError):
        model_cls(**kwargs)


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (
            ChunkCandidate,
            {
                "id": "chunk-1",
                "element_ids": ["element-1", "element-1"],
                "text": "text",
            },
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "text",
                "parse_run_id": "run-1",
                "element_ids": ["element-1", "element-1"],
            },
        ),
    ],
)
def test_models_reject_duplicate_source_element_ids(
    model_cls: type[Any],
    kwargs: dict[str, Any],
) -> None:
    """Source element ID lists should not contain duplicates."""
    with pytest.raises(ValidationError):
        model_cls(**kwargs)


def test_models_strip_non_blank_identity_and_provenance_strings() -> None:
    """Non-blank identities should be normalized while preserving list order."""
    chunk = ChunkCandidate(
        id=" chunk-1 ",
        element_ids=[" element-1 ", " element-2 "],
        text=" chunk text ",
    )
    passage = PassageRecord(
        id=" passage-1 ",
        paper_id=" paper-1 ",
        space_id=" space-1 ",
        original_text=" passage text ",
        parse_run_id=" run-1 ",
        element_ids=[" element-1 ", " element-2 "],
    )
    table = ParseTable(id=" table-1 ", element_id=" element-1 ")
    asset = ParseAsset(id=" asset-1 ", asset_type="figure", element_id=" element-2 ")

    assert chunk.id == "chunk-1"
    assert chunk.element_ids == ["element-1", "element-2"]
    assert chunk.text == "chunk text"
    assert passage.id == "passage-1"
    assert passage.paper_id == "paper-1"
    assert passage.space_id == "space-1"
    assert passage.original_text == "passage text"
    assert passage.parse_run_id == "run-1"
    assert passage.element_ids == ["element-1", "element-2"]
    assert table.id == "table-1"
    assert table.element_id == "element-1"
    assert asset.id == "asset-1"
    assert asset.element_id == "element-2"


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (
            ParseElement,
            {
                "id": "element-1",
                "element_index": 0,
                "element_type": "paragraph",
                "extraction_method": "native_text",
                "page_number": "3",
            },
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "text",
                "page_number": "3",
            },
        ),
    ],
)
def test_models_reject_scalar_type_coercion(
    model_cls: type[Any],
    kwargs: dict[str, Any],
) -> None:
    """Parser models should reject scalar values with the wrong exact type."""
    with pytest.raises(ValidationError):
        model_cls(**kwargs)


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
    quality_first = PdfQualityReport()
    quality_second = PdfQualityReport()
    element_first = ParseElement(
        id="element-1",
        element_index=0,
        element_type="paragraph",
        extraction_method="native_text",
    )
    element_second = ParseElement(
        id="element-2",
        element_index=1,
        element_type="paragraph",
        extraction_method="native_text",
    )
    table_first = ParseTable(id="table-1")
    table_second = ParseTable(id="table-2")
    document_first = ParseDocument(
        paper_id="paper-1",
        space_id="space-1",
        backend="parser",
        extraction_method="native_text",
        quality=PdfQualityReport(),
    )
    document_second = ParseDocument(
        paper_id="paper-2",
        space_id="space-1",
        backend="parser",
        extraction_method="native_text",
        quality=PdfQualityReport(),
    )
    chunk_first = ChunkCandidate(id="chunk-1", element_ids=["element-1"], text="text")
    chunk_second = ChunkCandidate(id="chunk-2", element_ids=["element-2"], text="text")
    passage_first = PassageRecord(
        id="passage-1",
        paper_id="paper-1",
        space_id="space-1",
        original_text="text",
    )
    passage_second = PassageRecord(
        id="passage-2",
        paper_id="paper-2",
        space_id="space-1",
        original_text="text",
    )

    quality_first.warnings.append("low text coverage")
    quality_first.metadata["backend"] = "fitz"
    element_first.heading_path.append("Method")
    element_first.metadata["role"] = "body"
    table_first.cells.append(["metric", "value"])
    table_first.metadata["source"] = "native"
    document_first.elements.append(element_first)
    document_first.tables.append(table_first)
    document_first.assets.append(ParseAsset(id="asset-1", asset_type="figure"))
    document_first.metadata["run"] = "run-1"
    chunk_first.heading_path.append("Results")
    chunk_first.quality_flags.append("short")
    chunk_first.metadata["source"] = "chunker"
    passage_first.element_ids.append("element-1")
    passage_first.heading_path.append("Discussion")
    passage_first.quality_flags.append("review")
    passage_first.metadata["source"] = "chunker"

    assert quality_second.warnings == []
    assert quality_second.metadata == {}
    assert element_second.heading_path == []
    assert element_second.metadata == {}
    assert table_second.cells == []
    assert table_second.metadata == {}
    assert document_second.elements == []
    assert document_second.tables == []
    assert document_second.assets == []
    assert document_second.metadata == {}
    assert chunk_second.heading_path == []
    assert chunk_second.quality_flags == []
    assert chunk_second.metadata == {}
    assert passage_second.element_ids == []
    assert passage_second.heading_path == []
    assert passage_second.quality_flags == []
    assert passage_second.metadata == {}


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


@pytest.mark.parametrize(
    ("field_name", "items"),
    [
        (
            "elements",
            [
                ParseElement(
                    id="element-1",
                    element_index=0,
                    element_type="paragraph",
                    extraction_method="native_text",
                ),
                ParseElement(
                    id="element-1",
                    element_index=1,
                    element_type="paragraph",
                    extraction_method="native_text",
                ),
            ],
        ),
        ("tables", [ParseTable(id="table-1"), ParseTable(id="table-1")]),
        (
            "assets",
            [
                ParseAsset(id="asset-1", asset_type="figure"),
                ParseAsset(id="asset-1", asset_type="figure"),
            ],
        ),
    ],
)
def test_parse_document_rejects_duplicate_parse_artifact_ids(
    field_name: str,
    items: list[Any],
) -> None:
    """ParseDocument should reject duplicate element, table, and asset IDs."""
    kwargs = {
        "paper_id": "paper-1",
        "space_id": "space-1",
        "backend": "structured-parser",
        "extraction_method": "native_text",
        "quality": PdfQualityReport(),
        field_name: items,
    }

    with pytest.raises(ValidationError):
        ParseDocument(**kwargs)


@pytest.mark.parametrize(
    ("tables", "assets"),
    [
        ([ParseTable(id="table-1", element_id="missing-element")], []),
        (
            [],
            [
                ParseAsset(
                    id="asset-1",
                    asset_type="figure",
                    element_id="missing-element",
                )
            ],
        ),
    ],
)
def test_parse_document_rejects_table_and_asset_references_missing_elements(
    tables: list[ParseTable],
    assets: list[ParseAsset],
) -> None:
    """ParseDocument should keep table and asset element references grounded."""
    element = ParseElement(
        id="element-1",
        element_index=0,
        element_type="paragraph",
        extraction_method="native_text",
    )

    with pytest.raises(ValidationError):
        ParseDocument(
            paper_id="paper-1",
            space_id="space-1",
            backend="structured-parser",
            extraction_method="native_text",
            quality=PdfQualityReport(),
            elements=[element],
            tables=tables,
            assets=assets,
        )


def test_parse_document_allows_ungrounded_tables_and_assets_without_element_id() -> None:
    """Legacy or backend-level tables and assets may omit element grounding."""
    document = ParseDocument(
        paper_id="paper-1",
        space_id="space-1",
        backend="structured-parser",
        extraction_method="native_text",
        quality=PdfQualityReport(),
        elements=[],
        tables=[ParseTable(id="table-1", element_id=None)],
        assets=[ParseAsset(id="asset-1", asset_type="figure", element_id=None)],
    )

    assert document.tables[0].element_id is None
    assert document.assets[0].element_id is None


@pytest.mark.parametrize("passage_type", ["conclusion", "reference", "unknown"])
def test_passage_record_rejects_unknown_passage_type(passage_type: str) -> None:
    """PassageRecord should enforce the same passage_type vocabulary as SQLite."""
    with pytest.raises(ValidationError):
        PassageRecord(
            id="passage-1",
            paper_id="paper-1",
            space_id="space-1",
            original_text="text",
            passage_type=passage_type,
        )


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


def test_passage_record_allows_legacy_rows_without_parse_run_grounding() -> None:
    """Legacy passages may omit parse_run_id and element grounding."""
    passage = PassageRecord(
        id="passage-1",
        paper_id="paper-1",
        space_id="space-1",
        original_text="Legacy passage text.",
        parse_run_id=None,
        element_ids=[],
    )

    assert passage.parse_run_id is None
    assert passage.element_ids == []


def test_passage_record_rejects_parse_run_without_element_grounding() -> None:
    """Structured passage provenance should identify source parse elements."""
    with pytest.raises(ValidationError):
        PassageRecord(
            id="passage-1",
            paper_id="paper-1",
            space_id="space-1",
            original_text="Structured passage text.",
            parse_run_id="run-1",
            element_ids=[],
        )


def test_passage_record_to_passage_row_maps_provenance_to_json_columns() -> None:
    """PassageRecord should serialize provenance into DB-ready passage columns."""
    passage = PassageRecord(
        id="passage-1",
        paper_id="paper-1",
        space_id="space-1",
        section="方法",
        page_number=5,
        paragraph_index=2,
        original_text="我们使用对比学习。",
        parse_confidence=0.92,
        passage_type="method",
        parse_run_id="run-1",
        element_ids=["element-1", "element-2"],
        heading_path=["方法", "训练"],
        bbox=[12.0, 24.0, 300.0, 420.0],
        token_count=42,
        char_count=128,
        content_hash="hash-1",
        parser_backend="structured-parser",
        extraction_method="llm_parser",
        quality_flags=["需要复核"],
        metadata={"source": "chunker"},
    )

    row = passage.to_passage_row()

    assert row == {
        "id": "passage-1",
        "paper_id": "paper-1",
        "space_id": "space-1",
        "section": "方法",
        "page_number": 5,
        "paragraph_index": 2,
        "original_text": "我们使用对比学习。",
        "parse_confidence": 0.92,
        "passage_type": "method",
        "parse_run_id": "run-1",
        "element_ids_json": json.dumps(["element-1", "element-2"], ensure_ascii=False),
        "heading_path_json": json.dumps(["方法", "训练"], ensure_ascii=False),
        "bbox_json": json.dumps([12.0, 24.0, 300.0, 420.0], ensure_ascii=False),
        "token_count": 42,
        "char_count": 128,
        "content_hash": "hash-1",
        "parser_backend": "structured-parser",
        "extraction_method": "llm_parser",
        "quality_flags_json": json.dumps(["需要复核"], ensure_ascii=False),
    }
    assert "metadata" not in row


def test_passage_record_to_passage_row_uses_nullable_bbox_and_empty_method() -> None:
    """Absent optional provenance should map to the migrated passages defaults."""
    passage = PassageRecord(
        id="passage-1",
        paper_id="paper-1",
        space_id="space-1",
        original_text="text",
    )

    row = passage.to_passage_row()

    assert row["element_ids_json"] == "[]"
    assert row["heading_path_json"] == "[]"
    assert row["bbox_json"] is None
    assert row["extraction_method"] == ""
    assert row["quality_flags_json"] == "[]"


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (
            ParseElement,
            {
                "id": "element-1",
                "element_index": 0,
                "element_type": "paragraph",
                "extraction_method": "native_text",
                "bbox": [1.0, 2.0, 3.0],
            },
        ),
        (ParseTable, {"id": "table-1", "bbox": [1.0, 2.0, 3.0, 4.0, 5.0]}),
        (
            ParseAsset,
            {"id": "asset-1", "asset_type": "figure", "bbox": [1.0, 2.0]},
        ),
        (
            PassageRecord,
            {
                "id": "passage-1",
                "paper_id": "paper-1",
                "space_id": "space-1",
                "original_text": "text",
                "bbox": [1.0, 2.0, 3.0],
            },
        ),
    ],
)
def test_models_reject_bbox_without_four_coordinates(
    model_cls: type[Any],
    kwargs: dict[str, Any],
) -> None:
    """Bounding boxes should use four coordinates when present."""
    with pytest.raises(ValidationError):
        model_cls(**kwargs)


@pytest.mark.parametrize("coordinate", [math.nan, math.inf, -math.inf])
def test_models_reject_bbox_with_non_finite_coordinates(coordinate: float) -> None:
    """Bounding box coordinates should be finite numbers."""
    with pytest.raises(ValidationError):
        ParseElement(
            id="element-1",
            element_index=0,
            element_type="paragraph",
            extraction_method="native_text",
            bbox=[0.0, coordinate, 10.0, 20.0],
        )


@pytest.mark.parametrize(
    "bbox",
    [
        [10.0, 0.0, 9.0, 20.0],
        [0.0, 20.0, 10.0, 19.0],
    ],
)
def test_models_reject_bbox_with_inverted_bounds(bbox: list[float]) -> None:
    """Bounding boxes should keep x0 <= x1 and y0 <= y1."""
    with pytest.raises(ValidationError):
        PassageRecord(
            id="passage-1",
            paper_id="paper-1",
            space_id="space-1",
            original_text="text",
            bbox=bbox,
        )


def test_models_accept_bbox_with_ordered_negative_coordinates() -> None:
    """Negative coordinates are valid when bounds remain ordered."""
    element = ParseElement(
        id="element-1",
        element_index=0,
        element_type="paragraph",
        extraction_method="native_text",
        bbox=[-10.0, -20.0, -1.0, -2.0],
    )

    assert element.bbox == [-10.0, -20.0, -1.0, -2.0]


def test_chunk_candidate_rejects_page_end_before_page_start() -> None:
    """ChunkCandidate should keep page ranges internally consistent."""
    with pytest.raises(ValidationError):
        ChunkCandidate(
            id="chunk-1",
            element_ids=["element-1"],
            text="text",
            page_start=5,
            page_end=4,
        )


def test_chunk_candidate_rejects_empty_element_ids() -> None:
    """ChunkCandidate should reference at least one source parse element."""
    with pytest.raises(ValidationError):
        ChunkCandidate(id="chunk-1", element_ids=[], text="text")


def test_chunk_candidate_rejects_empty_text() -> None:
    """ChunkCandidate should not store empty chunk text."""
    with pytest.raises(ValidationError):
        ChunkCandidate(id="chunk-1", element_ids=["element-1"], text="")


def test_passage_record_rejects_empty_original_text() -> None:
    """PassageRecord should not store empty passage text."""
    with pytest.raises(ValidationError):
        PassageRecord(
            id="passage-1",
            paper_id="paper-1",
            space_id="space-1",
            original_text="",
        )
