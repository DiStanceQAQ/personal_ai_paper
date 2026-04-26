"""Tests for packaged API sidecar entry point."""

import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import api_sidecar
import pytest
import uvicorn
from api_sidecar import ServerSettings, parse_args


def test_parse_args_uses_defaults() -> None:
    settings = parse_args([])
    assert settings == ServerSettings(
        host="127.0.0.1",
        port=8765,
        data_dir=None,
    )


def test_parse_args_accepts_port_and_data_dir(tmp_path: Path) -> None:
    settings = parse_args([
        "--host",
        "127.0.0.1",
        "--port",
        "9412",
        "--data-dir",
        str(tmp_path),
    ])
    assert settings.host == "127.0.0.1"
    assert settings.port == 9412
    assert settings.data_dir == tmp_path.resolve()


def test_main_sets_data_dir_before_importing_app(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_app = object()
    calls: list[Any] = []

    def fake_import_module(name: str) -> SimpleNamespace:
        assert name == "main"
        calls.append(os.environ.get("PAPER_ENGINE_DATA_DIR"))
        return SimpleNamespace(app=fake_app)

    def fake_run(app: object, host: str, port: int, log_level: str) -> None:
        calls.append((app, host, port, log_level))

    monkeypatch.delenv("PAPER_ENGINE_DATA_DIR", raising=False)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    monkeypatch.setattr(uvicorn, "run", fake_run)

    api_sidecar.main(["--port", "9412", "--data-dir", str(tmp_path)])

    assert calls == [
        str(tmp_path.resolve()),
        (fake_app, "127.0.0.1", 9412, "info"),
    ]


def test_startup_trace_is_quiet_by_default(
    monkeypatch: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PAPER_ENGINE_STARTUP_TRACE", raising=False)

    api_sidecar.startup_trace("main_entry")

    captured = capsys.readouterr()
    assert captured.err == ""


def test_startup_trace_writes_structured_timing(
    monkeypatch: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PAPER_ENGINE_STARTUP_TRACE", "1")

    api_sidecar.startup_trace("app_import_done", port=9412)

    captured = capsys.readouterr()
    assert "[paper-engine startup] python event=app_import_done" in captured.err
    assert "elapsed_ms=" in captured.err
    assert "port=9412" in captured.err
