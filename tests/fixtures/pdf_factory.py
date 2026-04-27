"""Generated PDF fixtures for parser and ingestion tests."""

from pathlib import Path

import pymupdf


def _save(doc: pymupdf.Document, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc.save(str(path))
    finally:
        doc.close()
    return path


def simple_academic_pdf(path: Path) -> Path:
    """Write a one-page academic-style PDF with selectable text."""
    doc = pymupdf.open()
    page = doc.new_page()
    text = (
        "A Small Study of Local Paper Knowledge\n\n"
        "Abstract\n"
        "This paper studies deterministic PDF fixtures for local research tools.\n\n"
        "Introduction\n"
        "Local paper workflows need stable documents with predictable text.\n\n"
        "Method\n"
        "We generate the document directly with PyMuPDF and fixed coordinates.\n\n"
        "Results\n"
        "The resulting PDF exposes native selectable text for extraction tests.\n\n"
        "Discussion\n"
        "Generated fixtures avoid committing binary PDFs while preserving coverage."
    )
    page.insert_textbox(pymupdf.Rect(54, 54, 540, 760), text, fontsize=11)
    return _save(doc, path)


def two_column_pdf(path: Path) -> Path:
    """Write a one-page PDF with visible selectable text in two columns."""
    doc = pymupdf.open()
    page = doc.new_page()
    left_text = (
        "Left column contribution\n"
        "The first column describes the research question, motivation, and "
        "background. It includes enough words for extraction to identify a "
        "substantial left-side text flow."
    )
    right_text = (
        "Right column evidence\n"
        "The second column reports observations, measurements, and conclusions. "
        "It is intentionally separate from the left column but remains selectable."
    )
    page.insert_textbox(pymupdf.Rect(54, 72, 280, 740), left_text, fontsize=10)
    page.insert_textbox(pymupdf.Rect(315, 72, 541, 740), right_text, fontsize=10)
    return _save(doc, path)


def table_pdf(path: Path) -> Path:
    """Write a one-page PDF containing a simple text table and drawn grid."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((54, 54), "Evaluation Table", fontsize=14)

    x0, y0 = 54, 96
    col_widths = [150, 150, 150]
    row_height = 34
    rows = [
        ("Metric", "Baseline", "Proposed"),
        ("Accuracy", "81.2", "89.7"),
        ("Latency", "240 ms", "180 ms"),
        ("Coverage", "72%", "91%"),
    ]

    total_width = sum(col_widths)
    total_height = row_height * len(rows)
    page.draw_rect(
        pymupdf.Rect(x0, y0, x0 + total_width, y0 + total_height),
        color=(0, 0, 0),
        width=0.8,
    )

    cursor_x = x0
    for width in col_widths[:-1]:
        cursor_x += width
        page.draw_line(
            (cursor_x, y0),
            (cursor_x, y0 + total_height),
            color=(0, 0, 0),
            width=0.8,
        )

    for row_index in range(1, len(rows)):
        y = y0 + row_height * row_index
        page.draw_line(
            (x0, y),
            (x0 + total_width, y),
            color=(0, 0, 0),
            width=0.8,
        )

    for row_index, row in enumerate(rows):
        cell_y = y0 + row_height * row_index + 21
        cell_x = x0 + 10
        for cell, width in zip(row, col_widths, strict=True):
            page.insert_text((cell_x, cell_y), cell, fontsize=10)
            cell_x += width

    return _save(doc, path)


def image_only_pdf(path: Path) -> Path:
    """Write a one-page PDF with drawn content and no selectable text."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(
        pymupdf.Rect(120, 140, 475, 500),
        color=(0.1, 0.1, 0.1),
        fill=(0.92, 0.94, 0.96),
        width=1.2,
    )
    page.draw_circle((240, 290), 72, color=(0.1, 0.35, 0.65), fill=(0.4, 0.7, 0.9))
    page.draw_line((180, 430), (420, 210), color=(0.75, 0.25, 0.2), width=3)
    return _save(doc, path)


def references_pdf(path: Path) -> Path:
    """Write a one-page PDF with a references section and citation entries."""
    doc = pymupdf.open()
    page = doc.new_page()
    text = (
        "Related Work Summary\n\n"
        "Prior work motivates citation-aware extraction and bibliographic parsing.\n\n"
        "References\n"
        "Smith, J. and Lee, A. (2024). Reliable document fixtures for research "
        "systems. Journal of Test Artifacts, 12(2), 15-29.\n"
        "Garcia, M. and Chen, R. (2025). Parsing scholarly PDFs with generated "
        "golden files. Proceedings of Local AI Evaluation, 44-58."
    )
    page.insert_textbox(pymupdf.Rect(54, 54, 540, 760), text, fontsize=11)
    return _save(doc, path)


def long_section_pdf(path: Path) -> Path:
    """Write a multi-page PDF with repeated section text for chunking tests."""
    doc = pymupdf.open()
    for page_number in range(3):
        page = doc.new_page()
        page.insert_text(
            (54, 54),
            f"Long Evaluation Section {page_number + 1}",
            fontsize=12,
        )
        y = 82
        for paragraph_number in range(44):
            line = (
                "This repeated section text gives future chunking tests stable "
                f"native content on page {page_number + 1}, paragraph "
                f"{paragraph_number + 1}, with enough words to exercise splitting."
            )
            page.insert_text((54, y), line, fontsize=8)
            y += 16
    return _save(doc, path)
