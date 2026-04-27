"""Tests for the legacy PyMuPDF parser backend."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from pdf_backend_base import ParserBackendError
from pdf_backend_legacy import LegacyPyMuPDFBackend
from pdf_models import ParseDocument, PdfQualityReport


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
