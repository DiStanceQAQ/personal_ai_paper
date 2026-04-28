"""PDF parser setting helpers."""

from __future__ import annotations

import sqlite3
from typing import Literal, TypedDict, cast

import httpx
from pydantic import BaseModel, Field

from paper_engine.pdf.backends.docling import DoclingBackend
from paper_engine.storage.repositories.settings import get_setting, set_setting

PdfParserBackendName = Literal["mineru", "docling"]
DEFAULT_PDF_PARSER_BACKEND: PdfParserBackendName = "docling"


class ParserSettings(BaseModel):
    """Frontend-visible PDF parser settings."""

    pdf_parser_backend: PdfParserBackendName = DEFAULT_PDF_PARSER_BACKEND
    mineru_base_url: str = ""
    has_mineru_api_key: bool = False
    parsers: dict[str, dict[str, object]] = Field(default_factory=dict)


class ParserSettingsUpdate(BaseModel):
    """Partial update payload for parser settings."""

    pdf_parser_backend: PdfParserBackendName | None = None
    mineru_base_url: str | None = None
    mineru_api_key: str | None = None


class MinerUConnectionResult(TypedDict):
    """Result of a MinerU configuration check."""

    status: str
    detail: str


def normalize_parser_backend(value: str) -> PdfParserBackendName:
    """Normalize stored parser setting values to the supported enum."""
    normalized = value.strip().lower()
    if normalized in {"mineru", "docling"}:
        return cast(PdfParserBackendName, normalized)
    return DEFAULT_PDF_PARSER_BACKEND


def get_parser_settings(conn: sqlite3.Connection) -> ParserSettings:
    """Return parser settings without exposing stored secrets."""
    backend = normalize_parser_backend(get_setting(conn, "pdf_parser_backend"))
    base_url = get_setting(conn, "mineru_base_url").rstrip("/")
    has_key = bool(get_setting(conn, "mineru_api_key"))
    availability = parser_availability()
    availability["mineru"]["configured"] = bool(base_url and has_key)
    return ParserSettings(
        pdf_parser_backend=backend,
        mineru_base_url=base_url,
        has_mineru_api_key=has_key,
        parsers=availability,
    )


def save_parser_settings(
    conn: sqlite3.Connection,
    update: ParserSettingsUpdate,
) -> None:
    """Persist parser settings while preserving empty secret updates."""
    if update.pdf_parser_backend is not None:
        set_setting(
            conn,
            "pdf_parser_backend",
            normalize_parser_backend(update.pdf_parser_backend),
        )
    if update.mineru_base_url is not None:
        set_setting(conn, "mineru_base_url", update.mineru_base_url.rstrip("/"))
    if update.mineru_api_key:
        set_setting(conn, "mineru_api_key", update.mineru_api_key)


def parser_availability() -> dict[str, dict[str, object]]:
    """Return parser availability details for settings UI warnings."""
    docling_available = DoclingBackend().is_available()
    return {
        "docling": {
            "available": docling_available,
            "install_hint": "" if docling_available else 'pip install -e ".[pdf-advanced]"',
        },
        "mineru": {
            "configured": False,
            "last_check_status": "unknown",
        },
    }


def test_mineru_connection(
    conn: sqlite3.Connection,
    *,
    http_client: httpx.Client | None = None,
) -> MinerUConnectionResult:
    """Check configured MinerU connectivity without blocking settings save."""
    base_url = get_setting(conn, "mineru_base_url").rstrip("/")
    api_key = get_setting(conn, "mineru_api_key")
    if not base_url or not api_key:
        return {
            "status": "missing_credentials",
            "detail": "MinerU Base URL and API Key are required",
        }

    client = http_client or httpx.Client(timeout=10)
    close_client = http_client is None
    try:
        response = client.get(
            f"{base_url}/health",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if response.status_code < 400:
            return {"status": "ok", "detail": "MinerU health check succeeded"}
        return {
            "status": "http_error",
            "detail": f"MinerU health check returned HTTP {response.status_code}",
        }
    except httpx.HTTPError as exc:
        return {"status": "network_error", "detail": str(exc)}
    finally:
        if close_client:
            client.close()
