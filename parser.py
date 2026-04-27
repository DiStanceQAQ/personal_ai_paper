"""PDF parsing compatibility wrapper."""

from pathlib import Path
from typing import Any

from pdf_backend_legacy import (
    LegacyPyMuPDFBackend,
    _guess_passage_type,
    _guess_section,
    _split_paragraphs,
)


def extract_passages_from_pdf(
    file_path: Path,
    paper_id: str,
    space_id: str,
) -> list[dict[str, Any]]:
    """Extract passages from a PDF file using the legacy PyMuPDF backend.

    Returns a list of passage dicts ready for database insertion.
    """
    try:
        return LegacyPyMuPDFBackend().extract_passages(file_path, paper_id, space_id)
    except Exception as exc:
        print(f"Failed to open PDF {file_path}: {exc}")
        return []


__all__ = [
    "LegacyPyMuPDFBackend",
    "_guess_passage_type",
    "_guess_section",
    "_split_paragraphs",
    "extract_passages_from_pdf",
]
