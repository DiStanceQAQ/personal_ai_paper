"""Parse worker orchestration."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_engine.pdf.backends.base import PdfParserBackend
from paper_engine.pdf.backends.docling import DoclingBackend
from paper_engine.pdf.backends.grobid import get_configured_grobid_client
from paper_engine.pdf.backends.mineru import MinerUBackend
from paper_engine.pdf.chunking import chunk_parse_document as default_chunk_parse_document
from paper_engine.pdf.jobs import (
    claim_next_parse_run,
    complete_parse_run,
    fail_parse_run,
    heartbeat_parse_run,
)
from paper_engine.pdf.persistence import (
    embed_passages_for_parse_run as default_embed_passages_for_parse_run,
)
from paper_engine.pdf.persistence import persist_parse_result as default_persist_parse_result
from paper_engine.pdf.profile import inspect_pdf as default_inspect_pdf
from paper_engine.storage.database import get_connection
from paper_engine.storage.repositories.settings import get_setting


@dataclass(frozen=True)
class ParserFactory:
    """Factory functions for selected parser backends."""

    mineru: Callable[[dict[str, Any]], PdfParserBackend]
    docling: Callable[[dict[str, Any]], PdfParserBackend]


def default_parser_factory(conn: sqlite3.Connection) -> ParserFactory:
    """Build parser factory from persisted settings and run config."""

    def mineru(config: dict[str, Any]) -> PdfParserBackend:
        return MinerUBackend(
            base_url=str(config.get("mineru_base_url") or get_setting(conn, "mineru_base_url")),
            api_key=get_setting(conn, "mineru_api_key"),
        )

    def docling(config: dict[str, Any]) -> PdfParserBackend:
        return DoclingBackend()

    return ParserFactory(mineru=mineru, docling=docling)


class ParseWorker:
    """Claim and execute queued parse runs."""

    def __init__(
        self,
        *,
        conn_factory: Callable[[], sqlite3.Connection] = get_connection,
        worker_id: str | None = None,
        parser_factory: ParserFactory | None = None,
        inspect_pdf: Callable[[Path], Any] = default_inspect_pdf,
        chunk_parse_document: Callable[[Any], Any] = default_chunk_parse_document,
        persist_parse_result: Callable[..., str] = default_persist_parse_result,
        embed_passages_for_parse_run: Callable[
            ..., list[str]
        ] = default_embed_passages_for_parse_run,
        grobid_enricher: Callable[[Path], dict[str, Any] | None] | None = None,
        close_connection: bool = True,
    ) -> None:
        self.conn_factory = conn_factory
        self.worker_id = worker_id or f"parse-worker-{os.getpid()}"
        self._parser_factory = parser_factory
        self.inspect_pdf = inspect_pdf
        self.chunk_parse_document = chunk_parse_document
        self.persist_parse_result = persist_parse_result
        self.embed_passages_for_parse_run = embed_passages_for_parse_run
        self.grobid_enricher = grobid_enricher or default_grobid_enricher
        self.close_connection = close_connection

    def run_once(self) -> bool:
        """Claim and execute one queued parse run if available."""
        conn = self.conn_factory()
        try:
            job = claim_next_parse_run(conn, worker_id=self.worker_id)
            if job is None:
                return False
            try:
                file_path = Path(job.file_path)
                if not file_path.exists():
                    raise FileNotFoundError(f"PDF file not found on disk: {file_path}")

                factory = self._parser_factory or default_parser_factory(conn)
                backend = (
                    factory.mineru(job.config)
                    if job.parser_backend == "mineru"
                    else factory.docling(job.config)
                )
                quality = self.inspect_pdf(file_path)
                with ThreadPoolExecutor(max_workers=1) as grobid_executor:
                    grobid_future = grobid_executor.submit(self.grobid_enricher, file_path)
                    document = backend.parse(file_path, job.paper_id, job.space_id, quality)
                    document = self._merge_grobid(file_path, document, grobid_future)

                heartbeat_parse_run(conn, job.id)
                passages = self.chunk_parse_document(document)
                if not passages:
                    raise RuntimeError("parsed document produced no passages")

                conn.execute("BEGIN")
                storage_run_id = self.persist_parse_result(
                    conn,
                    job.paper_id,
                    job.space_id,
                    document,
                    passages,
                    parse_run_id=job.id,
                )
                embedding_warnings = self.embed_passages_for_parse_run(
                    conn,
                    storage_run_id,
                )
                complete_parse_run(
                    conn,
                    job.id,
                    paper_id=job.paper_id,
                    warnings=[*document.quality.warnings, *embedding_warnings],
                )
                return True
            except Exception as exc:
                conn.rollback()
                fail_parse_run(
                    conn,
                    job.id,
                    paper_id=job.paper_id,
                    error=str(exc),
                    warnings=[str(exc)],
                )
                return True
        finally:
            if self.close_connection:
                conn.close()

    def _merge_grobid(
        self,
        file_path: Path,
        document: Any,
        grobid_future: Future[dict[str, Any] | None] | None = None,
    ) -> Any:
        try:
            grobid = (
                grobid_future.result()
                if grobid_future is not None
                else self.grobid_enricher(file_path)
            )
        except Exception as exc:
            document.quality.warnings.append(f"grobid_failed:{exc}")
            return document
        if not grobid:
            return document
        metadata = dict(document.metadata)
        metadata["grobid"] = grobid
        document.quality.warnings.append("grobid_merged")
        return document.model_copy(update={"metadata": metadata})


def default_grobid_enricher(file_path: Path) -> dict[str, Any] | None:
    """Return optional GROBID metadata and references for a PDF."""
    client = get_configured_grobid_client()
    if client is None:
        return None
    try:
        if not client.is_alive():
            return None
        result = client.process_fulltext(file_path)
        return {
            "metadata": {
                "title": result.metadata.title,
                "authors": result.metadata.authors,
                "year": result.metadata.year,
                "venue": result.metadata.venue,
                "doi": result.metadata.doi,
                "abstract": result.metadata.abstract,
            },
            "references": [
                {
                    "id": reference.id,
                    "title": reference.title,
                    "authors": reference.authors,
                    "year": reference.year,
                    "venue": reference.venue,
                    "doi": reference.doi,
                    "raw_text": reference.raw_text,
                }
                for reference in result.references
            ],
        }
    finally:
        client.close()


def run_worker_loop(
    worker: ParseWorker,
    *,
    poll_interval_seconds: float = 2.0,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Run the parse worker until stopped."""
    while stop is None or not stop():
        did_work = worker.run_once()
        if not did_work:
            time.sleep(poll_interval_seconds)
