"""Tests for the optional LlamaParse PDF parser backend."""

from __future__ import annotations

import importlib
import sqlite3
import tomllib
from pathlib import Path
from typing import Any

import httpx
import pytest

from pdf_backend_base import ParserBackendError, ParserBackendUnavailable
from pdf_models import PdfQualityReport


def _backend_module() -> Any:
    return importlib.import_module("pdf_backend_llamaparse")


def _quality() -> PdfQualityReport:
    return PdfQualityReport(page_count=2, native_text_pages=0, needs_layout_model=True)


def test_pyproject_packages_llamaparse_backend_module() -> None:
    """Setuptools module list should include the runtime backend module."""
    with Path("pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    assert "pdf_backend_llamaparse" in pyproject["tool"]["setuptools"]["py-modules"]


def test_backend_unavailable_without_api_key(tmp_path: Path) -> None:
    """The backend is disabled and refuses parsing until an API key is configured."""
    backend_module = _backend_module()
    backend = backend_module.LlamaParseBackend(api_key="")
    fake_pdf = tmp_path / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\n")

    assert backend.is_available() is False
    with pytest.raises(ParserBackendUnavailable):
        backend.parse(fake_pdf, "paper-1", "space-1", _quality())


def test_backend_posts_pdf_with_auth_header_and_converts_json_pages(tmp_path: Path) -> None:
    """Configured parsing should post the PDF and normalize JSON pages."""
    backend_module = _backend_module()
    fake_pdf = tmp_path / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\nbody")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(
            200,
            json={
                "job_id": "job-123",
                "pages": [
                    {
                        "page": 1,
                        "markdown": (
                            "# Paper Title\n\n"
                            "Opening paragraph.\n\n"
                            "| Metric | Value |\n"
                            "| --- | --- |\n"
                            "| Accuracy | 0.92 |"
                        ),
                        "tables": [
                            {
                                "caption": "Scores",
                                "rows": [["Metric", "Value"], ["Accuracy", "0.92"]],
                            }
                        ],
                        "images": [{"uri": "s3://bucket/figure.png"}],
                    },
                    {"page_number": 2, "text": "## Methods\n\nSecond page text."},
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = backend_module.LlamaParseBackend(
        api_key="llama-secret",
        base_url="https://llamaparse.test/api",
        http_client=client,
    )

    document = backend.parse(fake_pdf, "paper-1", "space-1", _quality())

    assert seen["url"] == "https://llamaparse.test/api/parse"
    assert seen["authorization"] == "Bearer llama-secret"
    assert "multipart/form-data" in seen["content_type"]
    assert b"%PDF-1.7" in seen["body"]

    assert document.backend == "llamaparse"
    assert document.extraction_method == "llm_parser"
    assert document.paper_id == "paper-1"
    assert document.space_id == "space-1"
    assert document.metadata["job_id"] == "job-123"
    assert [element.id for element in document.elements] == [
        "paper-1:llamaparse:element:0",
        "paper-1:llamaparse:element:1",
        "paper-1:llamaparse:element:2",
        "paper-1:llamaparse:element:3",
        "paper-1:llamaparse:element:4",
    ]
    assert [element.element_type for element in document.elements] == [
        "heading",
        "paragraph",
        "table",
        "heading",
        "paragraph",
    ]
    assert document.tables[0].id == "paper-1:llamaparse:table:0"
    assert document.tables[0].cells == [["Metric", "Value"], ["Accuracy", "0.92"]]
    assert document.assets[0].id == "paper-1:llamaparse:asset:0"
    assert document.assets[0].uri == "s3://bucket/figure.png"


def test_backend_converts_markdown_only_response(tmp_path: Path) -> None:
    """Markdown-only responses should still become structured elements and tables."""
    backend_module = _backend_module()
    fake_pdf = tmp_path / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\n")

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "markdown": (
                        "# Title\n\n"
                        "Paragraph one.\n\n"
                        "## Results\n\n"
                        "| Name | Score |\n"
                        "| --- | --- |\n"
                        "| A | 1 |"
                    )
                },
            )
        )
    )
    backend = backend_module.LlamaParseBackend(api_key="key", http_client=client)

    document = backend.parse(fake_pdf, "paper-md", "space-1", _quality())

    assert document.extraction_method == "llm_parser"
    assert [element.element_type for element in document.elements] == [
        "heading",
        "paragraph",
        "heading",
        "table",
    ]
    assert document.tables[0].cells == [["Name", "Score"], ["A", "1"]]
    assert all(element.page_number == 1 for element in document.elements)


def test_backend_wraps_http_errors(tmp_path: Path) -> None:
    """HTTP failures should be reported as parser backend errors."""
    backend_module = _backend_module()
    fake_pdf = tmp_path / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\n")
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, text="down"))
    )
    backend = backend_module.LlamaParseBackend(api_key="key", http_client=client)

    with pytest.raises(ParserBackendError) as exc_info:
        backend.parse(fake_pdf, "paper-1", "space-1", _quality())

    assert "llamaparse backend error" in str(exc_info.value)


def test_get_configured_backend_reads_app_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Configured backend helper should read LlamaParse keys from app_state."""
    backend_module = _backend_module()
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?)",
        ("llamaparse_api_key", "secret-key"),
    )
    connection.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?)",
        ("llamaparse_base_url", "https://configured.example/v1"),
    )
    connection.commit()
    connection.close()

    def get_test_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(backend_module, "get_connection", get_test_connection)

    config = backend_module.get_llamaparse_config()
    backend = backend_module.get_configured_llamaparse_backend()

    assert config == {
        "llamaparse_api_key": "secret-key",
        "llamaparse_base_url": "https://configured.example/v1",
    }
    assert backend is not None
    assert backend.is_available() is True
