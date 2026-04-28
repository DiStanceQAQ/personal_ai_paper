"""Tests for the PyMuPDF4LLM PDF parser backend."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from paper_engine.pdf.backends.base import ParserBackendError
from paper_engine.pdf.models import PdfQualityReport
from paper_engine.pdf.profile import inspect_pdf
from tests.fixtures.pdf_factory import simple_academic_pdf, table_pdf


def _backend_module() -> Any:
    return importlib.import_module("paper_engine.pdf.backends.pymupdf4llm")


def _quality(**kwargs: Any) -> PdfQualityReport:
    return PdfQualityReport(page_count=2, native_text_pages=2, **kwargs)


def test_pyproject_packages_pymupdf4llm_backend_module() -> None:
    """Setuptools module list should include the runtime backend module."""
    with Path("pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    assert pyproject["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "paper_engine*"
    ]


def test_backend_reports_availability() -> None:
    """The backend should report the installed PyMuPDF4LLM dependency."""
    backend_module = _backend_module()

    assert backend_module.PyMuPDF4LLMBackend().is_available() is True


def test_fake_chunk_conversion_covers_text_toc_layout_and_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Chunk dictionaries should normalize into document elements, tables, and assets."""
    backend_module = _backend_module()
    fake_pdf = tmp_path / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\n")

    page_one_text = (
        "Repeated Header\n\n"
        "Fake Paper Title\n\n"
        "## Background\n\n"
        "Body paragraph on page one.\n\n"
        "|Metric|Value|\n"
        "|---|---|\n"
        "|Score|0.9|\n\n"
        "Repeated Footer"
    )
    page_two_text = (
        "Repeated Header\n\n"
        "## Methods\n\n"
        "Body paragraph on page two.\n\n"
        "Repeated Footer"
    )

    def span(text: str, fragment: str) -> tuple[int, int]:
        start = text.index(fragment)
        return (start, start + len(fragment))

    chunks = [
        {
            "metadata": {"page": 1},
            "toc_items": [{"level": 1, "title": "Background", "page": 1}],
            "tables": [
                {
                    "bbox": (54, 160, 280, 220),
                    "rows": [["Metric", "Value"], ["Score", "0.9"]],
                }
            ],
            "images": [{"bbox": (300, 160, 340, 200), "xref": 12}],
            "graphics": [{"bbox": (350, 160, 420, 220), "kind": "line-art"}],
            "page_boxes": [
                {
                    "class": "page-header",
                    "bbox": (54, 20, 240, 34),
                    "pos": span(page_one_text, "Repeated Header"),
                },
                {
                    "class": "text",
                    "bbox": (54, 54, 240, 72),
                    "pos": span(page_one_text, "Fake Paper Title"),
                },
                {
                    "class": "section-header",
                    "bbox": (54, 88, 180, 104),
                    "pos": span(page_one_text, "## Background"),
                },
                {
                    "class": "text",
                    "bbox": (54, 116, 280, 132),
                    "pos": span(page_one_text, "Body paragraph on page one."),
                },
                {
                    "class": "table",
                    "bbox": (54, 160, 280, 220),
                    "pos": span(page_one_text, "|Metric|Value|"),
                },
                {
                    "class": "page-footer",
                    "bbox": (54, 760, 240, 778),
                    "pos": span(page_one_text, "Repeated Footer"),
                },
            ],
            "page_boxes_extra": "ignored",
            "text": page_one_text,
        },
        {
            "metadata": {"page": 2},
            "toc_items": [{"level": 1, "title": "Methods", "page": 2}],
            "page_boxes": [
                {
                    "class": "text",
                    "bbox": (54, 20, 240, 34),
                    "pos": span(page_two_text, "Repeated Header"),
                },
                {
                    "class": "section-header",
                    "bbox": (54, 88, 180, 104),
                    "pos": span(page_two_text, "## Methods"),
                },
                {
                    "class": "text",
                    "bbox": (54, 116, 280, 132),
                    "pos": span(page_two_text, "Body paragraph on page two."),
                },
                {
                    "class": "text",
                    "bbox": (54, 760, 240, 778),
                    "pos": span(page_two_text, "Repeated Footer"),
                },
            ],
            "text": page_two_text,
        },
    ]
    fake_module = SimpleNamespace(to_markdown=lambda *args, **kwargs: chunks)
    monkeypatch.setattr(backend_module, "_load_pymupdf4llm", lambda: fake_module)

    document = backend_module.PyMuPDF4LLMBackend().parse(
        fake_pdf,
        paper_id="paper-1",
        space_id="space-1",
        quality_report=_quality(needs_layout_model=True),
    )

    element_types = [element.element_type for element in document.elements]
    assert document.backend == "pymupdf4llm"
    assert document.extraction_method == "layout_model"
    assert "title" in element_types
    assert "heading" in element_types
    assert "paragraph" in element_types
    assert "table" in element_types
    assert "figure" in element_types

    filtered_text = {
        element.text: element
        for element in document.elements
        if element.metadata.get("filtered") is True
    }
    assert filtered_text["Repeated Header"].element_type == "page_header"
    assert filtered_text["Repeated Footer"].element_type == "page_footer"

    assert any(table.cells == [["Metric", "Value"], ["Score", "0.9"]] for table in document.tables)
    assert {asset.asset_type for asset in document.assets} >= {"image", "graphic"}
    assert any(
        item["title"] == "Background"
        for element in document.elements
        for item in element.metadata.get("toc_items", [])
    )


