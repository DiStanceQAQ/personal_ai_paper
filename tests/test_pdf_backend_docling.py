from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_engine.pdf.backends.base import ParserBackendError, ParserBackendUnavailable
from paper_engine.pdf.models import ParseDocument, PdfQualityReport

_DOCLING_LAYOUT_CACHE_DIRS = (
    Path("resources/models/docling-hf-cache/hub/models--docling-project--docling-layout-heron"),
    Path.home() / ".cache" / "huggingface" / "hub" / "models--docling-project--docling-layout-heron",
)


def test_pyproject_declares_docling_extra_and_module() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["optional-dependencies"]["pdf-advanced"] == [
        "docling>=2.0.0"
    ]
    assert pyproject["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "paper_engine*"
    ]


def test_availability_uses_find_spec_without_importing_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    from paper_engine.pdf.backends.docling import DoclingBackend

    calls: list[str] = []

    def fake_find_spec(name: str) -> object | None:
        calls.append(name)
        return object() if name == "docling" else None

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    assert DoclingBackend().is_available() is True
    assert calls == ["docling"]


def test_availability_is_false_when_docling_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    from paper_engine.pdf.backends.docling import DoclingBackend

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    assert DoclingBackend().is_available() is False


def test_parse_requires_docling_when_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from paper_engine.pdf.backends.docling import DoclingBackend

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    with pytest.raises(ParserBackendUnavailable):
        DoclingBackend().parse(
            Path("missing.pdf"),
            paper_id="paper-1",
            space_id="space-1",
            quality_report=PdfQualityReport(),
        )


def test_missing_docling_parse_resources_are_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling

    package_dir = tmp_path / "docling_parse"
    package_dir.mkdir()
    init_file = package_dir / "__init__.py"
    init_file.write_text("", encoding="utf-8")

    def fake_import_module(name: str) -> object:
        if name == "docling_parse":
            return SimpleNamespace(__file__=str(init_file))
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr(pdf_backend_docling.importlib, "import_module", fake_import_module)

    with pytest.raises(ParserBackendUnavailable) as exc_info:
        pdf_backend_docling._ensure_docling_parse_resources()

    assert "docling-parse PDF resources are missing" in str(exc_info.value)


def test_parse_wraps_docling_conversion_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling
    from paper_engine.pdf.backends.docling import DoclingBackend

    class FailingConverter:
        def convert(self, file_path: str) -> object:
            raise RuntimeError(f"cannot convert {file_path}")

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        pdf_backend_docling,
        "_create_docling_converter",
        lambda options=None: FailingConverter(),
    )

    with pytest.raises(ParserBackendError) as exc_info:
        DoclingBackend().parse(
            Path("bad.pdf"),
            paper_id="paper-1",
            space_id="space-1",
            quality_report=PdfQualityReport(),
        )

    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_create_docling_converter_uses_default_constructor_without_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling

    captured: dict[str, object] = {}

    class FakeDocumentConverter:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

    class FakeInputFormat:
        PDF = "pdf"

    monkeypatch.setattr(
        pdf_backend_docling,
        "_load_docling_components",
        lambda: (
            FakeDocumentConverter,
            FakeInputFormat,
            object(),
            object(),
            object(),
        ),
    )

    converter = pdf_backend_docling._create_docling_converter()

    assert isinstance(converter, FakeDocumentConverter)
    assert captured["kwargs"] == {}


