"""Tests for PyInstaller sidecar build commands."""

import importlib.util
import subprocess
from pathlib import Path
from typing import Any
import tomllib

from scripts import build_sidecars


def test_build_onefile_passes_hidden_imports(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        binary = tmp_path / "dist" / "paper-engine-api"
        binary.parent.mkdir()
        binary.write_text("fake binary")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(build_sidecars, "ROOT", tmp_path)
    monkeypatch.setattr("scripts.build_sidecars.subprocess.run", fake_run)

    build_sidecars.build_onefile(
        "paper-engine-api",
        "api_sidecar.py",
        hidden_imports=("main",),
    )

    assert "--hidden-import" in calls[0]
    assert "main" in calls[0]


def test_build_onefile_passes_excluded_modules_and_collected_submodules(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        binary = tmp_path / "dist" / "paper-engine-api"
        binary.parent.mkdir()
        binary.write_text("fake binary")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(build_sidecars, "ROOT", tmp_path)
    monkeypatch.setattr("scripts.build_sidecars.subprocess.run", fake_run)

    build_sidecars.build_onefile(
        "paper-engine-api",
        "api_sidecar.py",
        collect_submodules=("pymupdf4llm",),
        excluded_modules=("docling",),
    )

    command = calls[0]
    assert "--collect-submodules" in command
    assert "pymupdf4llm" in command
    assert "--exclude-module" in command
    assert "docling" in command


def test_api_sidecar_target_includes_upgraded_pdf_pipeline_modules() -> None:
    targets = build_sidecars.build_targets("api")
    api_target = targets[0]

    expected_modules = {
        "db_migrations",
        "analysis_models",
        "analysis_pipeline",
        "analysis_prompts",
        "analysis_verifier",
        "embeddings",
        "hybrid_search",
        "pdf_backend_base",
        "pdf_backend_docling",
        "pdf_backend_grobid",
        "pdf_backend_legacy",
        "pdf_backend_llamaparse",
        "pdf_backend_pymupdf4llm",
        "pdf_chunker",
        "pdf_models",
        "pdf_persistence",
        "pdf_profile",
        "pdf_router",
        "pymupdf",
        "pymupdf4llm",
        "tiktoken",
    }

    assert api_target.sidecar_name == "paper-engine-api"
    assert expected_modules.issubset(set(api_target.hidden_imports))


def test_default_targets_exclude_uninstalled_heavy_optional_dependencies(
    monkeypatch: Any,
) -> None:
    def fake_find_spec(name: str) -> object | None:
        if name in {"docling", "sentence_transformers"}:
            return None
        return importlib.util.find_spec(name)

    monkeypatch.setattr(build_sidecars.importlib.util, "find_spec", fake_find_spec)

    targets = build_sidecars.build_targets("all")

    for target in targets:
        assert "docling" not in target.collect_submodules
        assert "sentence_transformers" not in target.collect_submodules
        assert "docling" in target.excluded_modules
        assert "sentence_transformers" in target.excluded_modules


def test_installed_optional_dependencies_are_collected_when_available(
    monkeypatch: Any,
) -> None:
    def fake_find_spec(name: str) -> object | None:
        if name in {"docling", "sentence_transformers"}:
            return object()
        return importlib.util.find_spec(name)

    monkeypatch.setattr(build_sidecars.importlib.util, "find_spec", fake_find_spec)

    targets = build_sidecars.build_targets("all")
    targets_by_name = {target.sidecar_name: target for target in targets}

    api_target = targets_by_name["paper-engine-api"]
    assert "docling" in api_target.collect_submodules
    assert "sentence_transformers" in api_target.collect_submodules
    assert "docling" not in api_target.excluded_modules
    assert "sentence_transformers" not in api_target.excluded_modules

    mcp_target = targets_by_name["paper-engine-mcp"]
    assert "docling" not in mcp_target.collect_submodules
    assert "sentence_transformers" in mcp_target.collect_submodules
    assert "docling" in mcp_target.excluded_modules
    assert "sentence_transformers" not in mcp_target.excluded_modules


def test_pyproject_packages_database_migrations_module() -> None:
    pyproject = tomllib.loads((build_sidecars.ROOT / "pyproject.toml").read_text())

    assert "db_migrations" in pyproject["tool"]["setuptools"]["py-modules"]
