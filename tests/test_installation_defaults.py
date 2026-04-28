"""Tests for default developer install commands."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_make_install_includes_pdf_advanced_extra() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert 'pip install -e ".[dev,pdf-advanced]"' in makefile