def test_create_docling_converter_applies_performance_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling

    captured: dict[str, object] = {}

    class FakeDocumentConverter:
        def __init__(self, **kwargs: object) -> None:
            captured["converter_kwargs"] = kwargs

    class FakeInputFormat:
        PDF = "pdf"

    class FakePdfFormatOption:
        def __init__(self, **kwargs: object) -> None:
            captured["format_option_kwargs"] = kwargs

    class FakeTableStructureOptions:
        mode = "accurate"

    class FakePipelineOptions:
        def __init__(self, **kwargs: object) -> None:
            captured["pipeline_kwargs"] = kwargs
            self.table_structure_options = FakeTableStructureOptions()
            self.do_ocr = True
            self.do_table_structure = True
            self.ocr_batch_size = 4
            self.layout_batch_size = 4
            self.table_batch_size = 4

    class FakeAcceleratorOptions:
        def __init__(self, **kwargs: object) -> None:
            captured["accelerator_kwargs"] = kwargs

    monkeypatch.setattr(
        pdf_backend_docling,
        "_load_docling_components",
        lambda: (
            FakeDocumentConverter,
            FakeInputFormat,
            FakePdfFormatOption,
            FakePipelineOptions,
            FakeAcceleratorOptions,
        ),
    )

    options = pdf_backend_docling.DoclingPerformanceOptions(
        num_threads=10,
        device="cpu",
        do_ocr=False,
        do_table_structure=True,
        table_mode="fast",
        ocr_batch_size=10,
        layout_batch_size=10,
        table_batch_size=10,
    )

    converter = pdf_backend_docling._create_docling_converter(options)
    pipeline_options = captured["format_option_kwargs"]["pipeline_options"]

    assert isinstance(converter, FakeDocumentConverter)
    assert captured["accelerator_kwargs"] == {"num_threads": 10, "device": "cpu"}
    assert pipeline_options.do_ocr is False
    assert pipeline_options.do_table_structure is True
    assert pipeline_options.layout_batch_size == 10
    assert pipeline_options.table_batch_size == 10
    assert pipeline_options.ocr_batch_size == 10
    assert pipeline_options.table_structure_options.mode == "fast"


def test_docling_performance_options_use_fast_text_pdf_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling

    monkeypatch.setattr(pdf_backend_docling.os, "cpu_count", lambda: 16)

    options = pdf_backend_docling._docling_performance_options(
        PdfQualityReport(page_count=8, native_text_pages=8, needs_ocr=False),
    )

    assert options.num_threads == 12
    assert options.device == "auto"
    assert options.do_ocr is False
    assert options.do_table_structure is True
    assert options.table_mode == "fast"
    assert options.layout_batch_size == 12


def test_docling_performance_options_keep_ocr_for_scanned_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling

    monkeypatch.setattr(pdf_backend_docling.os, "cpu_count", lambda: 8)

    options = pdf_backend_docling._docling_performance_options(
        PdfQualityReport(page_count=8, image_only_pages=8, needs_ocr=True),
    )

    assert options.num_threads == 8
    assert options.do_ocr is True
    assert options.layout_batch_size == 8


def test_docling_performance_options_allow_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling

    monkeypatch.setenv("PAPER_ENGINE_DOCLING_THREADS", "6")
    monkeypatch.setenv("PAPER_ENGINE_DOCLING_DEVICE", "cpu")
    monkeypatch.setenv("PAPER_ENGINE_DOCLING_OCR", "1")
    monkeypatch.setenv("PAPER_ENGINE_DOCLING_TABLE_STRUCTURE", "0")
    monkeypatch.setenv("PAPER_ENGINE_DOCLING_TABLE_MODE", "accurate")
    monkeypatch.setenv("PAPER_ENGINE_DOCLING_BATCH_SIZE", "3")

    options = pdf_backend_docling._docling_performance_options(
        PdfQualityReport(page_count=8, native_text_pages=8, needs_ocr=False),
    )

    assert options.num_threads == 6
    assert options.device == "cpu"
    assert options.do_ocr is True
    assert options.do_table_structure is False
    assert options.table_mode == "accurate"
    assert options.layout_batch_size == 3


def test_shared_docling_converter_reuses_same_instance_until_factory_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling

    class FirstConverter:
        pass

    class SecondConverter:
        pass

    monkeypatch.setattr(
        pdf_backend_docling,
        "_create_docling_converter",
        lambda options=None: FirstConverter(),
    )
    first = pdf_backend_docling._shared_docling_converter()
    second = pdf_backend_docling._shared_docling_converter()

    monkeypatch.setattr(
        pdf_backend_docling,
        "_create_docling_converter",
        lambda options=None: SecondConverter(),
    )
    third = pdf_backend_docling._shared_docling_converter()

    assert isinstance(first, FirstConverter)
    assert first is second
    assert isinstance(third, SecondConverter)
    assert third is not first


def test_shared_docling_converter_caches_by_performance_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling

    created: list[object] = []

    def fake_create(options: object | None = None) -> object:
        converter = SimpleNamespace(options=options)
        created.append(converter)
        return converter

    fast_options = pdf_backend_docling.DoclingPerformanceOptions(
        num_threads=8,
        device="auto",
        do_ocr=False,
        do_table_structure=True,
        table_mode="fast",
        ocr_batch_size=8,
        layout_batch_size=8,
        table_batch_size=8,
    )
    scanned_options = pdf_backend_docling.DoclingPerformanceOptions(
        num_threads=8,
        device="auto",
        do_ocr=True,
        do_table_structure=True,
        table_mode="fast",
        ocr_batch_size=8,
        layout_batch_size=8,
        table_batch_size=8,
    )

    monkeypatch.setattr(pdf_backend_docling, "_create_docling_converter", fake_create)

    first = pdf_backend_docling._shared_docling_converter(fast_options)
    second = pdf_backend_docling._shared_docling_converter(fast_options)
    third = pdf_backend_docling._shared_docling_converter(scanned_options)

    assert first is second
    assert third is not first
    assert len(created) == 2


