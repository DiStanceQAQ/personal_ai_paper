"""Tests for the Tauri launcher wrapper."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_tauri_dev_dry_run_builds_api_sidecar_before_launch() -> None:
    """Desktop dev should prepare the local model and packaged API sidecar."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for launcher script validation")

    result = subprocess.run(
        [node, "scripts/run_tauri.mjs", "dev"],
        check=False,
        capture_output=True,
        env={
            "PAPER_ENGINE_TAURI_DRY_RUN": "1",
            "PATH": "",
        },
        text=True,
    )

    assert result.returncode == 0, result.stderr
    dry_run = json.loads(result.stdout)
    expected_python = str(Path(".venv/bin/python")) if Path(".venv/bin/python").exists() else "python"

    assert dry_run["pdfAdvancedInstall"] == {
        "command": expected_python,
        "args": ["scripts/ensure_pdf_advanced.py", "--if-missing"],
    }
    assert dry_run["modelDownload"] == {
        "command": expected_python,
        "args": ["scripts/download_embedding_model.py", "--if-missing"],
    }
    assert dry_run["sidecarBuild"] == {
        "command": expected_python,
        "args": ["scripts/build_sidecars.py", "--target", "api"],
    }
    assert dry_run["tauri"] == {
        "command": "tauri",
        "args": ["dev"],
    }
