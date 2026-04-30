"""Packaged background worker sidecar entry point."""

from __future__ import annotations

import argparse
import multiprocessing
import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from paper_engine.core.startup import StartupTracer

_tracer = StartupTracer("worker")
startup_trace = _tracer.trace


@dataclass(frozen=True)
class WorkerSettings:
    data_dir: Path | None
    resource_dir: Path | None
    parse_enabled: bool
    embedding_enabled: bool
    analysis_enabled: bool


def parse_args(argv: Sequence[str] | None = None) -> WorkerSettings:
    parser = argparse.ArgumentParser(
        prog="paper-engine-worker",
        description="Run Local Paper Knowledge Engine background workers.",
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--resource-dir", type=Path, default=None)
    parser.add_argument("--no-parse", action="store_true")
    parser.add_argument("--no-embedding", action="store_true")
    parser.add_argument("--no-analysis", action="store_true")
    args = parser.parse_args(argv)

    data_dir = args.data_dir.resolve() if args.data_dir else None
    resource_dir = args.resource_dir.resolve() if args.resource_dir else None
    return WorkerSettings(
        data_dir=data_dir,
        resource_dir=resource_dir,
        parse_enabled=not bool(args.no_parse),
        embedding_enabled=not bool(args.no_embedding),
        analysis_enabled=not bool(args.no_analysis),
    )


def configure_runtime_dirs(settings: WorkerSettings) -> None:
    if settings.data_dir is not None:
        os.environ["PAPER_ENGINE_DATA_DIR"] = str(settings.data_dir)
        startup_trace("data_dir_configured", data_dir=settings.data_dir)
    if settings.resource_dir is not None:
        os.environ["PAPER_ENGINE_RESOURCE_DIR"] = str(settings.resource_dir)
        startup_trace("resource_dir_configured", resource_dir=settings.resource_dir)
        docling_cache_dir = (
            settings.resource_dir / "models" / "docling-hf-cache" / "hub"
        )
        if docling_cache_dir.is_dir():
            os.environ["HF_HOME"] = str(docling_cache_dir.parent)
            os.environ["HF_HUB_CACHE"] = str(docling_cache_dir)
            startup_trace("docling_cache_configured", hf_hub_cache=docling_cache_dir)


def main(argv: Sequence[str] | None = None) -> None:
    multiprocessing.freeze_support()
    startup_trace("main_entry")
    settings = parse_args(argv)
    startup_trace(
        "args_parsed",
        data_dir=settings.data_dir or "",
        resource_dir=settings.resource_dir or "",
        parse_enabled=str(settings.parse_enabled),
        embedding_enabled=str(settings.embedding_enabled),
        analysis_enabled=str(settings.analysis_enabled),
    )
    configure_runtime_dirs(settings)

    startup_trace("worker_import_start")
    from paper_engine.analysis.jobs import recover_stale_analysis_runs
    from paper_engine.analysis.worker import AnalysisWorker, run_analysis_worker_loop
    from paper_engine.pdf.jobs import recover_stale_parse_runs
    from paper_engine.pdf.worker import ParseWorker, run_worker_loop
    from paper_engine.retrieval.embedding_jobs import recover_stale_embedding_runs
    from paper_engine.retrieval.embedding_worker import (
        EmbeddingWorker,
        run_embedding_worker_loop,
    )
    from paper_engine.storage.database import get_connection, init_db

    startup_trace("worker_import_done")
    init_conn = init_db()
    init_conn.close()
    startup_trace("database_ready")

    recovery_conn = get_connection()
    try:
        parse_recovered = recover_stale_parse_runs(
            recovery_conn,
            stale_after_seconds=int(os.getenv("PAPER_ENGINE_PARSE_STALE_SECONDS", "600")),
            max_attempts=int(os.getenv("PAPER_ENGINE_PARSE_MAX_ATTEMPTS", "3")),
        )
        embedding_recovered = recover_stale_embedding_runs(
            recovery_conn,
            stale_after_seconds=int(
                os.getenv("PAPER_ENGINE_EMBEDDING_STALE_SECONDS", "900")
            ),
            max_attempts=int(os.getenv("PAPER_ENGINE_EMBEDDING_MAX_ATTEMPTS", "3")),
        )
        analysis_recovered = recover_stale_analysis_runs(
            recovery_conn,
            stale_after_seconds=int(
                os.getenv("PAPER_ENGINE_ANALYSIS_STALE_SECONDS", "900")
            ),
            max_attempts=int(os.getenv("PAPER_ENGINE_ANALYSIS_MAX_ATTEMPTS", "2")),
        )
        startup_trace(
            "runs_recovered",
            parse=str(parse_recovered),
            embedding=str(embedding_recovered),
            analysis=str(analysis_recovered),
        )
    finally:
        recovery_conn.close()

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    threads: list[threading.Thread] = []

    if settings.parse_enabled:
        parse_worker = ParseWorker(worker_id=f"sidecar-parse-worker-{os.getpid()}")
        threads.append(
            threading.Thread(
                target=run_worker_loop,
                kwargs={
                    "worker": parse_worker,
                    "poll_interval_seconds": float(
                        os.getenv("PAPER_ENGINE_PARSE_POLL_SECONDS", "2")
                    ),
                    "stop": stop_event.is_set,
                },
                daemon=True,
                name="paper-engine-parse-worker",
            )
        )
        startup_trace("parse_worker_configured", worker_id=parse_worker.worker_id)

    if settings.embedding_enabled:
        embedding_worker = EmbeddingWorker(
            worker_id=f"sidecar-embedding-worker-{os.getpid()}"
        )
        threads.append(
            threading.Thread(
                target=run_embedding_worker_loop,
                kwargs={
                    "worker": embedding_worker,
                    "poll_interval_seconds": float(
                        os.getenv("PAPER_ENGINE_EMBEDDING_POLL_SECONDS", "2")
                    ),
                    "stop": stop_event.is_set,
                },
                daemon=True,
                name="paper-engine-embedding-worker",
            )
        )
        startup_trace(
            "embedding_worker_configured",
            worker_id=embedding_worker.worker_id,
        )

    if settings.analysis_enabled:
        analysis_worker = AnalysisWorker(
            worker_id=f"sidecar-analysis-worker-{os.getpid()}"
        )
        threads.append(
            threading.Thread(
                target=run_analysis_worker_loop,
                kwargs={
                    "worker": analysis_worker,
                    "poll_interval_seconds": float(
                        os.getenv("PAPER_ENGINE_ANALYSIS_POLL_SECONDS", "2")
                    ),
                    "stop": stop_event.is_set,
                },
                daemon=True,
                name="paper-engine-analysis-worker",
            )
        )
        startup_trace("analysis_worker_configured", worker_id=analysis_worker.worker_id)

    for thread in threads:
        thread.start()
        startup_trace("thread_started", name=thread.name)

    startup_trace("worker_ready", thread_count=str(len(threads)))
    try:
        while not stop_event.wait(1):
            pass
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=5)
            startup_trace("thread_stopped", name=thread.name)


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def handle_signal(signum: int, _frame: object) -> None:
        startup_trace("signal_received", signum=str(signum))
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


if __name__ == "__main__":
    main()
