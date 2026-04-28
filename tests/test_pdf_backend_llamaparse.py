"""Tests for the optional LlamaParse PDF parser backend."""

from __future__ import annotations

import importlib
import json
import sqlite3
import tomllib
from pathlib import Path
from typing import Any

import httpx
import pytest

from paper_engine.pdf.backends.base import ParserBackendError, ParserBackendUnavailable
from paper_engine.pdf.models import PdfQualityReport


def _backend_module() -> Any:
    return importlib.import_module("paper_engine.pdf.backends.llamaparse")


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


def test_backend_uses_official_upload_create_poll_flow(tmp_path: Path) -> None:
    """Default parsing should upload, create a parse job, then poll API v2 results."""
    backend_module = _backend_module()
    fake_pdf = tmp_path / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\nofficial-flow")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers.get("authorization") == "Bearer llama-secret"

        if request.method == "POST" and request.url.path == "/api/v1/beta/files":
            assert b'name="purpose"' in request.content
            assert b"parse" in request.content
            assert b"%PDF-1.7" in request.content
            return httpx.Response(200, json={"id": "file-123"})

        if request.method == "POST" and request.url.path == "/api/v2/parse":
            body = json.loads(request.content)
            assert body["file_id"] == "file-123"
            assert body["tier"] == "cost_effective"
            assert body["version"] == "latest"
            return httpx.Response(200, json={"id": "job-123"})

        if request.method == "GET" and request.url.path == "/api/v2/parse/job-123":
            assert request.url.params.get_list("expand") == ["markdown", "items"]
            if len([req for req in requests if req.method == "GET"]) == 1:
                return httpx.Response(200, json={"job": {"id": "job-123", "status": "PENDING"}})
            return httpx.Response(
                200,
                json={
                    "job": {"id": "job-123", "status": "COMPLETED"},
                    "markdown": "# Parsed Title\n\nParsed paragraph.",
                    "items": {
                        "pages": [
                            {
                                "page": 1,
                                "items": [
                                    {"type": "heading", "value": "Parsed Title", "md": "# Parsed Title"},
                                    {"type": "paragraph", "value": "Parsed paragraph."},
                                    {
                                        "type": "table",
                                        "rows": [["Metric", "Value"], ["Score", "1.0"]],
                                    },
                                ],
                            }
                        ]
                    },
                },
            )

        return httpx.Response(404, json={"error": str(request.url)})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = backend_module.LlamaParseBackend(
        api_key="llama-secret",
        http_client=client,
        max_poll_attempts=3,
        poll_interval_seconds=0,
    )

    document = backend.parse(fake_pdf, "paper-api-v2", "space-1", _quality())

    assert [request.method for request in requests] == ["POST", "POST", "GET", "GET"]
    assert [request.url.path for request in requests] == [
        "/api/v1/beta/files",
        "/api/v2/parse",
        "/api/v2/parse/job-123",
        "/api/v2/parse/job-123",
    ]
    assert document.metadata["job_id"] == "job-123"
    assert [element.element_type for element in document.elements] == [
        "heading",
        "paragraph",
        "table",
    ]
    assert document.tables[0].cells == [["Metric", "Value"], ["Score", "1.0"]]


