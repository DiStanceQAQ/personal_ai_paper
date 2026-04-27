from __future__ import annotations

import importlib
import importlib.util
import tomllib
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_declares_pymupdf4llm_runtime_dependency() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    dependencies = pyproject["project"]["dependencies"]

    assert "pymupdf4llm>=0.0.20" in dependencies


def test_pymupdf4llm_imports_when_environment_is_refreshed() -> None:
    if importlib.util.find_spec("pymupdf4llm") is None:
        pytest.skip(
            "pymupdf4llm is declared but not installed in this environment; "
            "refresh dependency installation/environment before running import checks."
        )

    module = importlib.import_module("pymupdf4llm")

    assert module is not None
