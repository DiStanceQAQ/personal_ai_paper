# PDF Parser Selection Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace automatic PDF parser routing with a settings-driven MinerU-or-Docling parse queue and worker.

**Architecture:** The frontend saves a global parser setting. Upload and manual re-parse create durable `parse_runs` rows with a parser snapshot in `config_json`. An in-process worker atomically claims queued runs, executes the selected parser directly, applies optional GROBID enrichment, then reuses the existing chunking, persistence, FTS, and embedding pipeline.

**Tech Stack:** FastAPI, SQLite, Pydantic, httpx, Pytest, React, TypeScript, Vite, Docling optional extra, MinerU-compatible HTTP API.

---

## File Structure

- Modify `paper_engine/storage/migrations.py`
  - Add schema version `5`.
  - Add parse-run worker columns and job indexes.
- Create `paper_engine/pdf/settings.py`
  - Own parser setting constants, normalization, availability reporting, and MinerU test connection.
- Create `tests/test_pdf_parser_settings.py`
  - Cover default parser settings, persistence, secret preservation, parser availability, and MinerU connection status.
- Create `paper_engine/pdf/jobs.py`
  - Own parse-run queueing, atomic claiming, heartbeat, stale recovery, completion, and failure status transitions.
- Create `tests/test_pdf_jobs.py`
  - Cover queue snapshots, same-paper concurrency, stale recovery, max attempts, and status updates.
- Create `paper_engine/pdf/backends/mineru.py`
  - Implement a MinerU HTTP backend returning `ParseDocument`.
  - First implementation targets a self-hosted MinerU-compatible direct upload endpoint, defaulting to `POST /file_parse`, with optional bearer auth.
- Create `tests/test_pdf_backend_mineru.py`
  - Mock HTTP calls and validate normalization from Markdown/content-list style responses.
- Create `paper_engine/pdf/worker.py`
  - Orchestrate selected parser execution, GROBID enrichment, chunking, persistence, embeddings, and run status transitions.
- Create `tests/test_pdf_worker.py`
  - Use fake parsers and fake persistence helpers to verify worker behavior without real Docling, MinerU, GROBID, or embeddings.
- Modify `paper_engine/agent/service.py`
  - Extend `/api/agent/config` storage and response with parser settings and parser availability.
  - Add MinerU test connection service function.
- Modify `paper_engine/api/routes/agent.py`
  - Add `POST /api/agent/config/mineru/test`.
- Modify `tests/test_config.py` or create `tests/test_agent_config_pdf_parser.py`
  - Cover parser settings API contract.
- Modify `paper_engine/papers/service.py`
  - Upload queues a parse run after ingest.
  - Same-space duplicate upload reuses the existing paper and queues a re-parse run instead of writing another file.
  - `POST /api/papers/{paper_id}/parse` queues a re-parse run instead of synchronously parsing.
- Modify `paper_engine/api/routes/papers.py`
  - Preserve route paths while delegating to updated service functions.
- Modify `tests/test_routes_papers.py`
  - Update upload, duplicate, parse, and parse-run tests for queued responses.
- Modify `paper_engine/api/app.py`
  - Run stale parse-run recovery on startup.
  - Start the in-process parse worker loop when enabled.
- Create `tests/test_api_parse_worker_startup.py`
  - Verify startup recovery calls are wired without starting a long-running loop in unit tests.
- Modify `frontend/src/types.ts`
  - Add parser setting, availability, MinerU test, queued parse response, and optional upload queued-run fields.
- Modify `frontend/src/api.ts`
  - Add parser config fields and `testMineruConnection`.
- Modify `frontend/src/hooks/useLlmConfig.ts`
  - Preserve existing LLM behavior while carrying parser settings.
- Modify `frontend/src/components/modals/SettingsModal.tsx`
  - Add parser selection UI, MinerU fields, availability messages, and test connection action.
- Modify `frontend/src/components/modals/ModalsContainer.tsx`
  - Pass MinerU test action and result into the settings modal.
- Modify `frontend/src/App.tsx`
  - Thread MinerU test action from the config hook into modal props.
- Modify `frontend/src/api-contract.test-d.ts`
  - Lock frontend API return types.
- Modify `docs/pdf-ingestion.md`
  - Replace automatic router guidance with selected-parser worker guidance.
- Modify `tests/test_pdf_router.py`
  - Reduce to compatibility tests or mark production path tests obsolete by replacing them with worker tests.

## Assumptions

- MinerU is configured as a self-hosted or gateway service that accepts direct PDF upload. The first backend will call `POST {mineru_base_url}/file_parse` by default and accept response shapes containing Markdown and optional content-list JSON. Official MinerU cloud APIs use asynchronous task endpoints and URL or signed-upload flows; that can be added as a separate backend profile after this selected-parser worker is in place.
- The existing `parse_runs.status` values are not constrained by SQLite today. Migration `5` can update old rows to `completed` and use new values for new jobs without a destructive table rebuild.
- The in-process worker can be single-concurrency for this app. Atomic claiming is still required because tests, development servers, or future sidecars can run more than one worker.

## Task 1: Add Parse Run Worker Schema

**Files:**
- Modify: `paper_engine/storage/migrations.py`
- Test: `tests/test_db_migrations.py`

- [ ] **Step 1: Write failing migration tests**

Add these assertions to `tests/test_db_migrations.py`:

```python
EXPECTED_SCHEMA_VERSION = 5


def test_migration_5_adds_parse_run_worker_columns_and_indexes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        columns = table_columns(conn, "parse_runs")
        assert {
            "claimed_at",
            "heartbeat_at",
            "worker_id",
            "attempt_count",
            "last_error",
        }.issubset(columns)

        column_info = table_column_info(conn, "parse_runs")
        assert column_info["attempt_count"]["dflt_value"] == "0"

        indexes = index_names(conn, "parse_runs")
        assert {
            "idx_parse_runs_status_started",
            "idx_parse_runs_paper_status",
        }.issubset(indexes)
        assert_index_columns(
            conn,
            {
                "idx_parse_runs_status_started": ("status", "started_at"),
                "idx_parse_runs_paper_status": ("paper_id", "status"),
            },
        )
        conn.close()
```

- [ ] **Step 2: Run the failing migration test**

Run: `pytest tests/test_db_migrations.py::test_migration_5_adds_parse_run_worker_columns_and_indexes -q`

Expected: FAIL because schema version is still `4` and the worker columns do not exist.

- [ ] **Step 3: Implement migration 5**

In `paper_engine/storage/migrations.py`, update the version and add:

```python
LATEST_SCHEMA_VERSION = 5


def _add_parse_run_worker_state(conn: sqlite3.Connection) -> None:
    statements = (
        "ALTER TABLE parse_runs ADD COLUMN claimed_at TEXT",
        "ALTER TABLE parse_runs ADD COLUMN heartbeat_at TEXT",
        "ALTER TABLE parse_runs ADD COLUMN worker_id TEXT",
        "ALTER TABLE parse_runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE parse_runs ADD COLUMN last_error TEXT",
        """
        CREATE INDEX IF NOT EXISTS idx_parse_runs_status_started
            ON parse_runs(status, started_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_parse_runs_paper_status
            ON parse_runs(paper_id, status)
        """,
        """
        UPDATE parse_runs
        SET status = 'completed'
        WHERE status = ''
        """,
    )
    for statement in statements:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
```

Register it:

```python
MIGRATIONS: dict[int, Migration] = {
    1: _create_parse_run_document_tables,
    2: _extend_passages_with_provenance_columns,
    3: _create_analysis_run_and_card_provenance_schema,
    4: _create_passage_embedding_schema,
    5: _add_parse_run_worker_state,
}
```

- [ ] **Step 4: Run migration tests**

Run: `pytest tests/test_db_migrations.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add paper_engine/storage/migrations.py tests/test_db_migrations.py
git commit -m "feat: add parse run worker state"
```

## Task 2: Add Parser Settings and Availability Service

**Files:**
- Create: `paper_engine/pdf/settings.py`
- Test: `tests/test_pdf_parser_settings.py`
- Modify: `paper_engine/agent/service.py`
- Modify: `paper_engine/api/routes/agent.py`
- Test: `tests/test_agent_config_pdf_parser.py`

- [ ] **Step 1: Write parser settings unit tests**

Create `tests/test_pdf_parser_settings.py`:

