"""Legacy PyMuPDF block parser backend."""

from __future__ import annotations

import importlib
import importlib.util
import re
import uuid
from pathlib import Path
from typing import Any

from pdf_backend_base import ParserBackendError, ParserBackendUnavailable
from pdf_models import ParseDocument, ParseElement, PdfQualityReport


_BACKEND_NAME = "legacy-pymupdf"


class LegacyPyMuPDFOpenError(ParserBackendError):
    """Raised when the legacy backend cannot import or open a PDF."""


def _load_pymupdf() -> Any:
    """Import PyMuPDF lazily so availability failures are explicit."""
    if importlib.util.find_spec("pymupdf") is None:
        raise ParserBackendUnavailable(_BACKEND_NAME, "pymupdf is not installed")
    return importlib.import_module("pymupdf")


class LegacyPyMuPDFBackend:
    """Parse PDFs with the original PyMuPDF block extraction heuristics."""

    name = _BACKEND_NAME

    def is_available(self) -> bool:
        """Return whether PyMuPDF can be imported."""
        return importlib.util.find_spec("pymupdf") is not None

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        """Parse a PDF into the normalized parser contract."""
        passages = self.extract_passages(file_path, paper_id, space_id)
        elements = [
            _passage_to_element(passage, element_index)
            for element_index, passage in enumerate(passages)
        ]

        return ParseDocument(
            paper_id=paper_id,
            space_id=space_id,
            backend=self.name,
            extraction_method="legacy",
            quality=quality_report,
            elements=elements,
            metadata={
                "passage_count": len(passages),
                "parser": "pymupdf.get_text_blocks",
            },
        )

    def extract_passages(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
    ) -> list[dict[str, Any]]:
        """Extract legacy storage-ready passage dictionaries from a PDF."""
        try:
            pymupdf = _load_pymupdf()
            doc = pymupdf.open(str(file_path))
        except ParserBackendUnavailable:
            raise
        except Exception as exc:
            raise LegacyPyMuPDFOpenError(
                self.name,
                "failed to open PDF",
                cause=exc,
            ) from exc

        passages: list[dict[str, Any]] = []
        try:
            current_section = "introduction"

            for page_num in range(len(doc)):
                page = doc[page_num]
                blocks = page.get_text("blocks")
                blocks.sort(key=lambda block: (block[1], block[0]))
                text_block_count = sum(
                    1 for block in blocks if block[6] == 0 and block[4].strip()
                )

                page_height = page.rect.height

                for block in blocks:
                    if block[6] != 0:
                        continue

                    block_text = block[4].strip()
                    if not block_text:
                        continue

                    y0 = block[1]
                    y1 = block[3]
                    if text_block_count > 3 and (
                        y0 < page_height * 0.07 or y1 > page_height * 0.93
                    ):
                        if len(block_text) < 150:
                            continue

                    if len(block_text) < 60:
                        new_section = _detect_section_header(block_text)
                        if new_section:
                            current_section = new_section
                            continue

                    clean_text = _normalize_text(block_text)
                    if len(clean_text) < 30:
                        continue

                    passages.append(
                        {
                            "id": str(uuid.uuid4()),
                            "paper_id": paper_id,
                            "space_id": space_id,
                            "section": current_section,
                            "page_number": page_num + 1,
                            "paragraph_index": block[5],
                            "original_text": clean_text,
                            "parse_confidence": 0.9,
                            "passage_type": _get_passage_type(current_section),
                        }
                    )
        except Exception as exc:
            raise ParserBackendError(self.name, "failed to parse PDF", cause=exc) from exc
        finally:
            doc.close()

        return passages


def _passage_to_element(passage: dict[str, Any], element_index: int) -> ParseElement:
    section = str(passage["section"])
    return ParseElement(
        id=str(passage["id"]),
        element_index=element_index,
        element_type="paragraph",
        text=str(passage["original_text"]),
        page_number=int(passage["page_number"]),
        heading_path=[section],
        extraction_method="legacy",
        metadata={
            "section": section,
            "paragraph_index": int(passage["paragraph_index"]),
            "parse_confidence": float(passage["parse_confidence"]),
            "passage_type": str(passage["passage_type"]),
        },
    )


def _split_paragraphs(text: str) -> list[str]:
    """Split raw extracted text into normalized paragraphs."""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    return [_normalize_text(paragraph) for paragraph in paragraphs if paragraph.strip()]


def _guess_section(text: str) -> str:
    """Infer a broad academic section from a paragraph or header."""
    header = _detect_section_header(text)
    if header:
        return header

    text_lower = text.strip().lower()
    if text_lower.startswith("abstract"):
        return "abstract"
    if "introduction" in text_lower or "related work" in text_lower:
        return "introduction"
    if "method" in text_lower or "algorithm" in text_lower or "architecture" in text_lower:
        return "method"
    if "result" in text_lower or "evaluation" in text_lower or "experiment" in text_lower:
        return "result"
    if "limitation" in text_lower or "future work" in text_lower:
        return "limitation"
    if text_lower.startswith("appendix") or text_lower.startswith("supplementary"):
        return "appendix"
    return "body"


def _guess_passage_type(section: str) -> str:
    """Compatibility wrapper for older parser tests and callers."""
    return _get_passage_type(section)


def _detect_section_header(text: str) -> str | None:
    """Detect if a string is a section header and return the normalized section name."""
    text_clean = text.strip().upper()

    patterns = {
        "abstract": r"^(ABSTRACT)$",
        "introduction": r"^(\d+\.?\s*)?(INTRODUCTION|BACKGROUND|MOTIVATION)$",
        "method": (
            r"^(\d+\.?\s*)?"
            r"(METHODS|METHODOLOGY|APPROACH|THE\s+MODEL|ARCHITECTURE|PROPOSED)$"
        ),
        "result": r"^(\d+\.?\s*)?(RESULTS|EVALUATION|EXPERIMENTS|PERFORMANCE)$",
        "discussion": r"^(\d+\.?\s*)?(DISCUSSION|ANALYSIS|FINDINGS)$",
        "limitation": r"^(\d+\.?\s*)?(LIMITATIONS|FUTURE\s+WORK)$",
        "appendix": r"^(APPENDIX|SUPPLEMENTARY)$",
        "conclusion": r"^(\d+\.?\s*)?(CONCLUSION|CONCLUDING)$",
        "reference": r"^(REFERENCES|BIBLIOGRAPHY)$",
    }

    for section, pattern in patterns.items():
        if re.search(pattern, text_clean):
            return section
    return None


def _normalize_text(text: str) -> str:
    """Fix common PDF extraction artifacts."""
    text = re.sub(r"(\w)-\s*\n(\w)", r"\1\2", text)

    lines = text.split("\n")
    processed_lines = []
    for line in lines:
        line = line.strip()
        if line:
            processed_lines.append(line)

    return " ".join(processed_lines)


def _get_passage_type(section: str) -> str:
    """Map section name to DB-compatible passage types."""
    mapping = {
        "abstract": "abstract",
        "introduction": "introduction",
        "method": "method",
        "result": "result",
        "discussion": "discussion",
        "limitation": "limitation",
        "appendix": "appendix",
        "conclusion": "discussion",
        "reference": "appendix",
    }
    return mapping.get(section, "body")
