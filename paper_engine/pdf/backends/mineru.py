"""MinerU HTTP parser backend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, cast

import httpx

from paper_engine.pdf.backends.base import ParserBackendError, ParserBackendUnavailable
from paper_engine.pdf.models import (
    ElementType,
    ParseDocument,
    ParseElement,
    ParseTable,
    PdfQualityReport,
)

_BACKEND_NAME = "mineru"


class MinerUBackend:
    """Parse PDFs through a MinerU-compatible HTTP API."""

    name = _BACKEND_NAME

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        parse_path: str = "/file_parse",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.parse_path = parse_path if parse_path.startswith("/") else f"/{parse_path}"
        self._client = http_client

    def is_available(self) -> bool:
        """Return whether the backend has enough configuration to run."""
        return bool(self.base_url and self.api_key)

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        """Upload a PDF to MinerU and normalize the response."""
        if not self.is_available():
            raise ParserBackendUnavailable(
                self.name,
                "mineru_base_url or mineru_api_key is not configured",
            )

        client = self._client or httpx.Client(timeout=120)
        close_client = self._client is None
        try:
            with file_path.open("rb") as handle:
                response = client.post(
                    f"{self.base_url}{self.parse_path}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"files": (file_path.name, handle, "application/pdf")},
                    data={
                        "return_md": "true",
                        "return_content_list": "true",
                        "return_images": "true",
                        "table_enable": "true",
                        "formula_enable": "true",
                    },
                )
            response.raise_for_status()
            payload = response.json()
            return _payload_to_document(
                payload,
                paper_id,
                space_id,
                quality_report,
            )
        except ParserBackendUnavailable:
            raise
        except Exception as exc:
            raise ParserBackendError(self.name, "failed to parse PDF", cause=exc) from exc
        finally:
            if close_client:
                client.close()


def _payload_to_document(
    payload: Mapping[str, Any],
    paper_id: str,
    space_id: str,
    quality_report: PdfQualityReport,
) -> ParseDocument:
    result = _first_result(payload)
    content_list = _content_list(result)
    elements: list[ParseElement] = []
    tables: list[ParseTable] = []

    for index, item in enumerate(content_list):
        element_type = _element_type(str(item.get("type", "text")))
        text = str(item.get("text") or item.get("content") or "")
        if not text:
            continue
        page_number = int(item.get("page_idx", item.get("page", 0))) + 1
        element_id = f"p{page_number:04d}-e{len(elements):04d}"
        elements.append(
            ParseElement(
                id=element_id,
                element_index=len(elements),
                element_type=cast(ElementType, element_type),
                text=text,
                page_number=page_number,
                extraction_method="layout_model",
                metadata={"source": "mineru_content_list", "raw_index": index},
            )
        )
        if element_type == "table":
            table_index = len(tables)
            tables.append(
                ParseTable(
                    id=f"table-{table_index:04d}",
                    element_id=element_id,
                    table_index=table_index,
                    page_number=page_number,
                    caption="",
                    cells=_markdown_table_cells(text),
                    metadata={"source": "mineru_content_list"},
                )
            )

    if not elements:
        md_content = str(
            result.get("md_content")
            or result.get("markdown")
            or payload.get("content")
            or ""
        )
        elements = _markdown_to_elements(md_content)

    return ParseDocument(
        paper_id=paper_id,
        space_id=space_id,
        backend=_BACKEND_NAME,
        extraction_method="layout_model",
        quality=quality_report,
        elements=elements,
        tables=tables,
        metadata={
            "mineru": {
                "backend": payload.get("backend"),
                "version": payload.get("version"),
            }
        },
    )


def _first_result(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    results = payload.get("results")
    if isinstance(results, Mapping) and results:
        first = next(iter(results.values()))
        if isinstance(first, Mapping):
            return first
    return payload


def _content_list(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = result.get("content_list")
    if isinstance(raw, str):
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    return raw if isinstance(raw, list) else []


def _element_type(value: str) -> str:
    mapping = {
        "title": "title",
        "text": "paragraph",
        "paragraph": "paragraph",
        "table": "table",
        "image": "figure",
        "figure": "figure",
        "equation": "equation",
        "formula": "equation",
    }
    return mapping.get(value.lower(), "paragraph")


def _markdown_to_elements(markdown: str) -> list[ParseElement]:
    elements: list[ParseElement] = []
    for block in [part.strip() for part in markdown.split("\n\n") if part.strip()]:
        element_type = "heading" if block.startswith("#") else "paragraph"
        text = block.lstrip("#").strip()
        elements.append(
            ParseElement(
                id=f"p0001-e{len(elements):04d}",
                element_index=len(elements),
                element_type=cast(ElementType, element_type),
                text=text,
                page_number=1,
                extraction_method="layout_model",
            )
        )
    return elements


def _markdown_table_cells(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and not all(set(cell) <= {"-", ":"} for cell in cells):
            rows.append(cells)
    return rows
