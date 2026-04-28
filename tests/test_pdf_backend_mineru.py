from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from paper_engine.pdf.backends.base import ParserBackendError, ParserBackendUnavailable
from paper_engine.pdf.backends.mineru import MinerUBackend
from paper_engine.pdf.models import PdfQualityReport


def test_backend_unavailable_without_base_url_or_key() -> None:
    assert MinerUBackend(base_url="", api_key="").is_available() is False
    with pytest.raises(ParserBackendUnavailable):
        MinerUBackend(base_url="", api_key="").parse(
            Path("paper.pdf"),
            "paper-1",
            "space-1",
            PdfQualityReport(),
        )


def test_backend_posts_pdf_to_file_parse_and_normalizes_markdown(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/file_parse"
        assert request.headers["authorization"] == "Bearer mineru-secret"
        assert b"%PDF-1.4" in request.content
        return httpx.Response(
            200,
            json={
                "backend": "pipeline",
                "version": "2.7.6",
                "results": {
                    "paper": {
                        "md_content": "# Parsed Title\n\nParsed paragraph.",
                        "content_list": json.dumps(
                            [
                                {
                                    "type": "title",
                                    "text": "Parsed Title",
                                    "page_idx": 0,
                                },
                                {
                                    "type": "text",
                                    "text": "Parsed paragraph.",
                                    "page_idx": 0,
                                },
                                {
                                    "type": "table",
                                    "text": "A | B\n1 | 2",
                                    "page_idx": 0,
                                },
                            ]
                        ),
                    }
                },
            },
        )

    backend = MinerUBackend(
        base_url="http://mineru.test",
        api_key="mineru-secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    document = backend.parse(
        pdf_path,
        "paper-1",
        "space-1",
        PdfQualityReport(page_count=1),
    )

    assert len(requests) == 1
    assert document.backend == "mineru"
    assert document.extraction_method == "layout_model"
    assert [element.element_type for element in document.elements] == [
        "title",
        "paragraph",
        "table",
    ]
    assert document.tables[0].cells == [["A", "B"], ["1", "2"]]
    assert document.metadata["mineru"]["version"] == "2.7.6"


def test_backend_raises_parser_error_for_http_failure(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    backend = MinerUBackend(
        base_url="http://mineru.test",
        api_key="secret",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, json={"error": "boom"})
            )
        ),
    )

    with pytest.raises(ParserBackendError):
        backend.parse(pdf_path, "paper-1", "space-1", PdfQualityReport())
