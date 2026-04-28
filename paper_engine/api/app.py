"""Local Paper Knowledge Engine FastAPI application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from db import init_db
from paper_engine.core.startup import StartupTracer
from routes_agent import router as agent_router
from routes_cards import router as cards_router
from routes_papers import router as papers_router
from routes_search import router as search_router
from routes_spaces import router as spaces_router

APP_IMPORTED_AT = time.perf_counter()
_tracer = StartupTracer("fastapi")
startup_trace = _tracer.trace


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize the database on startup."""
    startup_trace("lifespan_start")
    init_db_started_at = time.perf_counter()
    init_db()
    startup_trace(
        "database_ready",
        init_db_ms=f"{(time.perf_counter() - init_db_started_at) * 1000:.1f}",
    )
    startup_trace("lifespan_ready")
    yield


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