```python
import sqlite3

import httpx
import pytest

from paper_engine.pdf.settings import (
    DEFAULT_PDF_PARSER_BACKEND,
    ParserSettingsUpdate,
    get_parser_settings,
    parser_availability,
    save_parser_settings,
    test_mineru_connection,
)
from paper_engine.storage.repositories.settings import set_setting


def test_default_parser_settings_use_docling() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")

    settings = get_parser_settings(conn)

    assert settings.pdf_parser_backend == DEFAULT_PDF_PARSER_BACKEND
    assert settings.pdf_parser_backend == "docling"
    assert settings.mineru_base_url == ""
    assert settings.has_mineru_api_key is False


def test_save_parser_settings_preserves_existing_empty_secret() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    set_setting(conn, "mineru_api_key", "old-secret")

    save_parser_settings(
        conn,
        ParserSettingsUpdate(
            pdf_parser_backend="mineru",
            mineru_base_url="http://mineru.test",
            mineru_api_key="",
        ),
    )

    settings = get_parser_settings(conn)
    assert settings.pdf_parser_backend == "mineru"
    assert settings.mineru_base_url == "http://mineru.test"
    assert settings.has_mineru_api_key is True
    assert conn.execute("SELECT value FROM app_state WHERE key = 'mineru_api_key'").fetchone()["value"] == "old-secret"


def test_parser_availability_reports_missing_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "paper_engine.pdf.settings.DoclingBackend.is_available",
        lambda self: False,
    )

    availability = parser_availability()

    assert availability["docling"]["available"] is False
    assert availability["docling"]["install_hint"] == 'pip install -e ".[pdf-advanced]"'


def test_mineru_connection_distinguishes_missing_credentials() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")

    result = test_mineru_connection(conn)

    assert result["status"] == "missing_credentials"


def test_mineru_connection_uses_health_endpoint() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    set_setting(conn, "mineru_base_url", "http://mineru.test")
    set_setting(conn, "mineru_api_key", "secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"status": "ok"})

    result = test_mineru_connection(
        conn,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert result["status"] == "ok"
```

- [ ] **Step 2: Run failing parser settings tests**

Run: `pytest tests/test_pdf_parser_settings.py -q`

Expected: FAIL because `paper_engine.pdf.settings` does not exist.

- [ ] **Step 3: Implement `paper_engine/pdf/settings.py`**

Create:

```python
"""PDF parser setting helpers."""

from __future__ import annotations

import sqlite3
from typing import Literal, TypedDict

import httpx
from pydantic import BaseModel, Field

from paper_engine.pdf.backends.docling import DoclingBackend
from paper_engine.storage.repositories.settings import get_setting, set_setting

PdfParserBackendName = Literal["mineru", "docling"]
DEFAULT_PDF_PARSER_BACKEND: PdfParserBackendName = "docling"
MINERU_DEFAULT_PARSE_PATH = "/file_parse"


class ParserSettings(BaseModel):
    pdf_parser_backend: PdfParserBackendName = DEFAULT_PDF_PARSER_BACKEND
    mineru_base_url: str = ""
    has_mineru_api_key: bool = False
    parsers: dict[str, dict[str, object]] = Field(default_factory=dict)


class ParserSettingsUpdate(BaseModel):
    pdf_parser_backend: PdfParserBackendName | None = None
    mineru_base_url: str | None = None
    mineru_api_key: str | None = None


class MinerUConnectionResult(TypedDict):
    status: str
    detail: str


def normalize_parser_backend(value: str) -> PdfParserBackendName:
    normalized = value.strip().lower()
    if normalized in {"mineru", "docling"}:
        return normalized  # type: ignore[return-value]
    return DEFAULT_PDF_PARSER_BACKEND


def get_parser_settings(conn: sqlite3.Connection) -> ParserSettings:
    backend = normalize_parser_backend(get_setting(conn, "pdf_parser_backend"))
    base_url = get_setting(conn, "mineru_base_url").rstrip("/")
    has_key = bool(get_setting(conn, "mineru_api_key"))
    availability = parser_availability()
    availability["mineru"]["configured"] = bool(base_url and has_key)
    return ParserSettings(
        pdf_parser_backend=backend,
        mineru_base_url=base_url,
        has_mineru_api_key=has_key,
        parsers=availability,
    )


def save_parser_settings(conn: sqlite3.Connection, update: ParserSettingsUpdate) -> None:
    if update.pdf_parser_backend is not None:
        set_setting(conn, "pdf_parser_backend", normalize_parser_backend(update.pdf_parser_backend))
    if update.mineru_base_url is not None:
        set_setting(conn, "mineru_base_url", update.mineru_base_url.rstrip("/"))
    if update.mineru_api_key:
        set_setting(conn, "mineru_api_key", update.mineru_api_key)


def parser_availability() -> dict[str, dict[str, object]]:
    docling_available = DoclingBackend().is_available()
    return {
        "docling": {
            "available": docling_available,
            "install_hint": "" if docling_available else 'pip install -e ".[pdf-advanced]"',
        },
        "mineru": {
            "configured": False,
            "last_check_status": "unknown",
        },
    }


def test_mineru_connection(
    conn: sqlite3.Connection,
    *,
    http_client: httpx.Client | None = None,
) -> MinerUConnectionResult:
    base_url = get_setting(conn, "mineru_base_url").rstrip("/")
    api_key = get_setting(conn, "mineru_api_key")
    if not base_url or not api_key:
        return {"status": "missing_credentials", "detail": "MinerU Base URL and API Key are required"}

    client = http_client or httpx.Client(timeout=10)
    close_client = http_client is None
    try:
        response = client.get(
            f"{base_url}/health",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if response.status_code < 400:
            return {"status": "ok", "detail": "MinerU health check succeeded"}
        return {
            "status": "http_error",
            "detail": f"MinerU health check returned HTTP {response.status_code}",
        }
    except httpx.HTTPError as exc:
        return {"status": "network_error", "detail": str(exc)}
    finally:
        if close_client:
            client.close()
```

- [ ] **Step 4: Extend config API tests**

Create `tests/test_agent_config_pdf_parser.py`:

```python
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from paper_engine.api.app import app
from paper_engine.storage.database import init_db


@pytest.fixture
def client() -> Generator[AsyncClient, None, None]:
    import paper_engine.storage.database as db_module

    with tempfile.TemporaryDirectory() as tmpdir:
        original_db_path = db_module.DATABASE_PATH
        db_module.DATABASE_PATH = Path(tmpdir) / "test.db"
        init_db(database_path=db_module.DATABASE_PATH)
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        db_module.DATABASE_PATH = original_db_path


@pytest.mark.asyncio
async def test_agent_config_includes_pdf_parser_defaults(client: AsyncClient) -> None:
    response = await client.get("/api/agent/config")

    assert response.status_code == 200
    data = response.json()
    assert data["pdf_parser_backend"] == "docling"
    assert data["mineru_base_url"] == ""
    assert data["has_mineru_api_key"] is False
    assert data["parsers"]["docling"]["install_hint"] in {"", 'pip install -e ".[pdf-advanced]"'}


@pytest.mark.asyncio
async def test_update_agent_config_saves_pdf_parser_settings(client: AsyncClient) -> None:
    response = await client.put(
        "/api/agent/config",
        json={
            "llm_provider": "openai",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_model": "gpt-4o",
            "pdf_parser_backend": "mineru",
            "mineru_base_url": "http://mineru.test",
            "mineru_api_key": "secret",
        },
    )
    assert response.status_code == 200

    config = (await client.get("/api/agent/config")).json()
    assert config["pdf_parser_backend"] == "mineru"
    assert config["mineru_base_url"] == "http://mineru.test"
    assert config["has_mineru_api_key"] is True


@pytest.mark.asyncio
async def test_mineru_test_endpoint_reports_missing_credentials(client: AsyncClient) -> None:
    response = await client.post("/api/agent/config/mineru/test")

    assert response.status_code == 200
    assert response.json()["status"] == "missing_credentials"
```

- [ ] **Step 5: Run failing config API tests**

Run: `pytest tests/test_pdf_parser_settings.py tests/test_agent_config_pdf_parser.py -q`

Expected: FAIL because the API has not been extended.

- [ ] **Step 6: Extend `paper_engine/agent/service.py` and routes**

Modify `LLMConfig`:

```python
from typing import Literal

class LLMConfig(BaseModel):
    llm_provider: str = "openai"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    llm_api_key: str | None = None
    llamaparse_base_url: str = DEFAULT_LLAMAPARSE_BASE_URL
    llamaparse_api_key: str | None = None
    pdf_parser_backend: Literal["mineru", "docling"] | None = None
    mineru_base_url: str | None = None
    mineru_api_key: str | None = None
```

In `get_agent_config`, merge parser settings:

```python
from paper_engine.pdf.settings import get_parser_settings, save_parser_settings, test_mineru_connection

parser_settings = get_parser_settings(conn)
return {
    "llm_provider": config.get("llm_provider", "openai"),
    "llm_base_url": config.get("llm_base_url", "https://api.openai.com/v1"),
    "llm_model": config.get("llm_model", "gpt-4o"),
    "has_api_key": bool(config.get("llm_api_key")),
    "llamaparse_base_url": config.get("llamaparse_base_url", DEFAULT_LLAMAPARSE_BASE_URL),
    "has_llamaparse_api_key": bool(config.get("llamaparse_api_key")),
    **parser_settings.model_dump(),
}
```

In `update_agent_config`, route parser fields through `save_parser_settings` before storing LLM fields:

```python
parser_update = ParserSettingsUpdate(
    pdf_parser_backend=data.pop("pdf_parser_backend", None),
    mineru_base_url=data.pop("mineru_base_url", None),
    mineru_api_key=data.pop("mineru_api_key", None),
)
save_parser_settings(conn, parser_update)
```

Add service and route:

```python
async def test_mineru_config() -> dict[str, str]:
    conn = get_connection()
    try:
        return dict(test_mineru_connection(conn))
    finally:
        conn.close()
```

```python
@router.post("/config/mineru/test")
async def test_mineru_config() -> dict[str, str]:
    return await service.test_mineru_config()
```

- [ ] **Step 7: Run settings tests**

