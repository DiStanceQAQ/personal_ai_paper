# Backend Rearchitecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Python backend into a formal `paper_engine` package with clear API, storage, PDF, analysis, retrieval, agent, MCP, and sidecar boundaries while keeping the frontend HTTP API contract stable.

**Architecture:** Migrate in staged slices so every checkpoint can run tests. First introduce package entry points, then move storage, routes, PDF, analysis/retrieval/agent, MCP, and sidecar packaging, then extract route SQL into services and repositories and remove root-level backend modules. Frontend paths and JSON semantics stay stable; Python imports, packaging, tests, and PyInstaller hidden imports move to package paths.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Pydantic, HTTPX, PyInstaller, Tauri externalBin, pytest, mypy, Vite TypeScript API contract tests.

---

## Scope Check

This plan covers one coherent backend rearchitecture. The touched subsystems are coupled by Python import paths, console scripts, PyInstaller hidden imports, and route behavior, so implementing them under one plan is safer than producing separate incompatible package migrations. The plan still lands in small commits with tests after each domain move.

## File Structure

Create these package files:

```text
paper_engine/__init__.py
paper_engine/api/__init__.py
paper_engine/api/app.py
paper_engine/api/dependencies.py
paper_engine/api/routes/__init__.py
paper_engine/api/routes/spaces.py
paper_engine/api/routes/papers.py
paper_engine/api/routes/cards.py
paper_engine/api/routes/search.py
paper_engine/api/routes/agent.py
paper_engine/core/__init__.py
paper_engine/core/config.py
paper_engine/core/errors.py
paper_engine/core/startup.py
paper_engine/storage/__init__.py
paper_engine/storage/database.py
paper_engine/storage/migrations.py
paper_engine/storage/repositories/__init__.py
paper_engine/storage/repositories/spaces.py
paper_engine/storage/repositories/papers.py
paper_engine/storage/repositories/cards.py
paper_engine/storage/repositories/settings.py
paper_engine/spaces/__init__.py
paper_engine/spaces/service.py
paper_engine/papers/__init__.py
paper_engine/papers/service.py
paper_engine/cards/__init__.py
paper_engine/cards/extraction.py
paper_engine/cards/service.py
paper_engine/pdf/__init__.py
paper_engine/pdf/compat.py
paper_engine/pdf/models.py
paper_engine/pdf/router.py
paper_engine/pdf/profile.py
paper_engine/pdf/chunking.py
paper_engine/pdf/persistence.py
paper_engine/pdf/backends/__init__.py
paper_engine/pdf/backends/base.py
paper_engine/pdf/backends/docling.py
paper_engine/pdf/backends/grobid.py
paper_engine/pdf/backends/legacy.py
paper_engine/pdf/backends/llamaparse.py
paper_engine/pdf/backends/pymupdf4llm.py
paper_engine/analysis/__init__.py
paper_engine/analysis/models.py
paper_engine/analysis/pipeline.py
paper_engine/analysis/prompts.py
paper_engine/analysis/verifier.py
paper_engine/retrieval/__init__.py
paper_engine/retrieval/embeddings.py
paper_engine/retrieval/hybrid.py
paper_engine/retrieval/lexical.py
paper_engine/agent/__init__.py
paper_engine/agent/executor.py
paper_engine/agent/llm_client.py
paper_engine/mcp/__init__.py
paper_engine/mcp/server.py
paper_engine/sidecar/__init__.py
paper_engine/sidecar/api.py
```

Modify these support files:

```text
pyproject.toml
Makefile
scripts/build_sidecars.py
scripts/eval_analysis_pipeline.py
scripts/eval_pdf_pipeline.py
scripts/run_tauri.mjs
README.md
docs/pdf-ingestion.md
docs/packaging.md
tests/*.py
tests/eval/*.py
frontend/src/api-contract.test-d.ts
```

Root-level backend files to remove by the final task:

```text
agent_executor.py
analysis_models.py
analysis_pipeline.py
analysis_prompts.py
analysis_verifier.py
api_sidecar.py
card_extractor.py
config.py
db.py
db_migrations.py
embeddings.py
hybrid_search.py
llm_client.py
main.py
mcp_server.py
parser.py
pdf_backend_base.py
pdf_backend_docling.py
pdf_backend_grobid.py
pdf_backend_legacy.py
pdf_backend_llamaparse.py
pdf_backend_pymupdf4llm.py
pdf_chunker.py
pdf_models.py
pdf_persistence.py
pdf_profile.py
pdf_router.py
routes_agent.py
routes_cards.py
routes_papers.py
routes_search.py
routes_spaces.py
search.py
```

---

### Task 1: Package Entry Points And Startup Utilities

**Files:**
- Create: `paper_engine/__init__.py`
- Create: `paper_engine/api/__init__.py`
- Create: `paper_engine/api/app.py`
- Create: `paper_engine/api/dependencies.py`
- Create: `paper_engine/core/__init__.py`
- Create: `paper_engine/core/startup.py`
- Create: `paper_engine/sidecar/__init__.py`
- Create: `paper_engine/sidecar/api.py`
- Modify: `tests/test_main.py`
- Modify: `tests/test_api_sidecar.py`

- [ ] **Step 1: Write failing package app and sidecar tests**

Replace the imports at the top of `tests/test_main.py`:

```python
import paper_engine.api.app as app_module
from paper_engine.api.app import app
```

Replace `main.startup_trace(...)` calls in `tests/test_main.py` with:

```python
app_module.startup_trace(...)
```

Replace the imports at the top of `tests/test_api_sidecar.py`:

```python
import paper_engine.sidecar.api as api_sidecar
from paper_engine.sidecar.api import ServerSettings, parse_args
```

In `test_main_sets_data_dir_before_importing_app`, change the import assertion:

```python
def fake_import_module(name: str) -> SimpleNamespace:
    assert name == "paper_engine.api.app"
    calls.append(os.environ.get("PAPER_ENGINE_DATA_DIR"))
    return SimpleNamespace(app=fake_app)
```

- [ ] **Step 2: Run the package entry tests and verify failure**

Run:

```bash
.venv/bin/pytest -q tests/test_main.py tests/test_api_sidecar.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'paper_engine'`.

- [ ] **Step 3: Create package directories and empty `__init__.py` files**

Run:

