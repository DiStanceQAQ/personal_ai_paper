"""Performance budget tests for the local PDF ingestion pipeline."""

from __future__ import annotations

import importlib.util
import os
import time
import warnings
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.performance,
    pytest.mark.filterwarnings(
        "ignore:builtin type SwigPy.* has no __module__ attribute:DeprecationWarning"
    ),
    pytest.mark.filterwarnings(
        "ignore:builtin type swigvarlink has no __module__ attribute:DeprecationWarning"
    ),
]

LOCAL_BUDGET_SECONDS = float(
    os.environ.get("PDF_PIPELINE_PERFORMANCE_BUDGET_SECONDS", "15.0")
)
PAGE_COUNT = 20


def _suppress_pymupdf_import_warnings() -> None:
    warnings.filterwarnings(
        "ignore",
        message=r"builtin type SwigPy.* has no __module__ attribute",
        category=DeprecationWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=r"builtin type swigvarlink has no __module__ attribute",
        category=DeprecationWarning,
    )


def _skip_if_performance_budget_is_unavailable() -> None:
    if os.environ.get("CI") and os.environ.get("RUN_PDF_PERFORMANCE_TESTS") != "1":
        pytest.skip(
            "PDF pipeline performance budget is disabled in constrained CI; "
            "set RUN_PDF_PERFORMANCE_TESTS=1 to enable it"
        )

    if importlib.util.find_spec("pymupdf4llm") is None:
        pytest.skip(
            "pymupdf4llm is not installed; refresh dependencies to run the "
            "local PDF pipeline performance budget"
        )


def _twenty_page_pdf(path: Path) -> Path:
    _suppress_pymupdf_import_warnings()
    import pymupdf

    doc = pymupdf.open()
    try:
        for page_index in range(PAGE_COUNT):
            page = doc.new_page()
            page.insert_text(
                (54, 54),
                f"Performance Budget Fixture Page {page_index + 1}",
                fontsize=12,
            )
            page.insert_text(
                (54, 76),
                f"Section {page_index + 1}: Local PDF ingestion timing",
                fontsize=10,
            )

            y = 104
            for paragraph_index in range(18):
                text = (
                    "This deterministic native text paragraph exercises "
                    "PyMuPDF4LLM parsing, normalization, and retrieval chunking "
                    f"for page {page_index + 1}, paragraph {paragraph_index + 1}."
                )
                page.insert_textbox(
                    pymupdf.Rect(54, y, 540, y + 26),
                    text,
                    fontsize=8,
                )
                y += 34

        path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(path))
    finally:
        doc.close()
    return path


def test_local_pymupdf4llm_route_stays_within_20_page_budget(
    tmp_path: Path,
) -> None:
    """Budget: a 20-page native-text PDF should inspect, parse, and chunk under 15s."""
    _skip_if_performance_budget_is_unavailable()
    _suppress_pymupdf_import_warnings()
    from pdf_chunker import chunk_parse_document
    from pdf_profile import inspect_pdf
    from pdf_router import PdfBackendRouter

    pdf_path = _twenty_page_pdf(tmp_path / "twenty-page-performance.pdf")

    started = time.perf_counter()
    quality = inspect_pdf(pdf_path)
    document = PdfBackendRouter(
        docling=None,
        llamaparse=None,
        legacy=None,
        grobid_client=None,
    ).parse_pdf(
        pdf_path,
        paper_id="paper-performance",
        space_id="space-performance",
        quality_report=quality,
    )
    passages = chunk_parse_document(document)
    elapsed = time.perf_counter() - started

    assert quality.page_count == PAGE_COUNT
    assert document.backend == "pymupdf4llm"
    assert len(document.elements) >= PAGE_COUNT
    assert passages
    assert elapsed < LOCAL_BUDGET_SECONDS, (
        f"20-page local PyMuPDF4LLM route took {elapsed:.2f}s, exceeding "
        f"the {LOCAL_BUDGET_SECONDS:.2f}s local budget"
    )
