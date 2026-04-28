"""Legacy PyMuPDF block parser backend."""

from __future__ import annotations

import importlib
import importlib.util
import re
import uuid
from pathlib import Path
from typing import Any

from pdf_backend_base import ParserBackendError, ParserBackendUnavailable
from pdf_models import ParseDocument, ParseElement, ParseTable, PdfQualityReport


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

        try:
            table_elements, tables, table_bboxes = _extract_tables_from_doc(
                doc,
                should_extract=_should_extract_tables(quality_report),
            )
            passages = _extract_passages_from_doc(
                doc,
                paper_id,
                space_id,
                excluded_bboxes_by_page=table_bboxes,
            )
            elements = [
                _passage_to_element(passage, element_index)
                for element_index, passage in enumerate(passages)
            ]
            elements.extend(
                element.model_copy(update={"element_index": len(elements) + offset})
                for offset, element in enumerate(table_elements)
            )
        except Exception as exc:
            raise ParserBackendError(self.name, "failed to parse PDF", cause=exc) from exc
        finally:
            doc.close()

        return ParseDocument(
            paper_id=paper_id,
            space_id=space_id,
            backend=self.name,
            extraction_method="legacy",
            quality=quality_report,
            elements=elements,
            tables=tables,
            metadata={
                "passage_count": len(passages),
                "table_count": len(tables),
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

        try:
            passages = _extract_passages_from_doc(doc, paper_id, space_id)
        except Exception as exc:
            raise ParserBackendError(self.name, "failed to parse PDF", cause=exc) from exc
        finally:
            doc.close()

        return passages


def _extract_passages_from_doc(
    doc: Any,
    paper_id: str,
    space_id: str,
    *,
    excluded_bboxes_by_page: dict[int, list[list[float]]] | None = None,
) -> list[dict[str, Any]]:
    passages: list[dict[str, Any]] = []
    current_section = "introduction"
    excluded_bboxes_by_page = excluded_bboxes_by_page or {}

    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("blocks")
        blocks.sort(key=lambda block: (block[1], block[0]))
        text_block_count = sum(
            1 for block in blocks if block[6] == 0 and block[4].strip()
        )

        page_height = page.rect.height
        excluded_bboxes = excluded_bboxes_by_page.get(page_num + 1, [])

        for block in blocks:
            if block[6] != 0:
                continue
            if _block_overlaps_excluded_bbox(block, excluded_bboxes):
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
    return passages


def _extract_tables_from_doc(
    doc: Any,
    *,
    should_extract: bool,
) -> tuple[list[ParseElement], list[ParseTable], dict[int, list[list[float]]]]:
    if not should_extract:
        return [], [], {}

    elements: list[ParseElement] = []
    tables: list[ParseTable] = []
    bboxes_by_page: dict[int, list[list[float]]] = {}

    for page_num in range(len(doc)):
        page = doc[page_num]
        find_tables = getattr(page, "find_tables", None)
        if find_tables is None:
            continue

        table_finder = find_tables()
        for raw_table in list(getattr(table_finder, "tables", []) or []):
            cells = _extract_table_cells(raw_table)
            if not _has_table_shape(cells):
                continue

            bbox = _bbox(getattr(raw_table, "bbox", None))
            page_number = page_num + 1
            table_index = len(tables)
            element_id = f"legacy-table-e{table_index:04d}"
            text = _cells_to_text(cells)
            element = ParseElement(
                id=element_id,
                element_index=table_index,
                element_type="table",
                text=text,
                page_number=page_number,
                bbox=bbox,
                heading_path=["body"],
                extraction_method="legacy",
                metadata={
                    "source": "pymupdf.find_tables",
                    "table_index": table_index,
                },
            )
            elements.append(element)
            tables.append(
                ParseTable(
                    id=f"table-{table_index:04d}",
                    element_id=element_id,
                    table_index=table_index,
                    page_number=page_number,
                    cells=cells,
                    bbox=bbox,
                    metadata={
                        "source": "pymupdf.find_tables",
                        "header_rows": 1,
                    },
                )
            )
            if bbox is not None:
                bboxes_by_page.setdefault(page_number, []).append(bbox)

    return elements, tables, bboxes_by_page


def _should_extract_tables(quality_report: PdfQualityReport) -> bool:
    return (
        quality_report.estimated_table_pages > 0
        or quality_report.needs_layout_model
    )


def _extract_table_cells(raw_table: Any) -> list[list[str]]:
    extract = getattr(raw_table, "extract", None)
    raw_rows = extract() if callable(extract) else []
    if not isinstance(raw_rows, list):
        return []

    rows: list[list[str]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, (list, tuple)):
            continue
        row = [_normalize_table_cell(str(cell or "")) for cell in raw_row]
        if any(row):
            rows.append(row)
    return rows


def _normalize_table_cell(text: str) -> str:
    normalized = _normalize_text(text)
    split_decimal = re.fullmatch(r"(\d{2,})(\d)\s+\.", normalized)
    if split_decimal:
        return f"{split_decimal.group(1)}.{split_decimal.group(2)}"
    joined_unit = re.fullmatch(r"(\d+(?:\.\d+)?)([A-Za-z]+)", normalized)
    if joined_unit:
        return f"{joined_unit.group(1)} {joined_unit.group(2)}"
    return normalized


def _has_table_shape(cells: list[list[str]]) -> bool:
    if len(cells) < 2:
        return False
    return max((len(row) for row in cells), default=0) >= 2


def _cells_to_text(cells: list[list[str]]) -> str:
    return "\n".join(" | ".join(cell for cell in row if cell) for row in cells)


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = [float(coordinate) for coordinate in value]
    except (TypeError, ValueError):
        return None
    if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
        return None
    return bbox


def _block_overlaps_excluded_bbox(
    block: Any,
    excluded_bboxes: list[list[float]],
) -> bool:
    if not excluded_bboxes:
        return False
    block_bbox = _bbox(block[:4] if isinstance(block, (list, tuple)) else None)
    if block_bbox is None:
        return False
    return any(_bbox_overlap_ratio(block_bbox, bbox) >= 0.50 for bbox in excluded_bboxes)


def _bbox_overlap_ratio(inner: list[float], outer: list[float]) -> float:
    ix0 = max(inner[0], outer[0])
    iy0 = max(inner[1], outer[1])
    ix1 = min(inner[2], outer[2])
    iy1 = min(inner[3], outer[3])
    if ix0 >= ix1 or iy0 >= iy1:
        return 0.0

    intersection = (ix1 - ix0) * (iy1 - iy0)
    inner_area = max((inner[2] - inner[0]) * (inner[3] - inner[1]), 0.0)
    if inner_area == 0:
        return 0.0
    return intersection / inner_area


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
