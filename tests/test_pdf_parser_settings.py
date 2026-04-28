import sqlite3

import httpx
import pytest

from paper_engine.pdf.settings import (
    DEFAULT_PDF_PARSER_BACKEND,
    ParserSettingsUpdate,
    get_parser_settings,
    parser_availability,
    save_parser_settings,
    test_mineru_connection as check_mineru_connection,
)
from paper_engine.storage.repositories.settings import set_setting


def _settings_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"
    )
    return conn


def test_default_parser_settings_use_docling() -> None:
    conn = _settings_conn()

    settings = get_parser_settings(conn)

    assert settings.pdf_parser_backend == DEFAULT_PDF_PARSER_BACKEND
    assert settings.pdf_parser_backend == "docling"
    assert settings.mineru_base_url == ""
    assert settings.has_mineru_api_key is False


def test_save_parser_settings_preserves_existing_empty_secret() -> None:
    conn = _settings_conn()
    set_setting(conn, "mineru_api_key", "old-secret")

    save_parser_settings(
        conn,
        ParserSettingsUpdate(
            pdf_parser_backend="mineru",
            mineru_base_url="http://mineru.test",
            mineru_api_key="",
        ),
    )

    settings = get_parser_settings(conn)
    assert settings.pdf_parser_backend == "mineru"
    assert settings.mineru_base_url == "http://mineru.test"
    assert settings.has_mineru_api_key is True
    assert (
        conn.execute(
            "SELECT value FROM app_state WHERE key = 'mineru_api_key'"
        ).fetchone()["value"]
        == "old-secret"
    )


def test_parser_availability_reports_missing_docling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paper_engine.pdf.settings.DoclingBackend.is_available",
        lambda self: False,
    )

    availability = parser_availability()

    assert availability["docling"]["available"] is False
    assert availability["docling"]["install_hint"] == 'pip install -e ".[pdf-advanced]"'


def test_mineru_connection_distinguishes_missing_credentials() -> None:
    conn = _settings_conn()

    result = check_mineru_connection(conn)

    assert result["status"] == "missing_credentials"


def test_mineru_connection_uses_health_endpoint() -> None:
    conn = _settings_conn()
    set_setting(conn, "mineru_base_url", "http://mineru.test")
    set_setting(conn, "mineru_api_key", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"status": "ok"})

    result = check_mineru_connection(
        conn,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert result["status"] == "ok"
