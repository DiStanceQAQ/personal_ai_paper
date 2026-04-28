"""Tests for PDF quality profiling used by parser routing."""

import importlib.util
from pathlib import Path
import tomllib

import pytest

from paper_engine.pdf.models import PdfQualityReport
from paper_engine.pdf.profile import inspect_pdf
from tests.fixtures.pdf_factory import (
    image_only_pdf,
    long_section_pdf,
    simple_academic_pdf,
    table_pdf,
    two_column_pdf,
)


def _stacked_textbox_pdf(path: Path) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((54, 54), "Native Text Performance Page", fontsize=12)
    page.insert_text((54, 76), "Section 1: Local PDF ingestion timing", fontsize=10)

    y = 104
    for paragraph_index in range(18):
        text = (
            "This deterministic native text paragraph exercises PyMuPDF4LLM "
            "parsing, normalization, and retrieval chunking for page 1, "
            f"paragraph {paragraph_index + 1}."
        )
        page.insert_textbox(
            pymupdf.Rect(54, y, 540, y + 26),
            text,
            fontsize=8,
        )
        y += 34

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc.save(str(path))
    finally:
        doc.close()
    return path


def test_simple_academic_pdf_has_native_text_and_no_routing_flags(
    tmp_path: Path,
) -> None:
    pdf_path = simple_academic_pdf(tmp_path / "simple.pdf")

    report = inspect_pdf(pdf_path)

    assert isinstance(report, PdfQualityReport)
    assert report.page_count == 1
    assert report.native_text_pages == 1
    assert report.image_only_pages == 0
    assert report.estimated_table_pages == 0
    assert report.estimated_two_column_pages == 0
    assert report.needs_ocr is False
    assert report.needs_layout_model is False
    assert report.warnings == []


def test_image_only_pdf_requests_ocr(tmp_path: Path) -> None:
    pdf_path = image_only_pdf(tmp_path / "image-only.pdf")

    report = inspect_pdf(pdf_path)

    assert report.page_count == 1
    assert report.native_text_pages == 0
    assert report.image_only_pages == 1
    assert report.needs_ocr is True
    assert "image_only_pages_detected" in report.warnings
    assert "no_native_text_detected" in report.warnings


def test_two_column_pdf_requests_layout_model(tmp_path: Path) -> None:
    pdf_path = two_column_pdf(tmp_path / "two-column.pdf")

    report = inspect_pdf(pdf_path)

    assert report.page_count == 1
    assert report.native_text_pages == 1
    assert report.estimated_two_column_pages >= 1
    assert report.needs_layout_model is True
    assert "two_column_layout_detected" in report.warnings


def test_table_pdf_requests_layout_model(tmp_path: Path) -> None:
    pdf_path = table_pdf(tmp_path / "table.pdf")

    report = inspect_pdf(pdf_path)

    assert report.page_count == 1
    assert report.native_text_pages == 1
    assert report.estimated_table_pages >= 1
    assert report.needs_layout_model is True
    assert "tables_detected" in report.warnings


def test_layout_activation_does_not_make_paragraph_pages_look_like_tables(
    tmp_path: Path,
) -> None:
    if importlib.util.find_spec("pymupdf4llm") is None:
        pytest.skip("pymupdf4llm is required to reproduce layout activation")

    from paper_engine.pdf.backends.pymupdf4llm import PyMuPDF4LLMBackend

    warmup_pdf = simple_academic_pdf(tmp_path / "warmup.pdf")
    PyMuPDF4LLMBackend().parse(
        warmup_pdf,
        paper_id="paper-warmup",
        space_id="space",
        quality_report=inspect_pdf(warmup_pdf),
    )
    paragraph_pdf = _stacked_textbox_pdf(tmp_path / "paragraphs.pdf")

    report = inspect_pdf(paragraph_pdf)

    assert report.estimated_table_pages == 0
    assert report.needs_layout_model is False
    assert "tables_detected" not in report.warnings


def test_long_section_pdf_counts_all_native_text_pages(tmp_path: Path) -> None:
    pdf_path = long_section_pdf(tmp_path / "long-section.pdf")

    report = inspect_pdf(pdf_path)

    assert report.page_count == 3
    assert report.native_text_pages == 3
    assert report.image_only_pages == 0
    assert report.needs_ocr is False


def test_missing_pdf_returns_warning_instead_of_raising(tmp_path: Path) -> None:
    report = inspect_pdf(tmp_path / "missing.pdf")

    assert report.page_count == 0
    assert report.native_text_pages == 0
    assert report.needs_ocr is True
    assert "pdf_open_failed" in report.warnings
    assert report.metadata["error"]


def test_corrupt_pdf_returns_open_warning_instead_of_raising(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nnot a valid pdf body\n%%EOF")

    report = inspect_pdf(pdf_path)

    assert report.page_count == 0
    assert report.native_text_pages == 0
    assert report.needs_ocr is True
    assert "pdf_open_failed" in report.warnings
    assert report.metadata["error"]


def test_pdf_profile_is_in_packaged_module_allow_list() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "paper_engine*"
    ]