Run: `pytest tests/test_pdf_parser_settings.py tests/test_agent_config_pdf_parser.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add paper_engine/pdf/settings.py paper_engine/agent/service.py paper_engine/api/routes/agent.py tests/test_pdf_parser_settings.py tests/test_agent_config_pdf_parser.py
git commit -m "feat: add PDF parser settings"
```

## Task 3: Add Durable Parse Job Queue Helpers

**Files:**
- Create: `paper_engine/pdf/jobs.py`
- Test: `tests/test_pdf_jobs.py`

- [ ] **Step 1: Write failing job tests**

Create `tests/test_pdf_jobs.py`:

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from paper_engine.pdf.jobs import (
    claim_next_parse_run,
    complete_parse_run,
    fail_parse_run,
    heartbeat_parse_run,
    queue_parse_run,
    recover_stale_parse_runs,
)
from paper_engine.storage.database import init_db


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(database_path=tmp_path / "test.db")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        "INSERT INTO papers (id, space_id, file_path, file_hash) VALUES ('paper-1', 'space-1', '/tmp/paper.pdf', 'hash')"
    )
    conn.commit()
    return conn


def test_queue_parse_run_snapshots_parser_config(tmp_path: Path) -> None:
    conn = _conn(tmp_path)

    parse_run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="mineru",
        parser_config={"parser_backend": "mineru", "mineru_base_url": "http://mineru.test"},
    )

    row = conn.execute("SELECT status, backend, config_json FROM parse_runs WHERE id = ?", (parse_run_id,)).fetchone()
    assert row["status"] == "queued"
    assert row["backend"] == "mineru"
    assert json.loads(row["config_json"])["mineru_base_url"] == "http://mineru.test"


