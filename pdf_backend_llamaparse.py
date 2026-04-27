"""Optional LlamaParse-backed PDF parser implementation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import httpx

from db import get_connection
from pdf_backend_base import ParserBackendError, ParserBackendUnavailable
from pdf_models import (
    BBox,
    ElementType,
    ParseAsset,
    ParseDocument,
    ParseElement,
    ParseTable,
    PdfQualityReport,
)


BACKEND_NAME = "llamaparse"
DEFAULT_BASE_URL = "https://api.cloud.llamaindex.ai/api/parsing"
DEFAULT_TIMEOUT = 120.0

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$")


class LlamaParseBackend:
    """Parse PDF files through a configured LlamaParse HTTP endpoint."""

    name = BACKEND_NAME

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout)

    def is_available(self) -> bool:
        """Return whether the backend has enough configuration to parse."""
        return bool(self.api_key and self.base_url)

    def close(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_client:
            self._client.close()

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        """Parse a PDF into the normalized parser contract."""
        if not self.is_available():
            raise ParserBackendUnavailable(self.name, "llamaparse_api_key is not configured")

        try:
            response = self._post_pdf(Path(file_path))
            payload = response.json()
        except ParserBackendUnavailable:
            raise
        except httpx.HTTPStatusError as exc:
            raise ParserBackendError(self.name, "LlamaParse request failed", cause=exc) from exc
        except (OSError, httpx.HTTPError) as exc:
            raise ParserBackendError(self.name, "LlamaParse request failed", cause=exc) from exc
        except ValueError as exc:
            raise ParserBackendError(self.name, "LlamaParse returned invalid JSON", cause=exc) from exc

        try:
            return _payload_to_document(
                payload,
                paper_id=paper_id,
                space_id=space_id,
                quality_report=quality_report,
            )
        except Exception as exc:
            raise ParserBackendError(
                self.name,
                "failed to normalize LlamaParse output",
                cause=exc,
            ) from exc

    def _post_pdf(self, file_path: Path) -> httpx.Response:
        with file_path.open("rb") as pdf_file:
            response = self._client.post(
                f"{self.base_url}/parse",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (file_path.name, pdf_file, "application/pdf")},
            )
        response.raise_for_status()
        return response


def get_llamaparse_config() -> dict[str, str]:
    """Return stored LlamaParse configuration with defaults for missing values."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM app_state WHERE key IN (?, ?)",
            ("llamaparse_api_key", "llamaparse_base_url"),
        ).fetchall()
    finally:
        conn.close()

    values = {str(row["key"]): str(row["value"]) for row in rows}
    return {
        "llamaparse_api_key": values.get("llamaparse_api_key", "").strip(),
        "llamaparse_base_url": values.get("llamaparse_base_url", DEFAULT_BASE_URL).strip(),
    }


def get_configured_llamaparse_backend() -> LlamaParseBackend | None:
    """Return a configured optional backend, or None when disabled."""
    config = get_llamaparse_config()
    backend = LlamaParseBackend(
        api_key=config["llamaparse_api_key"],
        base_url=config["llamaparse_base_url"],
    )
    return backend if backend.is_available() else None


