"""Tests for PDF quality profiling used by parser routing."""

from pathlib import Path

from pdf_models import PdfQualityReport
from pdf_profile import inspect_pdf
from tests.fixtures.pdf_factory import (
    image_only_pdf,
    long_section_pdf,
    simple_academic_pdf,
    table_pdf,
    two_column_pdf,
)


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
