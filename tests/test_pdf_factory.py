"""Tests for generated PDF fixtures."""

from pathlib import Path

import pymupdf

from tests.fixtures.pdf_factory import (
    image_only_pdf,
    long_section_pdf,
    references_pdf,
    simple_academic_pdf,
    table_pdf,
    two_column_pdf,
)


def _text_for(path: Path) -> str:
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def test_simple_academic_pdf_contains_expected_sections(tmp_path: Path) -> None:
    pdf_path = simple_academic_pdf(tmp_path / "simple.pdf")

    with pymupdf.open(pdf_path) as doc:
        assert doc.page_count >= 1
        text = "\n".join(page.get_text() for page in doc)

    for heading in ("Abstract", "Introduction", "Method", "Results"):
        assert heading in text
    assert "A Small Study of Local Paper Knowledge" in text


def test_two_column_pdf_extracts_text_from_both_columns(tmp_path: Path) -> None:
    pdf_path = two_column_pdf(tmp_path / "two-column.pdf")

    with pymupdf.open(pdf_path) as doc:
        assert doc.page_count >= 1
        page = doc[0]
        text = page.get_text()
        words = page.get_text("words")

    assert "Left column contribution" in text
    assert "Right column evidence" in text
    assert len(words) > 20


def test_table_pdf_contains_header_terms_and_grid_drawing(tmp_path: Path) -> None:
    pdf_path = table_pdf(tmp_path / "table.pdf")

    with pymupdf.open(pdf_path) as doc:
        assert doc.page_count >= 1
        page = doc[0]
        text = page.get_text()
        drawings = page.get_drawings()

    for term in ("Metric", "Baseline", "Proposed"):
        assert term in text
    assert drawings


def test_image_only_pdf_has_image_without_selectable_text(tmp_path: Path) -> None:
    pdf_path = image_only_pdf(tmp_path / "image-only.pdf")

    with pymupdf.open(pdf_path) as doc:
        assert doc.page_count >= 1
        page = doc[0]
        assert page.get_text().strip() == ""
        assert page.get_images(full=True) or page.get_drawings()


def test_references_pdf_contains_heading_and_citation_entries(tmp_path: Path) -> None:
    pdf_path = references_pdf(tmp_path / "references.pdf")
    text = _text_for(pdf_path)

    assert "References" in text
    assert "Smith, J." in text
    assert "Garcia, M." in text
    assert "(2024)" in text
    assert "(2025)" in text


def test_long_section_pdf_has_multiple_pages_and_substantial_text(
    tmp_path: Path,
) -> None:
    pdf_path = long_section_pdf(tmp_path / "long-section.pdf")

    with pymupdf.open(pdf_path) as doc:
        text = "\n".join(page.get_text() for page in doc)
        assert doc.page_count >= 2

    assert text.count("Long Evaluation Section") >= 2
    assert len(text) > 4_000
