"""Route PDFs to the best available parser backend."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Final, cast

from db import get_connection
from pdf_backend_base import (
    ParserBackendError,
    ParserBackendUnavailable,
    PdfParserBackend,
)
from pdf_backend_docling import DoclingBackend
from pdf_backend_grobid import GrobidClient, get_configured_grobid_client
from pdf_backend_legacy import LegacyPyMuPDFBackend
from pdf_backend_llamaparse import get_configured_llamaparse_backend
from pdf_backend_pymupdf4llm import PyMuPDF4LLMBackend
from pdf_models import ParseDocument, PdfQualityReport


FORCED_BACKEND_KEY: Final = "pdf_forced_backend"
_UNSET: Final = object()


class PdfBackendRouter:
    """Select a parser backend from PDF quality signals and configuration."""

    def __init__(
        self,
        *,
        pymupdf4llm: PdfParserBackend | None | object = _UNSET,
        docling: PdfParserBackend | None | object = _UNSET,
        llamaparse: PdfParserBackend | None | object = _UNSET,
        legacy: PdfParserBackend | None | object = _UNSET,
        forced_backend: str | None | object = _UNSET,
        grobid_client: GrobidClient | None | object = _UNSET,
    ) -> None:
        self._pymupdf4llm = (
            PyMuPDF4LLMBackend() if pymupdf4llm is _UNSET else _as_backend(pymupdf4llm)
        )
        self._docling = DoclingBackend() if docling is _UNSET else _as_backend(docling)
        self._llamaparse = (
            get_configured_llamaparse_backend()
            if llamaparse is _UNSET
            else _as_backend(llamaparse)
        )
        self._legacy = LegacyPyMuPDFBackend() if legacy is _UNSET else _as_backend(legacy)
        self._forced_backend = (
            get_forced_backend_setting()
            if forced_backend is _UNSET
            else _normalize_backend_key(str(forced_backend or ""))
        )
        self._grobid_client = grobid_client

    def parse_pdf(
        self,
        file_path: Path | str,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport | None = None,
    ) -> ParseDocument:
        """Parse a PDF with the best available backend."""
        pdf_path = Path(file_path)
        quality = (quality_report or inspect_pdf(pdf_path)).model_copy(deep=True)

        document = self._parse_with_candidates(pdf_path, paper_id, space_id, quality)
        document = self._merge_grobid(pdf_path, document)
        return document

    def _parse_with_candidates(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality: PdfQualityReport,
    ) -> ParseDocument:
        errors: list[str] = []

        for backend in self._candidate_backends(quality):
            name = backend.name
            quality.warnings.append(f"router_attempt:{name}")

            try:
                if not backend.is_available():
                    warning = (
                        f"router_unavailable:{name}:is_available returned false"
                    )
                    quality.warnings.append(warning)
                    errors.append(warning)
                    continue
                document = backend.parse(file_path, paper_id, space_id, quality)
            except ParserBackendUnavailable as exc:
                warning = f"router_unavailable:{name}:{exc}"
                quality.warnings.append(warning)
                errors.append(warning)
                continue
            except ParserBackendError as exc:
                warning = f"router_failed:{name}:{exc}"
                quality.warnings.append(warning)
                errors.append(warning)
                continue

            quality.warnings.append(f"router_selected:{name}")
            return document.model_copy(update={"quality": quality})

        detail = "; ".join(errors) if errors else "no parser backends configured"
        raise ParserBackendUnavailable("router", detail)

    def _candidate_backends(self, quality: PdfQualityReport) -> list[PdfParserBackend]:
        candidates: list[PdfParserBackend] = []
        forced = self._backend_for_key(self._forced_backend)
        if forced is not None:
            candidates.append(forced)

        if quality.needs_ocr or quality.needs_layout_model:
            _append_backend(candidates, self._docling)
            _append_backend(candidates, self._llamaparse)
            _append_backend(candidates, self._legacy)
        else:
            _append_backend(candidates, self._pymupdf4llm)
            _append_backend(candidates, self._llamaparse)
            _append_backend(candidates, self._legacy)

        return candidates

    def _backend_for_key(self, key: str) -> PdfParserBackend | None:
        if key == "pymupdf4llm":
            return self._pymupdf4llm
        if key == "docling":
            return self._docling
        if key == "llamaparse":
            return self._llamaparse
        if key in {"legacy", "legacy-pymupdf"}:
            return self._legacy
        return None

    def _merge_grobid(self, file_path: Path, document: ParseDocument) -> ParseDocument:
        grobid_client, owns_client = self._resolve_grobid_client()
        if grobid_client is None:
            return document

        try:
            if not grobid_client.is_alive():
                document.quality.warnings.append(
                    "router_grobid_unavailable:is_alive returned false"
                )
                return document

            result = grobid_client.process_fulltext(file_path)
            metadata = dict(document.metadata)
            metadata["grobid"] = {
                "metadata": _json_safe_dataclass(result.metadata),
                "references": [_json_safe_dataclass(ref) for ref in result.references],
                "sections": [_json_safe_dataclass(section) for section in result.sections],
            }
            document.quality.warnings.append("router_grobid_merged")
            return document.model_copy(update={"metadata": metadata})
        except Exception as exc:
            document.quality.warnings.append(f"router_grobid_failed:{exc}")
            return document
        finally:
            if owns_client and hasattr(grobid_client, "close"):
                grobid_client.close()

    def _resolve_grobid_client(self) -> tuple[GrobidClient | None, bool]:
        if self._grobid_client is _UNSET:
            return get_configured_grobid_client(), True
        return cast(GrobidClient | None, self._grobid_client), False


def parse_pdf(
    file_path: Path | str,
    paper_id: str,
    space_id: str,
    quality_report: PdfQualityReport | None = None,
) -> ParseDocument:
    """Convenience parse entrypoint using configured router defaults."""
    return PdfBackendRouter().parse_pdf(file_path, paper_id, space_id, quality_report)


def get_forced_backend_setting() -> str:
    """Return the configured forced parser backend key, or blank when unset."""
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM app_state WHERE key = ?",
                (FORCED_BACKEND_KEY,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return ""

    if row is None:
        return ""
    return _normalize_backend_key(str(row["value"]))


def inspect_pdf(file_path: Path | str) -> PdfQualityReport:
    """Inspect PDF quality lazily so router imports do not require PyMuPDF."""
    from pdf_profile import inspect_pdf as _inspect_pdf

    return _inspect_pdf(Path(file_path))


def _as_backend(value: PdfParserBackend | None | object) -> PdfParserBackend | None:
    if value is None:
        return None
    return value  # type: ignore[return-value]


def _normalize_backend_key(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "pymupdf": "legacy-pymupdf",
        "legacy": "legacy",
        "legacy-pymupdf": "legacy-pymupdf",
        "pymupdf4llm": "pymupdf4llm",
        "pymupdf-4llm": "pymupdf4llm",
        "docling": "docling",
        "llamaparse": "llamaparse",
        "llama-parse": "llamaparse",
    }
    return aliases.get(normalized, normalized)


def _append_backend(
    candidates: list[PdfParserBackend],
    backend: PdfParserBackend | None,
) -> None:
    if backend is None:
        return
    if any(candidate.name == backend.name for candidate in candidates):
        return
    candidates.append(backend)


def _json_safe_dataclass(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_safe_dataclass(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, list):
        return [_json_safe_dataclass(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_dataclass(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe_dataclass(item) for key, item in value.items()}
    return value
