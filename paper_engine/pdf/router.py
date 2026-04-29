"""Deprecated automatic parser router.

Production paper parsing uses paper_engine.pdf.worker with a parser snapshot
from parse_runs.config_json. Keep this module only for compatibility tests and
local scripts during migration.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, Final, cast

from paper_engine.storage.database import get_connection
from paper_engine.pdf.backends.base import (
    ParserBackendError,
    ParserBackendUnavailable,
    PdfParserBackend,
)
from paper_engine.pdf.backends.docling import DoclingBackend
from paper_engine.pdf.backends.legacy import LegacyPyMuPDFBackend
from paper_engine.pdf.backends.llamaparse import get_configured_llamaparse_backend
from paper_engine.pdf.backends.pymupdf4llm import PyMuPDF4LLMBackend
from paper_engine.pdf.models import ParseDocument, PdfQualityReport


FORCED_BACKEND_KEY: Final = "pdf_forced_backend"
WARNING_DETAIL_LIMIT: Final = 120
_UNSET: Final = object()
BackendProvider = Callable[[], PdfParserBackend | None]


class PdfBackendRouter:
    """Select a parser backend from PDF quality signals and configuration."""

    def __init__(
        self,
        *,
        pymupdf4llm: PdfParserBackend | None | object = _UNSET,
        docling: PdfParserBackend | None | object = _UNSET,
        llamaparse: PdfParserBackend | BackendProvider | None | object = _UNSET,
        legacy: PdfParserBackend | None | object = _UNSET,
        forced_backend: str | None | object = _UNSET,
    ) -> None:
        self._pymupdf4llm = (
            PyMuPDF4LLMBackend() if pymupdf4llm is _UNSET else _as_backend(pymupdf4llm)
        )
        self._docling = DoclingBackend() if docling is _UNSET else _as_backend(docling)
        self._llamaparse = llamaparse
        self._legacy = LegacyPyMuPDFBackend() if legacy is _UNSET else _as_backend(legacy)
        self._forced_backend = (
            get_forced_backend_setting()
            if forced_backend is _UNSET
            else _normalize_backend_key(str(forced_backend or ""))
        )

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

        return self._parse_with_candidates(pdf_path, paper_id, space_id, quality)

    def _parse_with_candidates(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality: PdfQualityReport,
    ) -> ParseDocument:
        errors: list[str] = []

        for backend, owns_backend in self._candidate_backends(quality):
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
                warning = f"router_unavailable:{name}:{_warning_detail(exc)}"
                quality.warnings.append(warning)
                errors.append(warning)
                continue
            except ParserBackendError as exc:
                warning = f"router_failed:{name}:{_warning_detail(exc)}"
                quality.warnings.append(warning)
                errors.append(warning)
                continue

            else:
                if _is_degraded_legacy_selection(name, quality):
                    quality.warnings.append(
                        "router_degraded:legacy-pymupdf:"
                        "advanced_parser_unavailable_for_layout_pdf"
                    )
                quality.warnings.append(f"router_selected:{name}")
                return document.model_copy(update={"quality": quality})
            finally:
                if owns_backend and hasattr(backend, "close"):
                    backend.close()

        detail = "; ".join(errors) if errors else "no parser backends configured"
        raise ParserBackendUnavailable("router", detail)

    def _candidate_backends(
        self,
        quality: PdfQualityReport,
    ) -> Iterator[tuple[PdfParserBackend, bool]]:
        seen: set[str] = set()
        forced = self._backend_for_key(self._forced_backend, quality)
        if forced is not None:
            forced_backend, forced_owned = forced
            seen.add(forced_backend.name)
            yield forced_backend, forced_owned

        if quality.needs_ocr:
            yield from self._unique_backends(
                (self._docling, self._resolve_llamaparse_backend, self._legacy),
                quality,
                seen,
            )
        elif quality.needs_layout_model:
            yield from self._unique_backends(
                (
                    self._docling,
                    self._resolve_llamaparse_backend,
                    self._pymupdf4llm,
                    self._legacy,
                ),
                quality,
                seen,
            )
        else:
            yield from self._unique_backends(
                (self._pymupdf4llm, self._resolve_llamaparse_backend, self._legacy),
                quality,
                seen,
            )

    def _backend_for_key(
        self,
        key: str,
        quality: PdfQualityReport | None,
    ) -> tuple[PdfParserBackend, bool] | None:
        if key == "pymupdf4llm":
            return _owned_backend(self._pymupdf4llm, False)
        if key == "docling":
            return _owned_backend(self._docling, False)
        if key == "llamaparse":
            return self._resolve_llamaparse_backend(quality)
        if key in {"legacy", "legacy-pymupdf"}:
            return _owned_backend(self._legacy, False)
        return None

    def _unique_backends(
        self,
        backends: tuple[
            PdfParserBackend
            | None
            | Callable[[PdfQualityReport], tuple[PdfParserBackend, bool] | None],
            ...,
        ],
        quality: PdfQualityReport,
        seen: set[str],
    ) -> Iterator[tuple[PdfParserBackend, bool]]:
        for candidate in backends:
            resolved = (
                candidate(quality)
                if callable(candidate)
                else _owned_backend(candidate, False)
            )
            if resolved is None:
                continue
            backend, owns_backend = resolved
            if backend.name in seen:
                if owns_backend and hasattr(backend, "close"):
                    backend.close()
                continue
            seen.add(backend.name)
            yield backend, owns_backend

    def _resolve_llamaparse_backend(
        self,
        quality: PdfQualityReport | None,
    ) -> tuple[PdfParserBackend, bool] | None:
        try:
            if self._llamaparse is _UNSET:
                return _owned_backend(get_configured_llamaparse_backend(), True)
            if callable(self._llamaparse):
                return _owned_backend(self._llamaparse(), True)
            return _owned_backend(_as_backend(self._llamaparse), False)
        except Exception as exc:
            if quality is not None:
                quality.warnings.append(
                    f"router_llamaparse_config_failed:{_warning_detail(exc)}"
                )
            return None

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
    from paper_engine.pdf.profile import inspect_pdf as _inspect_pdf

    return _inspect_pdf(Path(file_path))


def _as_backend(value: PdfParserBackend | None | object) -> PdfParserBackend | None:
    if value is None:
        return None
    return value  # type: ignore[return-value]


def _owned_backend(
    backend: PdfParserBackend | None,
    owns_backend: bool,
) -> tuple[PdfParserBackend, bool] | None:
    if backend is None:
        return None
    return backend, owns_backend


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


def _warning_detail(exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    if len(detail) <= WARNING_DETAIL_LIMIT:
        return detail
    return f"{detail[: WARNING_DETAIL_LIMIT - 3]}..."


def _is_degraded_legacy_selection(name: str, quality: PdfQualityReport) -> bool:
    if name != "legacy-pymupdf":
        return False
    return (
        quality.needs_ocr
        or quality.needs_layout_model
        or quality.estimated_table_pages > 0
        or quality.estimated_two_column_pages > 0
    )