def test_parse_normalizes_mixed_docling_items_in_reading_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling
    from paper_engine.pdf.backends.docling import DoclingBackend

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
    monkeypatch.setattr(
        pdf_backend_docling,
        "_create_docling_converter",
        lambda options=None: FakeConverter(),
    )

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
    assert document.metadata["performance_options"]["do_ocr"] is False


def test_parse_prefers_docling_iterated_reading_order_over_top_level_texts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling
    from paper_engine.pdf.backends.docling import DoclingBackend

    heading = SimpleNamespace(label="section_header", text="Methods", level=1, page_no=1)
    paragraph = SimpleNamespace(label="text", text="We evaluate the parser.", page_no=1)
    table_caption = SimpleNamespace(label="caption", text="Table 1: Scores", page_no=1)
    figure_caption = SimpleNamespace(label="caption", text="Figure 1: Flow", page_no=2)
    formula = SimpleNamespace(label="formula", latex="x^2 + y^2", page_no=2)
    table = SimpleNamespace(
        label="table",
        captions=[{"ref": "#/texts/2"}],
        page_no=1,
        data=SimpleNamespace(table_cells=[["Name", "Score"], ["A", "1"]]),
    )
    picture = {
        "type": "picture",
        "captions": [SimpleNamespace(cref="#/texts/3")],
        "page_number": 2,
        "image": {"uri": "figures/flow.png"},
    }

    class DoclingLikeDocument:
        metadata = {"shape": "docling-v2-like"}
        texts = [heading, paragraph, table_caption, figure_caption, formula]
        tables = [table]
        pictures = [picture]

        def iterate_items(self) -> list[tuple[object, int]]:
            return [
                (heading, 1),
                (paragraph, 1),
                (table, 1),
                (picture, 1),
                (table_caption, 1),
                (formula, 1),
            ]

    class FakeResult:
        document = DoclingLikeDocument()

    class FakeConverter:
        def convert(self, file_path: str) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        pdf_backend_docling,
        "_create_docling_converter",
        lambda options=None: FakeConverter(),
    )

    document = DoclingBackend().parse(
        Path("docling-v2.pdf"),
        paper_id="paper-1",
        space_id="space-1",
        quality_report=PdfQualityReport(),
    )

    assert [element.element_type for element in document.elements] == [
        "heading",
        "paragraph",
        "table",
        "figure",
        "caption",
        "equation",
    ]
    assert document.elements[2].text == "Table 1: Scores"
    assert document.tables[0].caption == "Table 1: Scores"
    assert document.elements[3].text == "Figure 1: Flow"
    assert document.assets[0].uri == "figures/flow.png"


def test_parse_resolves_caption_ref_lists_without_caption_text_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling
    from paper_engine.pdf.backends.docling import DoclingBackend

    caption = SimpleNamespace(label="caption", text="Table 2: Ablation", page_no=3)
    table = SimpleNamespace(
        label="table",
        captions=[SimpleNamespace(cref="#/texts/0")],
        page_no=3,
        data=SimpleNamespace(table_cells=[["Variant", "Delta"], ["base", "0"]]),
    )
    fake_document = SimpleNamespace(
        metadata={},
        texts=[caption],
        tables=[table],
        pictures=[],
        items=[table],
    )

    class FakeResult:
        document = fake_document

    class FakeConverter:
        def convert(self, file_path: str) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        pdf_backend_docling,
        "_create_docling_converter",
        lambda options=None: FakeConverter(),
    )

    document = DoclingBackend().parse(
        Path("caption-ref.pdf"),
        paper_id="paper-1",
        space_id="space-1",
        quality_report=PdfQualityReport(),
    )

    assert document.elements[0].text == "Table 2: Ablation"
    assert document.tables[0].caption == "Table 2: Ablation"
    assert "namespace(" not in document.elements[0].text


