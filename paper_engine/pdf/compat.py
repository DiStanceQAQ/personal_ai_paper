"""PDF parsing compatibility wrapper."""

from pathlib import Path
from typing import Any

from paper_engine.pdf.backends.base import ParserBackendUnavailable
from paper_engine.pdf.backends.legacy import (
    LegacyPyMuPDFOpenError,
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
    except (LegacyPyMuPDFOpenError, ParserBackendUnavailable) as exc:
        print(f"Failed to open PDF {file_path}: {exc}")
        return []


def inspect_pdf(file_path: Path) -> Any:
    """Inspect PDF quality while keeping heavy parser imports lazy."""
    from paper_engine.pdf.profile import inspect_pdf as _inspect_pdf

    return _inspect_pdf(file_path)


def route_parse(
    file_path: Path,
    paper_id: str,
    space_id: str,
    quality_report: Any,
) -> Any:
    """Parse through the structured router while keeping imports lazy."""
    from paper_engine.pdf.router import parse_pdf

    return parse_pdf(file_path, paper_id, space_id, quality_report)


def chunk_parse_document(document: Any) -> Any:
    """Chunk a structured parse document while keeping imports lazy."""
    from paper_engine.pdf.chunking import chunk_parse_document as _chunk_parse_document

    return _chunk_parse_document(document)


def persist_parse_result(
    conn: Any,
    paper_id: str,
    space_id: str,
    parse_document: Any,
    passages: Any,
) -> str:
    """Persist structured parse results while keeping imports lazy."""
    from paper_engine.pdf.persistence import persist_parse_result as _persist_parse_result

    return _persist_parse_result(conn, paper_id, space_id, parse_document, passages)


__all__ = [
    "LegacyPyMuPDFBackend",
    "chunk_parse_document",
    "_guess_passage_type",
    "_guess_section",
    "_split_paragraphs",
    "extract_passages_from_pdf",
    "inspect_pdf",
    "persist_parse_result",
    "route_parse",
]