```bash
mkdir -p paper_engine/api paper_engine/core paper_engine/sidecar
touch paper_engine/__init__.py paper_engine/api/__init__.py paper_engine/api/dependencies.py paper_engine/core/__init__.py paper_engine/sidecar/__init__.py
```

- [ ] **Step 4: Implement shared startup tracing**

Create `paper_engine/core/startup.py`:

```python
"""Startup tracing utilities for API and sidecar entry points."""

from __future__ import annotations

import os
import sys
import time

STARTUP_TRACE_ENV = "PAPER_ENGINE_STARTUP_TRACE"


class StartupTracer:
    """Write structured startup timing lines when tracing is enabled."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._started_at = time.perf_counter()

    def trace(self, event: str, **fields: object) -> None:
        if os.environ.get(STARTUP_TRACE_ENV) != "1":
            return

        elapsed_ms = (time.perf_counter() - self._started_at) * 1000
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        suffix = f" {details}" if details else ""
        print(
            f"[paper-engine startup] {self._label} event={event} "
            f"elapsed_ms={elapsed_ms:.1f}{suffix}",
            file=sys.stderr,
            flush=True,
        )
```

- [ ] **Step 5: Implement package FastAPI app as a compatibility shell**

Create `paper_engine/api/app.py` by copying the behavior from root `main.py`, but import existing root modules for this first checkpoint:

```python
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
```

- [ ] **Step 6: Implement package API sidecar**

Create `paper_engine/sidecar/api.py`:

```python
"""Packaged FastAPI sidecar entry point for the Tauri desktop app."""

from __future__ import annotations

import argparse
import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from paper_engine.core.startup import StartupTracer

_tracer = StartupTracer("python")
startup_trace = _tracer.trace


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    data_dir: Path | None


def parse_args(argv: Sequence[str] | None = None) -> ServerSettings:
    parser = argparse.ArgumentParser(
        prog="paper-engine-api",
        description="Run the Local Paper Knowledge Engine API server.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    data_dir = args.data_dir.resolve() if args.data_dir else None
    return ServerSettings(host=str(args.host), port=int(args.port), data_dir=data_dir)


def main(argv: Sequence[str] | None = None) -> None:
    startup_trace("main_entry")
    settings = parse_args(argv)
    startup_trace(
        "args_parsed",
        host=settings.host,
        port=settings.port,
        data_dir=settings.data_dir or "",
    )
    if settings.data_dir is not None:
        os.environ["PAPER_ENGINE_DATA_DIR"] = str(settings.data_dir)
        startup_trace("data_dir_configured", data_dir=settings.data_dir)

    startup_trace("uvicorn_import_start")
    import uvicorn

    startup_trace("uvicorn_import_done")
    startup_trace("app_import_start")
    app: Any = importlib.import_module("paper_engine.api.app").app
    startup_trace("app_import_done")
    startup_trace("uvicorn_run_start", host=settings.host, port=settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run package entry tests and verify pass**

Run:

```bash
.venv/bin/pytest -q tests/test_main.py tests/test_api_sidecar.py
```

Expected: PASS.

- [ ] **Step 8: Commit package entry points**

Run:

```bash
git add paper_engine tests/test_main.py tests/test_api_sidecar.py
git commit -m "Introduce backend package entry points"
```

---

### Task 2: Move Core Configuration And Storage

**Files:**
- Move: `config.py` -> `paper_engine/core/config.py`
- Move: `db.py` -> `paper_engine/storage/database.py`
- Move: `db_migrations.py` -> `paper_engine/storage/migrations.py`
- Modify: `paper_engine/api/app.py`
- Modify: all Python files importing `config`, `db`, or `db_migrations`
- Modify: `tests/test_config.py`
- Modify: `tests/test_db.py`
- Modify: `tests/test_db_migrations.py`

- [ ] **Step 1: Write failing package storage imports in tests**

Update test imports:

```python
from paper_engine.core.config import APP_DATA_DIR, DATABASE_PATH, SPACES_DIR
from paper_engine.storage.database import SCHEMA_SQL, get_connection, get_table_names, init_db
from paper_engine.storage.migrations import apply_migrations, get_schema_version, set_schema_version
```

Run:

```bash
.venv/bin/pytest -q tests/test_config.py tests/test_db.py tests/test_db_migrations.py
```

Expected: FAIL until the modules are moved.

- [ ] **Step 2: Move files into package paths**

Run:

```bash
mkdir -p paper_engine/storage
git mv config.py paper_engine/core/config.py
git mv db.py paper_engine/storage/database.py
git mv db_migrations.py paper_engine/storage/migrations.py
touch paper_engine/storage/__init__.py
```

- [ ] **Step 3: Update storage imports inside moved modules**

In `paper_engine/storage/database.py`, replace:

```python
from config import APP_DATA_DIR, DATABASE_PATH
from db_migrations import apply_migrations
```

with:

```python
from paper_engine.core.config import APP_DATA_DIR, DATABASE_PATH
from paper_engine.storage.migrations import apply_migrations
```

- [ ] **Step 4: Rewrite repository-wide core/storage imports**

Run these mechanical import rewrites:

```bash
perl -pi -e 's/from db import /from paper_engine.storage.database import /g' $(rg -l "from db import" --glob "*.py")
perl -pi -e 's/import db_migrations/import paper_engine.storage.migrations as db_migrations/g' $(rg -l "import db_migrations" --glob "*.py")
perl -pi -e 's/from db_migrations import /from paper_engine.storage.migrations import /g' $(rg -l "from db_migrations import" --glob "*.py")
perl -pi -e 's/import config/from paper_engine.core import config/g' $(rg -l "^import config$" --glob "*.py")
perl -pi -e 's/from config import /from paper_engine.core.config import /g' $(rg -l "from config import" --glob "*.py")
```

- [ ] **Step 5: Run storage and route smoke tests**

Run:

```bash
.venv/bin/pytest -q tests/test_config.py tests/test_db.py tests/test_db_migrations.py tests/test_routes_spaces.py tests/test_routes_papers.py tests/test_routes_cards.py
```

Expected: PASS.

- [ ] **Step 6: Commit core and storage move**

Run:

```bash
git add -A
git commit -m "Move core config and storage into backend package"
```

---

### Task 3: Move API Routes Into Package

**Files:**
- Create: `paper_engine/api/routes/__init__.py`
- Move: `routes_spaces.py` -> `paper_engine/api/routes/spaces.py`
- Move: `routes_papers.py` -> `paper_engine/api/routes/papers.py`
- Move: `routes_cards.py` -> `paper_engine/api/routes/cards.py`
- Move: `routes_search.py` -> `paper_engine/api/routes/search.py`
- Move: `routes_agent.py` -> `paper_engine/api/routes/agent.py`
- Modify: `paper_engine/api/app.py`
- Modify: route tests importing route modules

- [ ] **Step 1: Update `paper_engine/api/app.py` route imports**

Replace root route imports with:

```python
from paper_engine.api.routes.agent import router as agent_router
from paper_engine.api.routes.cards import router as cards_router
from paper_engine.api.routes.papers import router as papers_router
from paper_engine.api.routes.search import router as search_router
from paper_engine.api.routes.spaces import router as spaces_router
```

Run:

```bash
.venv/bin/pytest -q tests/test_main.py
```

Expected: FAIL with `ModuleNotFoundError` for `paper_engine.api.routes`.

- [ ] **Step 2: Move route files**

Run:

```bash
mkdir -p paper_engine/api/routes
touch paper_engine/api/routes/__init__.py
git mv routes_spaces.py paper_engine/api/routes/spaces.py
git mv routes_papers.py paper_engine/api/routes/papers.py
git mv routes_cards.py paper_engine/api/routes/cards.py
git mv routes_search.py paper_engine/api/routes/search.py
git mv routes_agent.py paper_engine/api/routes/agent.py
```

- [ ] **Step 3: Rewrite test route imports**

Run:

```bash
perl -pi -e 's/import routes_papers/import paper_engine.api.routes.papers as routes_papers/g' $(rg -l "import routes_papers" tests --glob "*.py")
perl -pi -e 's/from routes_cards import /from paper_engine.api.routes.cards import /g' $(rg -l "from routes_cards import" tests --glob "*.py")
```

- [ ] **Step 4: Run API route tests**

Run:

```bash
.venv/bin/pytest -q tests/test_main.py tests/test_routes_spaces.py tests/test_routes_papers.py tests/test_routes_cards.py tests/test_search.py tests/test_agent.py tests/test_embeddings_parse_integration.py
```

Expected: PASS.

- [ ] **Step 5: Commit route move**

Run:

```bash
git add -A
git commit -m "Move API routes into backend package"
```

---

### Task 4: Move PDF Package And Compatibility Wrapper

**Files:**
- Create: `paper_engine/pdf/__init__.py`
- Create: `paper_engine/pdf/backends/__init__.py`
- Move: `parser.py` -> `paper_engine/pdf/compat.py`
- Move: `pdf_models.py` -> `paper_engine/pdf/models.py`
- Move: `pdf_router.py` -> `paper_engine/pdf/router.py`
- Move: `pdf_profile.py` -> `paper_engine/pdf/profile.py`
- Move: `pdf_chunker.py` -> `paper_engine/pdf/chunking.py`
- Move: `pdf_persistence.py` -> `paper_engine/pdf/persistence.py`
- Move: `pdf_backend_base.py` -> `paper_engine/pdf/backends/base.py`
- Move: `pdf_backend_docling.py` -> `paper_engine/pdf/backends/docling.py`
- Move: `pdf_backend_grobid.py` -> `paper_engine/pdf/backends/grobid.py`
- Move: `pdf_backend_legacy.py` -> `paper_engine/pdf/backends/legacy.py`
- Move: `pdf_backend_llamaparse.py` -> `paper_engine/pdf/backends/llamaparse.py`
- Move: `pdf_backend_pymupdf4llm.py` -> `paper_engine/pdf/backends/pymupdf4llm.py`
- Modify: all imports of `parser`, `pdf_*`, and `pdf_backend_*`
- Modify: PDF tests

- [ ] **Step 1: Update one parser import test to package path**

In `tests/test_parser_imports.py`, replace the subprocess code string with:

```python
code = (
    "import sys\n"
    "import paper_engine.pdf.compat\n"
    "raise SystemExit(1 if 'pymupdf' in sys.modules else 0)\n"
)
```

Run:

```bash
.venv/bin/pytest -q tests/test_parser_imports.py
```

Expected: FAIL until the PDF package exists.

- [ ] **Step 2: Move PDF files**

Run:

```bash
mkdir -p paper_engine/pdf/backends
touch paper_engine/pdf/__init__.py paper_engine/pdf/backends/__init__.py
git mv parser.py paper_engine/pdf/compat.py
git mv pdf_models.py paper_engine/pdf/models.py
git mv pdf_router.py paper_engine/pdf/router.py
git mv pdf_profile.py paper_engine/pdf/profile.py
git mv pdf_chunker.py paper_engine/pdf/chunking.py
git mv pdf_persistence.py paper_engine/pdf/persistence.py
git mv pdf_backend_base.py paper_engine/pdf/backends/base.py
git mv pdf_backend_docling.py paper_engine/pdf/backends/docling.py
git mv pdf_backend_grobid.py paper_engine/pdf/backends/grobid.py
git mv pdf_backend_legacy.py paper_engine/pdf/backends/legacy.py
git mv pdf_backend_llamaparse.py paper_engine/pdf/backends/llamaparse.py
git mv pdf_backend_pymupdf4llm.py paper_engine/pdf/backends/pymupdf4llm.py
```

- [ ] **Step 3: Rewrite PDF imports**

Run these mechanical rewrites:

```bash
perl -pi -e 's/from parser import /from paper_engine.pdf.compat import /g' $(rg -l "from parser import" --glob "*.py")
perl -pi -e 's/import parser/import paper_engine.pdf.compat as parser/g' $(rg -l "^import parser$" --glob "*.py")
perl -pi -e 's/from pdf_models import /from paper_engine.pdf.models import /g' $(rg -l "from pdf_models import" --glob "*.py")
perl -pi -e 's/import pdf_chunker/import paper_engine.pdf.chunking as pdf_chunker/g' $(rg -l "import pdf_chunker" --glob "*.py")
perl -pi -e 's/from pdf_chunker import /from paper_engine.pdf.chunking import /g' $(rg -l "from pdf_chunker import" --glob "*.py")
perl -pi -e 's/from pdf_persistence import /from paper_engine.pdf.persistence import /g' $(rg -l "from pdf_persistence import" --glob "*.py")
perl -pi -e 's/import pdf_persistence/import paper_engine.pdf.persistence as pdf_persistence/g' $(rg -l "import pdf_persistence" --glob "*.py")
perl -pi -e 's/from pdf_profile import /from paper_engine.pdf.profile import /g' $(rg -l "from pdf_profile import" --glob "*.py")
perl -pi -e 's/from pdf_router import /from paper_engine.pdf.router import /g' $(rg -l "from pdf_router import" --glob "*.py")
perl -pi -e 's/from pdf_backend_base import /from paper_engine.pdf.backends.base import /g' $(rg -l "from pdf_backend_base import" --glob "*.py")
perl -pi -e 's/from pdf_backend_docling import /from paper_engine.pdf.backends.docling import /g' $(rg -l "from pdf_backend_docling import" --glob "*.py")
perl -pi -e 's/from pdf_backend_grobid import /from paper_engine.pdf.backends.grobid import /g' $(rg -l "from pdf_backend_grobid import" --glob "*.py")
perl -pi -e 's/from pdf_backend_legacy import /from paper_engine.pdf.backends.legacy import /g' $(rg -l "from pdf_backend_legacy import" --glob "*.py")
perl -pi -e 's/from pdf_backend_llamaparse import /from paper_engine.pdf.backends.llamaparse import /g' $(rg -l "from pdf_backend_llamaparse import" --glob "*.py")
perl -pi -e 's/from pdf_backend_pymupdf4llm import /from paper_engine.pdf.backends.pymupdf4llm import /g' $(rg -l "from pdf_backend_pymupdf4llm import" --glob "*.py")
```

- [ ] **Step 4: Fix lazy imports inside `paper_engine/pdf/compat.py` and `paper_engine/pdf/router.py`**

Ensure `paper_engine/pdf/compat.py` imports package modules:

```python
from paper_engine.pdf.backends.base import ParserBackendUnavailable
from paper_engine.pdf.backends.legacy import (
    LegacyPyMuPDFOpenError,
    LegacyPyMuPDFBackend,
    _guess_passage_type,
    _guess_section,
    _split_paragraphs,
)
```

Ensure lazy imports use package paths:

```python
from paper_engine.pdf.profile import inspect_pdf as _inspect_pdf
from paper_engine.pdf.router import parse_pdf
from paper_engine.pdf.chunking import chunk_parse_document as _chunk_parse_document
from paper_engine.pdf.persistence import persist_parse_result as _persist_parse_result
```

Ensure `paper_engine/pdf/router.py` imports package backends:

```python
from paper_engine.pdf.backends.base import (
    ParserBackendError,
    ParserBackendUnavailable,
    PdfParserBackend,
)
from paper_engine.pdf.backends.docling import DoclingBackend
from paper_engine.pdf.backends.grobid import GrobidClient, get_configured_grobid_client
from paper_engine.pdf.backends.legacy import LegacyPyMuPDFBackend
from paper_engine.pdf.backends.llamaparse import get_configured_llamaparse_backend
from paper_engine.pdf.backends.pymupdf4llm import PyMuPDF4LLMBackend
from paper_engine.pdf.models import ParseDocument, PdfQualityReport
```

- [ ] **Step 5: Run focused PDF tests**

Run:

```bash
.venv/bin/pytest -q tests/test_parser_imports.py tests/test_parser.py tests/test_pdf_models.py tests/test_pdf_backend_base.py tests/test_pdf_backend_legacy.py tests/test_pdf_backend_docling.py tests/test_pdf_backend_grobid.py tests/test_pdf_backend_llamaparse.py tests/test_pdf_backend_pymupdf4llm.py tests/test_pdf_router.py tests/test_pdf_profile.py tests/test_pdf_chunker.py tests/test_pdf_persistence.py tests/test_embeddings_parse_integration.py
```

Expected: PASS.

- [ ] **Step 6: Commit PDF package move**

Run:

```bash
git add -A
git commit -m "Move PDF pipeline into backend package"
```

---

### Task 5: Move Analysis, Retrieval, Cards Extraction, And Agent Modules

**Files:**
- Move: `analysis_models.py` -> `paper_engine/analysis/models.py`
- Move: `analysis_pipeline.py` -> `paper_engine/analysis/pipeline.py`
- Move: `analysis_prompts.py` -> `paper_engine/analysis/prompts.py`
- Move: `analysis_verifier.py` -> `paper_engine/analysis/verifier.py`
- Move: `search.py` -> `paper_engine/retrieval/lexical.py`
- Move: `hybrid_search.py` -> `paper_engine/retrieval/hybrid.py`
- Move: `embeddings.py` -> `paper_engine/retrieval/embeddings.py`
- Move: `card_extractor.py` -> `paper_engine/cards/extraction.py`
- Move: `agent_executor.py` -> `paper_engine/agent/executor.py`
- Move: `llm_client.py` -> `paper_engine/agent/llm_client.py`
- Modify: imports in routes, PDF persistence, MCP, tests, scripts

- [ ] **Step 1: Create target packages**

Run:

```bash
mkdir -p paper_engine/analysis paper_engine/retrieval paper_engine/cards paper_engine/agent
touch paper_engine/analysis/__init__.py paper_engine/retrieval/__init__.py paper_engine/cards/__init__.py paper_engine/agent/__init__.py
```

- [ ] **Step 2: Update one analysis test to package imports and verify failure**

In `tests/test_analysis_models.py`, replace imports with:

```python
from paper_engine.api.routes.cards import CARD_TYPES as ROUTE_CARD_TYPES
from paper_engine.analysis.models import (
    AnalysisQualityReport,
    CardExtraction,
    CardExtractionBatch,
    MergedAnalysisResult,
    PaperMetadataExtraction,
)
```

Run:

```bash
.venv/bin/pytest -q tests/test_analysis_models.py
```

Expected: FAIL until analysis modules are moved.

- [ ] **Step 3: Move domain utility modules**

Run:

```bash
git mv analysis_models.py paper_engine/analysis/models.py
git mv analysis_pipeline.py paper_engine/analysis/pipeline.py
git mv analysis_prompts.py paper_engine/analysis/prompts.py
git mv analysis_verifier.py paper_engine/analysis/verifier.py
git mv search.py paper_engine/retrieval/lexical.py
git mv hybrid_search.py paper_engine/retrieval/hybrid.py
git mv embeddings.py paper_engine/retrieval/embeddings.py
git mv card_extractor.py paper_engine/cards/extraction.py
git mv agent_executor.py paper_engine/agent/executor.py
git mv llm_client.py paper_engine/agent/llm_client.py
```

- [ ] **Step 4: Rewrite analysis, retrieval, card, and agent imports**

Run:

```bash
perl -pi -e 's/from analysis_models import /from paper_engine.analysis.models import /g' $(rg -l "from analysis_models import" --glob "*.py")
perl -pi -e 's/import analysis_pipeline/import paper_engine.analysis.pipeline as analysis_pipeline/g' $(rg -l "import analysis_pipeline" --glob "*.py")
perl -pi -e 's/from analysis_pipeline import /from paper_engine.analysis.pipeline import /g' $(rg -l "from analysis_pipeline import" --glob "*.py")
perl -pi -e 's/from analysis_prompts import /from paper_engine.analysis.prompts import /g' $(rg -l "from analysis_prompts import" --glob "*.py")
perl -pi -e 's/from analysis_verifier import /from paper_engine.analysis.verifier import /g' $(rg -l "from analysis_verifier import" --glob "*.py")
perl -pi -e 's/from search import /from paper_engine.retrieval.lexical import /g' $(rg -l "from search import" --glob "*.py")
perl -pi -e 's/from hybrid_search import /from paper_engine.retrieval.hybrid import /g' $(rg -l "from hybrid_search import" --glob "*.py")
perl -pi -e 's/import hybrid_search/import paper_engine.retrieval.hybrid as hybrid_search/g' $(rg -l "import hybrid_search" --glob "*.py")
perl -pi -e 's/from embeddings import /from paper_engine.retrieval.embeddings import /g' $(rg -l "from embeddings import" --glob "*.py")
perl -pi -e 's/import embeddings/import paper_engine.retrieval.embeddings as embeddings/g' $(rg -l "import embeddings" --glob "*.py")
perl -pi -e 's/from card_extractor import /from paper_engine.cards.extraction import /g' $(rg -l "from card_extractor import" --glob "*.py")
perl -pi -e 's/from agent_executor import /from paper_engine.agent.executor import /g' $(rg -l "from agent_executor import" --glob "*.py")
perl -pi -e 's/from llm_client import /from paper_engine.agent.llm_client import /g' $(rg -l "from llm_client import" --glob "*.py")
perl -pi -e 's/import llm_client/import paper_engine.agent.llm_client as llm_client/g' $(rg -l "import llm_client" --glob "*.py")
```

- [ ] **Step 5: Fix moved-module internal imports**

Check and manually correct imports inside:

```text
paper_engine/analysis/pipeline.py
paper_engine/analysis/prompts.py
paper_engine/analysis/verifier.py
paper_engine/retrieval/lexical.py
paper_engine/retrieval/hybrid.py
paper_engine/retrieval/embeddings.py
paper_engine/pdf/persistence.py
paper_engine/agent/executor.py
paper_engine/agent/llm_client.py
paper_engine/api/routes/agent.py
paper_engine/api/routes/cards.py
```

Use these package forms:

```python
from paper_engine.analysis.models import ...
from paper_engine.analysis.prompts import ...
from paper_engine.analysis.verifier import ...
from paper_engine.agent.llm_client import ...
from paper_engine.pdf.chunking import count_text_tokens
from paper_engine.retrieval.embeddings import ...
from paper_engine.retrieval.hybrid import ...
from paper_engine.retrieval.lexical import FTS_TABLE
from paper_engine.storage.database import get_connection
```

- [ ] **Step 6: Run focused analysis, retrieval, card, and agent tests**

Run:

```bash
.venv/bin/pytest -q tests/test_analysis_models.py tests/test_analysis_prompts.py tests/test_analysis_verifier.py tests/test_analysis_pipeline_selection.py tests/test_analysis_pipeline_dedup.py tests/test_analysis_pipeline_cards.py tests/test_analysis_pipeline_metadata.py tests/test_analysis_persistence.py tests/test_embeddings.py tests/test_hybrid_search.py tests/test_search.py tests/test_card_extractor.py tests/test_card_extractor_domain_neutral.py tests/test_routes_cards.py tests/test_llm_client.py tests/test_agent_executor.py tests/test_agent.py
```

Expected: PASS.

- [ ] **Step 7: Commit analysis and retrieval move**

Run:

```bash
git add -A
git commit -m "Move analysis retrieval cards and agent modules"
```

---

### Task 6: Move MCP Server And Update Packaging Metadata

**Files:**
- Create: `paper_engine/mcp/__init__.py`
- Move: `mcp_server.py` -> `paper_engine/mcp/server.py`
- Modify: `pyproject.toml`
- Modify: `Makefile`
- Modify: `scripts/build_sidecars.py`
- Modify: `tests/test_mcp.py`
- Modify: `tests/test_build_sidecars.py`

- [ ] **Step 1: Update tests to package MCP and package discovery expectations**

In `tests/test_mcp.py`, replace any direct root MCP imports with:

```python
import paper_engine.mcp.server as mcp_server
```

In `tests/test_build_sidecars.py`, replace `test_pyproject_packages_database_migrations_module` with:

```python
def test_pyproject_uses_backend_package_discovery() -> None:
    pyproject = tomllib.loads((build_sidecars.ROOT / "pyproject.toml").read_text())

    find_config = pyproject["tool"]["setuptools"]["packages"]["find"]
    assert find_config["include"] == ["paper_engine*"]
    assert "py-modules" not in pyproject["tool"]["setuptools"]
