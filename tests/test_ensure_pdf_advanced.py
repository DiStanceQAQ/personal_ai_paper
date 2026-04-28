"""Tests for PDF advanced dependency bootstrapping."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

import scripts.ensure_pdf_advanced as ensure_pdf_advanced


def test_main_skips_install_when_docling_is_available(
    monkeypatch: Any,
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ensure_pdf_advanced, "docling_available", lambda: True)
    monkeypatch.setattr(ensure_pdf_advanced.subprocess, "run", fake_run)

    assert ensure_pdf_advanced.main(["--if-missing"]) == 0
    assert calls == []


def test_main_installs_pdf_advanced_when_docling_is_missing(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[list[str], Path, bool]] = []

    def fake_run(
        cmd: list[str],
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, cwd, check))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ensure_pdf_advanced, "ROOT", Path("/tmp/project"))
    monkeypatch.setattr(ensure_pdf_advanced, "docling_available", lambda: False)
    monkeypatch.setattr(ensure_pdf_advanced.subprocess, "run", fake_run)

    assert ensure_pdf_advanced.main(["--if-missing", "--python", "/tmp/python"]) == 0
    assert calls == [
        (
            ["/tmp/python", "-m", "pip", "install", "-e", ".[pdf-advanced]"],
            Path("/tmp/project"),
            True,
        )
    ]
