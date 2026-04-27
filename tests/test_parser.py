"""Tests for PDF parsing module."""

import tempfile
from pathlib import Path

import pymupdf
import parser

from parser import (
    _guess_passage_type,
    _guess_section,
    _split_paragraphs,
    extract_passages_from_pdf,
)


def _create_test_pdf(path: Path) -> None:
    """Create a PDF with known content for testing."""
    doc = pymupdf.open()
    page = doc.new_page()
    text = (
        "Abstract\n\n"
        "This is the abstract of the paper.\n\n"
        "Introduction\n\n"
        "This section introduces the problem. "
        "We discuss related work.\n\n"
        "Method\n\n"
        "Our method uses a novel architecture. "
        "We describe the algorithm in detail.\n\n"
        "Results\n\n"
        "Our method achieves 95% accuracy. "
        "The evaluation shows strong performance.\n\n"
        "Limitations\n\n"
        "Our approach has several limitations. "
        "Future work should address these.\n\n"
        "Discussion\n\n"
        "We analyze the implications of our findings."
    )
    page.insert_text((50, 50), text, fontsize=11)
    doc.save(str(path))
    doc.close()


def test_split_paragraphs() -> None:
    """Test paragraph splitting."""
    text = "Para one.\n\nPara two.\nLine two.\nLine three.\n\nPara three."
    result = _split_paragraphs(text)
    assert len(result) >= 3
    assert "Para one." in result[0]


def test_guess_section() -> None:
    """Test section guessing from text."""
    assert _guess_section("Abstract This paper presents") == "abstract"
    assert _guess_section("Introduction and related work") == "introduction"
    assert _guess_section("Our method uses transformers") == "method"
    assert _guess_section("Results show improvement") == "result"
    assert _guess_section("Limitations of this study") == "limitation"
    assert _guess_section("Appendix A") == "appendix"
    assert _guess_section("Some random text") == "body"


def test_guess_passage_type() -> None:
    """Test passage type mapping."""
    assert _guess_passage_type("abstract") == "abstract"
    assert _guess_passage_type("method") == "method"
    assert _guess_passage_type("body") == "body"


def test_extract_passages() -> None:
    """Test full passage extraction from a PDF."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "test.pdf"
        _create_test_pdf(pdf_path)

        passages = extract_passages_from_pdf(pdf_path, "paper-1", "space-1")
        assert len(passages) > 0

        for p in passages:
            assert p["paper_id"] == "paper-1"
            assert p["space_id"] == "space-1"
            assert p["id"]
            assert p["page_number"] >= 1
            assert p["original_text"]
            assert p["passage_type"] in {
                "abstract", "introduction", "method", "result",
                "discussion", "limitation", "appendix", "body",
            }

        # Should have at least abstract passage
        sections = {p["section"] for p in passages}
        assert "abstract" in sections or any(
            "abstract" in p["original_text"].lower() for p in passages
        )


def test_extract_passages_invalid_pdf() -> None:
    """Test that invalid PDF returns empty list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_path = Path(tmpdir) / "bad.pdf"
        bad_path.write_text("not a pdf")
        passages = extract_passages_from_pdf(bad_path, "paper-1", "space-1")
        assert passages == []


def test_extract_passages_nonexistent_file() -> None:
    """Test that nonexistent file returns empty list."""
    passages = extract_passages_from_pdf(
        Path("/nonexistent/file.pdf"), "paper-1", "space-1"
    )
    assert passages == []


def test_extract_passages_delegates_to_legacy_backend(monkeypatch) -> None:
    """Parser compatibility wrapper should delegate extraction to the legacy backend."""
    calls = []
    expected = [
        {
            "id": "passage-1",
            "paper_id": "paper-1",
            "space_id": "space-1",
            "section": "body",
            "page_number": 1,
            "paragraph_index": 0,
            "original_text": "Delegated passage text.",
            "parse_confidence": 0.9,
            "passage_type": "body",
        }
    ]

    class FakeLegacyBackend:
        def extract_passages(self, file_path: Path, paper_id: str, space_id: str):
            calls.append((file_path, paper_id, space_id))
            return expected

    monkeypatch.setattr(parser, "LegacyPyMuPDFBackend", FakeLegacyBackend)

    result = parser.extract_passages_from_pdf(Path("paper.pdf"), "paper-1", "space-1")

    assert result == expected
    assert calls == [(Path("paper.pdf"), "paper-1", "space-1")]