class _DocumentBuilder:
    def __init__(
        self,
        *,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
        payload: Mapping[str, Any],
    ) -> None:
        self.paper_id = paper_id
        self.space_id = space_id
        self.quality_report = quality_report
        self.payload = payload
        self.elements: list[ParseElement] = []
        self.tables: list[ParseTable] = []
        self.assets: list[ParseAsset] = []
        self._heading_path: list[str] = []

    def build(self) -> ParseDocument:
        pages = _pages_from_payload(self.payload)
        for page_index, page in enumerate(pages):
            page_number = _page_number(page, page_index)
            markdown = _page_markdown(page)
            page_table_start = len(self.tables)
            self._add_markdown(page_number, markdown)
            if len(self.tables) == page_table_start:
                self._add_page_tables(page_number, _as_list(page.get("tables")))
            self._add_page_assets(page_number, _as_list(page.get("images")), "image")
            self._add_page_assets(page_number, _as_list(page.get("figures")), "figure")

        metadata: dict[str, Any] = {
            "page_count": len(pages),
            "parser": BACKEND_NAME,
        }
        for key in ("job_id", "id"):
            value = self.payload.get(key)
            if value:
                metadata["job_id"] = str(value)
                break

        return ParseDocument(
            paper_id=self.paper_id,
            space_id=self.space_id,
            backend=BACKEND_NAME,
            extraction_method="llm_parser",
            quality=self.quality_report,
            elements=self.elements,
            tables=self.tables,
            assets=self.assets,
            metadata=metadata,
        )

    def _add_markdown(self, page_number: int, markdown: str) -> None:
        for block in _markdown_blocks(markdown):
            if _is_markdown_table(block):
                cells = _markdown_table_cells(block)
                element = self._add_element(
                    element_type="table",
                    text=_clean_markdown_text(block),
                    page_number=page_number,
                    metadata={"source": "markdown_table"},
                )
                self._add_table(
                    page_number=page_number,
                    element_id=element.id,
                    cells=cells,
                    metadata={"source": "markdown_table"},
                )
                continue

            heading_match = _HEADING_RE.match(block)
            if heading_match:
                text = heading_match.group(2).strip()
                self._heading_path = [text]
                self._add_element(
                    element_type="heading",
                    text=text,
                    page_number=page_number,
                    metadata={"source": "markdown"},
                )
                continue

            text = _clean_markdown_text(block)
            if text:
                self._add_element(
                    element_type="paragraph",
                    text=text,
                    page_number=page_number,
                    metadata={"source": "markdown"},
                )

    def _add_page_tables(self, page_number: int, raw_tables: list[Any]) -> None:
        for raw_table in raw_tables:
            if not isinstance(raw_table, Mapping):
                continue
            cells = _table_cells(raw_table)
            caption = str(raw_table.get("caption") or "")
            table_text = caption or _cells_to_text(cells)
            element = self._add_element(
                element_type="table",
                text=table_text,
                page_number=page_number,
                bbox=_bbox(raw_table.get("bbox")),
                metadata={"source": "json_table"},
            )
            self._add_table(
                page_number=page_number,
                element_id=element.id,
                cells=cells,
                caption=caption,
                bbox=_bbox(raw_table.get("bbox")),
                metadata={"source": "json_table"},
            )

    def _add_page_assets(
        self,
        page_number: int,
        raw_assets: list[Any],
        asset_type: str,
    ) -> None:
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, Mapping):
                continue
            uri = str(raw_asset.get("uri") or raw_asset.get("url") or "")
            self.assets.append(
                ParseAsset(
                    id=_asset_id(self.paper_id, len(self.assets)),
                    element_id=None,
                    asset_type=asset_type,
                    page_number=page_number,
                    uri=uri,
                    bbox=_bbox(raw_asset.get("bbox")),
                    metadata={"source": "json_asset"},
                )
            )

    def _add_element(
        self,
        *,
        element_type: ElementType,
        text: str,
        page_number: int,
        bbox: BBox | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ParseElement:
        element = ParseElement(
            id=_element_id(self.paper_id, len(self.elements)),
            element_index=len(self.elements),
            element_type=element_type,
            text=text,
            page_number=page_number,
            bbox=bbox,
            heading_path=[] if element_type == "heading" else list(self._heading_path),
            extraction_method="llm_parser",
            metadata=metadata or {},
        )
        self.elements.append(element)
        return element

    def _add_table(
        self,
        *,
        page_number: int,
        element_id: str,
        cells: list[list[str]],
        caption: str = "",
        bbox: BBox | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.tables.append(
            ParseTable(
                id=_table_id(self.paper_id, len(self.tables)),
                element_id=element_id,
                table_index=len(self.tables),
                page_number=page_number,
                caption=caption,
                cells=cells,
                bbox=bbox,
                metadata=metadata or {},
            )
        )


def _payload_to_document(
    payload: Any,
    *,
    paper_id: str,
    space_id: str,
    quality_report: PdfQualityReport,
) -> ParseDocument:
    if not isinstance(payload, Mapping):
        raise ValueError("LlamaParse response must be a JSON object")
    return _DocumentBuilder(
        paper_id=paper_id,
        space_id=space_id,
        quality_report=quality_report,
        payload=payload,
    ).build()


def _pages_from_payload(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    pages = payload.get("pages")
    if isinstance(pages, list):
        return [page for page in pages if isinstance(page, Mapping)]

    data = payload.get("data")
    if isinstance(data, list):
        return [page for page in data if isinstance(page, Mapping)]

    markdown = payload.get("markdown") or payload.get("text")
    if markdown is not None:
        return [{"page": 1, "markdown": str(markdown)}]

    raise ValueError("LlamaParse response did not include pages or markdown")


def _page_number(page: Mapping[str, Any], page_index: int) -> int:
    value = page.get("page") or page.get("page_number") or page.get("pageNumber")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return page_index + 1


def _page_markdown(page: Mapping[str, Any]) -> str:
    value = page.get("markdown") or page.get("md") or page.get("text") or ""
    return str(value)


def _markdown_blocks(markdown: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    table_lines: list[str] = []

    def flush_current() -> None:
        if current:
            block = "\n".join(current).strip()
            if block:
                blocks.append(block)
            current.clear()

    def flush_table() -> None:
        if table_lines:
            block = "\n".join(table_lines).strip()
            if block:
                blocks.append(block)
            table_lines.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if _is_table_line(line):
            flush_current()
            table_lines.append(line)
            continue
        flush_table()
        if not line.strip():
            flush_current()
            continue
        current.append(line)

    flush_table()
    flush_current()
    return blocks


def _is_markdown_table(block: str) -> bool:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    return len(lines) >= 2 and _TABLE_SEPARATOR_RE.match(lines[1]) is not None


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def _markdown_table_cells(block: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or _TABLE_SEPARATOR_RE.match(stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        rows.append(cells)
    return rows


def _table_cells(raw_table: Mapping[str, Any]) -> list[list[str]]:
    raw_rows = raw_table.get("rows")
    if raw_rows is None:
        raw_rows = raw_table.get("cells")
    if not isinstance(raw_rows, list):
        return []

    rows: list[list[str]] = []
    for row in raw_rows:
        if isinstance(row, list):
            rows.append([str(cell) for cell in row])
    return rows


def _cells_to_text(cells: list[list[str]]) -> str:
    return "\n".join(" | ".join(row) for row in cells)


def _clean_markdown_text(text: str) -> str:
    if _is_markdown_table(text):
        return _cells_to_text(_markdown_table_cells(text))
    return re.sub(r"\s+", " ", text).strip()


def _bbox(value: Any) -> BBox | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(coordinate) for coordinate in value]
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _element_id(paper_id: str, index: int) -> str:
    return f"{paper_id}:{BACKEND_NAME}:element:{index}"


def _table_id(paper_id: str, index: int) -> str:
    return f"{paper_id}:{BACKEND_NAME}:table:{index}"


def _asset_id(paper_id: str, index: int) -> str:
    return f"{paper_id}:{BACKEND_NAME}:asset:{index}"