```

Run:

```bash
.venv/bin/pytest -q tests/test_mcp.py tests/test_build_sidecars.py
```

Expected: FAIL until MCP and packaging metadata are updated.

- [ ] **Step 2: Move MCP server**

Run:

```bash
mkdir -p paper_engine/mcp
touch paper_engine/mcp/__init__.py
git mv mcp_server.py paper_engine/mcp/server.py
```

- [ ] **Step 3: Update `pyproject.toml` package metadata**

Replace console scripts:

```toml
[project.scripts]
paper-engine-api = "paper_engine.sidecar.api:main"
paper-engine-mcp = "paper_engine.mcp.server:main"
```

Replace `[tool.setuptools] py-modules = [...]` with:

```toml
[tool.setuptools.packages.find]
include = ["paper_engine*"]
```

Keep dependency and mypy sections unchanged except for module names that no longer exist. Replace mypy overrides:

```toml
[[tool.mypy.overrides]]
module = "pymupdf"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "mcp"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "paper_engine.pdf.profile"
disable_error_code = ["no-untyped-call"]

[[tool.mypy.overrides]]
module = "paper_engine.pdf.compat"
disable_error_code = ["no-untyped-call", "no-any-return"]
```

- [ ] **Step 4: Update `Makefile` dev and typecheck commands**

Change:

```make
dev:
	uvicorn main:app --reload --host 127.0.0.1 --port 8000

