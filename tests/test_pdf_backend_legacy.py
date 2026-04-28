"""Tests for the legacy PyMuPDF parser backend."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from pdf_backend_base import ParserBackendError
from pdf_backend_legacy import LegacyPyMuPDFBackend, _normalize_table_cell
from pdf_chunker import chunk_parse_document
from pdf_models import ParseDocument, PdfQualityReport
from tests.fixtures.pdf_factory import table_pdf


def _create_simple_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 90), "Abstract", fontsize=12)
    page.insert_text(
        (50, 125),
        "This abstract describes the paper with enough text to become a passage.",
        fontsize=11,
    )
    page.insert_text((50, 170), "1. Introduction", fontsize=12)
    page.insert_text(
        (50, 205),
        "This introduction explains the problem and related work in one paragraph.",
        fontsize=11,
    )
    doc.save(str(path))
    doc.close()


def test_backend_reports_available_when_pymupdf_can_import() -> None:
    backend = LegacyPyMuPDFBackend()

    assert backend.name == "legacy-pymupdf"
    assert backend.is_available() is True


def test_parse_simple_pdf_returns_legacy_parse_document(tmp_path: Path) -> None:
    pdf_path = tmp_path / "simple.pdf"
    _create_simple_pdf(pdf_path)
    quality = PdfQualityReport(page_count=1, native_text_pages=1)

    document = LegacyPyMuPDFBackend().parse(pdf_path, "paper-1", "space-1", quality)

    assert isinstance(document, ParseDocument)
    assert document.paper_id == "paper-1"
    assert document.space_id == "space-1"
    assert document.backend == "legacy-pymupdf"
    assert document.extraction_method == "legacy"
    assert document.quality is quality
    assert document.metadata["parser"] == "pymupdf.get_text_blocks"
    assert document.elements

    paragraph = document.elements[0]
    assert paragraph.element_type == "paragraph"
    assert paragraph.extraction_method == "legacy"
    assert paragraph.page_number == 1
    assert paragraph.metadata["section"] == "abstract"
    assert paragraph.metadata["passage_type"] == "abstract"
    assert paragraph.metadata["parse_confidence"] == 0.9
    assert "abstract describes" in paragraph.text


def test_backend_exposes_legacy_passage_dicts(tmp_path: Path) -> None:
    pdf_path = tmp_path / "simple.pdf"
    _create_simple_pdf(pdf_path)

    passages = LegacyPyMuPDFBackend().extract_passages(
        pdf_path,
        "paper-1",
        "space-1",
    )

    assert passages
    assert set(passages[0]) == {
        "id",
        "paper_id",
        "space_id",
        "section",
        "page_number",
        "paragraph_index",
        "original_text",
        "parse_confidence",
        "passage_type",
    }
    assert passages[0]["paper_id"] == "paper-1"
    assert passages[0]["space_id"] == "space-1"
    assert passages[0]["section"] == "abstract"
    assert passages[0]["passage_type"] == "abstract"


def test_parse_table_pdf_extracts_table_and_avoids_duplicate_body_passage(
    tmp_path: Path,
) -> None:
    pdf_path = table_pdf(tmp_path / "table.pdf")
    quality = PdfQualityReport(
        page_count=1,
        native_text_pages=1,
        estimated_table_pages=1,
        needs_layout_model=True,
    )

    document = LegacyPyMuPDFBackend().parse(pdf_path, "paper-1", "space-1", quality)

    assert document.tables
    table = document.tables[0]
    assert table.cells == [
        ["Metric", "Baseline", "Proposed"],
        ["Accuracy", "81.2", "89.7"],
        ["Latency", "240 ms", "180 ms"],
        ["Coverage", "72%", "91%"],
    ]
    assert any(
        element.id == table.element_id and element.element_type == "table"
        for element in document.elements
    )

    passages = chunk_parse_document(document)
    table_passages = [
        passage for passage in passages if passage.metadata.get("table_id") == table.id
    ]
    assert table_passages
    assert all(
        term in table_passages[0].original_text
        for term in ("Metric", "Accuracy", "Latency", "Coverage")
    )
    assert not any(
        "Accuracy" in passage.original_text and not passage.metadata.get("table_id")
        for passage in passages
    )


def test_normalize_table_cell_repairs_split_decimal_artifact() -> None:
    assert _normalize_table_cell("812 .") == "81.2"
    assert _normalize_table_cell("897 .") == "89.7"
    assert _normalize_table_cell("240ms") == "240 ms"
    assert _normalize_table_cell("240 ms") == "240 ms"
    assert _normalize_table_cell("72%") == "72%"


def test_backend_invalid_pdf_raises_parser_backend_error(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.pdf"
    bad_path.write_text("not a pdf")

    with pytest.raises(ParserBackendError) as exc_info:
        LegacyPyMuPDFBackend().parse(
            bad_path,
            "paper-1",
            "space-1",
            PdfQualityReport(),
        )

    assert exc_info.value.backend_name == "legacy-pymupdf"


def test_extract_passages_keeps_invalid_pdf_compatibility(tmp_path: Path) -> None:
    from parser import extract_passages_from_pdf

    bad_path = tmp_path / "bad.pdf"
    bad_path.write_text("not a pdf")

    assert extract_passages_from_pdf(bad_path, "paper-1", "space-1") == []


def test_pyproject_packages_legacy_backend() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    modules: list[Any] = pyproject["tool"]["setuptools"]["py-modules"]

    assert "pdf_backend_legacy" in modules
