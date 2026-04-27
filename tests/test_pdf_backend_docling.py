from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf_backend_base import ParserBackendError, ParserBackendUnavailable
from pdf_models import ParseDocument, PdfQualityReport


def test_pyproject_declares_docling_extra_and_module() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["optional-dependencies"]["pdf-advanced"] == [
        "docling>=2.0.0"
    ]
    assert "pdf_backend_docling" in pyproject["tool"]["setuptools"]["py-modules"]


def test_availability_uses_find_spec_without_importing_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdf_backend_docling import DoclingBackend

    calls: list[str] = []

    def fake_find_spec(name: str) -> object | None:
        calls.append(name)
        return object() if name == "docling" else None

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    assert DoclingBackend().is_available() is True
    assert calls == ["docling"]


def test_availability_is_false_when_docling_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdf_backend_docling import DoclingBackend

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    assert DoclingBackend().is_available() is False


def test_parse_requires_docling_when_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdf_backend_docling import DoclingBackend

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    with pytest.raises(ParserBackendUnavailable):
        DoclingBackend().parse(
            Path("missing.pdf"),
            paper_id="paper-1",
            space_id="space-1",
            quality_report=PdfQualityReport(),
        )


def test_parse_wraps_docling_conversion_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import pdf_backend_docling
    from pdf_backend_docling import DoclingBackend

    class FailingConverter:
        def convert(self, file_path: str) -> object:
            raise RuntimeError(f"cannot convert {file_path}")

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(pdf_backend_docling, "_load_docling_converter", lambda: FailingConverter)

    with pytest.raises(ParserBackendError) as exc_info:
        DoclingBackend().parse(
            Path("bad.pdf"),
            paper_id="paper-1",
            space_id="space-1",
            quality_report=PdfQualityReport(),
        )

    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_parse_normalizes_mixed_docling_items_in_reading_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pdf_backend_docling
    from pdf_backend_docling import DoclingBackend

    quality = PdfQualityReport(page_count=2, needs_layout_model=True)
    fake_document = SimpleNamespace(
        metadata={"source": {"nested": object()}},
        items=[
            SimpleNamespace(
                label="section_header",
                text="Introduction",
                level=1,
                page_no=1,
                bbox=[10, 20, 100, 40],
            ),
            {
                "type": "text",
                "text": "This is the opening paragraph.",
                "page": 1,
                "bbox": [10, 50, 300, 90],
            },
            SimpleNamespace(
                label="table",
                caption="Table 1: Results",
                page_number=2,
                bbox=[15, 100, 400, 220],
                data=SimpleNamespace(
                    table_cells=[
                        ["Metric", "Value"],
                        ["Accuracy", 0.95],
                    ]
                ),
            ),
            {
                "type": "picture",
                "caption": "Figure 1: Pipeline",
                "page_number": 2,
                "bbox": [20, 240, 260, 420],
                "image": {"uri": "images/figure-1.png"},
            },
            SimpleNamespace(
                label="caption",
                text="Additional caption text.",
                page=2,
            ),
            {
                "type": "formula",
                "latex": "E = mc^2",
                "page": 2,
            },
        ],
    )

    class FakeResult:
        document = fake_document

    class FakeConverter:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def convert(self, file_path: str) -> FakeResult:
            self.paths.append(file_path)
            return FakeResult()

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(pdf_backend_docling, "_load_docling_converter", lambda: FakeConverter)

    document = DoclingBackend().parse(
        Path("paper.pdf"),
        paper_id="paper-1",
        space_id="space-1",
        quality_report=quality,
    )

    assert isinstance(document, ParseDocument)
    assert document.paper_id == "paper-1"
    assert document.space_id == "space-1"
    assert document.backend == "docling"
    assert document.extraction_method == "layout_model"
    assert document.quality is quality
    assert [element.element_type for element in document.elements] == [
        "heading",
        "paragraph",
        "table",
        "figure",
        "caption",
        "equation",
    ]
    assert [element.id for element in document.elements] == [
        "p0001-e0000",
        "p0001-e0001",
        "p0002-e0002",
        "p0002-e0003",
        "p0002-e0004",
        "p0002-e0005",
    ]
    assert document.elements[0].heading_path == []
    assert document.elements[1].heading_path == ["Introduction"]
    assert document.elements[2].heading_path == ["Introduction"]
    assert document.elements[2].text == "Table 1: Results"
    assert document.tables[0].id == "table-0000"
    assert document.tables[0].element_id == document.elements[2].id
    assert document.tables[0].cells == [["Metric", "Value"], ["Accuracy", "0.95"]]
    assert document.assets[0].id == "asset-0000"
    assert document.assets[0].element_id == document.elements[3].id
    assert document.assets[0].asset_type == "picture"
    assert document.assets[0].uri == "images/figure-1.png"
    assert document.elements[-1].text == "E = mc^2"
    assert document.metadata["parser"] == "docling.DocumentConverter"
    assert document.metadata["item_count"] == 6


@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None,
    reason="docling is not installed",
)
def test_live_docling_conversion_when_installed(tmp_path: Path) -> None:
    from pdf_backend_docling import DoclingBackend

    pytest.importorskip("docling")

    pdf_path = tmp_path / "blank.pdf"
    pdf_path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )

    document = DoclingBackend().parse(
        pdf_path,
        paper_id="paper-live",
        space_id="space-live",
        quality_report=PdfQualityReport(),
    )

    assert isinstance(document, ParseDocument)
    assert document.backend == "docling"
