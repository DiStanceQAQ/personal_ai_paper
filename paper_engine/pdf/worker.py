"""Parse worker orchestration."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import traceback
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_engine.pdf.backends.base import PdfParserBackend
from paper_engine.pdf.backends.docling import DoclingBackend
from paper_engine.pdf.backends.mineru import MinerUBackend
from paper_engine.pdf.chunking import chunk_parse_document as default_chunk_parse_document
from paper_engine.pdf.jobs import (
    claim_next_parse_run,
    complete_parse_run,
    fail_parse_run,
    heartbeat_parse_run_for_worker,
)
from paper_engine.pdf.persistence import (
    delete_parse_run_outputs,
)
from paper_engine.pdf.persistence import persist_parse_result as default_persist_parse_result
from paper_engine.pdf.profile import inspect_pdf as default_inspect_pdf
from paper_engine.papers.metadata import (
    promote_core_metadata_from_parse as default_promote_core_metadata_from_parse,
)
from paper_engine.retrieval.embedding_jobs import (
    queue_embedding_run as default_queue_embedding_run,
)
from paper_engine.storage.database import get_connection
from paper_engine.storage.repositories.settings import get_setting


@dataclass(frozen=True)
class ParserFactory:
    """Factory functions for selected parser backends."""

    mineru: Callable[[dict[str, Any]], PdfParserBackend]
    docling: Callable[[dict[str, Any]], PdfParserBackend]


def _format_exception_details(exc: BaseException) -> str:
    message = str(exc)
    cause = exc.__cause__
    if cause is None:
        return f"{type(exc).__name__}: {message}"

    cause_message = str(cause)
    return (
        f"{type(exc).__name__}: {message} | "
        f"cause={type(cause).__name__}: {cause_message}"
    )


def _merge_parse_run_metadata(
    conn: sqlite3.Connection,
    parse_run_id: str,
    updates: dict[str, Any],
) -> None:
    row = conn.execute(
        "SELECT metadata_json FROM parse_runs WHERE id = ?",
        (parse_run_id,),
    ).fetchone()
    metadata: dict[str, Any] = {}
    if row is not None:
        raw_metadata = row["metadata_json"]
        if isinstance(raw_metadata, str) and raw_metadata.strip():
            try:
                parsed = json.loads(raw_metadata)
                if isinstance(parsed, dict):
                    metadata = parsed
            except json.JSONDecodeError:
                metadata = {}
    metadata.update(updates)
    conn.execute(
        "UPDATE parse_runs SET metadata_json = ? WHERE id = ?",
        (json.dumps(metadata, ensure_ascii=False), parse_run_id),
    )


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
        queue_embedding_run: Callable[..., str] = default_queue_embedding_run,
        promote_core_metadata_from_parse: Callable[..., Any] = (
            default_promote_core_metadata_from_parse
        ),
        heartbeat_interval_seconds: float | None = None,
        close_connection: bool = True,
    ) -> None:
        self.conn_factory = conn_factory
        self.worker_id = worker_id or f"parse-worker-{os.getpid()}"
        self._parser_factory = parser_factory
        self.inspect_pdf = inspect_pdf
        self.chunk_parse_document = chunk_parse_document
        self.persist_parse_result = persist_parse_result
        self.queue_embedding_run = queue_embedding_run
        self.promote_core_metadata_from_parse = promote_core_metadata_from_parse
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else float(os.getenv("PAPER_ENGINE_PARSE_HEARTBEAT_SECONDS", "30"))
        )
        self.close_connection = close_connection

    def run_once(self) -> bool:
        """Claim and execute one queued parse run if available."""
        conn = self.conn_factory()
        try:
            job = claim_next_parse_run(conn, worker_id=self.worker_id)
            if job is None:
                return False
            heartbeat = _ParseRunHeartbeat(
                conn_factory=self.conn_factory,
                parse_run_id=job.id,
                worker_id=self.worker_id,
                interval_seconds=self.heartbeat_interval_seconds,
                close_connection=self.close_connection,
            )
            persisted_parse_run_id: str | None = None
            stage_timings: dict[str, float] = {}
            try:
                heartbeat.start()
                file_path = Path(job.file_path)
                if not file_path.exists():
                    raise FileNotFoundError(f"PDF file not found on disk: {file_path}")

                factory = self._parser_factory or default_parser_factory(conn)
                backend = _backend_for_job(factory, job.parser_backend, job.config)
                inspect_started = time.perf_counter()
                quality = self.inspect_pdf(file_path)
                stage_timings["inspect_seconds"] = time.perf_counter() - inspect_started
                parse_started = time.perf_counter()
                document = backend.parse(file_path, job.paper_id, job.space_id, quality)
                stage_timings["parse_seconds"] = time.perf_counter() - parse_started

                heartbeat_parse_run_for_worker(
                    conn,
                    job.id,
                    worker_id=self.worker_id,
                )
                chunk_started = time.perf_counter()
                passages = self.chunk_parse_document(document)
                stage_timings["chunk_seconds"] = time.perf_counter() - chunk_started
                if not passages:
                    raise RuntimeError("parsed document produced no passages")

                conn.execute("BEGIN")
                try:
                    persist_started = time.perf_counter()
                    storage_run_id = self.persist_parse_result(
                        conn,
                        job.paper_id,
                        job.space_id,
                        document,
                        passages,
                        parse_run_id=job.id,
                    )
                    stage_timings["persist_seconds"] = time.perf_counter() - persist_started
                    conn.commit()
                    persisted_parse_run_id = storage_run_id
                except Exception:
                    conn.rollback()
                    raise

                queue_embedding_started = time.perf_counter()
                self.queue_embedding_run(
                    conn,
                    paper_id=job.paper_id,
                    space_id=job.space_id,
                    parse_run_id=storage_run_id,
                    commit=False,
                )
                stage_timings["queue_embedding_seconds"] = (
                    time.perf_counter() - queue_embedding_started
                )
                stage_timings["total_worker_seconds"] = sum(stage_timings.values())
                _merge_parse_run_metadata(
                    conn,
                    storage_run_id,
                    {
                        "timings": {
                            key: round(value, 4)
                            for key, value in stage_timings.items()
                        },
                        "passage_count": len(passages),
                        "element_count": len(document.elements),
                        "table_count": len(document.tables),
                        "asset_count": len(document.assets),
                    },
                )
                self.promote_core_metadata_from_parse(
                    conn,
                    paper_id=job.paper_id,
                    space_id=job.space_id,
                    parse_run_id=storage_run_id,
                )
                complete_parse_run(
                    conn,
                    job.id,
                    paper_id=job.paper_id,
                    space_id=job.space_id,
                    worker_id=self.worker_id,
                    warnings=[*document.quality.warnings],
                )
                return True
            except Exception as exc:
                traceback.print_exc()
                conn.rollback()
                if persisted_parse_run_id is not None:
                    delete_parse_run_outputs(
                        conn,
                        paper_id=job.paper_id,
                        space_id=job.space_id,
                        parse_run_id=persisted_parse_run_id,
                    )
                    conn.commit()
                error_detail = _format_exception_details(exc)
                fail_parse_run(
                    conn,
                    job.id,
                    paper_id=job.paper_id,
                    space_id=job.space_id,
                    worker_id=self.worker_id,
                    error=error_detail,
                    warnings=[error_detail],
                )
                return True
            finally:
                heartbeat.stop()
        finally:
            if self.close_connection:
                conn.close()


def _backend_for_job(
    factory: ParserFactory,
    parser_backend: str,
    config: dict[str, Any],
) -> PdfParserBackend:
    if parser_backend == "mineru":
        return factory.mineru(config)
    return factory.docling(config)


class _ParseRunHeartbeat:
    """Refresh a claimed parse run from a separate short-lived connection."""

    def __init__(
        self,
        *,
        conn_factory: Callable[[], sqlite3.Connection],
        parse_run_id: str,
        worker_id: str,
        interval_seconds: float,
        close_connection: bool,
    ) -> None:
        self._conn_factory = conn_factory
        self._parse_run_id = parse_run_id
        self._worker_id = worker_id
        self._interval_seconds = max(interval_seconds, 0.1)
        self._close_connection = close_connection
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"parse-heartbeat-{self._worker_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            conn = self._conn_factory()
            try:
                heartbeat_parse_run_for_worker(
                    conn,
                    self._parse_run_id,
                    worker_id=self._worker_id,
                )
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            finally:
                if self._close_connection:
                    conn.close()


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