def test_claim_next_parse_run_prevents_two_running_for_same_paper(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    first = queue_parse_run(conn, paper_id="paper-1", space_id="space-1", parser_backend="docling", parser_config={"parser_backend": "docling"})
    second = queue_parse_run(conn, paper_id="paper-1", space_id="space-1", parser_backend="docling", parser_config={"parser_backend": "docling"})

    claimed = claim_next_parse_run(conn, worker_id="worker-1")
    assert claimed is not None
    assert claimed.id in {first, second}
    assert claim_next_parse_run(conn, worker_id="worker-2") is None

    statuses = {
        row["id"]: row["status"]
        for row in conn.execute("SELECT id, status FROM parse_runs").fetchall()
    }
    assert list(statuses.values()).count("running") == 1
    assert statuses[claimed.id] == "running"


def test_recover_stale_parse_runs_requeues_stale_running_runs(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    run_id = queue_parse_run(conn, paper_id="paper-1", space_id="space-1", parser_backend="docling", parser_config={"parser_backend": "docling"})
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None
    conn.execute("UPDATE parse_runs SET heartbeat_at = datetime('now', '-20 minutes') WHERE id = ?", (run_id,))
    conn.commit()

    recovered = recover_stale_parse_runs(conn, stale_after_seconds=600, max_attempts=3)

    assert recovered == 1
    row = conn.execute("SELECT status, worker_id, last_error FROM parse_runs WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "queued"
    assert row["worker_id"] is None
    assert row["last_error"] == "worker_heartbeat_timeout"


def test_complete_and_fail_update_run_and_paper_status(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    run_id = queue_parse_run(conn, paper_id="paper-1", space_id="space-1", parser_backend="docling", parser_config={"parser_backend": "docling"})
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None

    complete_parse_run(conn, run_id, paper_id="paper-1", warnings=["ok"])
    row = conn.execute("SELECT status, warnings_json FROM parse_runs WHERE id = ?", (run_id,)).fetchone()
    paper = conn.execute("SELECT parse_status FROM papers WHERE id = 'paper-1'").fetchone()
    assert row["status"] == "completed"
    assert json.loads(row["warnings_json"]) == ["ok"]
    assert paper["parse_status"] == "parsed"

    failed_run = queue_parse_run(conn, paper_id="paper-1", space_id="space-1", parser_backend="docling", parser_config={"parser_backend": "docling"})
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None
    fail_parse_run(conn, failed_run, paper_id="paper-1", error="boom", warnings=["bad"])
    paper_after_failure = conn.execute("SELECT parse_status FROM papers WHERE id = 'paper-1'").fetchone()
    assert paper_after_failure["parse_status"] == "parsed"
```

- [ ] **Step 2: Run failing job tests**

Run: `pytest tests/test_pdf_jobs.py -q`

Expected: FAIL because `paper_engine.pdf.jobs` does not exist.

- [ ] **Step 3: Implement `paper_engine/pdf/jobs.py`**

Create dataclass and functions:

```python
"""Durable parse run job helpers."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParseRunJob:
    id: str
    paper_id: str
    space_id: str
    file_path: str
    parser_backend: str
    config: dict[str, Any]
    attempt_count: int


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def queue_parse_run(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    space_id: str,
    parser_backend: str,
    parser_config: dict[str, Any],
) -> str:
    parse_run_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO parse_runs (
            id, paper_id, space_id, backend, extraction_method, status,
            warnings_json, config_json, metadata_json
        )
        VALUES (?, ?, ?, ?, 'layout_model', 'queued', '[]', ?, '{}')
        """,
        (parse_run_id, paper_id, space_id, parser_backend, _json(parser_config)),
    )
    conn.commit()
    return parse_run_id
```

Add `claim_next_parse_run` using `BEGIN IMMEDIATE` and the conditional update from the spec:

```python
def claim_next_parse_run(conn: sqlite3.Connection, *, worker_id: str) -> ParseRunJob | None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        candidates = conn.execute(
            """
            SELECT pr.id, pr.paper_id, pr.space_id, pr.backend, pr.config_json,
                   pr.attempt_count, p.file_path
            FROM parse_runs pr
            JOIN papers p ON p.id = pr.paper_id AND p.space_id = pr.space_id
            WHERE pr.status = 'queued'
            ORDER BY pr.started_at, pr.id
            LIMIT 20
            """
        ).fetchall()
        for row in candidates:
            result = conn.execute(
                """
                UPDATE parse_runs
                SET status = 'running',
                    claimed_at = datetime('now'),
                    heartbeat_at = datetime('now'),
                    worker_id = ?,
                    attempt_count = attempt_count + 1,
                    last_error = NULL
                WHERE id = ?
                  AND status = 'queued'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM parse_runs active
                    WHERE active.paper_id = parse_runs.paper_id
                      AND active.status = 'running'
                  )
                """,
                (worker_id, row["id"]),
            )
            if result.rowcount == 1:
                conn.execute(
                    "UPDATE papers SET parse_status = 'parsing' WHERE id = ? AND space_id = ?",
                    (row["paper_id"], row["space_id"]),
                )
                conn.commit()
                return ParseRunJob(
                    id=str(row["id"]),
                    paper_id=str(row["paper_id"]),
                    space_id=str(row["space_id"]),
                    file_path=str(row["file_path"]),
                    parser_backend=str(row["backend"]),
                    config=json.loads(row["config_json"] or "{}"),
                    attempt_count=int(row["attempt_count"]) + 1,
                )
        conn.commit()
        return None
    except Exception:
        conn.rollback()
        raise
```

Implement status helpers:

```python
def heartbeat_parse_run(conn: sqlite3.Connection, parse_run_id: str) -> None:
    conn.execute(
        "UPDATE parse_runs SET heartbeat_at = datetime('now') WHERE id = ? AND status = 'running'",
        (parse_run_id,),
    )
    conn.commit()


def recover_stale_parse_runs(
    conn: sqlite3.Connection,
    *,
    stale_after_seconds: int,
    max_attempts: int,
) -> int:
    cutoff = f"-{stale_after_seconds} seconds"
    failed = conn.execute(
        """
        UPDATE parse_runs
        SET status = 'failed',
            worker_id = NULL,
            last_error = 'worker_heartbeat_timeout'
        WHERE status = 'running'
          AND attempt_count >= ?
          AND heartbeat_at < datetime('now', ?)
        """,
        (max_attempts, cutoff),
    ).rowcount
    requeued = conn.execute(
        """
        UPDATE parse_runs
        SET status = 'queued',
            worker_id = NULL,
            last_error = 'worker_heartbeat_timeout'
        WHERE status = 'running'
          AND attempt_count < ?
          AND heartbeat_at < datetime('now', ?)
        """,
        (max_attempts, cutoff),
    ).rowcount
    conn.commit()
    return int(failed + requeued)
```

Implement completion and failure:

```python
def complete_parse_run(conn: sqlite3.Connection, parse_run_id: str, *, paper_id: str, warnings: list[str]) -> None:
    conn.execute(
        """
        UPDATE parse_runs
        SET status = 'completed',
            completed_at = datetime('now'),
            heartbeat_at = datetime('now'),
            worker_id = NULL,
            warnings_json = ?
        WHERE id = ?
        """,
        (_json(warnings), parse_run_id),
    )
    conn.execute("UPDATE papers SET parse_status = 'parsed' WHERE id = ?", (paper_id,))
    conn.commit()


def fail_parse_run(conn: sqlite3.Connection, parse_run_id: str, *, paper_id: str, error: str, warnings: list[str]) -> None:
    conn.execute(
        """
        UPDATE parse_runs
        SET status = 'failed',
            completed_at = datetime('now'),
            heartbeat_at = datetime('now'),
            worker_id = NULL,
            last_error = ?,
            warnings_json = ?
        WHERE id = ?
        """,
        (error, _json(warnings), parse_run_id),
    )
    completed = conn.execute(
        "SELECT 1 FROM parse_runs WHERE paper_id = ? AND status = 'completed' LIMIT 1",
        (paper_id,),
    ).fetchone()
    if completed is None:
        conn.execute("UPDATE papers SET parse_status = 'error' WHERE id = ?", (paper_id,))
    conn.commit()
```

- [ ] **Step 4: Run job tests**

Run: `pytest tests/test_pdf_jobs.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add paper_engine/pdf/jobs.py tests/test_pdf_jobs.py
git commit -m "feat: add parse job queue helpers"
```

## Task 4: Persist Results Into Existing Parse Runs

**Files:**
- Modify: `paper_engine/pdf/persistence.py`
- Test: `tests/test_pdf_persistence.py`

- [ ] **Step 1: Write failing persistence test**

Add to `tests/test_pdf_persistence.py`:

```python
def test_persist_parse_result_can_use_existing_queued_parse_run() -> None:
    conn = _test_conn()
    _seed_space_and_paper(conn)
    conn.execute(
        """
        INSERT INTO parse_runs (id, paper_id, space_id, backend, status, config_json)
        VALUES ('queued-run-1', 'paper-1', 'space-1', 'docling', 'running', '{"parser_backend":"docling"}')
        """
    )
    conn.commit()

    document = _document()
    passages = [_passage("passage-1", "hash-1")]

    parse_run_id = persist_parse_result(
        conn,
        "paper-1",
        "space-1",
        document,
        passages,
        parse_run_id="queued-run-1",
    )

    assert parse_run_id == "queued-run-1"
    run = conn.execute("SELECT * FROM parse_runs WHERE id = 'queued-run-1'").fetchone()
    assert run["backend"] == document.backend
    assert run["status"] == "running"
    element_count = conn.execute(
        "SELECT COUNT(*) FROM document_elements WHERE parse_run_id = 'queued-run-1'"
    ).fetchone()[0]
    passage_count = conn.execute(
        "SELECT COUNT(*) FROM passages WHERE parse_run_id = 'queued-run-1'"
    ).fetchone()[0]
    assert element_count == len(document.elements)
    assert passage_count == len(passages)
```

- [ ] **Step 2: Run failing persistence test**

Run: `pytest tests/test_pdf_persistence.py::test_persist_parse_result_can_use_existing_queued_parse_run -q`

Expected: FAIL because `persist_parse_result` does not accept `parse_run_id` and deletes parse runs for the paper.

- [ ] **Step 3: Change deletion helper to preserve the active run**

Modify `_delete_old_generated_rows`:

```python
def _delete_old_generated_rows(
    conn: sqlite3.Connection,
    paper_id: str,
    space_id: str,
    old_passage_ids: Sequence[str],
    preserve_parse_run_id: str | None = None,
) -> None:
    if old_passage_ids:
        conn.executemany(
            f"DELETE FROM {FTS_TABLE} WHERE passage_id = ?",
            [(passage_id,) for passage_id in old_passage_ids],
        )
    conn.execute(
        """
        DELETE FROM passages
        WHERE paper_id = ?
          AND space_id = ?
          AND parse_run_id IS NOT NULL
        """,
        (paper_id, space_id),
    )
    if preserve_parse_run_id is None:
        conn.execute(
            "DELETE FROM parse_runs WHERE paper_id = ? AND space_id = ?",
            (paper_id, space_id),
        )
    else:
        conn.execute(
            """
            DELETE FROM parse_runs
            WHERE paper_id = ?
              AND space_id = ?
              AND id != ?
            """,
            (paper_id, space_id, preserve_parse_run_id),
        )
```

- [ ] **Step 4: Add parse-run update helper**

Add:

```python
def _update_existing_parse_run(
    conn: sqlite3.Connection,
    parse_run_id: str,
    parse_document: ParseDocument,
) -> None:
    conn.execute(
        """
        UPDATE parse_runs
        SET backend = ?,
            extraction_method = ?,
            quality_score = ?,
            warnings_json = ?,
            metadata_json = ?
        WHERE id = ?
        """,
        (
            parse_document.backend,
            parse_document.extraction_method,
            parse_document.quality.quality_score,
            _json(parse_document.quality.warnings),
            _json(parse_document.metadata),
            parse_run_id,
        ),
    )
```

- [ ] **Step 5: Extend `persist_parse_result` signature**

Change the signature:

```python
def persist_parse_result(
    conn: sqlite3.Connection,
    paper_id: str,
    space_id: str,
    parse_document: ParseDocument,
    passages: Sequence[PassageRecord],
    *,
    parse_run_id: str | None = None,
) -> str:
```

Inside the function:

```python
storage_parse_run_id = parse_run_id or f"parse-run-{uuid.uuid4()}"
```

Use `storage_parse_run_id` for element and passage storage IDs:

```python
element_id_map = {
    element.id: _storage_id(storage_parse_run_id, element.id)
    for element in parse_document.elements
}
passage_id_map = {
    passage.id: _storage_id(storage_parse_run_id, passage.id)
    for passage in passages
}
```

Replace every remaining storage-row reference to the old local `parse_run_id`
with `storage_parse_run_id`, including `_insert_element`, `_insert_table`,
`_insert_asset`, `_insert_passage`, `_storage_id(storage_parse_run_id, table.id)`,
and `_storage_id(storage_parse_run_id, asset.id)`.

Call the deletion helper:

```python
_delete_old_generated_rows(
    conn,
    paper_id,
    space_id,
    list(old_passage_hashes),
    preserve_parse_run_id=parse_run_id,
)
```

Insert or update the parse run:

```python
if parse_run_id is None:
    _insert_parse_run(conn, storage_parse_run_id, paper_id, space_id, parse_document)
else:
    _update_existing_parse_run(conn, storage_parse_run_id, parse_document)
```

Return `storage_parse_run_id`.

- [ ] **Step 6: Run persistence tests**

Run: `pytest tests/test_pdf_persistence.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add paper_engine/pdf/persistence.py tests/test_pdf_persistence.py
git commit -m "feat: persist parse results into queued runs"
```

## Task 5: Add MinerU Backend Adapter

**Files:**
- Create: `paper_engine/pdf/backends/mineru.py`
- Modify: `paper_engine/pdf/backends/__init__.py`
- Test: `tests/test_pdf_backend_mineru.py`

- [ ] **Step 1: Write failing MinerU backend tests**

Create `tests/test_pdf_backend_mineru.py`:

```python
from __future__ import annotations

from pathlib import Path
import json

import httpx
import pytest

from paper_engine.pdf.backends.base import ParserBackendError, ParserBackendUnavailable
from paper_engine.pdf.backends.mineru import MinerUBackend
from paper_engine.pdf.models import PdfQualityReport


def test_backend_unavailable_without_base_url_or_key() -> None:
    assert MinerUBackend(base_url="", api_key="").is_available() is False
    with pytest.raises(ParserBackendUnavailable):
        MinerUBackend(base_url="", api_key="").parse(Path("paper.pdf"), "paper-1", "space-1", PdfQualityReport())


def test_backend_posts_pdf_to_file_parse_and_normalizes_markdown(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/file_parse"
        assert request.headers["authorization"] == "Bearer mineru-secret"
        assert b"%PDF-1.4" in request.content
        return httpx.Response(
            200,
            json={
                "backend": "pipeline",
                "version": "2.7.6",
                "results": {
                    "paper": {
                        "md_content": "# Parsed Title\n\nParsed paragraph.",
                        "content_list": json.dumps([
                            {"type": "title", "text": "Parsed Title", "page_idx": 0},
                            {"type": "text", "text": "Parsed paragraph.", "page_idx": 0},
                            {"type": "table", "text": "A | B\n1 | 2", "page_idx": 0},
                        ]),
                    }
                },
            },
        )

    backend = MinerUBackend(
        base_url="http://mineru.test",
        api_key="mineru-secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    document = backend.parse(pdf_path, "paper-1", "space-1", PdfQualityReport(page_count=1))

    assert len(requests) == 1
    assert document.backend == "mineru"
    assert document.extraction_method == "layout_model"
    assert [element.element_type for element in document.elements] == ["title", "paragraph", "table"]
    assert document.tables[0].cells == [["A", "B"], ["1", "2"]]
    assert document.metadata["mineru"]["version"] == "2.7.6"


def test_backend_raises_parser_error_for_http_failure(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    backend = MinerUBackend(
        base_url="http://mineru.test",
        api_key="secret",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500, json={"error": "boom"}))
        ),
    )

    with pytest.raises(ParserBackendError):
        backend.parse(pdf_path, "paper-1", "space-1", PdfQualityReport())
```

- [ ] **Step 2: Run failing MinerU backend tests**

Run: `pytest tests/test_pdf_backend_mineru.py -q`

Expected: FAIL because `paper_engine.pdf.backends.mineru` does not exist.

- [ ] **Step 3: Implement MinerU backend**

Create `paper_engine/pdf/backends/mineru.py`:

```python
"""MinerU HTTP parser backend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import httpx

from paper_engine.pdf.backends.base import ParserBackendError, ParserBackendUnavailable
from paper_engine.pdf.models import ParseDocument, ParseElement, ParseTable, PdfQualityReport

_BACKEND_NAME = "mineru"


class MinerUBackend:
    name = _BACKEND_NAME

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        parse_path: str = "/file_parse",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.parse_path = parse_path if parse_path.startswith("/") else f"/{parse_path}"
        self._client = http_client

    def is_available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        if not self.is_available():
            raise ParserBackendUnavailable(self.name, "mineru_base_url or mineru_api_key is not configured")
        client = self._client or httpx.Client(timeout=120)
        close_client = self._client is None
        try:
            with file_path.open("rb") as handle:
                response = client.post(
                    f"{self.base_url}{self.parse_path}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files={"files": (file_path.name, handle, "application/pdf")},
                    data={
                        "return_md": "true",
                        "return_content_list": "true",
                        "return_images": "true",
                        "table_enable": "true",
                        "formula_enable": "true",
                    },
                )
            response.raise_for_status()
            payload = response.json()
            return _payload_to_document(payload, paper_id, space_id, quality_report)
        except ParserBackendUnavailable:
            raise
        except Exception as exc:
            raise ParserBackendError(self.name, "failed to parse PDF", cause=exc) from exc
        finally:
            if close_client:
                client.close()
```

Add normalization helpers in the same file:

```python
def _payload_to_document(
    payload: Mapping[str, Any],
    paper_id: str,
    space_id: str,
    quality_report: PdfQualityReport,
) -> ParseDocument:
    result = _first_result(payload)
    content_list = _content_list(result)
    elements: list[ParseElement] = []
    tables: list[ParseTable] = []

    for index, item in enumerate(content_list):
        element_type = _element_type(str(item.get("type", "text")))
        text = str(item.get("text") or item.get("content") or "")
        if not text:
            continue
        page_number = int(item.get("page_idx", item.get("page", 0))) + 1
        element_id = f"p{page_number:04d}-e{len(elements):04d}"
        elements.append(
            ParseElement(
                id=element_id,
                element_index=len(elements),
                element_type=element_type,
                text=text,
                page_number=page_number,
                extraction_method="layout_model",
                metadata={"source": "mineru_content_list", "raw_index": index},
            )
        )
        if element_type == "table":
            table_index = len(tables)
            tables.append(
                ParseTable(
                    id=f"table-{table_index:04d}",
                    element_id=element_id,
                    table_index=table_index,
                    page_number=page_number,
                    caption="",
                    cells=_markdown_table_cells(text),
                    metadata={"source": "mineru_content_list"},
                )
            )

    if not elements:
        md_content = str(result.get("md_content") or result.get("markdown") or payload.get("content") or "")
        elements = _markdown_to_elements(md_content)

    return ParseDocument(
        paper_id=paper_id,
        space_id=space_id,
        backend=_BACKEND_NAME,
        extraction_method="layout_model",
        quality=quality_report,
        elements=elements,
        tables=tables,
        metadata={"mineru": {"backend": payload.get("backend"), "version": payload.get("version")}},
    )
```

Use these deterministic helpers:

```python
def _first_result(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    results = payload.get("results")
    if isinstance(results, Mapping) and results:
        first = next(iter(results.values()))
        if isinstance(first, Mapping):
            return first
    return payload


def _content_list(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = result.get("content_list")
    if isinstance(raw, str):
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    return raw if isinstance(raw, list) else []


def _element_type(value: str) -> str:
    mapping = {
        "title": "title",
        "text": "paragraph",
        "paragraph": "paragraph",
        "table": "table",
        "image": "figure",
        "figure": "figure",
        "equation": "equation",
        "formula": "equation",
    }
    return mapping.get(value.lower(), "paragraph")
```

Complete Markdown fallback and table parsing:

```python
def _markdown_to_elements(markdown: str) -> list[ParseElement]:
    elements: list[ParseElement] = []
    for block in [part.strip() for part in markdown.split("\n\n") if part.strip()]:
        element_type = "heading" if block.startswith("#") else "paragraph"
        text = block.lstrip("#").strip()
        elements.append(
            ParseElement(
                id=f"p0001-e{len(elements):04d}",
                element_index=len(elements),
                element_type=element_type,
                text=text,
                page_number=1,
                extraction_method="layout_model",
            )
        )
    return elements


def _markdown_table_cells(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and not all(set(cell) <= {"-", ":"} for cell in cells):
            rows.append(cells)
    return rows
```

- [ ] **Step 4: Export backend and run tests**

Modify `paper_engine/pdf/backends/__init__.py` if it exports concrete backends.

Run: `pytest tests/test_pdf_backend_mineru.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add paper_engine/pdf/backends/mineru.py paper_engine/pdf/backends/__init__.py tests/test_pdf_backend_mineru.py
git commit -m "feat: add MinerU parser backend"
```

## Task 6: Add Selected-Parser Worker Orchestration

**Files:**
- Create: `paper_engine/pdf/worker.py`
- Test: `tests/test_pdf_worker.py`

- [ ] **Step 1: Write failing worker tests**

Create `tests/test_pdf_worker.py`:

```python
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pytest

from paper_engine.pdf.jobs import claim_next_parse_run, queue_parse_run
from paper_engine.pdf.models import ParseDocument, ParseElement, PassageRecord, PdfQualityReport
from paper_engine.pdf.worker import ParseWorker, ParserFactory
from paper_engine.storage.database import init_db


class FakeBackend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[Path] = []

    def is_available(self) -> bool:
        return True

    def parse(self, file_path: Path, paper_id: str, space_id: str, quality_report: PdfQualityReport) -> ParseDocument:
        self.calls.append(file_path)
        return ParseDocument(
            paper_id=paper_id,
            space_id=space_id,
            backend=self.name,
            extraction_method="layout_model",
            quality=quality_report,
            elements=[
                ParseElement(
                    id="element-1",
                    element_index=0,
                    element_type="paragraph",
                    text="parsed text",
                    page_number=1,
                    extraction_method="layout_model",
                )
            ],
        )


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(database_path=tmp_path / "test.db")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        "INSERT INTO papers (id, space_id, file_path, file_hash) VALUES ('paper-1', 'space-1', ?, 'hash')",
        (str(pdf),),
    )
    conn.commit()
    return conn


def test_worker_executes_selected_parser_and_persists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    queue_parse_run(conn, paper_id="paper-1", space_id="space-1", parser_backend="mineru", parser_config={"parser_backend": "mineru"})
    backend = FakeBackend("mineru")

    persisted: dict[str, Any] = {}

    def fake_persist(
        conn_arg: sqlite3.Connection,
        paper_id: str,
        space_id: str,
        document: ParseDocument,
        passages: list[PassageRecord],
        *,
        parse_run_id: str | None = None,
    ) -> str:
        persisted["document"] = document
        persisted["passages"] = passages
        return parse_run_id or "parse-run-storage-id"

    worker = ParseWorker(
        conn_factory=lambda: conn,
        worker_id="worker-1",
        parser_factory=ParserFactory(mineru=lambda config: backend, docling=lambda config: FakeBackend("docling")),
        persist_parse_result=fake_persist,
        embed_passages_for_parse_run=lambda conn_arg, parse_run_id: [],
        inspect_pdf=lambda file_path: PdfQualityReport(page_count=1),
        grobid_enricher=lambda file_path: {
            "metadata": {"title": "GROBID Title"},
            "references": [],
        },
        chunk_parse_document=lambda document: [
            PassageRecord(
                id="passage-1",
                paper_id=document.paper_id,
                space_id=document.space_id,
                original_text="parsed text",
                element_ids=["element-1"],
                parser_backend=document.backend,
                extraction_method="layout_model",
            )
        ],
        close_connection=False,
    )

    assert worker.run_once() is True

    row = conn.execute("SELECT status FROM parse_runs").fetchone()
    paper = conn.execute("SELECT parse_status FROM papers WHERE id = 'paper-1'").fetchone()
    assert row["status"] == "completed"
    assert paper["parse_status"] == "parsed"
    assert persisted["document"].backend == "mineru"
    assert persisted["document"].metadata["grobid"]["metadata"]["title"] == "GROBID Title"
    assert backend.calls


def test_worker_fails_missing_file(tmp_path: Path) -> None:
    conn = init_db(database_path=tmp_path / "test.db")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        "INSERT INTO papers (id, space_id, file_path, file_hash) VALUES ('paper-1', 'space-1', ?, 'hash')",
        (str(tmp_path / "missing.pdf"),),
    )
    queue_parse_run(conn, paper_id="paper-1", space_id="space-1", parser_backend="docling", parser_config={"parser_backend": "docling"})

    worker = ParseWorker(
        conn_factory=lambda: conn,
        worker_id="worker-1",
        close_connection=False,
    )

    assert worker.run_once() is True
    row = conn.execute("SELECT status, last_error FROM parse_runs").fetchone()
    assert row["status"] == "failed"
    assert "PDF file not found" in row["last_error"]
```

- [ ] **Step 2: Run failing worker tests**

Run: `pytest tests/test_pdf_worker.py -q`

Expected: FAIL because `paper_engine.pdf.worker` does not exist.

- [ ] **Step 3: Implement worker skeleton**

Create `paper_engine/pdf/worker.py` with injectable dependencies:

```python
"""Parse worker orchestration."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_engine.pdf.backends.base import PdfParserBackend
from paper_engine.pdf.backends.docling import DoclingBackend
from paper_engine.pdf.backends.grobid import get_configured_grobid_client
from paper_engine.pdf.backends.mineru import MinerUBackend
from paper_engine.pdf.chunking import chunk_parse_document as default_chunk_parse_document
from paper_engine.pdf.jobs import claim_next_parse_run, complete_parse_run, fail_parse_run, heartbeat_parse_run
from paper_engine.pdf.persistence import embed_passages_for_parse_run as default_embed_passages_for_parse_run
from paper_engine.pdf.persistence import persist_parse_result as default_persist_parse_result
from paper_engine.pdf.profile import inspect_pdf as default_inspect_pdf
from paper_engine.storage.database import get_connection
from paper_engine.storage.repositories.settings import get_setting


@dataclass(frozen=True)
class ParserFactory:
    mineru: Callable[[dict[str, Any]], PdfParserBackend]
    docling: Callable[[dict[str, Any]], PdfParserBackend]


def default_parser_factory(conn: sqlite3.Connection) -> ParserFactory:
    def mineru(config: dict[str, Any]) -> PdfParserBackend:
        return MinerUBackend(
            base_url=str(config.get("mineru_base_url") or get_setting(conn, "mineru_base_url")),
            api_key=get_setting(conn, "mineru_api_key"),
        )

    def docling(config: dict[str, Any]) -> PdfParserBackend:
        return DoclingBackend()

    return ParserFactory(mineru=mineru, docling=docling)
```

Implement `ParseWorker`:

```python
class ParseWorker:
    def __init__(
        self,
        *,
        conn_factory: Callable[[], sqlite3.Connection] = get_connection,
        worker_id: str = "parse-worker",
        parser_factory: ParserFactory | None = None,
        inspect_pdf: Callable[[Path], Any] = default_inspect_pdf,
        chunk_parse_document: Callable[[Any], Any] = default_chunk_parse_document,
        persist_parse_result: Callable[..., str] = default_persist_parse_result,
        embed_passages_for_parse_run: Callable[..., list[str]] = default_embed_passages_for_parse_run,
        grobid_enricher: Callable[[Path], dict[str, Any] | None] | None = None,
        close_connection: bool = True,
    ) -> None:
        self.conn_factory = conn_factory
        self.worker_id = worker_id
        self._parser_factory = parser_factory
        self.inspect_pdf = inspect_pdf
        self.chunk_parse_document = chunk_parse_document
        self.persist_parse_result = persist_parse_result
        self.embed_passages_for_parse_run = embed_passages_for_parse_run
        self.grobid_enricher = grobid_enricher or default_grobid_enricher
        self.close_connection = close_connection

    def run_once(self) -> bool:
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
                backend = factory.mineru(job.config) if job.parser_backend == "mineru" else factory.docling(job.config)
                quality = self.inspect_pdf(file_path)
                document = backend.parse(file_path, job.paper_id, job.space_id, quality)
                document = self._merge_grobid(file_path, document)
                heartbeat_parse_run(conn, job.id)
                passages = self.chunk_parse_document(document)
                if not passages:
                    raise RuntimeError("parsed document produced no passages")
                storage_run_id = self.persist_parse_result(
                    conn,
                    job.paper_id,
                    job.space_id,
                    document,
                    passages,
                    parse_run_id=job.id,
                )
                embedding_warnings = self.embed_passages_for_parse_run(conn, storage_run_id)
                complete_parse_run(conn, job.id, paper_id=job.paper_id, warnings=[*document.quality.warnings, *embedding_warnings])
                return True
            except Exception as exc:
                fail_parse_run(conn, job.id, paper_id=job.paper_id, error=str(exc), warnings=[str(exc)])
                return True
        finally:
            if self.close_connection:
                conn.close()

    def _merge_grobid(self, file_path: Path, document: Any) -> Any:
        try:
            grobid = self.grobid_enricher(file_path)
        except Exception as exc:
            document.quality.warnings.append(f"grobid_failed:{exc}")
            return document
        if not grobid:
            return document
        metadata = dict(document.metadata)
        metadata["grobid"] = grobid
        document.quality.warnings.append("grobid_merged")
        return document.model_copy(update={"metadata": metadata})
```

Add a simple loop helper:

```python
def run_worker_loop(worker: ParseWorker, *, poll_interval_seconds: float = 2.0, stop: Callable[[], bool] | None = None) -> None:
    while stop is None or not stop():
        did_work = worker.run_once()
        if not did_work:
            time.sleep(poll_interval_seconds)
```

Add default GROBID enrichment:

```python
def default_grobid_enricher(file_path: Path) -> dict[str, Any] | None:
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
```

- [ ] **Step 4: Run worker tests**

Run: `pytest tests/test_pdf_worker.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add paper_engine/pdf/worker.py tests/test_pdf_worker.py
git commit -m "feat: add selected parser worker"
```

## Task 7: Queue Parse Runs from Upload and Re-Parse Routes

**Files:**
- Modify: `paper_engine/papers/service.py`
- Modify: `paper_engine/api/routes/papers.py`
- Modify: `frontend/src/types.ts`
- Test: `tests/test_routes_papers.py`
- Test: `tests/test_embeddings_parse_integration.py`

- [ ] **Step 1: Update route tests for queued parsing**

In `tests/test_routes_papers.py`, update upload and parse expectations:

```python
@pytest.mark.asyncio
async def test_upload_pdf_queues_parse_run(client: AsyncClient) -> None:
    await _create_and_activate_space(client)

    resp = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["parse_status"] == "pending"
    assert data["queued_parse_run_id"]

    runs = await client.get(f"/api/papers/{data['id']}/parse-runs")
    assert runs.status_code == 200
    assert runs.json()[0]["status"] == "queued"
    assert runs.json()[0]["backend"] in {"docling", "mineru"}
```

Change duplicate behavior:

```python
@pytest.mark.asyncio
async def test_duplicate_upload_reuses_existing_paper_and_queues_parse(client: AsyncClient) -> None:
    await _create_and_activate_space(client)
    pdf = _make_minimal_pdf()

    first = await client.post("/api/papers/upload", files={"file": ("test.pdf", pdf, "application/pdf")})
    second = await client.post("/api/papers/upload", files={"file": ("test2.pdf", pdf, "application/pdf")})

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["queued_parse_run_id"] != first.json()["queued_parse_run_id"]
```

Update parse route:

```python
@pytest.mark.asyncio
async def test_parse_endpoint_queues_reparse(client: AsyncClient) -> None:
    await _create_and_activate_space(client)
    upload = await client.post(
        "/api/papers/upload",
        files={"file": ("test.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = upload.json()["id"]

    resp = await client.post(f"/api/papers/{paper_id}/parse")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["paper_id"] == paper_id
    assert data["parse_run_id"]
    assert data["backend"] in {"docling", "mineru"}
```

- [ ] **Step 2: Run failing route tests**

Run: `pytest tests/test_routes_papers.py::test_upload_pdf_queues_parse_run tests/test_routes_papers.py::test_duplicate_upload_reuses_existing_paper_and_queues_parse tests/test_routes_papers.py::test_parse_endpoint_queues_reparse -q`

Expected: FAIL because upload does not queue a parse run and duplicate still returns `409`.

- [ ] **Step 3: Add parser snapshot helper in `papers/service.py`**

Add imports:

```python
from paper_engine.pdf.jobs import queue_parse_run
from paper_engine.pdf.settings import get_parser_settings
```

Add helper:

```python
def _queue_parse_for_paper(conn: Any, *, paper_id: str, space_id: str) -> tuple[str, str]:
    settings = get_parser_settings(conn)
    config = {
        "parser_backend": settings.pdf_parser_backend,
        "mineru_base_url": settings.mineru_base_url,
        "grobid_enabled": bool(_get_setting_value(conn, "grobid_base_url")),
        "worker_version": "pdf-parser-selection-v1",
    }
    parse_run_id = queue_parse_run(
        conn,
        paper_id=paper_id,
        space_id=space_id,
        parser_backend=settings.pdf_parser_backend,
        parser_config=config,
    )
    return parse_run_id, settings.pdf_parser_backend


def _get_setting_value(conn: Any, key: str) -> str:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return "" if row is None else str(row["value"])
```

- [ ] **Step 4: Modify upload duplicate and new upload paths**

For duplicate row, replace the `409` block with:

```python
if existing is not None:
    dest_path.unlink()
    parse_run_id, _backend = _queue_parse_for_paper(
        conn,
        paper_id=str(existing["id"]),
        space_id=space_id,
    )
    conn.commit()
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (existing["id"],)).fetchone()
    result = _paper_row_to_dict(row)
    result["queued_parse_run_id"] = parse_run_id
    return result
```

After inserting a new paper:

```python
parse_run_id, _backend = _queue_parse_for_paper(conn, paper_id=paper_id, space_id=space_id)
conn.commit()
result = _paper_row_to_dict(row)
result["queued_parse_run_id"] = parse_run_id
```

Ensure `queue_parse_run` does not commit independently when called inside upload, or change it to accept `commit: bool = True`. If changing it:

```python
def queue_parse_run(..., commit: bool = True) -> str:
    ...
    if commit:
        conn.commit()
    return parse_run_id
```

- [ ] **Step 5: Replace synchronous parse endpoint**

Replace `parse_paper` body after file checks with:

```python
parse_run_id, backend = _queue_parse_for_paper(conn, paper_id=paper_id, space_id=str(row["space_id"]))
return {
    "status": "queued",
    "paper_id": paper_id,
    "passage_count": 0,
    "parse_run_id": parse_run_id,
    "backend": backend,
    "quality_score": None,
    "warnings": [],
}
```

Do not call `inspect_pdf`, `route_parse`, `chunk_parse_document`, `persist_parse_result`, or `embed_passages_for_parse_run` from this route.

- [ ] **Step 6: Update frontend types for queued status**

In `frontend/src/types.ts`:

```ts
export type ParsePaperStatus = 'queued' | 'parsed' | 'error';

export interface Paper {
  // existing fields
  queued_parse_run_id?: string;
}
```

- [ ] **Step 7: Run affected backend tests**

Run: `pytest tests/test_routes_papers.py tests/test_embeddings_parse_integration.py -q`

Expected: route tests updated for queued behavior pass. Embedding integration tests that relied on synchronous parse should fail before Step 8 because they still need to call the worker.

- [ ] **Step 8: Update embedding integration tests to call worker**

In `tests/test_embeddings_parse_integration.py`, replace `_upload_and_parse` with upload, claim, and worker execution using fake parser injection from Task 6. The assertion remains that embeddings are stored after the worker completes.

- [ ] **Step 9: Run updated tests**

Run: `pytest tests/test_routes_papers.py tests/test_embeddings_parse_integration.py -q`

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add paper_engine/papers/service.py paper_engine/api/routes/papers.py frontend/src/types.ts tests/test_routes_papers.py tests/test_embeddings_parse_integration.py
git commit -m "feat: queue PDF parse runs from paper routes"
```

## Task 8: Wire Worker Startup and Stale Recovery

**Files:**
- Modify: `paper_engine/api/app.py`
- Test: `tests/test_api_parse_worker_startup.py`

- [ ] **Step 1: Write startup wiring tests**

Create `tests/test_api_parse_worker_startup.py`:

```python
from pathlib import Path
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from paper_engine.api.app import app
from paper_engine.storage.database import init_db, get_connection


@pytest.mark.asyncio
async def test_lifespan_recovers_stale_parse_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    import paper_engine.storage.database as db_module

    with tempfile.TemporaryDirectory() as tmpdir:
        db_module.DATABASE_PATH = Path(tmpdir) / "test.db"
        init_db(database_path=db_module.DATABASE_PATH)
        conn = get_connection(db_module.DATABASE_PATH)
        conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
        conn.execute("INSERT INTO papers (id, space_id) VALUES ('paper-1', 'space-1')")
        conn.execute(
            """
            INSERT INTO parse_runs (
                id, paper_id, space_id, backend, status, heartbeat_at, attempt_count
            )
            VALUES ('run-1', 'paper-1', 'space-1', 'docling', 'running', datetime('now', '-20 minutes'), 1)
            """
        )
        conn.commit()
        conn.close()

        monkeypatch.setenv("PAPER_ENGINE_PARSE_WORKER_ENABLED", "0")
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/health")
                assert response.status_code == 200

        conn = get_connection(db_module.DATABASE_PATH)
        row = conn.execute("SELECT status FROM parse_runs WHERE id = 'run-1'").fetchone()
        assert row["status"] == "queued"
        conn.close()
```

- [ ] **Step 2: Run failing startup test**

Run: `pytest tests/test_api_parse_worker_startup.py -q`

Expected: FAIL because lifespan does not recover stale runs.

- [ ] **Step 3: Implement startup recovery and optional loop**

Modify `paper_engine/api/app.py`:

```python
import os
import threading

from paper_engine.pdf.jobs import recover_stale_parse_runs
from paper_engine.pdf.worker import ParseWorker, run_worker_loop
from paper_engine.storage.database import get_connection
```

Inside `lifespan` after `init_db()`:

```python
conn = get_connection()
try:
    recover_stale_parse_runs(
        conn,
        stale_after_seconds=int(os.getenv("PAPER_ENGINE_PARSE_STALE_SECONDS", "600")),
        max_attempts=int(os.getenv("PAPER_ENGINE_PARSE_MAX_ATTEMPTS", "3")),
    )
finally:
    conn.close()
```

Start the loop when enabled:

```python
stop_event = threading.Event()
worker_thread: threading.Thread | None = None
if os.getenv("PAPER_ENGINE_PARSE_WORKER_ENABLED", "1") == "1":
    worker = ParseWorker(worker_id=f"api-worker-{os.getpid()}")
    worker_thread = threading.Thread(
        target=run_worker_loop,
        kwargs={
            "worker": worker,
            "poll_interval_seconds": float(os.getenv("PAPER_ENGINE_PARSE_POLL_SECONDS", "2")),
            "stop": stop_event.is_set,
        },
        daemon=True,
    )
    worker_thread.start()
```

Before lifespan exits:

```python
try:
    yield
finally:
    stop_event.set()
    if worker_thread is not None:
        worker_thread.join(timeout=5)
```

- [ ] **Step 4: Run startup test**

Run: `pytest tests/test_api_parse_worker_startup.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add paper_engine/api/app.py tests/test_api_parse_worker_startup.py
git commit -m "feat: start parse worker with recovery"
```

## Task 9: Add Frontend Parser Settings UI

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/hooks/useLlmConfig.ts`
- Modify: `frontend/src/components/modals/SettingsModal.tsx`
- Modify: `frontend/src/api-contract.test-d.ts`

- [ ] **Step 1: Update frontend types**

Add to `frontend/src/types.ts`:

```ts
export type PdfParserBackend = 'mineru' | 'docling';

export interface ParserAvailability {
  docling: {
    available: boolean;
    install_hint: string;
  };
  mineru: {
    configured: boolean;
    last_check_status: string;
  };
}

export interface AgentConfig {
  llm_provider: string;
  llm_base_url: string;
  llm_model: string;
  llm_api_key: string;
  has_api_key: boolean;
  pdf_parser_backend: PdfParserBackend;
  mineru_base_url: string;
  mineru_api_key: string;
  has_mineru_api_key: boolean;
  parsers: ParserAvailability;
}

export interface MinerUTestResult {
  status: 'ok' | 'missing_credentials' | 'http_error' | 'network_error';
  detail: string;
}
```

- [ ] **Step 2: Update API contract test**

In `frontend/src/api-contract.test-d.ts`:

```ts
import type { AgentConfig, MinerUTestResult } from './types';

type _AgentConfigReturnsParserSettings = Assert<
  IsEqual<AsyncReturn<typeof api.getAgentConfig>, Omit<AgentConfig, 'llm_api_key' | 'mineru_api_key'>>
>;
type _MinerUTestReturnsStatus = Assert<
  IsEqual<AsyncReturn<typeof api.testMineruConnection>, MinerUTestResult>
>;
```

- [ ] **Step 3: Run failing typecheck**

Run: `npm run frontend:typecheck`

Expected: FAIL because `api.getAgentConfig` and `api.testMineruConnection` are not typed yet.

- [ ] **Step 4: Update frontend API and hook**

In `frontend/src/api.ts`, import new types and update:

```ts
getAgentConfig: () =>
  request<Omit<AgentConfig, 'llm_api_key' | 'mineru_api_key'>>('/api/agent/config'),
updateAgentConfig: (config: AgentConfig) =>
  request<{ status: string }>('/api/agent/config', { method: 'PUT', body: JSON.stringify(config) }),
testMineruConnection: () =>
  request<MinerUTestResult>('/api/agent/config/mineru/test', { method: 'POST' }),
```

In `frontend/src/hooks/useLlmConfig.ts`, initialize parser fields:

```ts
const [llmConfig, setLlmConfig] = useState<AgentConfig>({
  llm_provider: 'openai',
  llm_base_url: 'https://api.openai.com/v1',
  llm_model: 'gpt-4o',
  llm_api_key: '',
  has_api_key: false,
  pdf_parser_backend: 'docling',
  mineru_base_url: '',
  mineru_api_key: '',
  has_mineru_api_key: false,
  parsers: {
    docling: { available: true, install_hint: '' },
    mineru: { configured: false, last_check_status: 'unknown' },
  },
});
```

When loading:

```ts
setLlmConfig({ ...config, llm_api_key: '', mineru_api_key: '' });
```

Add MinerU test state and action:

```ts
const [mineruTestResult, setMineruTestResult] = useState<MinerUTestResult | null>(null);

const testMineruConnection = async () => {
  try {
    const result = await api.testMineruConnection();
    setMineruTestResult(result);
    setNotice({
      message: result.status === 'ok' ? 'MinerU 连接测试成功。' : result.detail,
      type: result.status === 'ok' ? 'success' : 'error',
    });
    return result;
  } catch {
    const result: MinerUTestResult = { status: 'network_error', detail: 'MinerU 连接测试失败。' };
    setMineruTestResult(result);
    setNotice({ message: result.detail, type: 'error' });
    return result;
  }
};
```

Return it from the hook:

```ts
return {
  llmConfig,
  setLlmConfig,
  loadLlmConfig,
  saveLlmConfig,
  mineruTestResult,
  testMineruConnection,
};
```

- [ ] **Step 5: Update SettingsModal props and UI**

In `SettingsModal.tsx`, extend props:

```ts
import type { AgentConfig, MinerUTestResult } from '../../types';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: () => void;
  onTestMineru: () => Promise<MinerUTestResult>;
  mineruTestResult: MinerUTestResult | null;
  config: AgentConfig;
  setConfig: (config: AgentConfig) => void;
}
```

Add parser select before LLM fields:

```tsx
<Select
  label="PDF 解析方式"
  value={config.pdf_parser_backend}
  onChange={(e) => setConfig({ ...config, pdf_parser_backend: e.target.value })}
  options={[
    { value: 'mineru', label: 'MinerU API（推荐）' },
    { value: 'docling', label: 'Docling 本地解析' },
  ]}
/>

{config.pdf_parser_backend === 'docling' && !config.parsers.docling.available && (
  <p className="field-warning">
    请安装 docling: {config.parsers.docling.install_hint || 'pip install -e ".[pdf-advanced]"'}
  </p>
)}

{config.pdf_parser_backend === 'mineru' && (
  <>
    <div className="form-group">
      <label>MinerU Base URL</label>
      <input
        value={config.mineru_base_url}
        onChange={(e) => setConfig({ ...config, mineru_base_url: e.target.value })}
        placeholder="例如：http://127.0.0.1:8000"
      />
    </div>
    <div className="form-group">
      <label>
        MinerU API Key {config.has_mineru_api_key && <span className="secure-tag">已安全保存</span>}
      </label>
      <input
        type="password"
        value={config.mineru_api_key}
        onChange={(e) => setConfig({ ...config, mineru_api_key: e.target.value })}
        placeholder="输入 MinerU API Key..."
      />
    </div>
    <button className="btn-secondary" type="button" onClick={onTestMineru}>
      测试 MinerU 连接
    </button>
    {mineruTestResult && (
      <p className={mineruTestResult.status === 'ok' ? 'field-success' : 'field-warning'}>
        {mineruTestResult.detail}
      </p>
    )}
  </>
)}
```

In `ModalsContainer.tsx`, extend props and pass through:

```tsx
testMineruConnection: () => Promise<MinerUTestResult>;
mineruTestResult: MinerUTestResult | null;
```

```tsx
<SettingsModal
  isOpen={modals.settings}
  onClose={() => modals.closeModal('settings')}
  onSave={saveLlmConfig}
  onTestMineru={testMineruConnection}
  mineruTestResult={mineruTestResult}
  config={llmConfig}
  setConfig={setLlmConfig}
/>
```

In `App.tsx`, destructure and pass the hook values:

```tsx
const {
  llmConfig,
  setLlmConfig,
  loadLlmConfig,
  saveLlmConfig,
  mineruTestResult,
  testMineruConnection,
} = useLlmConfig(setNotice);
```

- [ ] **Step 6: Run frontend typecheck and build**

Run: `npm run frontend:typecheck`

Expected: PASS.

Run: `npm run frontend:build`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/hooks/useLlmConfig.ts frontend/src/components/modals/SettingsModal.tsx frontend/src/components/modals/ModalsContainer.tsx frontend/src/App.tsx frontend/src/api-contract.test-d.ts
git commit -m "feat: add parser settings UI"
```

## Task 10: Remove Production Router Usage

**Files:**
- Modify: `paper_engine/pdf/compat.py`
- Modify: `paper_engine/pdf/router.py`
- Modify: `tests/test_pdf_router.py`
- Create: `tests/test_pdf_router_not_in_production.py`

- [ ] **Step 1: Add production-path guard test**

Create `tests/test_pdf_router_not_in_production.py`:

```python
from pathlib import Path


def test_paper_service_no_longer_imports_route_parse() -> None:
    source = Path("paper_engine/papers/service.py").read_text(encoding="utf-8")

    assert "route_parse" not in source
    assert "paper_engine.pdf.router" not in source
```

- [ ] **Step 2: Run failing guard test**

Run: `pytest tests/test_pdf_router_not_in_production.py -q`

Expected: FAIL until `paper_engine/papers/service.py` imports are cleaned.

- [ ] **Step 3: Clean production imports**

Remove these imports from `paper_engine/papers/service.py`:

```python
from paper_engine.pdf.backends.base import ParserBackendError
from paper_engine.pdf.persistence import PassageEmbeddingError, embed_passages_for_parse_run
from paper_engine.pdf.compat import (
    chunk_parse_document,
    inspect_pdf,
    persist_parse_result,
    route_parse,
)
```

Keep parse execution imports inside `paper_engine/pdf/worker.py`.

- [ ] **Step 4: Deprecate router compatibility**

At the top of `paper_engine/pdf/router.py`, add:

```python
"""Deprecated automatic parser router.

Production paper parsing uses paper_engine.pdf.worker with a parser snapshot
from parse_runs.config_json. Keep this module only for compatibility tests and
local scripts during migration.
"""
```

Do not add comments that disable code. The compatibility module remains executable until a separate deletion commit removes its tests and callers.

- [ ] **Step 5: Update router tests**

Keep `tests/test_pdf_router.py` focused on compatibility behavior only. Move production routing assertions to worker tests. Delete tests that assert automatic fallback is required for `/api/papers/{paper_id}/parse`.

- [ ] **Step 6: Run router guard tests**

Run: `pytest tests/test_pdf_router_not_in_production.py tests/test_pdf_router.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add paper_engine/papers/service.py paper_engine/pdf/router.py tests/test_pdf_router.py tests/test_pdf_router_not_in_production.py
git commit -m "refactor: retire automatic router from production parse path"
```

## Task 11: Documentation and Full Verification

**Files:**
- Modify: `docs/pdf-ingestion.md`
- Modify: `docs/superpowers/specs/2026-04-29-pdf-parser-selection-worker-design.md` if implementation reveals a necessary correction.

- [ ] **Step 1: Update ingestion documentation**

Rewrite the top of `docs/pdf-ingestion.md` to describe:

```markdown
# PDF Ingestion Configuration

PDF parsing is settings-driven. The app stores the selected parser in
`app_state.pdf_parser_backend` and snapshots that choice into each queued
`parse_run`.

Available parser modes:

| Mode | Backend | Runs where | Best for | Requirements |
| --- | --- | --- | --- | --- |
| MinerU API | `mineru` | Configured HTTP service | Higher-fidelity structure, tables, formulas, difficult layout | `mineru_base_url` and `mineru_api_key` |
| Docling local | `docling` | Local Python process | Local-first parsing without sending PDFs to a cloud parser | `pip install -e ".[pdf-advanced]"` in development or bundled dependency in desktop builds |

Upload queues a parse run automatically. Manual re-parse uses
`POST /api/papers/{paper_id}/parse` and returns `status="queued"`.
```

Document:

```markdown
## Worker Recovery

The API sidecar recovers stale `running` parse runs on startup and periodically
through the worker loop. Runs with old `heartbeat_at` values are requeued until
`PAPER_ENGINE_PARSE_MAX_ATTEMPTS` is reached.
```

- [ ] **Step 2: Run backend focused tests**

Run:

```bash
pytest tests/test_db_migrations.py tests/test_pdf_parser_settings.py tests/test_agent_config_pdf_parser.py tests/test_pdf_jobs.py tests/test_pdf_backend_mineru.py tests/test_pdf_worker.py tests/test_routes_papers.py tests/test_embeddings_parse_integration.py tests/test_api_parse_worker_startup.py tests/test_pdf_router_not_in_production.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full backend tests**

Run: `pytest -q`

Expected: PASS.

- [ ] **Step 4: Run frontend verification**

Run: `npm run frontend:typecheck`

Expected: PASS.

Run: `npm run frontend:build`

Expected: PASS.

- [ ] **Step 5: Final commit**

```bash
git add docs/pdf-ingestion.md docs/superpowers/specs/2026-04-29-pdf-parser-selection-worker-design.md
git commit -m "docs: update PDF parser selection guide"
```

## Self-Review Checklist

- Spec coverage:
  - Frontend global parser option: Task 9.
  - Parser settings stored in `app_state`: Task 2.
  - Upload creates queued parse run: Task 7.
  - Parse-run config snapshot: Task 3 and Task 7.
  - Parse persistence uses the queued run ID: Task 4.
  - MinerU adapter: Task 5.
  - Docling selected path: Task 6.
  - GROBID enrichment path: Task 6.
  - Atomic claim and same-paper concurrency: Task 3.
  - Stale running recovery: Task 3 and Task 8.
  - Docling availability warning: Task 2 and Task 9.
  - MinerU test connection: Task 2 and Task 9.
  - Router retired from production path: Task 10.
- Placeholder scan:
  - Tasks contain concrete file paths, commands, expected outcomes, and code snippets.
- Type consistency:
  - `pdf_parser_backend` is consistently `mineru | docling`.
  - `queued_parse_run_id` is the upload response extension.
  - Parse job status values are `queued`, `running`, `completed`, `failed`.
  - Paper summary status values remain `pending`, `parsing`, `parsed`, `error`.

## References

- Design spec: `docs/superpowers/specs/2026-04-29-pdf-parser-selection-worker-design.md`
- Current PDF ingestion docs: `docs/pdf-ingestion.md`
- MinerU API docs: https://mineru.org.cn/doc/docs/
- Current Docling backend: `paper_engine/pdf/backends/docling.py`
