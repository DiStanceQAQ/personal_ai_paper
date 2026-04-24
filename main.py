"""Local Paper Knowledge Engine - FastAPI application."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from db import init_db
from routes_spaces import router as spaces_router
from routes_papers import router as papers_router
from routes_search import router as search_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize the database on startup."""
    init_db()
    yield


app = FastAPI(
    title="Local Paper Knowledge Engine",
    version="0.1.0",
    description="A local-first paper knowledge engine organized by research idea spaces.",
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Register API routers
app.include_router(spaces_router)
app.include_router(papers_router)
app.include_router(search_router)


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
async def health_check() -> dict[str, str]:
    """Health check endpoint returning service status."""
    return {
        "status": "healthy",
        "service": "Local Paper Knowledge Engine",
        "version": "0.1.0",
    }
