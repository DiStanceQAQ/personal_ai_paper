"""Import-time tests for the PDF parser module."""

import subprocess
import sys


def test_importing_parser_does_not_import_pymupdf() -> None:
    """Keep PyMuPDF out of the app startup import path."""
    code = (
        "import sys\n"
        "import paper_engine.pdf.compat\n"
        "raise SystemExit(1 if 'pymupdf' in sys.modules else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
