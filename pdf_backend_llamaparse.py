"""Optional LlamaParse-backed PDF parser implementation."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Mapping

import httpx

from paper_engine.storage.database import get_connection
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
DEFAULT_BASE_URL = "https://api.cloud.llamaindex.ai"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_POLL_ATTEMPTS = 30
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
_COMPLETED_STATUSES = {"COMPLETED", "SUCCESS", "SUCCEEDED", "DONE"}
_FAILED_STATUSES = {"FAILED", "ERROR", "CANCELED", "CANCELLED"}

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
        tier: str = "cost_effective",
        version: str = "latest",
        max_poll_attempts: int = DEFAULT_MAX_POLL_ATTEMPTS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self.tier = tier
        self.version = version
        self.max_poll_attempts = max_poll_attempts
        self.poll_interval_seconds = poll_interval_seconds
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
            payload = self._parse_via_api_v2(Path(file_path))
        except ParserBackendUnavailable:
            raise
        except (OSError, httpx.HTTPError) as exc:
            raise ParserBackendError(self.name, "LlamaParse request failed", cause=exc) from exc
        except ValueError as exc:
            raise ParserBackendError(self.name, str(exc), cause=exc) from exc

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

    def _parse_via_api_v2(self, file_path: Path) -> Mapping[str, Any]:
        upload_payload = self._upload_file(file_path)
        file_id = _id_from_payload(upload_payload, "file")

        create_payload = self._create_parse_job(file_id)
        if _looks_like_final_payload(create_payload):
            return create_payload

        job_id = _id_from_payload(create_payload, "parse job")
        for attempt in range(self.max_poll_attempts):
            result = self._get_parse_result(job_id)
            status = _job_status(result)
            if status in _COMPLETED_STATUSES:
                return result
            if status in _FAILED_STATUSES:
                raise ValueError(_failed_job_message(job_id, status, result))
            if not status and _looks_like_final_payload(result):
                return result
            if attempt < self.max_poll_attempts - 1 and self.poll_interval_seconds > 0:
                time.sleep(self.poll_interval_seconds)

        raise ValueError(f"LlamaParse job {job_id} did not complete")

    def _upload_file(self, file_path: Path) -> Mapping[str, Any]:
        with file_path.open("rb") as pdf_file:
            return self._request_json(
                "POST",
                "/api/v1/beta/files",
                data={"purpose": "parse"},
                files={"file": (file_path.name, pdf_file, "application/pdf")},
            )

    def _create_parse_job(self, file_id: str) -> Mapping[str, Any]:
        return self._request_json(
            "POST",
            "/api/v2/parse",
            json={"file_id": file_id, "tier": self.tier, "version": self.version},
        )

    def _get_parse_result(self, job_id: str) -> Mapping[str, Any]:
        return self._request_json(
            "GET",
            f"/api/v2/parse/{job_id}",
            params=[("expand", "markdown"), ("expand", "items")],
        )

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Mapping[str, Any]:
        response = self._client.request(
            method,
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            **kwargs,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("LlamaParse returned a non-object JSON payload")
        return payload


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
    if not config["llamaparse_api_key"]:
        return None

    backend = LlamaParseBackend(
        api_key=config["llamaparse_api_key"],
        base_url=config["llamaparse_base_url"],
    )
    if backend.is_available():
        return backend
    backend.close()
    return None


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
            if isinstance(page.get("items"), list):
                self._add_page_items(page_number, _as_list(page.get("items")))
            else:
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
        job_id = _job_id_from_payload(self.payload)
        if job_id:
            metadata["job_id"] = job_id

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

    def _add_page_items(self, page_number: int, raw_items: list[Any]) -> None:
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                continue
            item_type = str(raw_item.get("type") or raw_item.get("item_type") or "").lower()
            if item_type in {"heading", "title", "section_header", "section-heading"}:
                text = _item_text(raw_item)
                if text:
                    self._heading_path = [text]
                    self._add_element(
                        element_type="heading",
                        text=text,
                        page_number=page_number,
                        bbox=_bbox(raw_item.get("bbox")),
                        metadata={"source": "api_v2_item", "item_type": item_type},
                    )
                continue

            if item_type == "table":
                cells = _table_cells(raw_item)
                if not cells:
                    cells = _markdown_table_cells(str(raw_item.get("md") or raw_item.get("markdown") or ""))
                element = self._add_element(
                    element_type="table",
                    text=_cells_to_text(cells) or _item_text(raw_item),
                    page_number=page_number,
                    bbox=_bbox(raw_item.get("bbox")),
                    metadata={"source": "api_v2_item", "item_type": item_type},
                )
                self._add_table(
                    page_number=page_number,
                    element_id=element.id,
                    cells=cells,
                    caption=str(raw_item.get("caption") or ""),
                    bbox=_bbox(raw_item.get("bbox")),
                    metadata={"source": "api_v2_item"},
                )
                continue

            if item_type in {"image", "figure"}:
                self._add_page_assets(page_number, [raw_item], item_type)
                continue

            text = _item_text(raw_item)
            if text:
                self._add_element(
                    element_type="paragraph",
                    text=text,
                    page_number=page_number,
                    bbox=_bbox(raw_item.get("bbox")),
                    metadata={"source": "api_v2_item", "item_type": item_type},
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

    items = payload.get("items")
    if isinstance(items, Mapping):
        item_pages = items.get("pages")
        if isinstance(item_pages, list):
            return [page for page in item_pages if isinstance(page, Mapping)]

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


def _item_text(item: Mapping[str, Any]) -> str:
    value = item.get("value") or item.get("text") or item.get("md") or item.get("markdown") or ""
    text = str(value)
    heading_match = _HEADING_RE.match(text)
    if heading_match:
        return heading_match.group(2).strip()
    return _clean_markdown_text(text)


def _id_from_payload(payload: Mapping[str, Any], label: str) -> str:
    value = payload.get("id") or payload.get("file_id") or payload.get("job_id")
    if value:
        return str(value)

    nested_key = "file" if label == "file" else "job"
    nested = payload.get(nested_key)
    if isinstance(nested, Mapping):
        nested_value = nested.get("id")
        if nested_value:
            return str(nested_value)

    raise ValueError(f"LlamaParse {label} response did not include an id")


def _job_id_from_payload(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("job_id") or payload.get("id")
    if value:
        return str(value)
    job = payload.get("job")
    if isinstance(job, Mapping):
        job_id = job.get("id")
        if job_id:
            return str(job_id)
    return None


def _job_status(payload: Mapping[str, Any]) -> str:
    status = payload.get("status")
    job = payload.get("job")
    if status is None and isinstance(job, Mapping):
        status = job.get("status")
    return str(status or "").upper()


def _failed_job_message(job_id: str, status: str, payload: Mapping[str, Any]) -> str:
    detail = payload.get("error") or payload.get("message")
    job = payload.get("job")
    if detail is None and isinstance(job, Mapping):
        detail = job.get("error") or job.get("message")
    if detail:
        return f"LlamaParse job {job_id} ended with status {status}: {detail}"
    return f"LlamaParse job {job_id} ended with status {status}"


def _looks_like_final_payload(payload: Mapping[str, Any]) -> bool:
    return any(key in payload for key in ("pages", "data", "markdown", "text", "items"))


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
