"""Tests for packaged API sidecar entry point."""

import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import paper_engine.sidecar.api as api_sidecar
import pytest
import uvicorn
from paper_engine.sidecar.api import ServerSettings, parse_args


def test_parse_args_uses_defaults() -> None:
    settings = parse_args([])
    assert settings == ServerSettings(
        host="127.0.0.1",
        port=8765,
        data_dir=None,
        resource_dir=None,
    )


def test_parse_args_accepts_port_data_dir_and_resource_dir(tmp_path: Path) -> None:
    resource_dir = tmp_path / "resources"
    settings = parse_args([
        "--host",
        "127.0.0.1",
        "--port",
        "9412",
        "--data-dir",
        str(tmp_path),
        "--resource-dir",
        str(resource_dir),
    ])
    assert settings.host == "127.0.0.1"
    assert settings.port == 9412
    assert settings.data_dir == tmp_path.resolve()
    assert settings.resource_dir == resource_dir.resolve()


def test_main_sets_runtime_dirs_before_importing_app(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_app = object()
    calls: list[Any] = []

    def fake_import_module(name: str) -> SimpleNamespace:
        assert name == "paper_engine.api.app"
        calls.append((
            os.environ.get("PAPER_ENGINE_DATA_DIR"),
            os.environ.get("PAPER_ENGINE_RESOURCE_DIR"),
        ))
        return SimpleNamespace(app=fake_app)

    def fake_run(app: object, host: str, port: int, log_level: str) -> None:
        calls.append((app, host, port, log_level))

    monkeypatch.setattr(
        api_sidecar.multiprocessing,
        "freeze_support",
        lambda: calls.append("freeze_support"),
    )
    monkeypatch.delenv("PAPER_ENGINE_DATA_DIR", raising=False)
    monkeypatch.delenv("PAPER_ENGINE_RESOURCE_DIR", raising=False)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    monkeypatch.setattr(uvicorn, "run", fake_run)

    resource_dir = tmp_path / "resources"
    api_sidecar.main([
        "--port",
        "9412",
        "--data-dir",
        str(tmp_path),
        "--resource-dir",
        str(resource_dir),
    ])

    assert calls == [
        "freeze_support",
        (str(tmp_path.resolve()), str(resource_dir.resolve())),
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
