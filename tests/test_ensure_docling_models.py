"""Tests for Docling model bootstrapping."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import scripts.ensure_docling_models as ensure_docling_models


def test_main_skips_when_required_docling_models_are_present(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, Path]] = []

    monkeypatch.setattr(
        ensure_docling_models,
        "docling_models_ready",
        lambda target: True,
    )
    monkeypatch.setattr(
        ensure_docling_models,
        "download_required_docling_models",
        lambda target: calls.append(("download", target)),
    )

    assert ensure_docling_models.main(["--if-missing", "--target", "/tmp/docling-hub"]) == 0
    assert calls == []


def test_main_downloads_layout_and_table_models_when_missing(
    monkeypatch: Any,
) -> None:
    calls: list[Path] = []

    monkeypatch.setattr(
        ensure_docling_models,
        "docling_models_ready",
        lambda target: False,
    )
    monkeypatch.setattr(
        ensure_docling_models,
        "download_required_docling_models",
        lambda target: calls.append(target),
    )

    assert ensure_docling_models.main(["--if-missing", "--target", "/tmp/docling-hub"]) == 0
    assert calls == [Path("/tmp/docling-hub")]
