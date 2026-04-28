"""Lightweight PDF quality inspection for parser routing."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any

import pymupdf

from paper_engine.pdf.models import PdfQualityReport

_MIN_NATIVE_TEXT_CHARS = 20
_MIN_NATIVE_WORDS = 3
_MIN_COLUMN_WORDS = 8
_MISSING_LAYOUT_ANALYZER = object()


def inspect_pdf(file_path: Path) -> PdfQualityReport:
    """Inspect a PDF and return quality signals for downstream parser routing."""
    warnings: list[str] = []
    metadata: dict[str, Any] = {"inspector": "pymupdf"}

    try:
        doc = pymupdf.open(str(file_path))
    except Exception as exc:
        return PdfQualityReport(
            needs_ocr=True,
            quality_score=0.0,
            warnings=["pdf_open_failed"],
            metadata={
                **metadata,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )

    page_count = 0
    native_text_pages = 0
    image_only_pages = 0
    estimated_table_pages = 0
    estimated_two_column_pages = 0

    try:
        page_count = int(doc.page_count)

        for page_index in range(page_count):
            page = doc[page_index]
            words = _page_words(page)
            has_native_text = _has_native_text(page, words)

            if has_native_text:
                native_text_pages += 1
            else:
                image_only_pages += 1

            if has_native_text and _has_table(page, words):
                estimated_table_pages += 1

            if has_native_text and _has_two_column_layout(page, words):
                estimated_two_column_pages += 1

    except Exception as exc:
        warnings.append("pdf_parse_failed")
        metadata["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        doc.close()

    if image_only_pages:
        _append_warning(warnings, "image_only_pages_detected")
    if estimated_table_pages:
        _append_warning(warnings, "tables_detected")
    if estimated_two_column_pages:
        _append_warning(warnings, "two_column_layout_detected")
    if page_count and native_text_pages == 0:
        _append_warning(warnings, "no_native_text_detected")

    native_coverage = native_text_pages / page_count if page_count else 0.0
    needs_ocr = image_only_pages > 0 or native_coverage < 0.5
    needs_layout_model = estimated_table_pages > 0 or estimated_two_column_pages > 0

    return PdfQualityReport(
        page_count=page_count,
        native_text_pages=native_text_pages,
        image_only_pages=image_only_pages,
        estimated_table_pages=estimated_table_pages,
        estimated_two_column_pages=estimated_two_column_pages,
        needs_ocr=needs_ocr,
        needs_layout_model=needs_layout_model,
        quality_score=_quality_score(page_count, native_coverage, needs_layout_model),
        warnings=warnings,
        metadata=metadata,
    )


def _page_words(page: pymupdf.Page) -> list[tuple[Any, ...]]:
    words = page.get_text("words")
    if isinstance(words, list):
        return words
    return list(words)


def _has_native_text(page: pymupdf.Page, words: list[tuple[Any, ...]]) -> bool:
    text = page.get_text("text").strip()
    return len(text) >= _MIN_NATIVE_TEXT_CHARS or len(words) >= _MIN_NATIVE_WORDS


def _has_table(page: pymupdf.Page, words: list[tuple[Any, ...]]) -> bool:
    if len(words) < 6:
        return False

    detected_tables = _find_tables_count(page)
    if detected_tables > 0:
        return True

    return _has_grid_drawings(page)


def _find_tables_count(page: pymupdf.Page) -> int:
    find_tables = getattr(page, "find_tables", None)
    if find_tables is None:
        return 0

    layout_analyzer = getattr(pymupdf, "_get_layout", _MISSING_LAYOUT_ANALYZER)
    try:
        if layout_analyzer is not _MISSING_LAYOUT_ANALYZER:
            # PyMuPDF4LLM activates pymupdf.layout globally. Keep profiling stable by
            # using PyMuPDF's native table finder behavior for this lightweight route.
            setattr(pymupdf, "_get_layout", None)
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                result = find_tables()
    except Exception:
        return 0
    finally:
        if layout_analyzer is not _MISSING_LAYOUT_ANALYZER:
            setattr(pymupdf, "_get_layout", layout_analyzer)

    tables = getattr(result, "tables", None)
    if tables is None:
        try:
            return len(result)
        except TypeError:
            return 0
    return len(tables)


def _has_grid_drawings(page: pymupdf.Page) -> bool:
    try:
        drawings = page.get_drawings()
    except Exception:
        return False

    horizontal_lines = 0
    vertical_lines = 0
    for drawing in drawings:
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l" and len(item) >= 3:
                first, second = item[1], item[2]
                horizontal_lines += int(_is_horizontal(first, second))
                vertical_lines += int(_is_vertical(first, second))
            elif kind == "re" and len(item) >= 2:
                vertical_lines += 2
                horizontal_lines += 2

    return horizontal_lines >= 3 and vertical_lines >= 3


def _has_two_column_layout(
    page: pymupdf.Page,
    words: list[tuple[Any, ...]],
) -> bool:
    if len(words) < (_MIN_COLUMN_WORDS * 2):
        return False

    page_width = float(page.rect.width)
    mid_x = page_width / 2
    min_gap = page_width * 0.025

    left_word_count = 0
    right_word_count = 0
    for word in words:
        x_center = (float(word[0]) + float(word[2])) / 2
        if x_center < mid_x - min_gap:
            left_word_count += 1
        elif x_center > mid_x + min_gap:
            right_word_count += 1

    if left_word_count < _MIN_COLUMN_WORDS or right_word_count < _MIN_COLUMN_WORDS:
        return False

    left_blocks: list[tuple[float, float, float, float]] = []
    right_blocks: list[tuple[float, float, float, float]] = []
    for block in page.get_text("blocks"):
        if len(block) < 7 or block[6] != 0 or not str(block[4]).strip():
            continue

        x0, y0, x1, y1 = map(float, block[:4])
        if x1 <= mid_x - min_gap:
            left_blocks.append((x0, y0, x1, y1))
        elif x0 >= mid_x + min_gap:
            right_blocks.append((x0, y0, x1, y1))

    return any(
        _vertical_overlap(left_block, right_block) >= 20
        for left_block in left_blocks
        for right_block in right_blocks
    )


def _is_horizontal(first: Any, second: Any) -> bool:
    return abs(float(first.y) - float(second.y)) <= 1.5 and abs(
        float(first.x) - float(second.x)
    ) >= 20


def _is_vertical(first: Any, second: Any) -> bool:
    return abs(float(first.x) - float(second.x)) <= 1.5 and abs(
        float(first.y) - float(second.y)
    ) >= 20


def _vertical_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    return max(0.0, min(first[3], second[3]) - max(first[1], second[1]))


def _quality_score(
    page_count: int,
    native_coverage: float,
    needs_layout_model: bool,
) -> float:
    if page_count == 0:
        return 0.0

    layout_penalty = 0.15 if needs_layout_model else 0.0
    return max(0.0, min(1.0, native_coverage - layout_penalty))


def _append_warning(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


__all__ = ["inspect_pdf"]