def test_backend_keeps_polling_pending_expanded_payload(tmp_path: Path) -> None:
    """Pending jobs with expanded partial content should not be treated as final."""
    backend_module = _backend_module()
    fake_pdf = tmp_path / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\npending-expanded")
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        if request.url.path == "/api/v1/beta/files":
            return httpx.Response(200, json={"id": "file-pending"})
        if request.url.path == "/api/v2/parse":
            return httpx.Response(200, json={"id": "job-pending"})
        if request.url.path == "/api/v2/parse/job-pending":
            poll_count += 1
            if poll_count == 1:
                return httpx.Response(
                    200,
                    json={
                        "job": {"id": "job-pending", "status": "PENDING"},
                        "markdown": "# partial",
                        "items": {"pages": []},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "job": {"id": "job-pending", "status": "COMPLETED"},
                    "markdown": "# Complete\n\nFinal content.",
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = backend_module.LlamaParseBackend(
        api_key="key",
        http_client=client,
        max_poll_attempts=2,
        poll_interval_seconds=0,
    )

    document = backend.parse(fake_pdf, "paper-pending", "space-1", _quality())

    assert poll_count == 2
    assert [element.text for element in document.elements] == ["Complete", "Final content."]


def test_backend_failed_job_status_raises_parser_error(tmp_path: Path) -> None:
    """Terminal failed parse job statuses should raise a useful parser error."""
    backend_module = _backend_module()
    fake_pdf = tmp_path / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\nfailed-status")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/beta/files":
            return httpx.Response(200, json={"id": "file-failed"})
        if request.url.path == "/api/v2/parse":
            return httpx.Response(200, json={"id": "job-failed"})
        if request.url.path == "/api/v2/parse/job-failed":
            return httpx.Response(
                200,
                json={
                    "job": {
                        "id": "job-failed",
                        "status": "FAILED",
                        "error": "unsupported document",
                    }
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = backend_module.LlamaParseBackend(
        api_key="key",
        http_client=client,
        max_poll_attempts=1,
        poll_interval_seconds=0,
    )

    with pytest.raises(ParserBackendError) as exc_info:
        backend.parse(fake_pdf, "paper-failed", "space-1", _quality())

    assert "job-failed" in str(exc_info.value)
    assert "FAILED" in str(exc_info.value)


def test_backend_converts_api_v2_items_pages_shape(tmp_path: Path) -> None:
    """API v2 items pages should normalize without relying on top-level markdown."""
    backend_module = _backend_module()
    fake_pdf = tmp_path / "paper.pdf"
    fake_pdf.write_bytes(b"%PDF-1.7\nitems-shape")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/beta/files":
            return httpx.Response(200, json={"id": "file-items"})
        if request.url.path == "/api/v2/parse":
            return httpx.Response(200, json={"id": "job-items"})
        if request.url.path == "/api/v2/parse/job-items":
            return httpx.Response(
                200,
                json={
                    "job": {"id": "job-items", "status": "COMPLETED"},
                    "items": {
                        "pages": [
                            {
                                "page": 2,
                                "items": [
                                    {"type": "heading", "value": "Methods"},
                                    {"type": "text", "value": "We trained a model."},
                                    {
                                        "type": "image",
                                        "url": "https://assets.example/fig1.png",
                                        "caption": "Architecture",
                                    },
                                    {
                                        "type": "table",
                                        "rows": [["Name", "Score"], ["A", "0.9"]],
                                    },
                                ],
                            }
                        ]
                    },
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = backend_module.LlamaParseBackend(
        api_key="key",
        http_client=client,
        max_poll_attempts=1,
        poll_interval_seconds=0,
    )

    document = backend.parse(fake_pdf, "paper-items", "space-1", _quality())

    assert [(element.element_type, element.text, element.page_number) for element in document.elements] == [
        ("heading", "Methods", 2),
        ("paragraph", "We trained a model.", 2),
        ("table", "Name | Score\nA | 0.9", 2),
    ]
    assert document.tables[0].cells == [["Name", "Score"], ["A", "0.9"]]
    assert document.assets[0].uri == "https://assets.example/fig1.png"


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
        if request.url.path == "/api/v1/beta/files":
            return httpx.Response(200, json={"id": "file-123"})
        if request.url.path == "/api/v2/parse":
            return httpx.Response(200, json={"id": "job-123"})
        if request.url.path != "/api/v2/parse/job-123":
            return httpx.Response(404)
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
        base_url="https://llamaparse.test",
        http_client=client,
        max_poll_attempts=1,
        poll_interval_seconds=0,
    )

    document = backend.parse(fake_pdf, "paper-1", "space-1", _quality())

    assert seen["url"] == "https://llamaparse.test/api/v2/parse/job-123?expand=markdown&expand=items"
    assert seen["authorization"] == "Bearer llama-secret"

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

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/beta/files":
            return httpx.Response(200, json={"id": "file-md"})
        if request.url.path == "/api/v2/parse":
            return httpx.Response(200, json={"id": "job-md"})
        if request.url.path == "/api/v2/parse/job-md":
            return httpx.Response(
                200,
                json={
                    "job": {"id": "job-md", "status": "COMPLETED"},
                    "markdown": (
                        "# Title\n\n"
                        "Paragraph one.\n\n"
                        "## Results\n\n"
                        "| Name | Score |\n"
                        "| --- | --- |\n"
                        "| A | 1 |"
                    ),
                },
            )
        return httpx.Response(404)

    client = httpx.Client(
        transport=httpx.MockTransport(handler)
    )
    backend = backend_module.LlamaParseBackend(
        api_key="key",
        http_client=client,
        max_poll_attempts=1,
        poll_interval_seconds=0,
    )

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


def test_get_configured_backend_closes_or_avoids_unavailable_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable configured helper paths should not leak owned clients."""
    backend_module = _backend_module()
    instances: list[Any] = []

    class FakeUnavailableBackend:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            self.api_key = api_key
            self.base_url = base_url
            self.closed = False
            instances.append(self)

        def is_available(self) -> bool:
            return False

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        backend_module,
        "get_llamaparse_config",
        lambda: {
            "llamaparse_api_key": "",
            "llamaparse_base_url": "https://configured.example/v1",
        },
    )
    monkeypatch.setattr(backend_module, "LlamaParseBackend", FakeUnavailableBackend)

    backend = backend_module.get_configured_llamaparse_backend()

    assert backend is None
    assert instances == [] or instances[0].closed is True
