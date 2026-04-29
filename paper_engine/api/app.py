"""Local Paper Knowledge Engine FastAPI application."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sys
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from paper_engine.storage.database import init_db
from paper_engine.core.startup import StartupTracer
from paper_engine.api.routes.agent import router as agent_router
from paper_engine.api.routes.cards import router as cards_router
from paper_engine.api.routes.papers import router as papers_router
from paper_engine.api.routes.search import router as search_router
from paper_engine.api.routes.spaces import router as spaces_router
from paper_engine.pdf.jobs import recover_stale_parse_runs
from paper_engine.pdf.worker import ParseWorker, run_worker_loop
from paper_engine.storage.database import get_connection

APP_IMPORTED_AT = time.perf_counter()
_tracer = StartupTracer("fastapi")
startup_trace = _tracer.trace


def run_parse_recovery_loop(
    *,
    poll_interval_seconds: float,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Requeue or fail stale parse runs while the API stays online."""
    while stop is None or not stop():
        conn = get_connection()
        try:
            recover_stale_parse_runs(
                conn,
                stale_after_seconds=int(os.getenv("PAPER_ENGINE_PARSE_STALE_SECONDS", "600")),
                max_attempts=int(os.getenv("PAPER_ENGINE_PARSE_MAX_ATTEMPTS", "3")),
            )
        finally:
            conn.close()
        time.sleep(poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize the database on startup."""
    startup_trace("lifespan_start")
    init_db_started_at = time.perf_counter()
    init_conn = init_db()
    init_conn.close()
    startup_trace(
        "database_ready",
        init_db_ms=f"{(time.perf_counter() - init_db_started_at) * 1000:.1f}",
    )
    conn = get_connection()
    try:
        recovered = recover_stale_parse_runs(
            conn,
            stale_after_seconds=int(os.getenv("PAPER_ENGINE_PARSE_STALE_SECONDS", "600")),
            max_attempts=int(os.getenv("PAPER_ENGINE_PARSE_MAX_ATTEMPTS", "3")),
        )
        startup_trace("parse_runs_recovered", count=str(recovered))
    finally:
        conn.close()

    stop_event = threading.Event()
    worker_thread: threading.Thread | None = None
    recovery_thread: threading.Thread | None = None
    if os.getenv("PAPER_ENGINE_PARSE_WORKER_ENABLED", "1") == "1":
        worker = ParseWorker(worker_id=f"api-worker-{os.getpid()}")
        worker_thread = threading.Thread(
            target=run_worker_loop,
            kwargs={
                "worker": worker,
                "poll_interval_seconds": float(
                    os.getenv("PAPER_ENGINE_PARSE_POLL_SECONDS", "2")
                ),
                "stop": stop_event.is_set,
            },
            daemon=True,
        )
        worker_thread.start()
        startup_trace("parse_worker_started", worker_id=worker.worker_id)
        recovery_thread = threading.Thread(
            target=run_parse_recovery_loop,
            kwargs={
                "poll_interval_seconds": float(
                    os.getenv("PAPER_ENGINE_PARSE_RECOVERY_POLL_SECONDS", "15")
                ),
                "stop": stop_event.is_set,
            },
            daemon=True,
        )
        recovery_thread.start()
        startup_trace("parse_recovery_started")

    startup_trace("lifespan_ready")
    try:
        yield
    finally:
        stop_event.set()
        if worker_thread is not None:
            worker_thread.join(timeout=5)
            startup_trace("parse_worker_stopped")
        if recovery_thread is not None:
            recovery_thread.join(timeout=5)
            startup_trace("parse_recovery_stopped")


app = FastAPI(
    title="Local Paper Knowledge Engine",
    version="0.1.0",
    description="A local-first paper knowledge engine organized by research idea spaces.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "http://tauri.localhost",
        "tauri://localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(spaces_router)
app.include_router(papers_router)
app.include_router(search_router)
app.include_router(cards_router)
app.include_router(agent_router)


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    """Serve the main web UI entry point."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        return HTMLResponse(content=content)
    return HTMLResponse(
        content="<h1>Local Paper Knowledge Engine</h1><p>UI not found.</p>",
        status_code=200,
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    """Health check endpoint returning service status."""
    return {
        "status": "healthy",
        "service": "Local Paper Knowledge Engine",
        "version": "0.1.0",
    }


@app.get("/api/info")
def get_app_info() -> dict[str, str]:
    """Return runtime information about the application."""
    return {
        "project_root": str(Path(__file__).resolve().parents[2]),
        "os": sys.platform,
    }
