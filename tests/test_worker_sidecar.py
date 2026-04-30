"""Tests for packaged background worker sidecar entry point."""

from __future__ import annotations

import os
from pathlib import Path

import paper_engine.sidecar.worker as worker_sidecar
from paper_engine.sidecar.worker import WorkerSettings, configure_runtime_dirs, parse_args


def test_parse_args_uses_defaults() -> None:
    settings = parse_args([])
    assert settings == WorkerSettings(
        data_dir=None,
        resource_dir=None,
        parse_enabled=True,
        embedding_enabled=True,
        analysis_enabled=True,
    )


def test_parse_args_accepts_dirs_and_disabled_workers(tmp_path: Path) -> None:
    resource_dir = tmp_path / "resources"
    settings = parse_args([
        "--data-dir",
        str(tmp_path),
        "--resource-dir",
        str(resource_dir),
        "--no-analysis",
    ])

    assert settings.data_dir == tmp_path.resolve()
    assert settings.resource_dir == resource_dir.resolve()
    assert settings.parse_enabled is True
    assert settings.embedding_enabled is True
    assert settings.analysis_enabled is False


def test_configure_runtime_dirs_sets_model_cache_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    resource_dir = tmp_path / "resources"
    (resource_dir / "models" / "docling-hf-cache" / "hub").mkdir(parents=True)
    monkeypatch.delenv("PAPER_ENGINE_DATA_DIR", raising=False)
    monkeypatch.delenv("PAPER_ENGINE_RESOURCE_DIR", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)

    configure_runtime_dirs(
        WorkerSettings(
            data_dir=tmp_path,
            resource_dir=resource_dir,
            parse_enabled=True,
            embedding_enabled=True,
            analysis_enabled=True,
        )
    )

    assert os.environ["PAPER_ENGINE_DATA_DIR"] == str(tmp_path)
    assert os.environ["PAPER_ENGINE_RESOURCE_DIR"] == str(resource_dir)
    assert os.environ["HF_HOME"] == str(resource_dir / "models" / "docling-hf-cache")
    assert os.environ["HF_HUB_CACHE"] == str(
        resource_dir / "models" / "docling-hf-cache" / "hub"
    )


def test_startup_trace_is_quiet_by_default(monkeypatch, capsys) -> None:
    monkeypatch.delenv("PAPER_ENGINE_STARTUP_TRACE", raising=False)

    worker_sidecar.startup_trace("main_entry")

    captured = capsys.readouterr()
    assert captured.err == ""
