"""Shared interface and exceptions for PDF parser backends."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from pdf_models import ParseDocument, PdfQualityReport


@runtime_checkable
class PdfParserBackend(Protocol):
    """Protocol implemented by concrete PDF parser backends."""

    name: str

    def is_available(self) -> bool:
        """Return whether the backend can parse PDFs in the current environment."""

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        """Parse a PDF into the normalized document contract."""


class ParserBackendError(Exception):
    """Base exception raised by parser backend implementations."""

    def __init__(
        self,
        backend_name: str,
        message: str,
        *,
        cause: BaseException | None = None,
    ) -> None:
        self.backend_name = backend_name
        super().__init__(f"{backend_name} backend error: {message}")
        if cause is not None:
            self.__cause__ = cause


class ParserBackendUnavailable(ParserBackendError):
    """Raised when a parser backend cannot run in the current environment."""

    def __init__(
        self,
        backend_name: str,
        message: str,
        *,
        cause: BaseException | None = None,
    ) -> None:
        Exception.__init__(self, f"{backend_name} backend unavailable: {message}")
        self.backend_name = backend_name
        if cause is not None:
            self.__cause__ = cause