def test_parse_maps_docling_page_margins_without_corrupting_heading_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling
    from paper_engine.pdf.backends.docling import DoclingBackend

    page_header = SimpleNamespace(
        label="page_header",
        text="Conference 2026",
        page_no=1,
    )
    title = SimpleNamespace(label="title", text="Paper Title", page_no=1)
    section = SimpleNamespace(
        label="section_header",
        text="Methods",
        level=1,
        page_no=1,
    )
    paragraph = SimpleNamespace(
        label="text",
        text="The method preserves reading order.",
        page_no=1,
    )
    page_footer = SimpleNamespace(label="page_footer", text="1", page_no=1)

    fake_document = SimpleNamespace(
        metadata={},
        items=[page_header, title, section, paragraph, page_footer],
    )

    class FakeResult:
        document = fake_document

    class FakeConverter:
        def convert(self, file_path: str) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        pdf_backend_docling,
        "_create_docling_converter",
        lambda options=None: FakeConverter(),
    )

    document = DoclingBackend().parse(
        Path("page-margins.pdf"),
        paper_id="paper-1",
        space_id="space-1",
        quality_report=PdfQualityReport(),
    )

    assert [element.element_type for element in document.elements] == [
        "page_header",
        "title",
        "heading",
        "paragraph",
        "page_footer",
    ]
    assert document.elements[0].metadata["filtered"] is True
    assert document.elements[4].metadata["filtered"] is True
    assert document.elements[1].heading_path == []
    assert document.elements[2].heading_path == []
    assert document.elements[3].heading_path == ["Methods"]


def test_parse_preserves_docling_provenance_bbox_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.docling as pdf_backend_docling
    from paper_engine.pdf.backends.docling import DoclingBackend

    class TupleBBox:
        def as_tuple(self) -> tuple[float, float, float, float]:
            return (1.0, 2.0, 30.0, 40.0)

    class AttrBBox:
        l = 5.0
        t = 6.0
        r = 70.0
        b = 80.0

    first = SimpleNamespace(
        label="text",
        text="Tuple bbox paragraph.",
        prov=[SimpleNamespace(page_no=2, bbox=TupleBBox())],
    )
    second = SimpleNamespace(
        label="text",
        text="Attribute bbox paragraph.",
        prov=[SimpleNamespace(page_no=3, bbox=AttrBBox())],
    )
    fake_document = SimpleNamespace(metadata={}, items=[first, second])

    class FakeResult:
        document = fake_document

    class FakeConverter:
        def convert(self, file_path: str) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        pdf_backend_docling,
        "_create_docling_converter",
        lambda options=None: FakeConverter(),
    )

    document = DoclingBackend().parse(
        Path("bbox.pdf"),
        paper_id="paper-1",
        space_id="space-1",
        quality_report=PdfQualityReport(),
    )

    assert document.elements[0].bbox == [1.0, 2.0, 30.0, 40.0]
    assert document.elements[1].bbox == [5.0, 6.0, 70.0, 80.0]


@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None
    or not any(path.exists() for path in _DOCLING_LAYOUT_CACHE_DIRS),
    reason="docling or its local model cache is not available",
)
def test_live_docling_conversion_when_installed(tmp_path: Path) -> None:
    from paper_engine.pdf.backends.docling import DoclingBackend

    import fitz
    import os
    pytest.importorskip("docling")
    bundled_hf_hub_cache = Path("resources/models/docling-hf-cache/hub").resolve()
    previous_hf_hub_cache = os.environ.get("HF_HUB_CACHE")
    previous_hf_home = os.environ.get("HF_HOME")
    if bundled_hf_hub_cache.is_dir():
        os.environ["HF_HUB_CACHE"] = str(bundled_hf_hub_cache)
        os.environ["HF_HOME"] = str(bundled_hf_hub_cache.parent)

    pdf_path = tmp_path / "blank.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Docling live conversion test.")
    document.save(pdf_path)
    document.close()

    try:
        document = DoclingBackend().parse(
            pdf_path,
            paper_id="paper-live",
            space_id="space-live",
            quality_report=PdfQualityReport(),
        )
    finally:
        if previous_hf_hub_cache is None:
            os.environ.pop("HF_HUB_CACHE", None)
        else:
            os.environ["HF_HUB_CACHE"] = previous_hf_hub_cache
        if previous_hf_home is None:
            os.environ.pop("HF_HOME", None)
        else:
            os.environ["HF_HOME"] = previous_hf_home

    assert isinstance(document, ParseDocument)
    assert document.backend == "docling"