typecheck:
	mypy main.py api_sidecar.py tests/
```

to:

```make
dev:
	uvicorn paper_engine.api.app:app --reload --host 127.0.0.1 --port 8000

typecheck:
	mypy paper_engine tests/
```

- [ ] **Step 5: Update sidecar build hidden imports and entrypoints**

In `scripts/build_sidecars.py`, replace hidden import groups with package paths:

```python
PDF_PIPELINE_HIDDEN_IMPORTS = (
    "paper_engine.storage.migrations",
    "paper_engine.pdf.backends.base",
    "paper_engine.pdf.backends.docling",
    "paper_engine.pdf.backends.grobid",
    "paper_engine.pdf.backends.legacy",
    "paper_engine.pdf.backends.llamaparse",
    "paper_engine.pdf.backends.pymupdf4llm",
    "paper_engine.pdf.chunking",
    "paper_engine.pdf.models",
    "paper_engine.pdf.persistence",
    "paper_engine.pdf.profile",
    "paper_engine.pdf.router",
    "pymupdf",
    "pymupdf4llm",
    "tiktoken",
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
)
ANALYSIS_PIPELINE_HIDDEN_IMPORTS = (
    "paper_engine.analysis.models",
    "paper_engine.analysis.pipeline",
    "paper_engine.analysis.prompts",
    "paper_engine.analysis.verifier",
)
RETRIEVAL_HIDDEN_IMPORTS = (
    "paper_engine.retrieval.embeddings",
    "paper_engine.retrieval.hybrid",
    "paper_engine.retrieval.lexical",
)
API_HIDDEN_IMPORTS = (
    "paper_engine.api.app",
    "paper_engine.agent.executor",
    *ANALYSIS_PIPELINE_HIDDEN_IMPORTS,
    "paper_engine.cards.extraction",
    "paper_engine.core.config",
    "paper_engine.storage.database",
    "paper_engine.agent.llm_client",
    "paper_engine.pdf.compat",
    *PDF_PIPELINE_HIDDEN_IMPORTS,
    *RETRIEVAL_HIDDEN_IMPORTS,
    "paper_engine.api.routes.agent",
    "paper_engine.api.routes.cards",
    "paper_engine.api.routes.papers",
    "paper_engine.api.routes.search",
    "paper_engine.api.routes.spaces",
)
MCP_HIDDEN_IMPORTS = (
    "paper_engine.core.config",
    "paper_engine.storage.database",
    "paper_engine.storage.migrations",
    *RETRIEVAL_HIDDEN_IMPORTS,
)
```

Change target entrypoints:

```python
entrypoint="paper_engine/sidecar/api.py"
entrypoint="paper_engine/mcp/server.py"
```

Update `tests/test_build_sidecars.py` expected modules to package paths, for example:

```python
expected_modules = {
    "paper_engine.storage.migrations",
    "paper_engine.analysis.models",
    "paper_engine.analysis.pipeline",
    "paper_engine.analysis.prompts",
    "paper_engine.analysis.verifier",
    "paper_engine.retrieval.embeddings",
    "paper_engine.retrieval.hybrid",
    "paper_engine.pdf.backends.base",
    "paper_engine.pdf.backends.docling",
    "paper_engine.pdf.backends.grobid",
    "paper_engine.pdf.backends.legacy",
    "paper_engine.pdf.backends.llamaparse",
    "paper_engine.pdf.backends.pymupdf4llm",
    "paper_engine.pdf.chunking",
    "paper_engine.pdf.models",
    "paper_engine.pdf.persistence",
    "paper_engine.pdf.profile",
    "paper_engine.pdf.router",
    "pymupdf",
    "pymupdf4llm",
    "tiktoken",
}
```

- [ ] **Step 6: Run packaging tests**

Run:

```bash
.venv/bin/pytest -q tests/test_mcp.py tests/test_api_sidecar.py tests/test_build_sidecars.py tests/test_run_tauri_script.py
```

Expected: PASS.

- [ ] **Step 7: Commit MCP and packaging move**

Run:

```bash
git add -A
git commit -m "Move MCP server and package metadata"
```

---

### Task 7: Extract Repositories And Services From Routes

**Files:**
- Create: `paper_engine/core/errors.py`
- Create: `paper_engine/storage/repositories/__init__.py`
- Create: `paper_engine/storage/repositories/settings.py`
- Create: `paper_engine/storage/repositories/spaces.py`
- Create: `paper_engine/storage/repositories/papers.py`
- Create: `paper_engine/storage/repositories/cards.py`
- Create: `paper_engine/spaces/__init__.py`
- Create: `paper_engine/spaces/service.py`
- Create: `paper_engine/papers/__init__.py`
- Create: `paper_engine/papers/service.py`
- Create: `paper_engine/cards/service.py`
- Modify: `paper_engine/api/routes/spaces.py`
- Modify: `paper_engine/api/routes/papers.py`
- Modify: `paper_engine/api/routes/cards.py`
- Modify: `paper_engine/api/routes/search.py`
- Modify: `paper_engine/api/routes/agent.py`
- Create: `tests/test_backend_architecture.py`

- [ ] **Step 1: Write architecture tests that routes do not own raw storage**

Create `tests/test_backend_architecture.py`:

```python
"""Architecture checks for backend package boundaries."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES_DIR = ROOT / "paper_engine" / "api" / "routes"


def test_routes_do_not_open_database_connections_directly() -> None:
    offenders: list[str] = []
    for route_file in sorted(ROUTES_DIR.glob("*.py")):
        if route_file.name == "__init__.py":
            continue
        source = route_file.read_text(encoding="utf-8")
        if "get_connection(" in source or "sqlite3.connect" in source:
            offenders.append(route_file.name)

    assert offenders == []


def test_routes_do_not_contain_large_sql_blocks() -> None:
    offenders: list[str] = []
    for route_file in sorted(ROUTES_DIR.glob("*.py")):
        if route_file.name == "__init__.py":
            continue
        source = route_file.read_text(encoding="utf-8")
        if "SELECT " in source or "INSERT " in source or "UPDATE " in source or "DELETE " in source:
            offenders.append(route_file.name)

    assert offenders == []
```

Run:

```bash
.venv/bin/pytest -q tests/test_backend_architecture.py
```

Expected: FAIL because route modules still contain direct DB access and SQL.

- [ ] **Step 2: Add domain exceptions**

Create `paper_engine/core/errors.py`:

```python
"""Domain exceptions used below the FastAPI route layer."""


class PaperEngineError(Exception):
    """Base exception for expected application errors."""


class ActiveSpaceRequired(PaperEngineError):
    """Raised when an operation needs an active idea space."""


class NotFound(PaperEngineError):
    """Raised when a requested object does not exist."""


class Conflict(PaperEngineError):
    """Raised when a request conflicts with existing local state."""


class ValidationError(PaperEngineError):
    """Raised when a request is structurally invalid for this domain."""


class ParserFailed(PaperEngineError):
    """Raised when PDF parsing cannot produce a usable result."""
```

- [ ] **Step 3: Add settings repository for `app_state`**

Create `paper_engine/storage/repositories/settings.py`:

```python
"""Repository helpers for app_state settings."""

from __future__ import annotations

import sqlite3


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return "" if row is None else str(row["value"])


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_state (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
```

- [ ] **Step 4: Extract active-space helpers**

Create `paper_engine/storage/repositories/spaces.py` with functions copied from current route SQL:

```python
"""SQLite repository helpers for idea spaces."""

from __future__ import annotations

import sqlite3
from typing import Any

ACTIVE_SPACE_KEY = "active_space"


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def get_active_space(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT s.*
           FROM spaces s
           JOIN app_state a ON a.value = s.id
           WHERE a.key = ? AND s.status = 'active'""",
        (ACTIVE_SPACE_KEY,),
    ).fetchone()
    return None if row is None else row_to_dict(row)


