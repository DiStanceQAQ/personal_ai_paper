from pathlib import Path

from paper_engine.pdf.backends.base import (
    ParserBackendError,
    ParserBackendUnavailable,
    PdfParserBackend,
)
from paper_engine.pdf.models import ParseDocument, PdfQualityReport


class WorkingBackend:
    name = "working"

    def is_available(self) -> bool:
        return True

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        return ParseDocument(
            paper_id=paper_id,
            space_id=space_id,
            backend=self.name,
            extraction_method="native_text",
            quality=quality_report,
            metadata={"source": str(file_path)},
        )


def test_backend_protocol_accepts_expected_parser_shape() -> None:
    backend = WorkingBackend()
    quality_report = PdfQualityReport(page_count=3, native_text_pages=3)

    document = backend.parse(
        Path("paper.pdf"),
        paper_id="paper-1",
        space_id="space-1",
        quality_report=quality_report,
    )

    assert isinstance(backend, PdfParserBackend)
    assert backend.name == "working"
    assert backend.is_available() is True
    assert document.paper_id == "paper-1"
    assert document.space_id == "space-1"
    assert document.backend == "working"
    assert document.quality is quality_report
    assert document.metadata == {"source": "paper.pdf"}


def test_unavailable_exception_is_a_backend_error_with_backend_name() -> None:
    error = ParserBackendUnavailable("local-parser", "missing executable")

    assert isinstance(error, ParserBackendError)
    assert error.backend_name == "local-parser"
    assert str(error) == "local-parser backend unavailable: missing executable"


def test_backend_error_preserves_backend_name_and_message() -> None:
    error = ParserBackendError("local-parser", "parse failed")

    assert error.backend_name == "local-parser"
    assert str(error) == "local-parser backend error: parse failed"


def test_backend_error_accepts_original_cause() -> None:
    cause = RuntimeError("boom")
    error = ParserBackendError("local-parser", "parse failed", cause=cause)

    assert error.__cause__ is cause
