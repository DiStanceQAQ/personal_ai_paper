"""Tests for the main FastAPI application."""

import pytest
from httpx import ASGITransport, AsyncClient

import main
from main import app


@pytest.mark.asyncio
async def test_health_check() -> None:
    """Test that the health check endpoint returns healthy status."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "Local Paper Knowledge Engine"


@pytest.mark.asyncio
async def test_root_returns_html() -> None:
    """Test that the root endpoint returns HTML."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_cors_preflight_allows_tauri_dev_origin() -> None:
    """Allow the Tauri dev webview to call the sidecar API."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/api/spaces",
            headers={
                "Origin": "http://127.0.0.1:1420",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:1420"


def test_fastapi_startup_trace_is_quiet_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PAPER_ENGINE_STARTUP_TRACE", raising=False)

    main.startup_trace("lifespan_start")

    captured = capsys.readouterr()
    assert captured.err == ""


def test_fastapi_startup_trace_writes_structured_timing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PAPER_ENGINE_STARTUP_TRACE", "1")

    main.startup_trace("database_ready", init_db_ms=12)

    captured = capsys.readouterr()
    assert "[paper-engine startup] fastapi event=database_ready" in captured.err
    assert "elapsed_ms=" in captured.err
    assert "init_db_ms=12" in captured.err
