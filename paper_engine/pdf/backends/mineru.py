"""MinerU HTTP parser backend."""

from __future__ import annotations

from io import BytesIO
from html.parser import HTMLParser
import json
from pathlib import Path
import time
from typing import Any, Mapping, cast
from urllib.parse import urlsplit
import zipfile

import httpx

from paper_engine.pdf.backends.base import ParserBackendError, ParserBackendUnavailable
from paper_engine.pdf.models import (
    ElementType,
    ParseDocument,
    ParseElement,
    ParseAsset,
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
        poll_interval_seconds: float = 2.0,
        poll_timeout_seconds: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.parse_path = parse_path if parse_path.startswith("/") else f"/{parse_path}"
        self._client = http_client
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds

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

        client = self._client or httpx.Client(timeout=120, trust_env=False)
        close_client = self._client is None
        try:
            if self._uses_official_precise_api():
                payload = self._parse_via_official_precise_api(
                    client,
                    file_path=file_path,
                    data_id=paper_id,
                )
            else:
                payload = self._parse_via_direct_upload_api(client, file_path=file_path)
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

    def _parse_via_direct_upload_api(
        self,
        client: httpx.Client,
        *,
        file_path: Path,
    ) -> Mapping[str, Any]:
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
        return cast(Mapping[str, Any], response.json())

    def _parse_via_official_precise_api(
        self,
        client: httpx.Client,
        *,
        file_path: Path,
        data_id: str,
    ) -> Mapping[str, Any]:
        upload_response = client.post(
            f"{self._official_api_origin()}/api/v4/file-urls/batch",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "files": [
                    {
                        "name": file_path.name,
                        "data_id": data_id,
                    }
                ],
                "model_version": "vlm",
            },
        )
        upload_data = _official_response_data(upload_response)
        batch_id = str(upload_data.get("batch_id") or "")
        file_urls = upload_data.get("file_urls")
        if not batch_id or not isinstance(file_urls, list) or not file_urls:
            raise RuntimeError("mineru precise API did not return upload URLs")

        with file_path.open("rb") as handle:
            upload_file_response = client.put(
                str(file_urls[0]),
                content=handle.read(),
            )
        upload_file_response.raise_for_status()

        result = self._poll_official_precise_batch_result(
            client,
            batch_id=batch_id,
            data_id=data_id,
            file_name=file_path.name,
        )
        full_zip_url = str(result.get("full_zip_url") or "")
        if not full_zip_url:
            raise RuntimeError("mineru precise API result did not include full_zip_url")

        payload = _payload_from_precise_result_zip(
            client,
            full_zip_url,
        )
        payload["backend"] = "precise"
        payload["version"] = "api/v4"
        payload["model_version"] = "vlm"
        return payload

    def _poll_official_precise_batch_result(
        self,
        client: httpx.Client,
        *,
        batch_id: str,
        data_id: str,
        file_name: str,
    ) -> Mapping[str, Any]:
        deadline = time.monotonic() + self.poll_timeout_seconds
        last_state = "queued"

        while time.monotonic() < deadline:
            response = client.get(
                f"{self._official_api_origin()}/api/v4/extract-results/batch/{batch_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            data = _official_response_data(response)
            extract_results = data.get("extract_result")
            if not isinstance(extract_results, list) or not extract_results:
                raise RuntimeError("mineru precise API did not return extract_result")

            matched = _match_precise_extract_result(
                extract_results,
                data_id=data_id,
                file_name=file_name,
            )
            state = str(matched.get("state") or "").lower()
            last_state = state or last_state
            if state in {"done", "completed", "success"}:
                return matched
            if state in {"failed", "error"}:
                error_message = str(matched.get("err_msg") or matched.get("message") or state)
                raise RuntimeError(error_message)

            time.sleep(self.poll_interval_seconds)

        raise TimeoutError(
            f"timed out waiting for MinerU batch {batch_id} to complete (last_state={last_state})"
        )

    def _uses_official_precise_api(self) -> bool:
        return urlsplit(self.base_url).path.rstrip("/") == "/api/v4/extract/task"

    def _official_api_origin(self) -> str:
        parsed = urlsplit(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"


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
    assets: list[ParseAsset] = []
    heading_path: list[str] = []
    saw_title = False

    for index, item in enumerate(content_list):
        element_type = _element_type(item, saw_title=saw_title)
        text = _item_text(item, element_type)
        if not text:
            continue
        page_number = int(item.get("page_idx", item.get("page", 0))) + 1
        element_id = f"p{page_number:04d}-e{len(elements):04d}"
        item_heading_path = list(heading_path)
        metadata = {
            "source": "mineru_content_list",
            "raw_index": index,
            "raw_type": str(item.get("type", "")),
        }
        text_level = item.get("text_level")
        if text_level is not None:
            metadata["text_level"] = text_level
        elements.append(
            ParseElement(
                id=element_id,
                element_index=len(elements),
                element_type=cast(ElementType, element_type),
                text=text,
                page_number=page_number,
                heading_path=item_heading_path,
                extraction_method="layout_model",
                metadata=metadata,
            )
        )
        if element_type == "title":
            saw_title = True
        elif element_type == "heading":
            heading_path = _updated_heading_path(heading_path, text, item.get("text_level"))
        if element_type == "table":
            table_index = len(tables)
            tables.append(
                ParseTable(
                    id=f"table-{table_index:04d}",
                    element_id=element_id,
                    table_index=table_index,
                    page_number=page_number,
                    caption="",
                    cells=_table_cells(item, text),
                    metadata={"source": "mineru_content_list"},
                )
            )
        elif element_type == "figure":
            assets.append(
                ParseAsset(
                    id=f"asset-{len(assets):04d}",
                    element_id=element_id,
                    asset_type="image",
                    page_number=page_number,
                    uri=str(item.get("img_path") or item.get("image_path") or ""),
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
        assets=assets,
        metadata={
            "mineru": {
                "backend": payload.get("backend"),
                "version": payload.get("version"),
                "model_version": payload.get("model_version"),
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


def _element_type(
    item: Mapping[str, Any] | str,
    *,
    saw_title: bool = False,
) -> str:
    if isinstance(item, Mapping):
        value = str(item.get("type", "text"))
        if item.get("text_level") is not None:
            level = _int_value(item.get("text_level"), default=1)
            return "title" if level <= 1 and not saw_title else "heading"
    else:
        value = item
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


def _item_text(item: Mapping[str, Any], element_type: str) -> str:
    if element_type == "table":
        return str(
            item.get("table_body")
            or item.get("text")
            or item.get("content")
            or ""
        ).strip()
    return str(item.get("text") or item.get("content") or "").strip()


def _updated_heading_path(
    current: list[str],
    text: str,
    raw_level: Any,
) -> list[str]:
    level = _int_value(raw_level, default=len(current) + 1)
    depth = max(level - 1, 0)
    return [*current[:depth], text]


def _int_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def _table_cells(item: Mapping[str, Any], text: str) -> list[list[str]]:
    html = str(item.get("table_body") or "")
    if html.strip():
        rows = _html_table_cells(html)
        if rows:
            return rows
    return _markdown_table_cells(text)


def _html_table_cells(html: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    parser.feed(html)
    return parser.rows


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() == "tr":
            self._current_row = []
        elif tag.lower() in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._current_cell is not None:
            if self._current_row is not None:
                self._current_row.append(" ".join("".join(self._current_cell).split()))
            self._current_cell = None
        elif lowered == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None


def _official_response_data(response: httpx.Response) -> Mapping[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise RuntimeError("mineru precise API returned a non-object payload")
    if int(payload.get("code", 0) or 0) != 0:
        raise RuntimeError(str(payload.get("msg") or "mineru precise API request failed"))
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise RuntimeError("mineru precise API response is missing data")
    return cast(Mapping[str, Any], data)


def _match_precise_extract_result(
    extract_results: list[Any],
    *,
    data_id: str,
    file_name: str,
) -> Mapping[str, Any]:
    for item in extract_results:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("data_id") or "") == data_id:
            return cast(Mapping[str, Any], item)
    for item in extract_results:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("file_name") or "") == file_name:
            return cast(Mapping[str, Any], item)
    first = extract_results[0]
    if not isinstance(first, Mapping):
        raise RuntimeError("mineru precise API extract_result payload is invalid")
    return cast(Mapping[str, Any], first)


def _payload_from_precise_result_zip(
    client: httpx.Client,
    full_zip_url: str,
) -> dict[str, Any]:
    response = client.get(full_zip_url)
    response.raise_for_status()

    payload: dict[str, Any] = {}
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        for name in archive.namelist():
            lower_name = name.lower()
            if lower_name.endswith("content_list.json"):
                payload["content_list"] = json.loads(archive.read(name).decode("utf-8"))
            elif lower_name.endswith("full.md"):
                payload["md_content"] = archive.read(name).decode("utf-8")

    if "content_list" not in payload and "md_content" not in payload:
        raise RuntimeError("mineru precise API ZIP did not contain content_list.json or full.md")
    return payload
