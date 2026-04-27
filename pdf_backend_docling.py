"""Optional Docling-backed PDF parser implementation."""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from pdf_backend_base import ParserBackendError, ParserBackendUnavailable
from pdf_models import (
    ElementType,
    ParseAsset,
    ParseDocument,
    ParseElement,
    ParseTable,
    PdfQualityReport,
)


_BACKEND_NAME = "docling"


def _load_docling_converter() -> Any:
    """Import Docling lazily so startup does not require the optional extra."""
    if importlib.util.find_spec("docling") is None:
        raise ParserBackendUnavailable(_BACKEND_NAME, "docling is not installed")
    module = importlib.import_module("docling.document_converter")
    return module.DocumentConverter


class DoclingBackend:
    """Parse PDF files through Docling's document conversion pipeline."""

    name = _BACKEND_NAME

    def is_available(self) -> bool:
        """Return whether Docling appears importable without importing it."""
        return importlib.util.find_spec("docling") is not None

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        """Parse a PDF into the normalized parser contract."""
        if not self.is_available():
            raise ParserBackendUnavailable(self.name, "docling is not installed")

        try:
            converter_class = _load_docling_converter()
            converter = converter_class()
            result = converter.convert(str(file_path))
        except ParserBackendUnavailable:
            raise
        except Exception as exc:
            raise ParserBackendError(self.name, "failed to parse PDF", cause=exc) from exc

        try:
            return _docling_result_to_document(
                result,
                paper_id=paper_id,
                space_id=space_id,
                quality_report=quality_report,
            )
        except Exception as exc:
            raise ParserBackendError(
                self.name,
                "failed to normalize Docling output",
                cause=exc,
            ) from exc


class _DocumentBuilder:
    def __init__(
        self,
        *,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
        docling_document: Any,
        items: list[Any],
    ) -> None:
        self.paper_id = paper_id
        self.space_id = space_id
        self.quality_report = quality_report
        self.docling_document = docling_document
        self.items = items
        self.elements: list[ParseElement] = []
        self.tables: list[ParseTable] = []
        self.assets: list[ParseAsset] = []
        self._heading_path: list[str] = []

    def build(self) -> ParseDocument:
        for item_index, item in enumerate(self.items):
            self._add_item(item, item_index)

        return ParseDocument(
            paper_id=self.paper_id,
            space_id=self.space_id,
            backend=_BACKEND_NAME,
            extraction_method="layout_model",
            quality=self.quality_report,
            elements=self.elements,
            tables=self.tables,
            assets=self.assets,
            metadata={
                "item_count": len(self.items),
                "parser": "docling.DocumentConverter",
                "docling_metadata": _json_safe(_get(self.docling_document, "metadata")),
            },
        )

    def _add_item(self, item: Any, item_index: int) -> None:
        node = _item_node(item)
        tuple_level = _item_level(item)
        label = _label(node)
        element_type = _element_type(label)
        text = _item_text(node, element_type, self.docling_document)
        cells = _table_cells(node) if element_type == "table" else []
        page_number = _page_number(node)
        bbox = _bbox(_get_any(node, ("bbox", "bounding_box", "prov_bbox"))) or _prov_bbox(
            node
        )
        metadata = {
            "source": "docling_item",
            "item_index": item_index,
            "label": label,
            "raw": _json_safe(item),
        }

        if element_type == "heading":
            previous_path = list(self._heading_path)
            element = self._add_element(
                element_type=element_type,
                text=text,
                page_number=page_number,
                bbox=bbox,
                heading_path=previous_path,
                metadata=metadata,
            )
            self._update_heading_path(node, element.text, tuple_level)
            return

        if not text and element_type not in {"table", "figure"}:
            return

        if element_type == "table" and not text:
            text = _cells_to_text(cells) or "Table"
        if element_type == "figure" and not text:
            text = "Figure"

        element = self._add_element(
            element_type=element_type,
            text=text,
            page_number=page_number,
            bbox=bbox,
            heading_path=list(self._heading_path),
            metadata=metadata,
        )

        if element_type == "table":
            self._add_table(
                element_id=element.id,
                page_number=page_number,
                caption=text if _caption_text(node, self.docling_document) else "",
                cells=cells,
                bbox=bbox,
                metadata=metadata,
            )
        elif element_type == "figure":
            self._add_asset(
                element_id=element.id,
                page_number=page_number,
                asset_type=_asset_type(label),
                uri=_asset_uri(node),
                bbox=bbox,
                metadata=metadata,
            )

    def _add_element(
        self,
        *,
        element_type: str,
        text: str,
        page_number: int,
        bbox: list[float] | None,
        heading_path: list[str],
        metadata: dict[str, Any],
    ) -> ParseElement:
        element = ParseElement(
            id=f"p{page_number:04d}-e{len(self.elements):04d}",
            element_index=len(self.elements),
            element_type=cast(ElementType, element_type),
            text=text,
            page_number=page_number,
            bbox=bbox,
            heading_path=heading_path,
            extraction_method="layout_model",
            metadata=metadata,
        )
        self.elements.append(element)
        return element

    def _add_table(
        self,
        *,
        element_id: str,
        page_number: int,
        caption: str,
        cells: list[list[str]],
        bbox: list[float] | None,
        metadata: dict[str, Any],
    ) -> None:
        table_index = len(self.tables)
        self.tables.append(
            ParseTable(
                id=f"table-{table_index:04d}",
                element_id=element_id,
                table_index=table_index,
                page_number=page_number,
                caption=caption,
                cells=cells,
                bbox=bbox,
                metadata=metadata,
            )
        )

    def _add_asset(
        self,
        *,
        element_id: str,
        page_number: int,
        asset_type: str,
        uri: str,
        bbox: list[float] | None,
        metadata: dict[str, Any],
    ) -> None:
        asset_index = len(self.assets)
        self.assets.append(
            ParseAsset(
                id=f"asset-{asset_index:04d}",
                element_id=element_id,
                asset_type=asset_type,
                page_number=page_number,
                uri=uri,
                bbox=bbox,
                metadata=metadata,
            )
        )

    def _update_heading_path(
        self,
        item: Any,
        text: str,
        tuple_level: int | None,
    ) -> None:
        if not text:
            return
        level = tuple_level or _int_or_none(_get_any(item, ("level", "heading_level", "depth")))
        if level is None or level <= 1:
            self._heading_path = [text]
            return
        next_path = self._heading_path[: level - 1]
        while len(next_path) < level - 1:
            next_path.append("")
        next_path.append(text)
        self._heading_path = [part for part in next_path if part]


