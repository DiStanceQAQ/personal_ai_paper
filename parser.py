"""PDF parsing: extract text and split into passages with layout awareness."""

import uuid
from pathlib import Path
from typing import Any
import re


def extract_passages_from_pdf(
    file_path: Path,
    paper_id: str,
    space_id: str,
) -> list[dict[str, Any]]:
    """Extract passages from a PDF file with layout-aware block processing.

    Returns a list of passage dicts ready for database insertion.
    """
    passages: list[dict[str, Any]] = []

    try:
        import pymupdf  # PyMuPDF
        doc = pymupdf.open(str(file_path))
    except Exception as e:
        print(f"Failed to open PDF {file_path}: {e}")
        return passages

    try:
        current_section = "introduction"
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            # get_text("blocks") returns: (x0, y0, x1, y1, "text", block_no, block_type)
            # This is layout-aware and handles multi-column text correctly.
            blocks = page.get_text("blocks")
            
            # Sort blocks primarily by y0 (vertical) then x0 (horizontal) 
            # PyMuPDF usually does this, but we ensure it.
            blocks.sort(key=lambda b: (b[1], b[0]))

            page_height = page.rect.height
            page_width = page.rect.width

            for b in blocks:
                # b[6] == 0 is text; b[6] == 1 is image
                if b[6] != 0:
                    continue
                
                block_text = b[4].strip()
                if not block_text:
                    continue

                # 1. Heuristic Header/Footer filtering
                # Ignore blocks in the top 7% or bottom 7% of the page
                # (Often contains page numbers, journal names, DOI)
                y0 = b[1]
                y1 = b[3]
                if y0 < page_height * 0.07 or y1 > page_height * 0.93:
                    # Double check: headers/footers are usually short
                    if len(block_text) < 150:
                        continue

                # 2. Section Header Detection
                # Look for typical section headers (e.g., "1. Introduction", "ABSTRACT")
                # Headers are usually short and follow specific patterns
                if len(block_text) < 60:
                    new_section = _detect_section_header(block_text)
                    if new_section:
                        current_section = new_section
                        continue # Don't store the header as a passage itself

                # 3. Text Normalization
                # Replace soft hyphens and fix broken lines
                clean_text = _normalize_text(block_text)

                if len(clean_text) < 30: # Skip very short noise
                    continue

                passages.append({
                    "id": str(uuid.uuid4()),
                    "paper_id": paper_id,
                    "space_id": space_id,
                    "section": current_section,
                    "page_number": page_num + 1,
                    "paragraph_index": b[5],
                    "original_text": clean_text,
                    "parse_confidence": 0.9,
                    "passage_type": _get_passage_type(current_section),
                })
    finally:
        doc.close()

    return passages


def _detect_section_header(text: str) -> str | None:
    """Detect if a string is a section header and return the normalized section name."""
    text_clean = text.strip().upper()
    
    # Common academic section markers
    patterns = {
        "abstract": r"^(ABSTRACT)$",
        "introduction": r"^(\d+\.?\s*)?(INTRODUCTION|BACKGROUND|MOTIVATION)$",
        "method": r"^(\d+\.?\s*)?(METHODS|METHODOLOGY|APPROACH|THE\s+MODEL|ARCHITECTURE|PROPOSED)$",
        "result": r"^(\d+\.?\s*)?(RESULTS|EVALUATION|EXPERIMENTS|PERFORMANCE)$",
        "discussion": r"^(\d+\.?\s*)?(DISCUSSION|ANALYSIS|FINDINGS)$",
        "limitation": r"^(\d+\.?\s*)?(LIMITATIONS|FUTURE\s+WORK)$",
        "appendix": r"^(APPENDIX|SUPPLEMENTARY)$",
        "conclusion": r"^(\d+\.?\s*)?(CONCLUSION|CONCLUDING)$",
        "reference": r"^(REFERENCES|BIBLIOGRAPHY)$"
    }
    
    for section, pattern in patterns.items():
        if re.search(pattern, text_clean):
            return section
    return None


def _normalize_text(text: str) -> str:
    """Fix common PDF extraction artifacts."""
    # Remove hyphen at end of line (soft hyphenation)
    # Example: "process-\ning" -> "processing"
    text = re.sub(r"(\w)-\s*\n(\w)", r"\1\2", text)
    
    # Replace single newlines with space, but keep multiple newlines
    # This reunites broken sentences while preserving paragraph breaks
    lines = text.split("\n")
    processed_lines = []
    for line in lines:
        line = line.strip()
        if line:
            processed_lines.append(line)
    
    return " ".join(processed_lines)


def _get_passage_type(section: str) -> str:
    """Map section name to a controlled vocabulary of passage types that match DB constraints."""
    mapping = {
        "abstract": "abstract",
        "introduction": "introduction",
        "method": "method",
        "result": "result",
        "discussion": "discussion",
        "limitation": "limitation",
        "appendix": "appendix",
        "conclusion": "discussion", # Map to discussion as per DB constraints
        "reference": "appendix"     # Map to appendix as per DB constraints
    }
    return mapping.get(section, "body")