def get_active_space_id(conn: sqlite3.Connection) -> str | None:
    space = get_active_space(conn)
    return None if space is None else str(space["id"])
```

Then move the remaining SQL from `paper_engine/api/routes/spaces.py` into repository functions named:

```python
create_space(conn, space_id: str, name: str, description: str) -> dict[str, Any]
list_spaces(conn) -> list[dict[str, Any]]
get_space(conn, space_id: str) -> dict[str, Any] | None
set_active_space(conn, space_id: str) -> dict[str, Any] | None
update_space(conn, space_id: str, fields: dict[str, Any]) -> dict[str, Any] | None
archive_space(conn, space_id: str) -> bool
delete_space(conn, space_id: str) -> bool
clear_active_space_if_matches(conn, space_id: str) -> None
```

- [ ] **Step 5: Add spaces service and thin route mapping**

Create `paper_engine/spaces/service.py`. Use `get_connection()` internally, call the spaces repository, and raise:

```python
from paper_engine.core.errors import ActiveSpaceRequired, NotFound
```

Routes in `paper_engine/api/routes/spaces.py` should call service functions and map exceptions:

```python
from fastapi import APIRouter, Body, HTTPException

from paper_engine.core.errors import ActiveSpaceRequired, NotFound
from paper_engine.spaces import service