def _docling_result_to_document(
    result: Any,
    *,
    paper_id: str,
    space_id: str,
    quality_report: PdfQualityReport,
) -> ParseDocument:
    document = _get_any(result, ("document", "doc")) or result
    items = _reading_order_items(document)
    if not items:
        text = _exported_text(document)
        if text:
            items = [{"type": "text", "text": text}]
    return _DocumentBuilder(
        paper_id=paper_id,
        space_id=space_id,
        quality_report=quality_report,
        docling_document=document,
        items=items,
    ).build()


def _reading_order_items(document: Any) -> list[Any]:
    for name in ("items", "texts", "body", "children"):
        value = _get(document, name)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return list(value)

    for method_name in ("iterate_items", "iter_items", "iterate_elements", "iter_elements"):
        method = _get(document, method_name)
        if callable(method):
            return list(method())

    ordered: list[Any] = []
    for name in ("texts", "tables", "pictures"):
        value = _get(document, name)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            ordered.extend(value)
    return ordered


def _item_node(item: Any) -> Any:
    if isinstance(item, tuple) and item:
        return item[0]
    return item


def _item_level(item: Any) -> int | None:
    if isinstance(item, tuple) and len(item) > 1:
        return _int_or_none(item[1])
    return None


def _label(item: Any) -> str:
    raw = _get_any(item, ("label", "type", "element_type", "kind", "name", "class"))
    if raw is None:
        return item.__class__.__name__.lower()
    label = _get(raw, "value") or _get(raw, "name") or raw
    return str(label).strip().lower()


def _element_type(label: str) -> str:
    if any(token in label for token in ("heading", "header", "section", "title")):
        return "heading"
    if "table" in label:
        return "table"
    if any(token in label for token in ("picture", "figure", "image")):
        return "figure"
    if "caption" in label:
        return "caption"
    if any(token in label for token in ("formula", "equation")):
        return "equation"
    if "code" in label:
        return "code"
    if "list" in label:
        return "list"
    return "paragraph"


def _item_text(item: Any, element_type: str, document: Any) -> str:
    if element_type == "equation":
        value = _get_any(item, ("latex", "formula", "math", "text"))
    elif element_type in {"table", "figure"}:
        value = _caption_text(item, document) or _get_any(
            item,
            ("text", "content", "name"),
        )
    else:
        value = _get_any(item, ("text", "content", "caption", "title", "name"))
    return _normalize_text(value)


