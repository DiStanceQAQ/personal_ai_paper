"""Tests for the built-in LLM agent executor module."""

from __future__ import annotations

import py_compile
from pathlib import Path


def test_agent_executor_module_compiles() -> None:
    """Agent executor must remain importable for PyInstaller sidecar packaging."""
    py_compile.compile(str(Path("agent_executor.py")), doraise=True)