router = APIRouter(prefix="/api/spaces", tags=["spaces"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ActiveSpaceRequired):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=500, detail="Unexpected space error")
```

Apply the same route pattern to papers and cards.

- [ ] **Step 6: Extract papers and cards repositories**

Create `paper_engine/storage/repositories/papers.py` and move SQL from `paper_engine/api/routes/papers.py` into functions named:

```python
paper_row_to_dict(row) -> dict[str, Any]
get_paper(conn, paper_id: str) -> dict[str, Any] | None
list_papers(conn, space_id: str) -> list[dict[str, Any]]
find_duplicate_by_hash(conn, space_id: str, file_hash: str) -> dict[str, Any] | None
insert_paper_upload(conn, paper_id: str, space_id: str, file_path: str, file_hash: str) -> dict[str, Any]
update_paper_fields(conn, paper_id: str, fields: dict[str, Any]) -> None
set_parse_status(conn, paper_id: str, status: str) -> None
require_paper_in_space(conn, paper_id: str, space_id: str) -> None
list_parse_runs(conn, paper_id: str, space_id: str) -> list[dict[str, Any]]
list_document_elements(conn, paper_id: str, space_id: str, element_type: str | None, page: int | None, limit: int) -> list[dict[str, Any]]
list_document_tables(conn, paper_id: str, space_id: str) -> list[dict[str, Any]]
list_passages(conn, paper_id: str) -> list[dict[str, Any]]
delete_paper_rows(conn, paper_id: str, fts_table: str) -> None
```

Create `paper_engine/storage/repositories/cards.py` and move SQL from `paper_engine/api/routes/cards.py` into functions named:

```python
card_row_to_dict(row) -> dict[str, Any]
create_user_card(conn, card_id: str, space_id: str, paper_id: str, source_passage_id: str | None, card_type: str, summary: str, confidence: float) -> dict[str, Any]
list_cards(conn, space_id: str, paper_id: str | None, card_type: str | None) -> list[dict[str, Any]]
get_card(conn, card_id: str) -> dict[str, Any] | None
update_card(conn, card_id: str, fields: dict[str, Any]) -> None
delete_card(conn, card_id: str) -> bool
persist_heuristic_card(conn, card: dict[str, Any]) -> bool
```

- [ ] **Step 7: Add papers and cards services**

Create `paper_engine/papers/service.py` and move non-HTTP behavior from `papers.py`, including:

```python
compute_sha256(file_path: Path) -> str
papers_dir(space_id: str) -> Path
upload_paper(file_name: str, content: bytes) -> dict[str, Any]
parse_paper(paper_id: str) -> dict[str, Any]
delete_paper(paper_id: str) -> dict[str, str]
```

Create `paper_engine/cards/service.py` and move non-HTTP behavior from `cards.py`, including:

```python
CARD_TYPES = [...]
create_card(...)
list_cards(...)
get_card(...)
update_card(...)
delete_card(...)
extract_cards(paper_id: str) -> dict[str, Any]
```

Keep FastAPI `UploadFile`, `Body`, `Query`, and `HTTPException` imports out of service modules.

- [ ] **Step 8: Run architecture and route behavior tests**

Run:

```bash
.venv/bin/pytest -q tests/test_backend_architecture.py tests/test_routes_spaces.py tests/test_routes_papers.py tests/test_routes_cards.py tests/test_search.py tests/test_agent.py tests/test_embeddings_parse_integration.py
```

Expected: PASS.

- [ ] **Step 9: Commit service and repository extraction**

Run:

```bash
git add -A
git commit -m "Extract backend services and repositories"
```

---

### Task 8: Remove Root Backend Modules And Update Tests

**Files:**
- Create: `tests/test_backend_package_structure.py`
- Modify: any tests still importing root backend modules
- Delete: all root-level backend module files listed in File Structure

- [ ] **Step 1: Add root-module removal test**

Create `tests/test_backend_package_structure.py`:

```python
"""Backend package structure regression tests."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ROOT_BACKEND_MODULES = {
    "agent_executor.py",
    "analysis_models.py",
    "analysis_pipeline.py",
    "analysis_prompts.py",
    "analysis_verifier.py",
    "api_sidecar.py",
    "card_extractor.py",
    "config.py",
    "db.py",
    "db_migrations.py",
    "embeddings.py",
    "hybrid_search.py",
    "llm_client.py",
    "main.py",
    "mcp_server.py",
    "parser.py",
    "pdf_backend_base.py",
    "pdf_backend_docling.py",
    "pdf_backend_grobid.py",
    "pdf_backend_legacy.py",
    "pdf_backend_llamaparse.py",
    "pdf_backend_pymupdf4llm.py",
    "pdf_chunker.py",
    "pdf_models.py",
    "pdf_persistence.py",
    "pdf_profile.py",
    "pdf_router.py",
    "routes_agent.py",
    "routes_cards.py",
    "routes_papers.py",
    "routes_search.py",
    "routes_spaces.py",
    "search.py",
}


def test_backend_modules_live_under_package() -> None:
    remaining = sorted(path for path in ROOT_BACKEND_MODULES if (ROOT / path).exists())
    assert remaining == []
```

Run:

```bash
.venv/bin/pytest -q tests/test_backend_package_structure.py
```

Expected: PASS if all moves were real `git mv` operations. FAIL lists remaining root files if any were copied instead of moved.

- [ ] **Step 2: Find stale root imports**

Run:

```bash
rg -n "^(from|import) (agent_executor|analysis_|api_sidecar|card_extractor|config|db|db_migrations|embeddings|hybrid_search|llm_client|main|mcp_server|parser|pdf_|routes_|search)" --glob "*.py"
```

Expected: no output. If output exists, replace each import with the package path introduced in earlier tasks.

- [ ] **Step 3: Run broad backend tests**

Run:

```bash
.venv/bin/pytest -q tests
```

Expected: PASS.

- [ ] **Step 4: Commit root-module cleanup**

Run:

```bash
git add -A
git commit -m "Remove root backend modules"
```

---

### Task 9: Update Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/packaging.md`
- Modify: `docs/pdf-ingestion.md`
- Modify: `docs/product-overview.md` if it mentions root startup commands

- [ ] **Step 1: Update startup documentation**

Replace root module startup examples:

```text
uvicorn main:app --reload --host 127.0.0.1 --port 8000
python mcp_server.py
```

with package entrypoints:

```text
uvicorn paper_engine.api.app:app --reload --host 127.0.0.1 --port 8000
paper-engine-mcp
```

Keep user-facing URLs and sidecar binary names unchanged.

- [ ] **Step 2: Run backend verification**

Run:

```bash
make test
```

Expected: PASS.

- [ ] **Step 3: Run type checking**

Run:

```bash
make typecheck
```

Expected: PASS, or the same mypy baseline errors that existed before this branch. If mypy reports package-path errors caused by this refactor, fix them before continuing.

- [ ] **Step 4: Run frontend API contract typecheck**

Run:

```bash
npm run frontend:typecheck
```

Expected: PASS.

- [ ] **Step 5: Run sidecar build unit tests**

Run:

```bash
.venv/bin/pytest -q tests/test_build_sidecars.py tests/test_api_sidecar.py tests/test_run_tauri_script.py
```

Expected: PASS.

- [ ] **Step 6: Run real sidecar build**

Run:

```bash
make build-sidecars
```

Expected: PyInstaller builds `paper-engine-api` and `paper-engine-mcp` into `src-tauri/binaries/` with the current host triple suffix.

- [ ] **Step 7: Commit documentation and verification adjustments**

Run:

```bash
git add README.md docs/packaging.md docs/pdf-ingestion.md docs/product-overview.md
git commit -m "Document backend package entrypoints"
```

---

## Final Acceptance Checklist

- [ ] `paper_engine/` contains all backend application code.
- [ ] Root-level backend modules listed in Task 8 are absent.
- [ ] `pyproject.toml` uses package discovery for `paper_engine*`.
- [ ] `paper-engine-api` points to `paper_engine.sidecar.api:main`.
- [ ] `paper-engine-mcp` points to `paper_engine.mcp.server:main`.
- [ ] `make dev` uses `paper_engine.api.app:app`.
- [ ] Frontend HTTP API paths and response semantics remain compatible.
- [ ] Tauri `externalBin` names remain `paper-engine-api` and `paper-engine-mcp`.
- [ ] `tests/test_backend_architecture.py` enforces thin routes.
- [ ] `tests/test_parser_imports.py` still protects startup from eager PyMuPDF import.
- [ ] `make test` passes.
- [ ] `make typecheck` passes or only reports a documented pre-existing baseline.
- [ ] `npm run frontend:typecheck` passes.
- [ ] `make build-sidecars` passes before completion is declared.