def _caption_text(item: Any, document: Any | None = None) -> str:
    caption_method = _get(item, "caption_text")
    if callable(caption_method):
        try:
            return _normalize_text(caption_method(document))
        except TypeError:
            return _normalize_text(caption_method())
    return _normalize_text(_get_any(item, ("caption", "captions", "description")))


def _table_cells(item: Any) -> list[list[str]]:
    table_data = _get_any(item, ("data", "table", "table_data"))
    candidates = (
        _get(table_data, "table_cells"),
        _get(table_data, "cells"),
        _get(table_data, "grid"),
        _get(table_data, "rows"),
        _get(item, "table_cells"),
        _get(item, "cells"),
        _get(item, "rows"),
    )
    for candidate in candidates:
        cells = _normalize_cells(candidate)
        if cells:
            return cells
    return []


def _normalize_cells(value: Any) -> list[list[str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    if all(isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)) for row in value):
        return [[_normalize_text(cell) for cell in row] for row in value]

    positioned: dict[tuple[int, int], str] = {}
    max_row = -1
    max_col = -1
    for cell in value:
        row = _int_or_none(_get_any(cell, ("row", "row_index", "start_row_offset_idx")))
        col = _int_or_none(_get_any(cell, ("col", "column", "col_index", "start_col_offset_idx")))
        text = _normalize_text(_get_any(cell, ("text", "content", "value")))
        if row is None or col is None:
            continue
        positioned[(row, col)] = text
        max_row = max(max_row, row)
        max_col = max(max_col, col)
    if max_row < 0 or max_col < 0:
        return []
    return [
        [positioned.get((row, col), "") for col in range(max_col + 1)]
        for row in range(max_row + 1)
    ]


def _cells_to_text(cells: list[list[str]]) -> str:
    return "\n".join(" | ".join(cell for cell in row) for row in cells).strip()


def _page_number(item: Any) -> int:
    value = _get_any(item, ("page_number", "page_no", "page"))
    page = _int_or_none(value)
    if page is not None:
        return max(page, 1)

    provenance = _get_any(item, ("prov", "provenance"))
    if isinstance(provenance, Sequence) and not isinstance(provenance, (str, bytes, bytearray)):
        for entry in provenance:
            page = _int_or_none(_get_any(entry, ("page_no", "page_number", "page")))
            if page is not None:
                return max(page, 1)

    page_index = _int_or_none(_get(item, "page_index"))
    if page_index is not None:
        return max(page_index + 1, 1)
    return 1


def _prov_bbox(item: Any) -> list[float] | None:
    provenance = _get_any(item, ("prov", "provenance"))
    if not isinstance(provenance, Sequence) or isinstance(
        provenance,
        (str, bytes, bytearray),
    ):
        return None
    for entry in provenance:
        bbox = _bbox(_get_any(entry, ("bbox", "bounding_box", "prov_bbox")))
        if bbox is not None:
            return bbox
    return None


def _bbox(value: Any) -> list[float] | None:
    if isinstance(value, Mapping):
        value = [
            value.get("l", value.get("x0", value.get("left"))),
            value.get("t", value.get("y0", value.get("top"))),
            value.get("r", value.get("x1", value.get("right"))),
            value.get("b", value.get("y1", value.get("bottom"))),
        ]
    elif not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if len(value) != 4:
        return None
    try:
        bbox = [float(coordinate) for coordinate in value]
    except (TypeError, ValueError):
        return None
    if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
        return None
    return bbox


def _asset_type(label: str) -> str:
    if "image" in label:
        return "image"
    if "figure" in label:
        return "figure"
    if "picture" in label:
        return "picture"
    return "figure"


def _asset_uri(item: Any) -> str:
    direct = _get_any(item, ("uri", "path", "image_uri", "image_path"))
    if direct is not None:
        return str(direct)
    image = _get_any(item, ("image", "picture"))
    nested = _get_any(image, ("uri", "path", "image_uri", "image_path"))
    return "" if nested is None else str(nested)


def _exported_text(document: Any) -> str:
    for method_name in ("export_to_text", "export_to_markdown"):
        method = _get(document, method_name)
        if callable(method):
            try:
                return _normalize_text(method())
            except Exception:
                return ""
    return _normalize_text(_get_any(document, ("text", "content")))


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(_normalize_text(part) for part in value if _normalize_text(part)).strip()
    return " ".join(str(value).split())


def _get_any(obj: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        value = _get(obj, name)
        if value is not None:
            return value
    return None


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            key: _json_safe(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)
