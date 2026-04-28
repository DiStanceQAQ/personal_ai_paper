"""Ensure the optional PDF advanced dependencies are available."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]


def docling_available() -> bool:
    """Return whether Docling can be imported in the current environment."""
    return importlib.util.find_spec("docling") is not None


def install_pdf_advanced(
    python_executable: str,
    root: Path = ROOT,
) -> None:
    """Install the editable package with the pdf-advanced extra."""
    subprocess.run(
        [python_executable, "-m", "pip", "install", "-e", ".[pdf-advanced]"],
        cwd=root,
        check=True,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure Docling-backed PDF parsing dependencies are installed.",
    )
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help="Install only when Docling is not already importable.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used for the pip install command.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.if_missing and docling_available():
        print("Docling already available")
        return 0

    install_pdf_advanced(args.python, ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
