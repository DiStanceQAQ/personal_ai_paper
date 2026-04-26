"""PDF parsing: extract text and split into passages."""

import uuid
from pathlib import Path
from typing import Any


def extract_passages_from_pdf(
    file_path: Path,
    paper_id: str,
    space_id: str,
) -> list[dict[str, Any]]:
    """Extract passages from a PDF file.

    Returns a list of passage dicts ready for database insertion.
    Each passage has: id, paper_id, space_id, section, page_number,
    paragraph_index, original_text, parse_confidence, passage_type.
    """
    passages: list[dict[str, Any]] = []

    try:
        import pymupdf  # PyMuPDF

        doc = pymupdf.open(str(file_path))
    except Exception:
        # File is not a valid PDF or cannot be opened
        return passages

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            if not text.strip():
                continue

            paragraphs = _split_paragraphs(text)
            for idx, para in enumerate(paragraphs):
                if not para.strip():
                    continue
                section = _guess_section(para)
                passage_type = _guess_passage_type(section)

                passages.append({
                    "id": str(uuid.uuid4()),
                    "paper_id": paper_id,
                    "space_id": space_id,
                    "section": section,
                    "page_number": page_num + 1,
                    "paragraph_index": idx,
                    "original_text": para.strip(),
                    "parse_confidence": 0.8,
                    "passage_type": passage_type,
                })
    finally:
        doc.close()

    return passages


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs by double newlines or single newlines."""
    raw = text.strip()
    if not raw:
        return []
    # Split by double newlines first
    parts = raw.split("\n\n")
    result: list[str] = []
    for part in parts:
        stripped = part.strip()
        if stripped:
            # Further split single newlines if the block is long
            lines = stripped.split("\n")
            if len(lines) > 5:
                # Keep as single block
                result.append(stripped)
            else:
                for line in lines:
                    if line.strip():
                        result.append(line.strip())
    return result


def _guess_section(text: str) -> str:
    """Guess the section name from text content."""
    lower = text.lower()
    if any(kw in lower for kw in ["abstract"]):
        return "abstract"
    if any(kw in lower for kw in ["introduction", "related work", "background"]):
        return "introduction"
    if any(kw in lower for kw in ["method", "approach", "architecture", "algorithm", "protocol"]):
        return "method"
    if any(kw in lower for kw in ["result", "evaluation", "performance"]):
        return "result"
    if any(kw in lower for kw in ["discussion", "analysis"]):
        return "discussion"
    if any(kw in lower for kw in ["limitation", "limitations", "future work"]):
        return "limitation"
    if any(kw in lower for kw in ["appendix", "supplementary"]):
        return "appendix"
    return "body"


def _guess_passage_type(section: str) -> str:
    """Map section to passage type."""
    mapping: dict[str, str] = {
        "abstract": "abstract",
        "introduction": "introduction",
        "method": "method",
        "result": "result",
        "discussion": "discussion",
        "limitation": "limitation",
        "appendix": "appendix",
    }
    return mapping.get(section, "body")
