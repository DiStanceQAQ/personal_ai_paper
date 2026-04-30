"""Background worker for queued passage embedding runs."""

from __future__ import annotations

import os
import sqlite3
import threading
import traceback
import time
from collections.abc import Callable

from paper_engine.pdf.persistence import (
    PassageEmbeddingError,
    PassageEmbeddingResult,
    embed_passages_for_parse_run as default_embed_passages_for_parse_run,
    sync_passage_embedding_vector_index,
)
from paper_engine.retrieval.embedding_jobs import (
    claim_next_embedding_run,
    complete_embedding_run,
    fail_embedding_run,
    heartbeat_embedding_run_for_worker,
)
from paper_engine.retrieval.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderUnavailable,
    get_embedding_config,
    get_embedding_provider,
)
from paper_engine.storage.database import get_connection


def _format_exception_details(exc: BaseException) -> str:
    if isinstance(exc, PassageEmbeddingError) and exc.warnings:
        return "; ".join(exc.warnings)

    message = str(exc)
    cause = exc.__cause__
    if cause is None:
        return f"{type(exc).__name__}: {message}"
    return (
        f"{type(exc).__name__}: {message} | "
        f"cause={type(cause).__name__}: {cause}"
    )


class EmbeddingWorker:
    """Claim and execute queued embedding runs."""

    def __init__(
        self,
        *,
        conn_factory: Callable[[], sqlite3.Connection] = get_connection,
        worker_id: str | None = None,
        heartbeat_interval_seconds: float | None = None,
        batch_size: int | None = None,
        prewarm_provider: bool | None = None,
        close_connection: bool = True,
        embed_passages_for_parse_run: Callable[..., PassageEmbeddingResult] = (
            default_embed_passages_for_parse_run
        ),
        provider_factory: Callable[[sqlite3.Connection], EmbeddingProvider] | None = None,
    ) -> None:
        self.conn_factory = conn_factory
        self.worker_id = worker_id or f"embedding-worker-{os.getpid()}"
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else float(os.getenv("PAPER_ENGINE_EMBEDDING_HEARTBEAT_SECONDS", "30"))
        )
        self.batch_size = (
            batch_size
            if batch_size is not None
            else int(os.getenv("PAPER_ENGINE_EMBEDDING_BATCH_SIZE", "16"))
        )
        self.prewarm_provider = (
            prewarm_provider
            if prewarm_provider is not None
            else os.getenv("PAPER_ENGINE_EMBEDDING_PREWARM_ENABLED", "1") == "1"
        )
        self.close_connection = close_connection
        self.embed_passages_for_parse_run = embed_passages_for_parse_run
        self.provider_factory = provider_factory or _default_provider_factory
        self._provider: EmbeddingProvider | None = None
        if self.prewarm_provider:
            try:
                self.prewarm()
            except Exception:
                traceback.print_exc()
                self._provider = None

    def prewarm(self) -> None:
        """Load and validate the embedding provider before work arrives."""
        if self._provider is not None:
            return
        conn = self.conn_factory()
        try:
            self._provider = self.provider_factory(conn)
            if not self._provider.is_configured():
                raise EmbeddingProviderUnavailable("Embedding provider is not configured.")
        finally:
            if self.close_connection:
                conn.close()

    def close(self) -> None:
        """Release provider resources owned by this worker."""
        provider = self._provider
        self._provider = None
        close = getattr(provider, "close", None)
        if callable(close):
            close()

    def run_once(self) -> bool:
        """Claim and execute one queued embedding run if available."""
        conn = self.conn_factory()
        try:
            job = claim_next_embedding_run(conn, worker_id=self.worker_id)
            if job is None:
                return False
            heartbeat = _EmbeddingRunHeartbeat(
                conn_factory=self.conn_factory,
                embedding_run_id=job.id,
                worker_id=self.worker_id,
                interval_seconds=self.heartbeat_interval_seconds,
                close_connection=self.close_connection,
            )
            try:
                heartbeat.start()
                provider = self._provider
                if provider is None:
                    provider = self.provider_factory(conn)
                    self._provider = provider
                result = self.embed_passages_for_parse_run(
                    conn,
                    job.parse_run_id,
                    provider=provider,
                    batch_size=self.batch_size,
                )
                complete_embedding_run(
                    conn,
                    job.id,
                    paper_id=job.paper_id,
                    space_id=job.space_id,
                    worker_id=self.worker_id,
                    passage_count=result.passage_count,
                    embedded_count=result.embedded_count,
                    reused_count=result.reused_count,
                    skipped_count=result.skipped_count,
                    batch_count=result.batch_count,
                    warnings=result.warnings,
                    metadata={
                        "provider": result.provider,
                        "model": result.model,
                        "batch_size": self.batch_size,
                    },
                )
                sync_passage_embedding_vector_index(conn, job.parse_run_id)
                return True
            except Exception as exc:
                traceback.print_exc()
                conn.rollback()
                error_detail = _format_exception_details(exc)
                fail_embedding_run(
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


class _EmbeddingRunHeartbeat:
    """Refresh a claimed embedding run from a separate short-lived connection."""

    def __init__(
        self,
        *,
        conn_factory: Callable[[], sqlite3.Connection],
        embedding_run_id: str,
        worker_id: str,
        interval_seconds: float,
        close_connection: bool,
    ) -> None:
        self._conn_factory = conn_factory
        self._embedding_run_id = embedding_run_id
        self._worker_id = worker_id
        self._interval_seconds = max(interval_seconds, 0.1)
        self._close_connection = close_connection
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"embedding-heartbeat-{self._worker_id}",
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
                heartbeat_embedding_run_for_worker(
                    conn,
                    self._embedding_run_id,
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


def _default_provider_factory(conn: sqlite3.Connection) -> EmbeddingProvider:
    return get_embedding_provider(get_embedding_config(conn))


def run_embedding_worker_loop(
    worker: EmbeddingWorker,
    *,
    poll_interval_seconds: float = 2.0,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Run the embedding worker until stopped."""
    try:
        while stop is None or not stop():
            did_work = worker.run_once()
            if not did_work:
                time.sleep(poll_interval_seconds)
    finally:
        worker.close()


__all__ = ["EmbeddingWorker", "run_embedding_worker_loop"]
