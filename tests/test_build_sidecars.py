"""Tests for PyInstaller sidecar build commands."""

import subprocess
from pathlib import Path
from typing import Any

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
