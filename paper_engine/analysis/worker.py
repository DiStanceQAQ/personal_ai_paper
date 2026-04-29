"""Background worker for queued AI analysis runs."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
import traceback
import time
from collections.abc import Callable

from paper_engine.analysis.jobs import (
    AnalysisRunCancelled,
    claim_next_analysis_run,
    fail_analysis_run,
    heartbeat_analysis_run_for_worker,
)
from paper_engine.analysis.pipeline import run_paper_analysis
from paper_engine.storage.database import get_connection


def _format_exception_details(exc: BaseException) -> str:
    message = str(exc)
    cause = exc.__cause__
    if cause is None:
        return f"{type(exc).__name__}: {message}"
    return (
        f"{type(exc).__name__}: {message} | "
        f"cause={type(cause).__name__}: {cause}"
    )


class AnalysisWorker:
    """Claim and execute queued AI analysis runs."""

    def __init__(
        self,
        *,
        conn_factory: Callable[[], sqlite3.Connection] = get_connection,
        worker_id: str | None = None,
        heartbeat_interval_seconds: float | None = None,
        close_connection: bool = True,
    ) -> None:
        self.conn_factory = conn_factory
        self.worker_id = worker_id or f"analysis-worker-{os.getpid()}"
        self.heartbeat_interval_seconds = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds is not None
            else float(os.getenv("PAPER_ENGINE_ANALYSIS_HEARTBEAT_SECONDS", "30"))
        )
        self.close_connection = close_connection

    def run_once(self) -> bool:
        """Claim and execute one queued analysis run if available."""
        conn = self.conn_factory()
        try:
            job = claim_next_analysis_run(conn, worker_id=self.worker_id)
            if job is None:
                return False
            heartbeat = _AnalysisRunHeartbeat(
                conn_factory=self.conn_factory,
                analysis_run_id=job.id,
                worker_id=self.worker_id,
                interval_seconds=self.heartbeat_interval_seconds,
                close_connection=self.close_connection,
            )
            try:
                heartbeat.start()
                asyncio.run(
                    run_paper_analysis(
                        job.paper_id,
                        job.space_id,
                        analysis_run_id=job.id,
                    )
                )
                return True
            except AnalysisRunCancelled:
                conn.rollback()
                return True
            except Exception as exc:
                traceback.print_exc()
                conn.rollback()
                error_detail = _format_exception_details(exc)
                fail_analysis_run(
                    conn,
                    job.id,
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


class _AnalysisRunHeartbeat:
    """Refresh a claimed analysis run from a separate short-lived connection."""

    def __init__(
        self,
        *,
        conn_factory: Callable[[], sqlite3.Connection],
        analysis_run_id: str,
        worker_id: str,
        interval_seconds: float,
        close_connection: bool,
    ) -> None:
        self._conn_factory = conn_factory
        self._analysis_run_id = analysis_run_id
        self._worker_id = worker_id
        self._interval_seconds = max(interval_seconds, 0.1)
        self._close_connection = close_connection
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"analysis-heartbeat-{self._worker_id}",
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
                heartbeat_analysis_run_for_worker(
                    conn,
                    self._analysis_run_id,
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


def run_analysis_worker_loop(
    worker: AnalysisWorker,
    *,
    poll_interval_seconds: float = 2.0,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Run the analysis worker until stopped."""
    while stop is None or not stop():
        did_work = worker.run_once()
        if not did_work:
            time.sleep(poll_interval_seconds)


__all__ = ["AnalysisWorker", "run_analysis_worker_loop"]
