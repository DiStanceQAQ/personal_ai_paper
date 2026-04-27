"""Tests for routing PDFs to the best parser backend."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any, Literal

import pytest

from pdf_backend_base import ParserBackendError, ParserBackendUnavailable
from pdf_backend_grobid import (
    GrobidMetadata,
    GrobidParseResult,
    GrobidReference,
    GrobidSection,
)
from pdf_models import ParseDocument, PdfQualityReport


RouterAction = Literal["ok", "unavailable", "error"]


class FakeBackend:
    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        action: RouterAction = "ok",
        extraction_method: str = "native_text",
    ) -> None:
        self.name = name
        self.available = available
        self.action = action
        self.extraction_method = extraction_method
        self.calls: list[tuple[Path, str, str, PdfQualityReport]] = []

    def is_available(self) -> bool:
        return self.available

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        self.calls.append((file_path, paper_id, space_id, quality_report))
        if self.action == "unavailable":
            raise ParserBackendUnavailable(self.name, "missing dependency")
        if self.action == "error":
            raise ParserBackendError(self.name, "parse exploded")
        return ParseDocument(
            paper_id=paper_id,
            space_id=space_id,
            backend=self.name,
            extraction_method=extraction_method_for(self.name, self.extraction_method),
            quality=quality_report,
            metadata={"backend": self.name},
        )


class ClosableFakeBackend(FakeBackend):
    def __init__(self, name: str, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def extraction_method_for(name: str, preferred: str) -> Any:
    if preferred in {"native_text", "ocr", "layout_model", "llm_parser", "legacy"}:
        return preferred
    if name == "docling":
        return "layout_model"
    if name == "llamaparse":
        return "llm_parser"
    if name == "legacy-pymupdf":
        return "legacy"
    return "native_text"


class FakeGrobidClient:
    def __init__(
        self,
        *,
        alive: bool = True,
        fail: bool = False,
        failure_message: str = "grobid exploded",
    ) -> None:
        self.alive = alive
        self.fail = fail
        self.failure_message = failure_message
        self.closed = False
        self.processed: list[Path] = []

    def is_alive(self) -> bool:
        return self.alive

    def process_fulltext(self, file_path: Path) -> GrobidParseResult:
        self.processed.append(file_path)
        if self.fail:
            raise RuntimeError(self.failure_message)
        return GrobidParseResult(
            metadata=GrobidMetadata(
                title="GROBID Title",
                authors=["Ada Lovelace"],
                year=1843,
                venue="Notes",
                doi="10.0000/example",
                abstract="GROBID abstract",
            ),
            sections=[GrobidSection(heading="Intro", text="Section text")],
            references=[
                GrobidReference(
                    id="b1",
                    title="Reference Title",
                    authors=["Grace Hopper"],
                    year=1952,
                    venue="Compiler Conf",
                    doi="10.0000/ref",
                    raw_text="Reference raw text",
                )
            ],
            raw_tei="<tei>large</tei>",
        )

    def close(self) -> None:
        self.closed = True


def _router_module() -> Any:
    return importlib.import_module("pdf_router")


def _quality(**overrides: Any) -> PdfQualityReport:
    values = {
        "page_count": 2,
        "native_text_pages": 2,
        "needs_ocr": False,
        "needs_layout_model": False,
    }
    values.update(overrides)
    return PdfQualityReport(**values)


def _backends(
    *,
    pymupdf: FakeBackend | None = None,
    docling: FakeBackend | None = None,
    llamaparse: FakeBackend | None = None,
    legacy: FakeBackend | None = None,
) -> dict[str, FakeBackend | None]:
    return {
        "pymupdf4llm": pymupdf or FakeBackend("pymupdf4llm"),
        "docling": docling,
        "llamaparse": llamaparse,
        "legacy": legacy or FakeBackend("legacy-pymupdf", extraction_method="legacy"),
    }


def test_clean_digital_pdf_selects_pymupdf_before_legacy(tmp_path: Path) -> None:
    router_module = _router_module()
    pymupdf = FakeBackend("pymupdf4llm")
    legacy = FakeBackend("legacy-pymupdf", extraction_method="legacy")
    quality = _quality()

    router = router_module.PdfBackendRouter(**_backends(pymupdf=pymupdf, legacy=legacy))
    document = router.parse_pdf(tmp_path / "clean.pdf", "paper-1", "space-1", quality)

    assert document.backend == "pymupdf4llm"
    assert len(pymupdf.calls) == 1
    assert legacy.calls == []
    assert document.quality.warnings == [
        "router_attempt:pymupdf4llm",
        "router_selected:pymupdf4llm",
    ]
    assert quality.warnings == []


def test_default_digital_parse_does_not_resolve_llamaparse_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router_module = _router_module()
    pymupdf = FakeBackend("pymupdf4llm")
    monkeypatch.setattr(
        router_module,
        "get_configured_llamaparse_backend",
        lambda: pytest.fail("LlamaParse provider should not be called"),
    )

    router = router_module.PdfBackendRouter(
        pymupdf4llm=pymupdf,
        docling=None,
        legacy=FakeBackend("legacy-pymupdf", extraction_method="legacy"),
        grobid_client=None,
    )
    document = router.parse_pdf(tmp_path / "clean.pdf", "paper-1", "space-1", _quality())

    assert document.backend == "pymupdf4llm"


def test_layout_fallback_resolves_lazy_llamaparse_only_when_reached(
    tmp_path: Path,
) -> None:
    router_module = _router_module()
    docling = FakeBackend("docling", available=False, extraction_method="layout_model")
    llamaparse = FakeBackend("llamaparse", extraction_method="llm_parser")
    provider_calls = 0

    def llamaparse_provider() -> FakeBackend:
        nonlocal provider_calls
        provider_calls += 1
        return llamaparse

    router = router_module.PdfBackendRouter(
        pymupdf4llm=FakeBackend("pymupdf4llm"),
        docling=docling,
        llamaparse=llamaparse_provider,
        legacy=FakeBackend("legacy-pymupdf", extraction_method="legacy"),
        grobid_client=None,
    )
    assert provider_calls == 0

    document = router.parse_pdf(
        tmp_path / "layout.pdf",
        "paper-1",
        "space-1",
        _quality(needs_layout_model=True),
    )

    assert document.backend == "llamaparse"
    assert provider_calls == 1


def test_lazy_llamaparse_provider_backend_is_closed_after_success(
    tmp_path: Path,
) -> None:
    router_module = _router_module()
    docling = FakeBackend("docling", available=False, extraction_method="layout_model")
    llamaparse = ClosableFakeBackend("llamaparse", extraction_method="llm_parser")

    router = router_module.PdfBackendRouter(
        pymupdf4llm=FakeBackend("pymupdf4llm"),
        docling=docling,
        llamaparse=lambda: llamaparse,
        legacy=FakeBackend("legacy-pymupdf", extraction_method="legacy"),
        grobid_client=None,
    )
    document = router.parse_pdf(
        tmp_path / "layout.pdf",
        "paper-1",
        "space-1",
        _quality(needs_layout_model=True),
    )

    assert document.backend == "llamaparse"
    assert llamaparse.closed is True


def test_lazy_llamaparse_provider_backend_is_closed_after_failure(
    tmp_path: Path,
) -> None:
    router_module = _router_module()
    llamaparse = ClosableFakeBackend(
        "llamaparse",
        action="error",
        extraction_method="llm_parser",
    )
    legacy = FakeBackend("legacy-pymupdf", extraction_method="legacy")

    router = router_module.PdfBackendRouter(
        pymupdf4llm=FakeBackend("pymupdf4llm"),
        docling=FakeBackend("docling", available=False, extraction_method="layout_model"),
        llamaparse=lambda: llamaparse,
        legacy=legacy,
        grobid_client=None,
    )
    document = router.parse_pdf(
        tmp_path / "layout.pdf",
        "paper-1",
        "space-1",
        _quality(needs_layout_model=True),
    )

    assert document.backend == "legacy-pymupdf"
    assert len(legacy.calls) == 1
    assert llamaparse.closed is True


def test_injected_llamaparse_backend_instance_is_not_closed(tmp_path: Path) -> None:
    router_module = _router_module()
    llamaparse = ClosableFakeBackend("llamaparse", extraction_method="llm_parser")

    router = router_module.PdfBackendRouter(
        pymupdf4llm=FakeBackend("pymupdf4llm"),
        docling=FakeBackend("docling", available=False, extraction_method="layout_model"),
        llamaparse=llamaparse,
        legacy=FakeBackend("legacy-pymupdf", extraction_method="legacy"),
        grobid_client=None,
    )
    document = router.parse_pdf(
        tmp_path / "layout.pdf",
        "paper-1",
        "space-1",
        _quality(needs_layout_model=True),
    )

    assert document.backend == "llamaparse"
    assert llamaparse.closed is False


def test_scanned_layout_pdf_selects_docling_when_available(tmp_path: Path) -> None:
    router_module = _router_module()
    docling = FakeBackend("docling", extraction_method="layout_model")
    pymupdf = FakeBackend("pymupdf4llm")

    router = router_module.PdfBackendRouter(**_backends(pymupdf=pymupdf, docling=docling))
    document = router.parse_pdf(
        tmp_path / "scan.pdf",
        "paper-1",
        "space-1",
        _quality(needs_ocr=True, native_text_pages=0, image_only_pages=2),
    )

    assert document.backend == "docling"
    assert len(docling.calls) == 1
    assert pymupdf.calls == []
    assert document.quality.warnings[:2] == [
        "router_attempt:docling",
        "router_selected:docling",
    ]


def test_docling_unavailable_on_scanned_pdf_uses_llamaparse_then_legacy(
    tmp_path: Path,
) -> None:
    router_module = _router_module()
    docling = FakeBackend("docling", available=False, extraction_method="layout_model")
    llamaparse = FakeBackend("llamaparse", extraction_method="llm_parser")
    legacy = FakeBackend("legacy-pymupdf", extraction_method="legacy")
    quality = _quality(needs_layout_model=True)

    router = router_module.PdfBackendRouter(
        **_backends(docling=docling, llamaparse=llamaparse, legacy=legacy)
    )
    document = router.parse_pdf(tmp_path / "layout.pdf", "paper-1", "space-1", quality)

    assert document.backend == "llamaparse"
    assert legacy.calls == []
    assert document.quality.warnings == [
        "router_attempt:docling",
        "router_unavailable:docling:is_available returned false",
        "router_attempt:llamaparse",
        "router_selected:llamaparse",
    ]

    no_cloud_legacy = FakeBackend("legacy-pymupdf", extraction_method="legacy")
    router_without_cloud = router_module.PdfBackendRouter(
        **_backends(
            docling=docling,
            llamaparse=None,
            legacy=no_cloud_legacy,
        )
    )
    fallback = router_without_cloud.parse_pdf(
        tmp_path / "layout.pdf", "paper-2", "space-1", quality
    )

    assert fallback.backend == "legacy-pymupdf"
    assert len(no_cloud_legacy.calls) == 1
    assert fallback.quality.warnings == [
        "router_attempt:docling",
        "router_unavailable:docling:is_available returned false",
        "router_attempt:legacy-pymupdf",
        "router_selected:legacy-pymupdf",
    ]


def test_forced_backend_from_settings_is_tried_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router_module = _router_module()
    monkeypatch.setattr(router_module, "get_forced_backend_setting", lambda: "legacy")
    legacy = FakeBackend("legacy-pymupdf", extraction_method="legacy")
    pymupdf = FakeBackend("pymupdf4llm")

    router = router_module.PdfBackendRouter(**_backends(pymupdf=pymupdf, legacy=legacy))
    document = router.parse_pdf(tmp_path / "clean.pdf", "paper-1", "space-1", _quality())

    assert document.backend == "legacy-pymupdf"
    assert len(legacy.calls) == 1
    assert pymupdf.calls == []
    assert document.quality.warnings == [
        "router_attempt:legacy-pymupdf",
        "router_selected:legacy-pymupdf",
    ]


def test_forced_backend_failure_continues_to_normal_fallback(tmp_path: Path) -> None:
    router_module = _router_module()
    forced = FakeBackend("docling", action="error", extraction_method="layout_model")
    pymupdf = FakeBackend("pymupdf4llm")

    router = router_module.PdfBackendRouter(
        **_backends(pymupdf=pymupdf, docling=forced),
        forced_backend="docling",
    )
    document = router.parse_pdf(tmp_path / "clean.pdf", "paper-1", "space-1", _quality())

    assert document.backend == "pymupdf4llm"
    assert document.quality.warnings == [
        "router_attempt:docling",
        "router_failed:docling:docling backend error: parse exploded",
        "router_attempt:pymupdf4llm",
        "router_selected:pymupdf4llm",
    ]


def test_all_preferred_backends_fail_then_legacy_fallback_works(tmp_path: Path) -> None:
    router_module = _router_module()
    docling = FakeBackend("docling", action="error", extraction_method="layout_model")
    llamaparse = FakeBackend("llamaparse", action="unavailable", extraction_method="llm_parser")
    legacy = FakeBackend("legacy-pymupdf", extraction_method="legacy")

    router = router_module.PdfBackendRouter(
        **_backends(docling=docling, llamaparse=llamaparse, legacy=legacy)
    )
    document = router.parse_pdf(
        tmp_path / "hard.pdf",
        "paper-1",
        "space-1",
        _quality(needs_ocr=True),
    )

    assert document.backend == "legacy-pymupdf"
    assert document.quality.warnings == [
        "router_attempt:docling",
        "router_failed:docling:docling backend error: parse exploded",
        "router_attempt:llamaparse",
        "router_unavailable:llamaparse:llamaparse backend unavailable: missing dependency",
        "router_attempt:legacy-pymupdf",
        "router_selected:legacy-pymupdf",
    ]


def test_grobid_healthy_client_merges_metadata_and_references(tmp_path: Path) -> None:
    router_module = _router_module()
    grobid = FakeGrobidClient()
    router = router_module.PdfBackendRouter(
        **_backends(),
        grobid_client=grobid,
    )
    pdf_path = tmp_path / "paper.pdf"

    document = router.parse_pdf(pdf_path, "paper-1", "space-1", _quality())

    assert grobid.processed == [pdf_path]
    assert document.metadata["grobid"] == {
        "metadata": {
            "title": "GROBID Title",
            "authors": ["Ada Lovelace"],
            "year": 1843,
            "venue": "Notes",
            "doi": "10.0000/example",
            "abstract": "GROBID abstract",
        },
        "references": [
            {
                "id": "b1",
                "title": "Reference Title",
                "authors": ["Grace Hopper"],
                "year": 1952,
                "venue": "Compiler Conf",
                "doi": "10.0000/ref",
                "raw_text": "Reference raw text",
            }
        ],
    }
    assert "sections" not in document.metadata["grobid"]
    assert "router_grobid_merged" in document.quality.warnings
    assert grobid.closed is False


@pytest.mark.parametrize(
    ("grobid", "expected_warning"),
    [
        (None, None),
        (FakeGrobidClient(alive=False), "router_grobid_unavailable:is_alive returned false"),
        (FakeGrobidClient(fail=True), "router_grobid_failed:grobid exploded"),
    ],
)
def test_grobid_absent_unhealthy_or_failing_does_not_fail_primary_parse(
    tmp_path: Path,
    grobid: FakeGrobidClient | None,
    expected_warning: str | None,
) -> None:
    router_module = _router_module()
    router = router_module.PdfBackendRouter(**_backends(), grobid_client=grobid)

    document = router.parse_pdf(tmp_path / "paper.pdf", "paper-1", "space-1", _quality())

    assert document.backend == "pymupdf4llm"
    if expected_warning is None:
        assert not any(warning.startswith("router_grobid") for warning in document.quality.warnings)
    else:
        assert expected_warning in document.quality.warnings


def test_llamaparse_provider_error_does_not_break_local_fallback(tmp_path: Path) -> None:
    router_module = _router_module()

    def broken_llamaparse_provider() -> None:
        raise RuntimeError("sqlite unavailable")

    router = router_module.PdfBackendRouter(
        pymupdf4llm=FakeBackend("pymupdf4llm"),
        docling=FakeBackend("docling", available=False, extraction_method="layout_model"),
        llamaparse=broken_llamaparse_provider,
        legacy=FakeBackend("legacy-pymupdf", extraction_method="legacy"),
        grobid_client=None,
    )
    document = router.parse_pdf(
        tmp_path / "layout.pdf",
        "paper-1",
        "space-1",
        _quality(needs_layout_model=True),
    )

    assert document.backend == "legacy-pymupdf"
    assert "router_llamaparse_config_failed:sqlite unavailable" in document.quality.warnings


def test_grobid_provider_error_does_not_break_primary_parse(tmp_path: Path) -> None:
    router_module = _router_module()

    def broken_grobid_provider() -> None:
        raise RuntimeError("db locked")

    router = router_module.PdfBackendRouter(
        **_backends(),
        grobid_client=broken_grobid_provider,
    )
    document = router.parse_pdf(tmp_path / "paper.pdf", "paper-1", "space-1", _quality())

    assert document.backend == "pymupdf4llm"
    assert "router_grobid_config_failed:db locked" in document.quality.warnings


def test_grobid_failure_warning_is_bounded(tmp_path: Path) -> None:
    router_module = _router_module()
    grobid = FakeGrobidClient(fail=True, failure_message="x" * 500)
    router = router_module.PdfBackendRouter(**_backends(), grobid_client=grobid)

    document = router.parse_pdf(tmp_path / "paper.pdf", "paper-1", "space-1", _quality())
    warning = next(
        warning
        for warning in document.quality.warnings
        if warning.startswith("router_grobid_failed:")
    )

    assert len(warning) <= 160


def test_router_closes_configured_grobid_client_it_owns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router_module = _router_module()
    grobid = FakeGrobidClient()
    monkeypatch.setattr(router_module, "get_configured_grobid_client", lambda: grobid)

    router = router_module.PdfBackendRouter(**_backends())
    router.parse_pdf(tmp_path / "paper.pdf", "paper-1", "space-1", _quality())

    assert grobid.closed is True


def test_parse_pdf_convenience_inspects_when_quality_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router_module = _router_module()
    inspected = _quality(page_count=5, native_text_pages=5)
    monkeypatch.setattr(router_module, "inspect_pdf", lambda file_path: inspected)
    backend = FakeBackend("pymupdf4llm")
    monkeypatch.setattr(
        router_module,
        "PyMuPDF4LLMBackend",
        lambda: backend,
    )
    monkeypatch.setattr(router_module, "get_configured_llamaparse_backend", lambda: None)
    monkeypatch.setattr(router_module, "get_configured_grobid_client", lambda: None)

    document = router_module.parse_pdf(tmp_path / "paper.pdf", "paper-1", "space-1")

    assert document.backend == "pymupdf4llm"
    assert backend.calls[0][3].page_count == 5


def test_pyproject_packages_router_module() -> None:
    with Path("pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    assert "pdf_router" in pyproject["tool"]["setuptools"]["py-modules"]