def test_legacy_table_metadata_merges_with_markdown_cells(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Legacy count-only table metadata should not create an empty duplicate."""
    backend_module = _backend_module()
    fake_pdf = tmp_path / "legacy-table.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\n")
    chunks = [
        {
            "metadata": {"page": 1},
            "tables": [{"bbox": (1, 2, 3, 4), "rows": 3, "columns": 2}],
            "text": (
                "Legacy Table\n\n"
                "|Metric|Value|\n"
                "|---|---|\n"
                "|Accuracy|0.91|\n"
                "|Recall|0.88|\n"
            ),
        }
    ]
    fake_module = SimpleNamespace(to_markdown=lambda *args, **kwargs: chunks)
    monkeypatch.setattr(backend_module, "_load_pymupdf4llm", lambda: fake_module)

    document = backend_module.PyMuPDF4LLMBackend().parse(
        fake_pdf,
        paper_id="paper-legacy-table",
        space_id="space",
        quality_report=_quality(needs_layout_model=True),
    )

    assert len(document.tables) == 1
    table = document.tables[0]
    assert table.cells == [
        ["Metric", "Value"],
        ["Accuracy", "0.91"],
        ["Recall", "0.88"],
    ]
    assert table.bbox == [1.0, 2.0, 3.0, 4.0]
    assert table.metadata["rows"] == 3
    assert table.metadata["columns"] == 2
    assert all(candidate.cells for candidate in document.tables)


def test_markdown_only_repeated_margins_are_filtered_not_title(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repeated first/last markdown blocks should become filtered page margins."""
    backend_module = _backend_module()
    fake_pdf = tmp_path / "markdown-margins.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\n")
    chunks = [
        {
            "metadata": {"page": 1},
            "text": (
                "Repeated Header\n\n"
                "Real Paper Title\n\n"
                "Body paragraph on page one.\n\n"
                "Repeated Footer"
            ),
        },
        {
            "metadata": {"page": 2},
            "text": (
                "Repeated Header\n\n"
                "## Methods\n\n"
                "Body paragraph on page two.\n\n"
                "Repeated Footer"
            ),
        },
    ]
    fake_module = SimpleNamespace(to_markdown=lambda *args, **kwargs: chunks)
    monkeypatch.setattr(backend_module, "_load_pymupdf4llm", lambda: fake_module)

    document = backend_module.PyMuPDF4LLMBackend().parse(
        fake_pdf,
        paper_id="paper-markdown-margins",
        space_id="space",
        quality_report=_quality(),
    )

    filtered = [
        element
        for element in document.elements
        if element.metadata.get("filtered") is True
    ]
    assert [element.element_type for element in filtered].count("page_header") == 2
    assert [element.element_type for element in filtered].count("page_footer") == 2
    assert {element.text for element in filtered} == {
        "Repeated Header",
        "Repeated Footer",
    }
    titles = [element.text for element in document.elements if element.element_type == "title"]
    assert titles == ["Real Paper Title"]
    assert all(element.text != "Repeated Header" for element in document.elements if element.element_type == "title")


def test_live_simple_academic_pdf_yields_title_heading_and_paragraph(
    tmp_path: Path,
) -> None:
    """A selectable academic fixture should produce core text element types."""
    backend_module = _backend_module()
    pdf_path = simple_academic_pdf(tmp_path / "simple.pdf")

    document = backend_module.PyMuPDF4LLMBackend().parse(
        pdf_path,
        paper_id="paper-simple",
        space_id="space",
        quality_report=inspect_pdf(pdf_path),
    )

    element_types = [element.element_type for element in document.elements]
    assert "title" in element_types
    assert "heading" in element_types
    assert "paragraph" in element_types
    assert any("Small Study" in element.text for element in document.elements)
    assert any("Abstract" in element.text for element in document.elements)


def test_live_table_pdf_yields_table_or_table_like_element(tmp_path: Path) -> None:
    """A table fixture should expose a normalized table artifact or table element."""
    backend_module = _backend_module()
    pdf_path = table_pdf(tmp_path / "table.pdf")

    document = backend_module.PyMuPDF4LLMBackend().parse(
        pdf_path,
        paper_id="paper-table",
        space_id="space",
        quality_report=inspect_pdf(pdf_path),
    )

    assert document.tables or any(
        element.element_type == "table" for element in document.elements
    )


def test_parse_errors_are_wrapped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PyMuPDF4LLM exceptions should surface as backend errors with cause chaining."""
    backend_module = _backend_module()

    class BrokenModule:
        @staticmethod
        def to_markdown(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("boom")

    monkeypatch.setattr(backend_module, "_load_pymupdf4llm", lambda: BrokenModule)

    with pytest.raises(ParserBackendError) as exc_info:
        backend_module.PyMuPDF4LLMBackend().parse(
            tmp_path / "broken.pdf",
            paper_id="paper",
            space_id="space",
            quality_report=_quality(),
        )

    assert exc_info.value.backend_name == "pymupdf4llm"
    assert isinstance(exc_info.value.__cause__, RuntimeError)
