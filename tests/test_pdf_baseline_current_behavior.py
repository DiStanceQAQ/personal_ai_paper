"""Baseline tests for current PDF parser behavior before refactoring."""

from pathlib import Path

import pymupdf

from paper_engine.pdf.compat import extract_passages_from_pdf


def _create_simple_one_page_pdf(path: Path) -> None:
    """Create a one-page PDF with a section header and extractable body text."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Abstract", fontsize=12)
    page.insert_text(
        (72, 110),
        (
            "This baseline document contains enough academic prose for the "
            "current parser to create at least one passage from a text block. "
            "It documents the existing extraction behavior before refactoring."
        ),
        fontsize=11,
    )
    doc.save(str(path))
    doc.close()


def test_extract_passages_from_simple_one_page_pdf_current_behavior(
    tmp_path: Path,
) -> None:
    """A simple text PDF currently yields one or more passage dictionaries."""
    pdf_path = tmp_path / "simple.pdf"
    _create_simple_one_page_pdf(pdf_path)

    passages = extract_passages_from_pdf(pdf_path, "paper-baseline", "space-baseline")

    assert len(passages) >= 1
    first_passage = passages[0]
    assert first_passage["paper_id"] == "paper-baseline"
    assert first_passage["space_id"] == "space-baseline"
    assert first_passage["id"]
    assert first_passage["page_number"] == 1
    assert first_passage["paragraph_index"] >= 0
    assert first_passage["section"] == "abstract"
    assert first_passage["passage_type"] == "abstract"
    assert first_passage["parse_confidence"] == 0.9
    assert "baseline document contains enough academic prose" in first_passage[
        "original_text"
    ]


def test_extract_passages_from_blank_page_pdf_current_behavior(tmp_path: Path) -> None:
    """A PDF with a blank page currently yields no passages."""
    pdf_path = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(pdf_path))
    doc.close()

    passages = extract_passages_from_pdf(pdf_path, "paper-baseline", "space-baseline")

    assert passages == []


def test_extract_passages_from_invalid_pdf_current_behavior(tmp_path: Path) -> None:
    """An invalid PDF file currently yields no passages."""
    pdf_path = tmp_path / "invalid.pdf"
    pdf_path.write_text("not a pdf", encoding="utf-8")

    passages = extract_passages_from_pdf(pdf_path, "paper-baseline", "space-baseline")

    assert passages == []
