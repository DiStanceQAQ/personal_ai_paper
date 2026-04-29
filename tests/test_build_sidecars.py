"""Tests for PyInstaller sidecar build commands."""

import importlib.metadata
import importlib.util
import json
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
        "paper_engine/sidecar/api.py",
        hidden_imports=("paper_engine.api.app",),
    )

    assert "--hidden-import" in calls[0]
    assert "paper_engine.api.app" in calls[0]


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
        "paper_engine/sidecar/api.py",
        collect_submodules=("pymupdf4llm",),
        excluded_modules=("docling",),
    )

    command = calls[0]
    assert "--collect-submodules" in command
    assert "pymupdf4llm" in command
    assert "--exclude-module" in command
    assert "docling" in command


def test_build_onefile_passes_collected_data(
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
        "paper_engine/sidecar/api.py",
        collect_data=("docling_parse",),
    )

    command = calls[0]
    assert "--collect-data" in command
    assert "docling_parse" in command


def test_build_onefile_passes_copy_metadata(
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
        "paper_engine/sidecar/api.py",
        copy_metadata=("docling", "docling-core"),
    )

    command = calls[0]
    assert command.count("--copy-metadata") == 2
    assert "docling" in command
    assert "docling-core" in command


def test_api_sidecar_target_includes_upgraded_pdf_pipeline_modules() -> None:
    targets = build_sidecars.build_targets("api")
    api_target = targets[0]

    expected_modules = {
        "paper_engine.storage.migrations",
        "paper_engine.analysis.models",
        "paper_engine.analysis.pipeline",
        "paper_engine.analysis.prompts",
        "paper_engine.analysis.verifier",
        "paper_engine.retrieval.embeddings",
        "paper_engine.retrieval.hybrid",
        "paper_engine.pdf.backends.base",
        "paper_engine.pdf.backends.docling",
        "paper_engine.pdf.backends.grobid",
        "paper_engine.pdf.backends.legacy",
        "paper_engine.pdf.backends.llamaparse",
        "paper_engine.pdf.backends.pymupdf4llm",
        "paper_engine.pdf.chunking",
        "paper_engine.pdf.models",
        "paper_engine.pdf.persistence",
        "paper_engine.pdf.profile",
        "paper_engine.pdf.router",
        "pymupdf",
        "pymupdf4llm",
        "tiktoken",
    }

    assert api_target.sidecar_name == "paper-engine-api"
    assert expected_modules.issubset(set(api_target.hidden_imports))


def test_default_targets_collect_required_embeddings_and_exclude_uninstalled_pdf_dependencies(
    monkeypatch: Any,
) -> None:
    def fake_find_spec(name: str) -> object | None:
        if name in {
            "docling",
            "docling_ibm_models",
            "docling_parse",
            "transformers.models.rt_detr_v2",
            "sentence_transformers",
        }:
            return None
        return importlib.util.find_spec(name)

    monkeypatch.setattr(build_sidecars.importlib.util, "find_spec", fake_find_spec)

    targets = build_sidecars.build_targets("all")

    for target in targets:
        assert "docling" not in target.collect_submodules
        assert "docling_ibm_models" not in target.collect_submodules
        assert "transformers.models.rt_detr_v2" not in target.collect_submodules
        assert "sentence_transformers" in target.collect_submodules
        assert "docling_parse" not in target.collect_data
        assert "docling" in target.excluded_modules
        assert "docling_ibm_models" in target.excluded_modules
        assert "transformers.models.rt_detr_v2" in target.excluded_modules
        assert "sentence_transformers" not in target.excluded_modules


def test_installed_optional_dependencies_are_collected_when_available(
    monkeypatch: Any,
) -> None:
    def fake_find_spec(name: str) -> object | None:
        if name in {
            "docling",
            "docling_ibm_models",
            "docling_parse",
            "transformers.models.rt_detr_v2",
            "sentence_transformers",
        }:
            return object()
        return importlib.util.find_spec(name)

    monkeypatch.setattr(build_sidecars.importlib.util, "find_spec", fake_find_spec)

    targets = build_sidecars.build_targets("all")
    targets_by_name = {target.sidecar_name: target for target in targets}

    api_target = targets_by_name["paper-engine-api"]
    assert "docling" in api_target.collect_submodules
    assert "docling_ibm_models" in api_target.collect_submodules
    assert "transformers.models.rt_detr_v2" in api_target.collect_submodules
    assert "sentence_transformers" in api_target.collect_submodules
    assert "docling_parse" in api_target.collect_data
    assert "docling" not in api_target.excluded_modules
    assert "docling_ibm_models" not in api_target.excluded_modules
    assert "transformers.models.rt_detr_v2" not in api_target.excluded_modules
    assert "sentence_transformers" not in api_target.excluded_modules

    mcp_target = targets_by_name["paper-engine-mcp"]
    assert "docling" not in mcp_target.collect_submodules
    assert "docling_ibm_models" not in mcp_target.collect_submodules
    assert "transformers.models.rt_detr_v2" not in mcp_target.collect_submodules
    assert "sentence_transformers" in mcp_target.collect_submodules
    assert "docling_parse" not in mcp_target.collect_data
    assert "docling" in mcp_target.excluded_modules
    assert "docling_ibm_models" in mcp_target.excluded_modules
    assert "transformers.models.rt_detr_v2" in mcp_target.excluded_modules
    assert "sentence_transformers" not in mcp_target.excluded_modules


def test_optional_metadata_is_copied_when_distributions_exist(
    monkeypatch: Any,
) -> None:
    class FakeDistribution:
        pass

    def fake_distribution(name: str) -> object:
        if name in {"docling", "docling-core", "docling-ibm-models", "docling-parse"}:
            return FakeDistribution()
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(build_sidecars.importlib.metadata, "distribution", fake_distribution)

    targets = build_sidecars.build_targets("api")
    api_target = targets[0]

    assert api_target.copy_metadata == (
        "docling",
        "docling-core",
        "docling-ibm-models",
        "docling-parse",
    )


def test_pyproject_uses_backend_package_discovery() -> None:
    pyproject = tomllib.loads((build_sidecars.ROOT / "pyproject.toml").read_text())

    find_config = pyproject["tool"]["setuptools"]["packages"]["find"]
    assert find_config["include"] == ["paper_engine*"]
    assert "py-modules" not in pyproject["tool"]["setuptools"]


def test_sentence_transformers_is_a_required_dependency() -> None:
    pyproject = tomllib.loads((build_sidecars.ROOT / "pyproject.toml").read_text())

    assert "sentence-transformers>=2.7.0" in pyproject["project"]["dependencies"]


def test_tauri_bundle_includes_local_embedding_model_resource() -> None:
    tauri_config = json.loads((build_sidecars.ROOT / "src-tauri/tauri.conf.json").read_text())

    assert tauri_config["bundle"]["resources"] == {
        "../resources/models/docling-hf-cache": "models/docling-hf-cache",
        "../resources/models/intfloat-multilingual-e5-small": (
            "models/intfloat-multilingual-e5-small"
        )
    }
