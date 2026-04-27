# Local Paper PDF Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully local, paper-only PDF parsing layer that routes normal PDFs to PyMuPDF4LLM, routes scanned or complex papers to MinerU, extracts metadata with bounded GROBID calls, persists parse jobs and diagnostics, and falls back visibly when structured parsing is incomplete.

**Architecture:** Keep the current `ParseDocument` -> `pdf_chunker` -> `pdf_persistence` contract as the downstream boundary. Add a durable `parse_jobs` state machine, a `ParsePlan` produced by the PyMuPDF profiler, local-only parser routing, quality-gated fallback, GROBID metadata enrichment, and a worker facade that can run in-process now and move to Celery/RQ later.

**Tech Stack:** Python 3.11, FastAPI, SQLite, PyMuPDF, PyMuPDF4LLM, optional MinerU local package, GROBID HTTP service, pytest.

---

## File Structure

### New Files

- `pdf_parse_plan.py`: Builds `ParsePlan` from `PdfQualityReport` and profiler metadata. Owns the routing heuristics.
- `pdf_quality_gate.py`: Computes text density, garbled ratio, page coverage, and the V1 low-quality decision.
- `parse_jobs.py`: SQLite helpers for creating jobs, transitions, cancellation, warnings, timings, and reusable parse lookup.
- `parse_worker.py`: In-process local parse orchestration. Owns stage order, GROBID/body parallelism, MinerU concurrency, fallback, persistence, and paper status updates.
- `pdf_backend_mineru.py`: Optional MinerU adapter behind `PdfParserBackend`.
- `pdf_backend_raw.py`: Last-resort raw PyMuPDF text backend that preserves searchable text and marks `raw_text_only_fallback`.
- `pdf_enrichment.py`: Local academic enrichment that merges structured references, binds nearby captions, and preserves formula metadata.
- `paper_metadata.py`: Metadata merge helpers for updating `papers` from GROBID without overwriting user-filled fields.
- `scripts/eval_reference_papers.py`: Local evaluation runner for untracked PDFs under `reference_paper/`.
- `requirements-local-parser.txt`: Reproducible local parser dependency layer.
- `tests/test_parse_jobs.py`
- `tests/test_pdf_parse_plan.py`
- `tests/test_pdf_quality_gate.py`
- `tests/test_pdf_backend_mineru.py`
- `tests/test_pdf_backend_raw.py`
- `tests/test_pdf_enrichment.py`
- `tests/test_parse_worker.py`
- `tests/test_paper_metadata.py`
- `tests/test_eval_reference_papers.py`

### Existing Files To Modify

- `.gitignore`: Ignore `.DS_Store` and `reference_paper/`.
- `db_migrations.py`: Add schema version 5 with `parse_jobs` and parse-run diagnostic columns.
- `tests/test_db_migrations.py`: Assert schema version 5 and parse-job diagnostics schema.
- `pdf_models.py`: Add `raw_text` extraction method and diagnostics metadata expectations.
- `pdf_profile.py`: Emit profiler metadata needed by `ParsePlan`.
- `pdf_router.py`: Remove LlamaParse from routing, add MinerU and raw PyMuPDF candidates.
- `tests/test_pdf_router.py`: Replace LlamaParse routing tests with local-only router expectations.
- `pdf_backend_grobid.py`: Set default timeout to 60 seconds, add one retry, keep failure non-fatal to the worker.
- `pdf_persistence.py`: Persist parse-plan diagnostics, stage timings, review flags, and clone reusable parse results.
- `parser.py`: Export lazy wrappers for the new local worker entry points.
- `routes_papers.py`: Create parse jobs on upload, expose job status/cancel endpoints, and dispatch the in-process worker.
- `pyproject.toml`: Add new modules to `py-modules`; keep parser dependencies local-first.
- `docs/pdf-ingestion.md`: Update the parser architecture once tests pass.

## Invariants

- The parser router must not call LlamaParse or any cloud parser.
- `papers.parse_status` stays coarse: `pending`, `parsing`, `parsed`, `error`. Detailed statuses live in `parse_jobs.status`.
- `completed_with_warnings` and `review_needed` parse jobs map to `papers.parse_status = 'parsed'` when searchable passages exist.
- GROBID failure records a warning and never fails the whole job.
- MinerU has a process-wide concurrency limit of one active MinerU parse.
- V1 cancellation is cooperative: worker checks before every stage and before acquiring the MinerU gate.
- V1 does not link in-text citation mentions to reference entries.
- `reference_paper/` PDFs remain local and untracked.

---

## Task 1: Git Hygiene And Local Parser Requirements

**Files:**
- Modify: `.gitignore`
- Create: `requirements-local-parser.txt`

- [ ] **Step 1: Write the ignore and requirements changes**

Add these lines to `.gitignore`:

```gitignore
.DS_Store
reference_paper/
```

Create `requirements-local-parser.txt`:

```text
PyMuPDF>=1.24.0
pymupdf4llm>=0.0.20
httpx>=0.27.0
tiktoken>=0.7.0
```

The MinerU adapter will detect either `magic_pdf` or `mineru` at runtime. Keep MinerU outside this file until the local installation in this machine confirms the import path and model profile that work with `reference_paper/`.

- [ ] **Step 2: Verify the local PDFs are ignored**

Run:

```bash
git check-ignore -v reference_paper
git check-ignore -v reference_paper/example.pdf
```

Expected: both commands print a matching `.gitignore` rule.

- [ ] **Step 3: Commit**

```bash
git add .gitignore requirements-local-parser.txt
git commit -m "Add local parser dependency hygiene"
```

---

## Task 2: Parse Job Schema And Diagnostics Columns

**Files:**
- Modify: `db_migrations.py`
- Modify: `tests/test_db_migrations.py`

- [ ] **Step 1: Write failing migration tests**

Add these tests to `tests/test_db_migrations.py` and change `EXPECTED_SCHEMA_VERSION = 5`:

```python
def test_parse_jobs_schema_created() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path)

        columns = table_columns(conn, "parse_jobs")
        assert {
            "id",
            "paper_id",
            "space_id",
            "status",
            "requested_backend",
            "parse_plan_json",
            "stage_timings_json",
            "warnings_json",
            "review_flags_json",
            "error_message",
            "created_at",
            "started_at",
            "completed_at",
            "cancelled_at",
        }.issubset(columns)

        indexes = index_names(conn, "parse_jobs")
        assert "idx_parse_jobs_paper_id" in indexes
        assert "idx_parse_jobs_status" in indexes
        assert ("papers", ("paper_id", "space_id"), ("id", "space_id")) in foreign_key_groups(
            conn,
            "parse_jobs",
        )
        conn.close()


def test_parse_runs_has_parser_diagnostics_columns() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(db_path)

        columns = table_columns(conn, "parse_runs")
        assert "parse_plan_json" in columns
        assert "stage_timings_json" in columns
        assert "review_flags_json" in columns
        assert "parser_versions_json" in columns

        conn.close()
```

- [ ] **Step 2: Run the migration tests and verify failure**

Run:

```bash
.venv/bin/pytest -q tests/test_db_migrations.py::test_parse_jobs_schema_created tests/test_db_migrations.py::test_parse_runs_has_parser_diagnostics_columns
```

Expected: FAIL because schema version 5 and the new columns do not exist yet.

- [ ] **Step 3: Implement migration version 5**

In `db_migrations.py`, set `LATEST_SCHEMA_VERSION = 5` and add:

```python
def _create_parse_job_schema(conn: sqlite3.Connection) -> None:
    """Create durable parse-job state and parser diagnostics columns."""
    statements = (
        """
        CREATE TABLE IF NOT EXISTS parse_jobs (
            id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_backend TEXT NOT NULL DEFAULT '',
            parse_plan_json TEXT NOT NULL DEFAULT '{}',
            stage_timings_json TEXT NOT NULL DEFAULT '{}',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            review_flags_json TEXT NOT NULL DEFAULT '[]',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            completed_at TEXT,
            cancelled_at TEXT,
            FOREIGN KEY (paper_id, space_id)
                REFERENCES papers(id, space_id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_parse_jobs_paper_id
            ON parse_jobs(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_parse_jobs_space_id
            ON parse_jobs(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_parse_jobs_status
            ON parse_jobs(status)
        """,
        """
        ALTER TABLE parse_runs
        ADD COLUMN parse_plan_json TEXT NOT NULL DEFAULT '{}'
        """,
        """
        ALTER TABLE parse_runs
        ADD COLUMN stage_timings_json TEXT NOT NULL DEFAULT '{}'
        """,
        """
        ALTER TABLE parse_runs
        ADD COLUMN review_flags_json TEXT NOT NULL DEFAULT '[]'
        """,
        """
        ALTER TABLE parse_runs
        ADD COLUMN parser_versions_json TEXT NOT NULL DEFAULT '{}'
        """,
    )
    for statement in statements:
        conn.execute(statement)
```

Register the migration:

```python
MIGRATIONS: dict[int, Migration] = {
    1: _create_parse_run_document_tables,
    2: _extend_passages_with_provenance_columns,
    3: _create_analysis_run_and_card_provenance_schema,
    4: _create_passage_embedding_schema,
    5: _create_parse_job_schema,
}
```

- [ ] **Step 4: Run migration tests**

```bash
.venv/bin/pytest -q tests/test_db_migrations.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db_migrations.py tests/test_db_migrations.py
git commit -m "Add parse job schema"
```

---

## Task 3: Parse Job Repository

**Files:**
- Create: `parse_jobs.py`
- Create: `tests/test_parse_jobs.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing repository tests**

Create `tests/test_parse_jobs.py`:

```python
import json
import sqlite3
from pathlib import Path

import pytest

from db import init_db
from parse_jobs import (
    ParseJobCancelled,
    append_job_warning,
    cancel_parse_job,
    check_not_cancelled,
    create_parse_job,
    get_parse_job,
    record_stage_timing,
    transition_parse_job,
)


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(tmp_path / "test.db")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        "INSERT INTO papers (id, space_id, title) VALUES ('paper-1', 'space-1', 'Paper')"
    )
    conn.commit()
    return conn


def test_create_and_transition_parse_job(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    job = create_parse_job(conn, paper_id="paper-1", space_id="space-1")

    assert job["status"] == "pending"

    transition_parse_job(conn, job["id"], "profiling")
    updated = get_parse_job(conn, job["id"])
    assert updated["status"] == "profiling"
    assert updated["started_at"] is not None


def test_cancelled_job_raises_before_stage(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    job = create_parse_job(conn, paper_id="paper-1", space_id="space-1")
    cancel_parse_job(conn, job["id"])

    with pytest.raises(ParseJobCancelled):
        check_not_cancelled(conn, job["id"])


def test_append_warning_and_stage_timing_are_json_lists_and_maps(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    job = create_parse_job(conn, paper_id="paper-1", space_id="space-1")

    append_job_warning(conn, job["id"], "grobid_failed:timeout")
    record_stage_timing(conn, job["id"], "grobid", 1.25)

    updated = get_parse_job(conn, job["id"])
    assert json.loads(updated["warnings_json"]) == ["grobid_failed:timeout"]
    assert json.loads(updated["stage_timings_json"]) == {"grobid": 1.25}
```

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_parse_jobs.py
```

Expected: FAIL because `parse_jobs.py` does not exist.

- [ ] **Step 3: Implement parse job helpers**

Create `parse_jobs.py`:

```python
"""Durable parse-job state helpers."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Final, Literal, TypeAlias

ParseJobStatus: TypeAlias = Literal[
    "pending",
    "profiling",
    "metadata",
    "body_parsing",
    "enriching",
    "indexing",
    "completed",
    "completed_with_warnings",
    "review_needed",
    "cancelled",
    "failed",
]

TERMINAL_STATUSES: Final[set[str]] = {
    "completed",
    "completed_with_warnings",
    "review_needed",
    "cancelled",
    "failed",
}


class ParseJobCancelled(RuntimeError):
    """Raised when a worker observes a cancelled job."""


def _json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def create_parse_job(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    space_id: str,
    requested_backend: str = "",
) -> sqlite3.Row:
    job_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO parse_jobs (id, paper_id, space_id, status, requested_backend)
        VALUES (?, ?, ?, 'pending', ?)
        """,
        (job_id, paper_id, space_id, requested_backend),
    )
    conn.commit()
    return get_parse_job(conn, job_id)


def get_parse_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM parse_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(f"parse job not found: {job_id}")
    return row


def transition_parse_job(
    conn: sqlite3.Connection,
    job_id: str,
    status: ParseJobStatus,
    *,
    error_message: str = "",
) -> None:
    current = get_parse_job(conn, job_id)
    if current["status"] == "cancelled" and status != "cancelled":
        raise ParseJobCancelled(f"parse job cancelled: {job_id}")

    started_sql = ", started_at = COALESCE(started_at, datetime('now'))"
    completed_sql = ""
    cancelled_sql = ""
    if status in TERMINAL_STATUSES:
        completed_sql = ", completed_at = COALESCE(completed_at, datetime('now'))"
    if status == "cancelled":
        cancelled_sql = ", cancelled_at = COALESCE(cancelled_at, datetime('now'))"

    conn.execute(
        f"""
        UPDATE parse_jobs
        SET status = ?,
            error_message = ?
            {started_sql}
            {completed_sql}
            {cancelled_sql}
        WHERE id = ?
        """,
        (status, error_message, job_id),
    )
    conn.commit()


def cancel_parse_job(conn: sqlite3.Connection, job_id: str) -> None:
    transition_parse_job(conn, job_id, "cancelled")


def check_not_cancelled(conn: sqlite3.Connection, job_id: str) -> None:
    if get_parse_job(conn, job_id)["status"] == "cancelled":
        raise ParseJobCancelled(f"parse job cancelled: {job_id}")


def append_job_warning(conn: sqlite3.Connection, job_id: str, warning: str) -> None:
    row = get_parse_job(conn, job_id)
    warnings = list(_json_loads(row["warnings_json"], []))
    warnings.append(warning)
    conn.execute(
        "UPDATE parse_jobs SET warnings_json = ? WHERE id = ?",
        (json.dumps(warnings, ensure_ascii=False), job_id),
    )
    conn.commit()


def record_stage_timing(
    conn: sqlite3.Connection,
    job_id: str,
    stage_name: str,
    elapsed_seconds: float,
) -> None:
    row = get_parse_job(conn, job_id)
    timings = dict(_json_loads(row["stage_timings_json"], {}))
    timings[stage_name] = round(elapsed_seconds, 4)
    conn.execute(
        "UPDATE parse_jobs SET stage_timings_json = ? WHERE id = ?",
        (json.dumps(timings, ensure_ascii=False), job_id),
    )
    conn.commit()
```

Add `parse_jobs` to `pyproject.toml` under `py-modules`.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest -q tests/test_parse_jobs.py tests/test_db_migrations.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add parse_jobs.py tests/test_parse_jobs.py pyproject.toml
git commit -m "Add parse job repository"
```

---

## Task 4: ParsePlan Model And Profiler Routing Rules

**Files:**
- Create: `pdf_parse_plan.py`
- Create: `tests/test_pdf_parse_plan.py`
- Modify: `pdf_profile.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing ParsePlan tests**

Create `tests/test_pdf_parse_plan.py`:

```python
from pdf_models import PdfQualityReport
from pdf_parse_plan import build_parse_plan


def test_normal_text_pdf_routes_to_pymupdf4llm() -> None:
    quality = PdfQualityReport(
        page_count=10,
        native_text_pages=10,
        image_only_pages=0,
        estimated_table_pages=0,
        estimated_two_column_pages=0,
        needs_ocr=False,
        needs_layout_model=False,
        metadata={"formula_signal_count": 0},
    )

    plan = build_parse_plan(quality)

    assert plan.is_scanned is False
    assert plan.is_complex_layout is False
    assert plan.primary_backend == "pymupdf4llm"
    assert plan.fallback_backend == "mineru"
    assert plan.run_grobid is True


def test_scanned_pdf_routes_to_mineru() -> None:
    quality = PdfQualityReport(
        page_count=10,
        native_text_pages=5,
        image_only_pages=5,
        estimated_table_pages=0,
        estimated_two_column_pages=0,
        needs_ocr=True,
        needs_layout_model=False,
    )

    plan = build_parse_plan(quality)

    assert plan.native_text_page_ratio == 0.5
    assert plan.is_scanned is True
    assert plan.primary_backend == "mineru"
    assert plan.fallback_backend == "raw-pymupdf"


def test_formula_or_multicolumn_pdf_is_complex() -> None:
    quality = PdfQualityReport(
        page_count=5,
        native_text_pages=5,
        image_only_pages=0,
        estimated_table_pages=0,
        estimated_two_column_pages=2,
        metadata={"formula_signal_count": 3},
    )

    plan = build_parse_plan(quality)

    assert plan.is_complex_layout is True
    assert plan.has_formulas is True
    assert plan.has_multi_column_pages is True
    assert plan.primary_backend == "mineru"
```

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_pdf_parse_plan.py
```

Expected: FAIL because `pdf_parse_plan.py` does not exist.

- [ ] **Step 3: Implement ParsePlan**

Create `pdf_parse_plan.py`:

```python
"""Parser routing plan derived from cheap PDF profiling."""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from pdf_models import PdfQualityReport

ParserBackendName: TypeAlias = Literal["pymupdf4llm", "mineru", "raw-pymupdf"]


class ParsePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    page_count: int = Field(ge=0)
    native_text_page_ratio: float = Field(ge=0.0, le=1.0)
    image_only_page_ratio: float = Field(ge=0.0, le=1.0)
    estimated_table_pages: int = Field(ge=0)
    estimated_formula_signals: int = Field(ge=0)
    estimated_two_column_pages: int = Field(ge=0)
    detected_language: str = ""
    has_tables: bool
    has_formulas: bool
    has_multi_column_pages: bool
    is_scanned: bool
    is_complex_layout: bool
    primary_backend: ParserBackendName
    fallback_backend: ParserBackendName
    run_grobid: bool = True


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def build_parse_plan(quality: PdfQualityReport) -> ParsePlan:
    page_count = quality.page_count
    native_ratio = _ratio(quality.native_text_pages, page_count)
    image_ratio = _ratio(quality.image_only_pages, page_count)
    formula_signals = int(quality.metadata.get("formula_signal_count", 0) or 0)
    language = str(quality.metadata.get("detected_language", "") or "")

    has_tables = quality.estimated_table_pages > 0
    has_formulas = formula_signals > 0
    has_multi_column_pages = quality.estimated_two_column_pages > 0
    is_scanned = native_ratio < 0.8
    is_complex = has_tables or has_formulas or has_multi_column_pages
    primary = "mineru" if is_scanned or is_complex else "pymupdf4llm"
    fallback = "mineru" if primary != "mineru" else "raw-pymupdf"

    return ParsePlan(
        page_count=page_count,
        native_text_page_ratio=native_ratio,
        image_only_page_ratio=image_ratio,
        estimated_table_pages=quality.estimated_table_pages,
        estimated_formula_signals=formula_signals,
        estimated_two_column_pages=quality.estimated_two_column_pages,
        detected_language=language,
        has_tables=has_tables,
        has_formulas=has_formulas,
        has_multi_column_pages=has_multi_column_pages,
        is_scanned=is_scanned,
        is_complex_layout=is_complex,
        primary_backend=primary,
        fallback_backend=fallback,
    )
```

Add `pdf_parse_plan` to `pyproject.toml`.

- [ ] **Step 4: Add profiler metadata signals**

In `pdf_profile.py`, add two cheap metadata keys to the returned `PdfQualityReport`:

```python
metadata={
    **existing_metadata,
    "formula_signal_count": formula_signal_count,
    "detected_language": detected_language,
}
```

Use cheap text scanning only:

```python
FORMULA_SIGNAL_RE = re.compile(r"(\([0-9]{1,3}\)\s*$|[∑∫√≈≤≥]|\\(?:alpha|beta|sum|int))")


def _count_formula_signals(text: str) -> int:
    return len(FORMULA_SIGNAL_RE.findall(text))


def _detect_language(text: str) -> str:
    ascii_letters = sum(1 for char in text if "A" <= char <= "z")
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    if cjk > ascii_letters * 0.2:
        return "zh"
    if ascii_letters:
        return "en"
    return ""
```

- [ ] **Step 5: Run ParsePlan and profiler tests**

```bash
.venv/bin/pytest -q tests/test_pdf_parse_plan.py tests/test_pdf_profile.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pdf_parse_plan.py pdf_profile.py tests/test_pdf_parse_plan.py pyproject.toml
git commit -m "Add local PDF parse planning"
```

---

## Task 5: Quality Gate

**Files:**
- Create: `pdf_quality_gate.py`
- Create: `tests/test_pdf_quality_gate.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing quality tests**

Create `tests/test_pdf_quality_gate.py`:

```python
from pdf_models import ParseDocument, ParseElement, PdfQualityReport
from pdf_quality_gate import evaluate_parse_quality, garbled_ratio


def _document(text: str, page_count: int = 2) -> ParseDocument:
    return ParseDocument(
        paper_id="paper-1",
        space_id="space-1",
        backend="pymupdf4llm",
        extraction_method="native_text",
        quality=PdfQualityReport(page_count=page_count, native_text_pages=page_count),
        elements=[
            ParseElement(
                id="e1",
                element_index=0,
                element_type="paragraph",
                text=text,
                page_number=1,
                extraction_method="native_text",
            )
        ],
    )


def test_text_density_below_threshold_is_low_quality() -> None:
    decision = evaluate_parse_quality(_document("short", page_count=3))

    assert decision.low_quality is True
    assert "low_text_density" in decision.flags


def test_garbled_ratio_over_threshold_is_low_quality() -> None:
    text = "good text " * 100 + "\ufffd" * 30

    decision = evaluate_parse_quality(_document(text, page_count=1))

    assert decision.low_quality is True
    assert decision.garbled_ratio > 0.1
    assert "high_garbled_ratio" in decision.flags


def test_normal_density_is_accepted() -> None:
    text = "method result discussion " * 80

    decision = evaluate_parse_quality(_document(text, page_count=1))

    assert decision.low_quality is False
    assert decision.text_density >= 0.3


def test_garbled_ratio_counts_replacement_and_control_chars() -> None:
    assert garbled_ratio("abc\ufffd\x00") == 0.4
```

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_pdf_quality_gate.py
```

Expected: FAIL because `pdf_quality_gate.py` does not exist.

- [ ] **Step 3: Implement quality gate**

Create `pdf_quality_gate.py`:

```python
"""V1 parse quality gate for local PDF parsing."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pdf_models import ParseDocument


class QualityDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    low_quality: bool
    text_chars: int = Field(ge=0)
    text_density: float = Field(ge=0.0)
    garbled_ratio: float = Field(ge=0.0, le=1.0)
    page_coverage: float = Field(ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list)


def _document_text(document: ParseDocument) -> str:
    return "\n".join(element.text for element in document.elements if element.text)


def garbled_ratio(text: str) -> float:
    if not text:
        return 0.0
    garbled = 0
    for char in text:
        if char == "\ufffd" or (ord(char) < 32 and char not in "\n\r\t"):
            garbled += 1
    return round(garbled / len(text), 4)


def evaluate_parse_quality(document: ParseDocument) -> QualityDecision:
    text = _document_text(document)
    text_chars = len(text)
    page_count = max(document.quality.page_count, 1)
    text_density = round(text_chars / max(page_count * 1000, 1), 4)
    ratio = garbled_ratio(text)
    pages_with_text = {element.page_number for element in document.elements if element.text.strip()}
    page_coverage = round(len(pages_with_text) / page_count, 4)

    flags: list[str] = []
    if text_density < 0.3:
        flags.append("low_text_density")
    if ratio > 0.1:
        flags.append("high_garbled_ratio")

    return QualityDecision(
        low_quality=bool(flags),
        text_chars=text_chars,
        text_density=text_density,
        garbled_ratio=ratio,
        page_coverage=page_coverage,
        flags=flags,
    )
```

Add `pdf_quality_gate` to `pyproject.toml`.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest -q tests/test_pdf_quality_gate.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_quality_gate.py tests/test_pdf_quality_gate.py pyproject.toml
git commit -m "Add local parse quality gate"
```

---

## Task 6: Raw PyMuPDF Text Fallback

**Files:**
- Create: `pdf_backend_raw.py`
- Create: `tests/test_pdf_backend_raw.py`
- Modify: `pdf_models.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing raw backend tests**

Create `tests/test_pdf_backend_raw.py`:

```python
from pathlib import Path
from typing import Any

import pytest

from pdf_backend_raw import RawPyMuPDFBackend
from pdf_models import PdfQualityReport


class FakePage:
    rect = type("Rect", (), {"width": 612.0, "height": 792.0})()

    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self, mode: str) -> str:
        assert mode == "text"
        return self.text


class FakeDoc:
    def __init__(self) -> None:
        self.pages = [FakePage("Title\n\nBody text."), FakePage("More text.")]
        self.closed = False

    def __len__(self) -> int:
        return len(self.pages)

    def __getitem__(self, index: int) -> FakePage:
        return self.pages[index]

    def close(self) -> None:
        self.closed = True


def test_raw_backend_produces_searchable_page_elements(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_doc = FakeDoc()

    monkeypatch.setattr("pdf_backend_raw._load_pymupdf", lambda: type("PM", (), {"open": lambda _, __: fake_doc})())

    document = RawPyMuPDFBackend().parse(
        Path("paper.pdf"),
        "paper-1",
        "space-1",
        PdfQualityReport(page_count=2, native_text_pages=2),
    )

    assert document.backend == "raw-pymupdf"
    assert document.extraction_method == "raw_text"
    assert [element.page_number for element in document.elements] == [1, 2]
    assert document.metadata["raw_text_only_fallback"] is True
    assert "raw_text_only_fallback" in document.quality.warnings
    assert fake_doc.closed is True
```

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_pdf_backend_raw.py
```

Expected: FAIL because `raw_text` is not a valid extraction method and `pdf_backend_raw.py` does not exist.

- [ ] **Step 3: Add the raw extraction method**

In `pdf_models.py`, extend the literal and tuple:

```python
ExtractionMethod: TypeAlias = Literal[
    "native_text",
    "ocr",
    "layout_model",
    "llm_parser",
    "legacy",
    "raw_text",
]

EXTRACTION_METHODS: Final[tuple[ExtractionMethod, ...]] = (
    "native_text",
    "ocr",
    "layout_model",
    "llm_parser",
    "legacy",
    "raw_text",
)
```

- [ ] **Step 4: Implement raw backend**

Create `pdf_backend_raw.py`:

```python
"""Last-resort raw PyMuPDF text backend."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

from pdf_backend_base import ParserBackendError, ParserBackendUnavailable
from pdf_models import ParseDocument, ParseElement, PdfQualityReport

_BACKEND_NAME = "raw-pymupdf"


def _load_pymupdf() -> Any:
    if importlib.util.find_spec("pymupdf") is None:
        raise ParserBackendUnavailable(_BACKEND_NAME, "pymupdf is not installed")
    return importlib.import_module("pymupdf")


class RawPyMuPDFBackend:
    name = _BACKEND_NAME

    def is_available(self) -> bool:
        return importlib.util.find_spec("pymupdf") is not None

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        try:
            pymupdf = _load_pymupdf()
            doc = pymupdf.open(str(file_path))
        except ParserBackendUnavailable:
            raise
        except Exception as exc:
            raise ParserBackendError(self.name, "failed to open PDF", cause=exc) from exc

        elements: list[ParseElement] = []
        try:
            for page_index in range(len(doc)):
                text = doc[page_index].get_text("text").strip()
                if not text:
                    continue
                elements.append(
                    ParseElement(
                        id=f"raw-page-{page_index + 1:04d}",
                        element_index=len(elements),
                        element_type="paragraph",
                        text=text,
                        page_number=page_index + 1,
                        extraction_method="raw_text",
                        metadata={"source": "pymupdf.get_text_text", "raw_page_text": True},
                    )
                )
        except Exception as exc:
            raise ParserBackendError(self.name, "failed to extract raw text", cause=exc) from exc
        finally:
            doc.close()

        warnings = list(quality_report.warnings)
        if "raw_text_only_fallback" not in warnings:
            warnings.append("raw_text_only_fallback")
        quality = quality_report.model_copy(update={"warnings": warnings})

        return ParseDocument(
            paper_id=paper_id,
            space_id=space_id,
            backend=self.name,
            extraction_method="raw_text",
            quality=quality,
            elements=elements,
            metadata={
                "raw_text_only_fallback": True,
                "parser": "pymupdf.get_text_text",
            },
        )
```

Add `pdf_backend_raw` to `pyproject.toml`.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest -q tests/test_pdf_backend_raw.py tests/test_pdf_models.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pdf_backend_raw.py pdf_models.py tests/test_pdf_backend_raw.py pyproject.toml
git commit -m "Add raw PyMuPDF fallback backend"
```

---

## Task 7: MinerU Backend Adapter And Concurrency Gate

**Files:**
- Create: `pdf_backend_mineru.py`
- Create: `tests/test_pdf_backend_mineru.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing MinerU adapter tests**

Create `tests/test_pdf_backend_mineru.py`:

```python
from pathlib import Path

import pytest

from pdf_backend_mineru import MinerUBackend, MinerUConcurrencyGate
from pdf_models import PdfQualityReport


def test_mineru_backend_normalizes_markdown_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = MinerUBackend(
        runner=lambda path: {
            "markdown": "# Title\n\nParagraph text\n\n$$x=1$$",
            "tables": [
                {"page": 2, "caption": "Table 1", "cells": [["A", "B"], ["1", "2"]]},
            ],
            "assets": [
                {"type": "figure", "page": 3, "uri": "figures/fig1.png", "caption": "Figure 1"},
            ],
            "metadata": {"model_profile": "local-test"},
        }
    )

    document = backend.parse(
        Path("paper.pdf"),
        "paper-1",
        "space-1",
        PdfQualityReport(page_count=3, needs_layout_model=True),
    )

    assert document.backend == "mineru"
    assert document.extraction_method == "layout_model"
    assert any(element.element_type == "heading" for element in document.elements)
    assert any(element.element_type == "equation" for element in document.elements)
    assert len(document.tables) == 1
    assert len(document.assets) == 1
    assert document.metadata["mineru"]["model_profile"] == "local-test"


def test_mineru_gate_allows_one_active_task() -> None:
    gate = MinerUConcurrencyGate(limit=1)
    assert gate.try_acquire() is True
    assert gate.try_acquire() is False
    gate.release()
    assert gate.try_acquire() is True
    gate.release()


def test_mineru_gate_acquire_can_timeout() -> None:
    gate = MinerUConcurrencyGate(limit=1)
    assert gate.acquire(timeout_seconds=0.01) is True
    assert gate.acquire(timeout_seconds=0.01) is False
    gate.release()
```

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_pdf_backend_mineru.py
```

Expected: FAIL because `pdf_backend_mineru.py` does not exist.

- [ ] **Step 3: Implement MinerU adapter**

Create `pdf_backend_mineru.py`:

```python
"""Optional MinerU parser backend for local complex-paper parsing."""

from __future__ import annotations

import importlib
import importlib.util
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pdf_backend_base import ParserBackendError, ParserBackendUnavailable
from pdf_models import ParseAsset, ParseDocument, ParseElement, ParseTable, PdfQualityReport

_BACKEND_NAME = "mineru"


class MinerUConcurrencyGate:
    def __init__(self, limit: int = 1) -> None:
        self._semaphore = threading.BoundedSemaphore(limit)

    def try_acquire(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def acquire(self, *, timeout_seconds: float | None = None) -> bool:
        if timeout_seconds is None:
            self._semaphore.acquire()
            return True
        return self._semaphore.acquire(timeout=timeout_seconds)

    def release(self) -> None:
        self._semaphore.release()


MINERU_GATE = MinerUConcurrencyGate(limit=1)


def _mineru_available() -> bool:
    return (
        importlib.util.find_spec("magic_pdf") is not None
        or importlib.util.find_spec("mineru") is not None
    )


def _run_installed_mineru(file_path: Path) -> dict[str, Any]:
    if importlib.util.find_spec("magic_pdf") is not None:
        magic_pdf = importlib.import_module("magic_pdf")
        analyze = getattr(magic_pdf, "analyze_pdf", None)
        if callable(analyze):
            return dict(analyze(str(file_path)))
    if importlib.util.find_spec("mineru") is not None:
        mineru = importlib.import_module("mineru")
        analyze = getattr(mineru, "analyze_pdf", None)
        if callable(analyze):
            return dict(analyze(str(file_path)))
    raise ParserBackendUnavailable(_BACKEND_NAME, "MinerU import path is not available")


class MinerUBackend:
    name = _BACKEND_NAME

    def __init__(
        self,
        *,
        runner: Callable[[Path], dict[str, Any]] | None = None,
        gate: MinerUConcurrencyGate = MINERU_GATE,
        acquire_timeout_seconds: float = 600.0,
    ) -> None:
        self._runner = runner or _run_installed_mineru
        self._gate = gate
        self._acquire_timeout_seconds = acquire_timeout_seconds

    def is_available(self) -> bool:
        return self._runner is not _run_installed_mineru or _mineru_available()

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        acquired = self._gate.acquire(timeout_seconds=self._acquire_timeout_seconds)
        if not acquired:
            raise ParserBackendUnavailable(
                self.name,
                f"timed out waiting for MinerU gate after {self._acquire_timeout_seconds}s",
            )
        try:
            payload = self._runner(file_path)
            return _payload_to_document(payload, paper_id, space_id, quality_report)
        except ParserBackendUnavailable:
            raise
        except Exception as exc:
            raise ParserBackendError(self.name, "MinerU parse failed", cause=exc) from exc
        finally:
            self._gate.release()


def _payload_to_document(
    payload: dict[str, Any],
    paper_id: str,
    space_id: str,
    quality_report: PdfQualityReport,
) -> ParseDocument:
    markdown = str(payload.get("markdown", "") or "")
    elements: list[ParseElement] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            element_type = "heading"
            text = stripped.lstrip("#").strip()
        elif stripped.startswith("$$") and stripped.endswith("$$"):
            element_type = "equation"
            text = stripped.strip("$").strip()
        else:
            element_type = "paragraph"
            text = stripped
        elements.append(
            ParseElement(
                id=f"mineru-e{len(elements):04d}",
                element_index=len(elements),
                element_type=element_type,
                text=text,
                page_number=0,
                extraction_method="layout_model",
                metadata={"source": "mineru"},
            )
        )

    tables: list[ParseTable] = []
    for raw in list(payload.get("tables", []) or []):
        tables.append(
            ParseTable(
                id=f"mineru-t{len(tables):04d}",
                table_index=len(tables),
                page_number=int(raw.get("page", 0) or 0),
                caption=str(raw.get("caption", "") or ""),
                cells=[[str(cell) for cell in row] for row in list(raw.get("cells", []) or [])],
                metadata={"source": "mineru"},
            )
        )

    assets: list[ParseAsset] = []
    for raw in list(payload.get("assets", []) or []):
        assets.append(
            ParseAsset(
                id=f"mineru-a{len(assets):04d}",
                asset_type=str(raw.get("type", "figure") or "figure"),
                page_number=int(raw.get("page", 0) or 0),
                uri=str(raw.get("uri", "") or ""),
                metadata={"source": "mineru", "caption": str(raw.get("caption", "") or "")},
            )
        )

    return ParseDocument(
        paper_id=paper_id,
        space_id=space_id,
        backend=_BACKEND_NAME,
        extraction_method="layout_model",
        quality=quality_report,
        elements=elements,
        tables=tables,
        assets=assets,
        metadata={"mineru": dict(payload.get("metadata", {}) or {})},
    )
```

Add `pdf_backend_mineru` to `pyproject.toml`.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest -q tests/test_pdf_backend_mineru.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_backend_mineru.py tests/test_pdf_backend_mineru.py pyproject.toml
git commit -m "Add MinerU parser backend"
```

---

## Task 8: Local-Only Router

**Files:**
- Modify: `pdf_router.py`
- Modify: `tests/test_pdf_router.py`

- [ ] **Step 1: Replace LlamaParse router tests**

In `tests/test_pdf_router.py`, remove tests that expect lazy LlamaParse resolution. Add these tests:

```python
def test_complex_pdf_selects_mineru_before_pymupdf(tmp_path: Path) -> None:
    router_module = _router_module()
    mineru = FakeBackend("mineru", extraction_method="layout_model")
    pymupdf = FakeBackend("pymupdf4llm")

    router = router_module.PdfBackendRouter(
        pymupdf4llm=pymupdf,
        mineru=mineru,
        raw=FakeBackend("raw-pymupdf", extraction_method="raw_text"),
        grobid_client=None,
    )
    document = router.parse_pdf(
        tmp_path / "complex.pdf",
        "paper-1",
        "space-1",
        _quality(needs_layout_model=True, estimated_table_pages=1),
    )

    assert document.backend == "mineru"
    assert len(mineru.calls) == 1
    assert pymupdf.calls == []


def test_router_never_resolves_llamaparse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    router_module = _router_module()
    monkeypatch.delattr(router_module, "get_configured_llamaparse_backend", raising=False)

    router = router_module.PdfBackendRouter(
        pymupdf4llm=FakeBackend("pymupdf4llm"),
        mineru=FakeBackend("mineru", available=False, extraction_method="layout_model"),
        raw=FakeBackend("raw-pymupdf", extraction_method="raw_text"),
        grobid_client=None,
    )

    document = router.parse_pdf(tmp_path / "clean.pdf", "paper-1", "space-1", _quality())

    assert document.backend == "pymupdf4llm"


def test_mineru_failure_falls_back_to_raw_for_scanned_pdf(tmp_path: Path) -> None:
    router_module = _router_module()
    mineru = FakeBackend("mineru", action="error", extraction_method="layout_model")
    raw = FakeBackend("raw-pymupdf", extraction_method="raw_text")

    router = router_module.PdfBackendRouter(
        pymupdf4llm=FakeBackend("pymupdf4llm"),
        mineru=mineru,
        raw=raw,
        grobid_client=None,
    )

    document = router.parse_pdf(
        tmp_path / "scan.pdf",
        "paper-1",
        "space-1",
        _quality(needs_ocr=True, native_text_pages=0, image_only_pages=2),
    )

    assert document.backend == "raw-pymupdf"
    assert len(raw.calls) == 1
```

Update `FakeBackend.extraction_method_for()` to accept `raw_text`.

- [ ] **Step 2: Run router tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_pdf_router.py
```

Expected: FAIL because `PdfBackendRouter` still has LlamaParse routing and no MinerU/raw arguments.

- [ ] **Step 3: Implement local router constructor and candidates**

In `pdf_router.py`, replace the default backend imports with local backends:

```python
from pdf_backend_mineru import MinerUBackend
from pdf_backend_pymupdf4llm import PyMuPDF4LLMBackend
from pdf_backend_raw import RawPyMuPDFBackend
from pdf_parse_plan import ParsePlan, build_parse_plan
```

Change the router constructor shape:

```python
class PdfBackendRouter:
    def __init__(
        self,
        *,
        pymupdf4llm: PdfParserBackend | None = None,
        mineru: PdfParserBackend | None = None,
        raw: PdfParserBackend | None = None,
        grobid_client: GrobidClient | None = None,
    ) -> None:
        self.backends = {
            "pymupdf4llm": pymupdf4llm or PyMuPDF4LLMBackend(),
            "mineru": mineru or MinerUBackend(),
            "raw-pymupdf": raw or RawPyMuPDFBackend(),
        }
        self.grobid_client = grobid_client
```

Select candidate names from `ParsePlan`:

```python
def _candidate_names(self, plan: ParsePlan) -> list[str]:
    names = [plan.primary_backend]
    if plan.fallback_backend not in names:
        names.append(plan.fallback_backend)
    if "raw-pymupdf" not in names:
        names.append("raw-pymupdf")
    return names
```

In `parse_pdf()`, build a plan when the caller does not pass one:

```python
def parse_pdf(
    self,
    file_path: Path,
    paper_id: str,
    space_id: str,
    quality_report: PdfQualityReport,
    *,
    parse_plan: ParsePlan | None = None,
) -> ParseDocument:
    plan = parse_plan or build_parse_plan(quality_report)
    for backend_name in self._candidate_names(plan):
        document = self._try_backend(backend_name, file_path, paper_id, space_id, quality_report)
        if document is not None:
            document.metadata.setdefault("parse_plan", plan.model_dump())
            return self._merge_grobid(file_path, document)
    raise ParserBackendError("router", "no local parser backend succeeded")
```

Keep `_merge_grobid()` but make it non-fatal, as it is today.

- [ ] **Step 4: Run router tests**

```bash
.venv/bin/pytest -q tests/test_pdf_router.py tests/test_pdf_parse_plan.py tests/test_pdf_backend_raw.py tests/test_pdf_backend_mineru.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_router.py tests/test_pdf_router.py
git commit -m "Route PDFs through local parser backends"
```

---

## Task 9: Bounded GROBID Calls

**Files:**
- Modify: `pdf_backend_grobid.py`
- Create or modify: `tests/test_pdf_backend_grobid.py`

- [ ] **Step 1: Write failing GROBID timeout/retry tests**

Add to `tests/test_pdf_backend_grobid.py`:

```python
from pathlib import Path

import httpx
import pytest

from pdf_backend_grobid import GrobidClient, GrobidClientError


def test_grobid_default_timeout_is_sixty_seconds() -> None:
    client = GrobidClient(base_url="http://grobid.test")
    assert client.timeout == 60.0
    assert client.max_retries == 1


def test_grobid_retries_once_on_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    calls = 0

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.TimeoutException("timeout")

    client = GrobidClient(base_url="http://grobid.test", timeout=60.0, max_retries=1)
    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(GrobidClientError, match="processFulltextDocument"):
        client.process_fulltext(pdf)

    assert calls == 2
```

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_pdf_backend_grobid.py
```

Expected: FAIL because default timeout is 30 seconds and retry is not implemented.

- [ ] **Step 3: Implement timeout and retry**

In `GrobidClient.__init__()`:

```python
def __init__(
    self,
    base_url: str,
    timeout: float = 60.0,
    *,
    max_retries: int = 1,
    http_client: httpx.Client | None = None,
) -> None:
    self.base_url = base_url.strip().rstrip("/")
    self.timeout = timeout
    self.max_retries = max_retries
    self._owns_client = http_client is None
    self._client = http_client or httpx.Client(timeout=timeout)
```

Keep the existing `is_alive()` method that calls `/api/isalive`; the worker depends on it before trying fulltext extraction.

Wrap the POST:

```python
def _post_pdf(self, endpoint: str, file_path: Path) -> str:
    last_exc: BaseException | None = None
    for attempt in range(self.max_retries + 1):
        try:
            with file_path.open("rb") as pdf_file:
                response = self._client.post(
                    f"{self.base_url}/{endpoint}",
                    files={"input": (file_path.name, pdf_file, "application/pdf")},
                )
            response.raise_for_status()
            return response.text
        except (OSError, httpx.HTTPError) as exc:
            last_exc = exc
            if attempt >= self.max_retries:
                raise GrobidClientError(f"GROBID {endpoint} request failed") from exc
    raise GrobidClientError(f"GROBID {endpoint} request failed") from last_exc
```

Update `process_header()` and `process_fulltext()` to call `_post_pdf()`.

- [ ] **Step 4: Run GROBID tests**

```bash
.venv/bin/pytest -q tests/test_pdf_backend_grobid.py tests/test_pdf_router.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_backend_grobid.py tests/test_pdf_backend_grobid.py
git commit -m "Bound GROBID metadata calls"
```

---

## Task 10: Paper Metadata Merge

**Files:**
- Create: `paper_metadata.py`
- Create: `tests/test_paper_metadata.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing metadata tests**

Create `tests/test_paper_metadata.py`:

```python
import sqlite3
from pathlib import Path

from db import init_db
from paper_metadata import apply_extracted_metadata
from pdf_backend_grobid import GrobidMetadata


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(tmp_path / "test.db")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, title, authors, year, doi, abstract)
        VALUES ('paper-1', 'space-1', '', '', NULL, '', '')
        """
    )
    conn.commit()
    return conn


def test_apply_extracted_metadata_fills_empty_fields(tmp_path: Path) -> None:
    conn = _conn(tmp_path)

    apply_extracted_metadata(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        metadata=GrobidMetadata(
            title="GROBID Title",
            authors=["Ada Lovelace", "Grace Hopper"],
            year=1843,
            venue="Notes",
            doi="10.0000/example",
            abstract="Abstract text",
        ),
    )

    row = conn.execute("SELECT * FROM papers WHERE id = 'paper-1'").fetchone()
    assert row["title"] == "GROBID Title"
    assert row["authors"] == "Ada Lovelace; Grace Hopper"
    assert row["year"] == 1843
    assert row["venue"] == "Notes"
    assert row["doi"] == "10.0000/example"
    assert row["abstract"] == "Abstract text"


def test_apply_extracted_metadata_does_not_overwrite_existing_user_title(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    conn.execute("UPDATE papers SET title = 'User Title' WHERE id = 'paper-1'")
    conn.commit()

    apply_extracted_metadata(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        metadata=GrobidMetadata(title="GROBID Title"),
    )

    row = conn.execute("SELECT title FROM papers WHERE id = 'paper-1'").fetchone()
    assert row["title"] == "User Title"
```

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_paper_metadata.py
```

Expected: FAIL because `paper_metadata.py` does not exist.

- [ ] **Step 3: Implement metadata merge helper**

Create `paper_metadata.py`:

```python
"""Paper metadata merge helpers for local parser output."""

from __future__ import annotations

import sqlite3

from pdf_backend_grobid import GrobidMetadata


def _authors_value(authors: list[str]) -> str:
    return "; ".join(author for author in authors if author.strip())


def apply_extracted_metadata(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    space_id: str,
    metadata: GrobidMetadata,
) -> None:
    row = conn.execute(
        "SELECT * FROM papers WHERE id = ? AND space_id = ?",
        (paper_id, space_id),
    ).fetchone()
    if row is None:
        raise KeyError(f"paper not found: {paper_id}")

    values: dict[str, object] = {}
    if metadata.title and not row["title"]:
        values["title"] = metadata.title
    if metadata.authors and not row["authors"]:
        values["authors"] = _authors_value(metadata.authors)
    if metadata.year and row["year"] is None:
        values["year"] = metadata.year
    if metadata.venue and not row["venue"]:
        values["venue"] = metadata.venue
    if metadata.doi and not row["doi"]:
        values["doi"] = metadata.doi
    if metadata.abstract and not row["abstract"]:
        values["abstract"] = metadata.abstract

    if not values:
        return

    assignments = ", ".join(f"{field} = ?" for field in values)
    conn.execute(
        f"UPDATE papers SET {assignments} WHERE id = ? AND space_id = ?",
        (*values.values(), paper_id, space_id),
    )
    conn.commit()
```

Add `paper_metadata` to `pyproject.toml`.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest -q tests/test_paper_metadata.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add paper_metadata.py tests/test_paper_metadata.py pyproject.toml
git commit -m "Merge extracted paper metadata"
```

---

## Task 11: Academic Enrichment Without Citation-Mention Linking

**Files:**
- Create: `pdf_enrichment.py`
- Create: `tests/test_pdf_enrichment.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing enrichment tests**

Create `tests/test_pdf_enrichment.py`:

```python
from pdf_backend_grobid import GrobidParseResult, GrobidReference
from pdf_enrichment import enrich_document
from pdf_models import ParseAsset, ParseDocument, ParseElement, ParseTable, PdfQualityReport


def test_enrichment_stores_structured_references_without_mentions() -> None:
    document = ParseDocument(
        paper_id="paper-1",
        space_id="space-1",
        backend="pymupdf4llm",
        extraction_method="native_text",
        quality=PdfQualityReport(page_count=1, native_text_pages=1),
        elements=[],
    )
    grobid = GrobidParseResult(
        references=[
            GrobidReference(
                id="b1",
                title="Reference Title",
                authors=["Grace Hopper"],
                year=1952,
                raw_text="raw",
            )
        ],
        raw_tei="<tei />",
    )

    enriched = enrich_document(document, grobid_result=grobid)

    assert enriched.metadata["references"][0]["title"] == "Reference Title"
    assert "citation_mentions" not in enriched.metadata


def test_enrichment_binds_caption_to_table_and_asset() -> None:
    document = ParseDocument(
        paper_id="paper-1",
        space_id="space-1",
        backend="mineru",
        extraction_method="layout_model",
        quality=PdfQualityReport(page_count=2, native_text_pages=2),
        elements=[
            ParseElement(
                id="caption-1",
                element_index=0,
                element_type="caption",
                text="Figure 1: Architecture",
                page_number=1,
                extraction_method="layout_model",
            ),
            ParseElement(
                id="caption-2",
                element_index=1,
                element_type="caption",
                text="Table 1: Results",
                page_number=2,
                extraction_method="layout_model",
            ),
        ],
        tables=[ParseTable(id="table-1", table_index=0, page_number=2)],
        assets=[ParseAsset(id="asset-1", asset_type="figure", page_number=1)],
    )

    enriched = enrich_document(document, grobid_result=None)

    assert enriched.tables[0].caption == "Table 1: Results"
    assert enriched.assets[0].metadata["caption"] == "Figure 1: Architecture"
```

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_pdf_enrichment.py
```

Expected: FAIL because `pdf_enrichment.py` does not exist.

- [ ] **Step 3: Implement enrichment**

Create `pdf_enrichment.py`:

```python
"""Academic enrichment for normalized paper parse output."""

from __future__ import annotations

from pdf_backend_grobid import GrobidParseResult
from pdf_models import ParseDocument


def _reference_dicts(grobid_result: GrobidParseResult | None) -> list[dict[str, object]]:
    if grobid_result is None:
        return []
    return [reference.model_dump() for reference in grobid_result.references]


def _caption_by_prefix(document: ParseDocument, prefix: str) -> dict[int, str]:
    captions: dict[int, str] = {}
    lowered_prefix = prefix.lower()
    for element in document.elements:
        text = element.text.strip()
        if element.element_type == "caption" and text.lower().startswith(lowered_prefix):
            captions.setdefault(element.page_number, text)
    return captions


def enrich_document(
    document: ParseDocument,
    *,
    grobid_result: GrobidParseResult | None,
) -> ParseDocument:
    metadata = dict(document.metadata)
    references = _reference_dicts(grobid_result)
    if references:
        metadata["references"] = references
        metadata["reference_source"] = "grobid"

    table_captions = _caption_by_prefix(document, "table")
    figure_captions = _caption_by_prefix(document, "figure")

    tables = []
    for table in document.tables:
        caption = table.caption or table_captions.get(table.page_number, "")
        tables.append(table.model_copy(update={"caption": caption}))

    assets = []
    for asset in document.assets:
        asset_metadata = dict(asset.metadata)
        if asset.asset_type == "figure" and "caption" not in asset_metadata:
            caption = figure_captions.get(asset.page_number)
            if caption:
                asset_metadata["caption"] = caption
        assets.append(asset.model_copy(update={"metadata": asset_metadata}))

    return document.model_copy(update={"metadata": metadata, "tables": tables, "assets": assets})
```

Add `pdf_enrichment` to `pyproject.toml`.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest -q tests/test_pdf_enrichment.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_enrichment.py tests/test_pdf_enrichment.py pyproject.toml
git commit -m "Add local academic enrichment"
```

---

## Task 12: Parse Persistence Diagnostics And Parse Result Reuse

**Files:**
- Modify: `pdf_persistence.py`
- Create or modify: `tests/test_pdf_persistence.py`

- [ ] **Step 1: Write failing persistence tests**

Add tests to `tests/test_pdf_persistence.py`:

```python
import json

from pdf_models import ParseDocument, ParseElement, PassageRecord, PdfQualityReport
from pdf_persistence import clone_parse_result, persist_parse_result


def test_persist_parse_result_stores_diagnostics() -> None:
    conn = _test_conn()
    _seed_space_and_paper(conn)
    document = ParseDocument(
        paper_id="paper-1",
        space_id="space-1",
        backend="pymupdf4llm",
        extraction_method="native_text",
        quality=PdfQualityReport(page_count=1, native_text_pages=1, quality_score=0.9),
        elements=[
            ParseElement(
                id="e1",
                element_index=0,
                element_type="paragraph",
                text="body text",
                page_number=1,
                extraction_method="native_text",
            )
        ],
        metadata={
            "parse_plan": {"primary_backend": "pymupdf4llm"},
            "stage_timings": {"body_parsing": 0.2},
            "review_flags": [],
            "parser_versions": {"pymupdf4llm": "0.0.20"},
        },
    )
    passages = [
        PassageRecord(
            id="p1",
            paper_id="paper-1",
            space_id="space-1",
            original_text="body text",
            element_ids=["e1"],
        )
    ]

    parse_run_id = persist_parse_result(conn, "paper-1", "space-1", document, passages)

    row = conn.execute("SELECT * FROM parse_runs WHERE id = ?", (parse_run_id,)).fetchone()
    assert json.loads(row["parse_plan_json"]) == {"primary_backend": "pymupdf4llm"}
    assert json.loads(row["stage_timings_json"]) == {"body_parsing": 0.2}
    assert json.loads(row["review_flags_json"]) == []
    assert json.loads(row["parser_versions_json"]) == {"pymupdf4llm": "0.0.20"}


def test_clone_parse_result_creates_new_scoped_rows() -> None:
    conn = _test_conn()
    _seed_space_and_paper(conn)
    source_run_id = conn.execute(
        """
        INSERT INTO parse_runs (id, paper_id, space_id, backend, extraction_method, status)
        VALUES ('run-1', 'paper-1', 'space-1', 'pymupdf4llm', 'native_text', 'completed')
        RETURNING id
        """
    ).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO document_elements (
            id, parse_run_id, paper_id, space_id, element_index, element_type, text, page_number
        )
        VALUES ('e1', 'run-1', 'paper-1', 'space-1', 0, 'paragraph', 'text', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO passages (
            id, paper_id, space_id, original_text, parse_run_id, element_ids_json
        )
        VALUES ('passage-1', 'paper-1', 'space-1', 'text', 'run-1', '["e1"]')
        """
    )
    conn.execute(
        "INSERT INTO papers (id, space_id, title) VALUES ('paper-2', 'space-1', 'Copy')"
    )
    conn.commit()

    cloned_run_id = clone_parse_result(
        conn,
        source_parse_run_id=source_run_id,
        target_paper_id="paper-2",
        target_space_id="space-1",
    )

    assert cloned_run_id != source_run_id
    rows = conn.execute(
        "SELECT paper_id, space_id FROM document_elements WHERE parse_run_id = ?",
        (cloned_run_id,),
    ).fetchall()
    assert [dict(row) for row in rows] == [{"paper_id": "paper-2", "space_id": "space-1"}]
```

`tests/test_pdf_persistence.py` already defines `_test_conn()` and `_seed_space_and_paper()`. Use those helpers for both tests.

- [ ] **Step 2: Run persistence tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_pdf_persistence.py
```

Expected: FAIL because diagnostics columns are not written and `clone_parse_result()` does not exist.

- [ ] **Step 3: Store diagnostics in parse runs**

In `_insert_parse_run()`, include the new columns:

```python
parse_plan = parse_document.metadata.get("parse_plan", {})
stage_timings = parse_document.metadata.get("stage_timings", {})
review_flags = parse_document.metadata.get("review_flags", [])
parser_versions = parse_document.metadata.get("parser_versions", {})

conn.execute(
    """
    INSERT INTO parse_runs (
        id, paper_id, space_id, backend, extraction_method, status,
        quality_score, completed_at, warnings_json, config_json, metadata_json,
        parse_plan_json, stage_timings_json, review_flags_json, parser_versions_json
    )
    VALUES (?, ?, ?, ?, ?, 'completed', ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)
    """,
    (
        parse_run_id,
        paper_id,
        space_id,
        parse_document.backend,
        parse_document.extraction_method,
        parse_document.quality.quality_score,
        _json(parse_document.quality.warnings),
        _json({}),
        _json(parse_document.metadata),
        _json(parse_plan),
        _json(stage_timings),
        _json(review_flags),
        _json(parser_versions),
    ),
)
```

- [ ] **Step 4: Implement parse result clone helper**

Add `clone_parse_result` to the existing `__all__` list in `pdf_persistence.py`. The helper is in the same module as the existing `_json()` helper and `FTS_TABLE` import, so reuse those definitions directly.

Add the exported helper in `pdf_persistence.py`:

```python
def clone_parse_result(
    conn: sqlite3.Connection,
    *,
    source_parse_run_id: str,
    target_paper_id: str,
    target_space_id: str,
) -> str:
    source_run = conn.execute(
        "SELECT * FROM parse_runs WHERE id = ?",
        (source_parse_run_id,),
    ).fetchone()
    if source_run is None:
        raise KeyError(f"parse run not found: {source_parse_run_id}")

    clone_run_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO parse_runs (
            id, paper_id, space_id, backend, extraction_method, status, quality_score,
            completed_at, warnings_json, config_json, metadata_json,
            parse_plan_json, stage_timings_json, review_flags_json, parser_versions_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clone_run_id,
            target_paper_id,
            target_space_id,
            source_run["backend"],
            source_run["extraction_method"],
            source_run["status"],
            source_run["quality_score"],
            source_run["warnings_json"],
            source_run["config_json"],
            source_run["metadata_json"],
            source_run["parse_plan_json"],
            source_run["stage_timings_json"],
            source_run["review_flags_json"],
            source_run["parser_versions_json"],
        ),
    )

    element_id_map: dict[str, str] = {}
    for row in conn.execute(
        "SELECT * FROM document_elements WHERE parse_run_id = ? ORDER BY element_index",
        (source_parse_run_id,),
    ).fetchall():
        new_id = f"{clone_run_id}:{row['id']}"
        element_id_map[row["id"]] = new_id
        conn.execute(
            """
            INSERT INTO document_elements (
                id, parse_run_id, paper_id, space_id, element_index, element_type,
                text, page_number, bbox_json, heading_path_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                clone_run_id,
                target_paper_id,
                target_space_id,
                row["element_index"],
                row["element_type"],
                row["text"],
                row["page_number"],
                row["bbox_json"],
                row["heading_path_json"],
                row["metadata_json"],
            ),
        )

    for row in conn.execute(
        "SELECT * FROM passages WHERE parse_run_id = ? ORDER BY page_number, paragraph_index",
        (source_parse_run_id,),
    ).fetchall():
        element_ids = [element_id_map.get(element_id, element_id) for element_id in json.loads(row["element_ids_json"])]
        conn.execute(
            """
            INSERT INTO passages (
                id, paper_id, space_id, section, page_number, paragraph_index,
                original_text, parse_confidence, passage_type, parse_run_id,
                element_ids_json, heading_path_json, bbox_json, token_count,
                char_count, content_hash, parser_backend, extraction_method,
                quality_flags_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{clone_run_id}:{row['id']}",
                target_paper_id,
                target_space_id,
                row["section"],
                row["page_number"],
                row["paragraph_index"],
                row["original_text"],
                row["parse_confidence"],
                row["passage_type"],
                clone_run_id,
                _json(element_ids),
                row["heading_path_json"],
                row["bbox_json"],
                row["token_count"],
                row["char_count"],
                row["content_hash"],
                row["parser_backend"],
                row["extraction_method"],
                row["quality_flags_json"],
            ),
        )

    for row in conn.execute(
        "SELECT * FROM document_tables WHERE parse_run_id = ? ORDER BY table_index",
        (source_parse_run_id,),
    ).fetchall():
        source_element_id = row["element_id"]
        conn.execute(
            """
            INSERT INTO document_tables (
                id, parse_run_id, paper_id, space_id, element_id, table_index,
                page_number, caption, cells_json, bbox_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{clone_run_id}:{row['id']}",
                clone_run_id,
                target_paper_id,
                target_space_id,
                element_id_map.get(source_element_id) if source_element_id else None,
                row["table_index"],
                row["page_number"],
                row["caption"],
                row["cells_json"],
                row["bbox_json"],
                row["metadata_json"],
            ),
        )

    for row in conn.execute(
        "SELECT * FROM document_assets WHERE parse_run_id = ? ORDER BY page_number, id",
        (source_parse_run_id,),
    ).fetchall():
        source_element_id = row["element_id"]
        conn.execute(
            """
            INSERT INTO document_assets (
                id, parse_run_id, paper_id, space_id, element_id, asset_type,
                page_number, uri, bbox_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{clone_run_id}:{row['id']}",
                clone_run_id,
                target_paper_id,
                target_space_id,
                element_id_map.get(source_element_id) if source_element_id else None,
                row["asset_type"],
                row["page_number"],
                row["uri"],
                row["bbox_json"],
                row["metadata_json"],
            ),
        )

    for row in conn.execute(
        f"SELECT * FROM {FTS_TABLE} WHERE paper_id = ? AND space_id = ?",
        (source_run["paper_id"], source_run["space_id"]),
    ).fetchall():
        conn.execute(
            f"""
            INSERT INTO {FTS_TABLE} (passage_id, paper_id, space_id, section, original_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"{clone_run_id}:{row['passage_id']}",
                target_paper_id,
                target_space_id,
                row["section"],
                row["original_text"],
            ),
        )

    conn.commit()
    return clone_run_id
```

- [ ] **Step 5: Run persistence tests**

```bash
.venv/bin/pytest -q tests/test_pdf_persistence.py tests/test_db_migrations.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pdf_persistence.py tests/test_pdf_persistence.py
git commit -m "Persist parser diagnostics and parse reuse clones"
```

---

## Task 13: Parse Worker Orchestration

**Files:**
- Create: `parse_worker.py`
- Create: `tests/test_parse_worker.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing worker tests**

Create `tests/test_parse_worker.py`:

```python
import sqlite3
from pathlib import Path

import pytest

from db import init_db
from parse_jobs import create_parse_job, get_parse_job
from parse_worker import LocalParseWorker
from pdf_backend_grobid import GrobidMetadata, GrobidParseResult
from pdf_models import ParseDocument, ParseElement, PdfQualityReport


def _conn(tmp_path: Path) -> sqlite3.Connection:
    return init_db(tmp_path / "test.db")


def _doc(backend: str, text: str = "method result " * 100) -> ParseDocument:
    method = "layout_model" if backend == "mineru" else "native_text"
    return ParseDocument(
        paper_id="paper-1",
        space_id="space-1",
        backend=backend,
        extraction_method=method,
        quality=PdfQualityReport(page_count=1, native_text_pages=1),
        elements=[
            ParseElement(
                id="e1",
                element_index=0,
                element_type="paragraph",
                text=text,
                page_number=1,
                extraction_method=method,
            )
        ],
    )


def test_worker_runs_grobid_and_primary_parser_then_persists(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, file_path, file_hash, parse_status)
        VALUES ('paper-1', 'space-1', ?, 'hash-1', 'pending')
        """,
        (str(pdf),),
    )
    conn.commit()
    job = create_parse_job(conn, paper_id="paper-1", space_id="space-1")

    worker = LocalParseWorker(
        conn_factory=lambda: conn,
        profiler=lambda path: PdfQualityReport(page_count=1, native_text_pages=1),
        body_parser=lambda path, paper_id, space_id, quality, plan: _doc(plan.primary_backend),
        grobid_parser=lambda path: GrobidParseResult(metadata=GrobidMetadata(title="Title")),
        embedder=lambda conn, parse_run_id: [],
    )

    result = worker.run_job(job["id"])

    updated_job = get_parse_job(conn, job["id"])
    paper = conn.execute("SELECT title, parse_status FROM papers WHERE id = 'paper-1'").fetchone()
    assert result.status == "completed"
    assert updated_job["status"] == "completed"
    assert paper["title"] == "Title"
    assert paper["parse_status"] == "parsed"


def test_worker_reruns_mineru_when_primary_quality_is_low(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, file_path, file_hash, parse_status)
        VALUES ('paper-1', 'space-1', ?, 'hash-1', 'pending')
        """,
        (str(pdf),),
    )
    conn.commit()
    job = create_parse_job(conn, paper_id="paper-1", space_id="space-1")
    calls: list[str] = []

    def body_parser(path, paper_id, space_id, quality, plan):
        calls.append(plan.primary_backend)
        if plan.primary_backend == "pymupdf4llm":
            return _doc("pymupdf4llm", text="short")
        return _doc("mineru", text="method result " * 100)

    worker = LocalParseWorker(
        conn_factory=lambda: conn,
        profiler=lambda path: PdfQualityReport(page_count=1, native_text_pages=1),
        body_parser=body_parser,
        grobid_parser=lambda path: None,
        embedder=lambda conn, parse_run_id: [],
    )

    result = worker.run_job(job["id"])

    assert result.status == "completed"
    assert calls == ["pymupdf4llm", "mineru"]


def test_worker_marks_raw_fallback_as_completed_with_warnings(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, file_path, file_hash, parse_status)
        VALUES ('paper-1', 'space-1', ?, 'hash-1', 'pending')
        """,
        (str(pdf),),
    )
    conn.commit()
    job = create_parse_job(conn, paper_id="paper-1", space_id="space-1")

    worker = LocalParseWorker(
        conn_factory=lambda: conn,
        profiler=lambda path: PdfQualityReport(page_count=1, native_text_pages=0, needs_ocr=True),
        body_parser=lambda path, paper_id, space_id, quality, plan: _doc("raw-pymupdf", text="raw text " * 100),
        grobid_parser=lambda path: None,
        embedder=lambda conn, parse_run_id: [],
    )

    result = worker.run_job(job["id"])

    assert result.status == "completed_with_warnings"
    assert "raw_text_only_fallback" in result.warnings
```

- [ ] **Step 2: Run worker tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_parse_worker.py
```

Expected: FAIL because `parse_worker.py` does not exist.

- [ ] **Step 3: Implement worker result and constructor**

Create `parse_worker.py`:

```python
"""In-process local PDF parse worker."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from db import get_connection
from parse_jobs import (
    ParseJobCancelled,
    append_job_warning,
    check_not_cancelled,
    get_parse_job,
    record_stage_timing,
    transition_parse_job,
)
from paper_metadata import apply_extracted_metadata
from pdf_backend_grobid import GrobidParseResult
from pdf_chunker import chunk_parse_document
from pdf_enrichment import enrich_document
from pdf_models import ParseDocument, PdfQualityReport
from pdf_parse_plan import ParsePlan, build_parse_plan
from pdf_persistence import embed_passages_for_parse_run, persist_parse_result
from pdf_quality_gate import evaluate_parse_quality
from pdf_router import PdfBackendRouter


class ParseWorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    job_id: str
    status: str
    parse_run_id: str | None = None
    passage_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
```

Add injectable type aliases:

```python
Profiler = Callable[[Path], PdfQualityReport]
BodyParser = Callable[[Path, str, str, PdfQualityReport, ParsePlan], ParseDocument]
GrobidParser = Callable[[Path], GrobidParseResult | None]
Embedder = Callable[[sqlite3.Connection, str], list[str]]
```

- [ ] **Step 4: Implement `LocalParseWorker.run_job()`**

Add:

```python
class LocalParseWorker:
    def __init__(
        self,
        *,
        conn_factory: Callable[[], sqlite3.Connection] = get_connection,
        profiler: Profiler | None = None,
        body_parser: BodyParser | None = None,
        grobid_parser: GrobidParser | None = None,
        embedder: Embedder = embed_passages_for_parse_run,
    ) -> None:
        self._conn_factory = conn_factory
        self._profiler = profiler or self._default_profiler
        self._body_parser = body_parser or self._default_body_parser
        self._grobid_parser = grobid_parser or self._default_grobid_parser
        self._embedder = embedder

    def run_job(self, job_id: str) -> ParseWorkerResult:
        conn = self._conn_factory()
        try:
            job = get_parse_job(conn, job_id)
            paper = self._load_paper(conn, job["paper_id"], job["space_id"])
            file_path = Path(paper["file_path"])
            if not file_path.exists():
                raise FileNotFoundError(str(file_path))

            transition_parse_job(conn, job_id, "profiling")
            self._set_paper_status(conn, paper["id"], "parsing")
            check_not_cancelled(conn, job_id)
            quality = self._timed(conn, job_id, "profiling", lambda: self._profiler(file_path))
            plan = build_parse_plan(quality)
            conn.execute(
                "UPDATE parse_jobs SET parse_plan_json = ? WHERE id = ?",
                (plan.model_dump_json(), job_id),
            )
            conn.commit()

            transition_parse_job(conn, job_id, "metadata")
            transition_parse_job(conn, job_id, "body_parsing")
            check_not_cancelled(conn, job_id)
            document, grobid_result = self._run_body_and_grobid(
                conn,
                job_id,
                file_path,
                paper["id"],
                paper["space_id"],
                quality,
                plan,
            )
            document = self._fallback_if_needed(conn, job_id, file_path, paper, quality, plan, document)

            transition_parse_job(conn, job_id, "enriching")
            check_not_cancelled(conn, job_id)
            document = self._timed(
                conn,
                job_id,
                "enriching",
                lambda: enrich_document(document, grobid_result=grobid_result),
            )
            document = self._attach_diagnostics(conn, job_id, document)

            transition_parse_job(conn, job_id, "indexing")
            check_not_cancelled(conn, job_id)
            passages = chunk_parse_document(document)
            if not passages:
                append_job_warning(conn, job_id, "no_passages_after_chunking")
                transition_parse_job(conn, job_id, "failed", error_message="no passages after chunking")
                self._set_paper_status(conn, paper["id"], "error")
                return ParseWorkerResult(job_id=job_id, status="failed", warnings=["no_passages_after_chunking"])

            parse_run_id = persist_parse_result(conn, paper["id"], paper["space_id"], document, passages)
            embedding_warnings = self._embedder(conn, parse_run_id)
            for warning in embedding_warnings:
                append_job_warning(conn, job_id, warning)

            warnings = [*document.quality.warnings, *embedding_warnings]
            final_status = self._final_status(document, warnings)
            transition_parse_job(conn, job_id, final_status)
            self._set_paper_status(conn, paper["id"], "parsed")
            return ParseWorkerResult(
                job_id=job_id,
                status=final_status,
                parse_run_id=parse_run_id,
                passage_count=len(passages),
                warnings=warnings,
            )
        except ParseJobCancelled:
            transition_parse_job(conn, job_id, "cancelled")
            return ParseWorkerResult(job_id=job_id, status="cancelled")
        except Exception as exc:
            conn.rollback()
            transition_parse_job(conn, job_id, "failed", error_message=str(exc))
            job = get_parse_job(conn, job_id)
            self._set_paper_status(conn, job["paper_id"], "error")
            return ParseWorkerResult(job_id=job_id, status="failed", warnings=[str(exc)])
```

- [ ] **Step 5: Implement worker helpers**

Add:

```python
    def _default_profiler(self, file_path: Path) -> PdfQualityReport:
        from pdf_profile import inspect_pdf

        return inspect_pdf(file_path)

    def _default_body_parser(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality: PdfQualityReport,
        plan: ParsePlan,
    ) -> ParseDocument:
        return PdfBackendRouter().parse_pdf(
            file_path,
            paper_id,
            space_id,
            quality,
            parse_plan=plan,
        )

    def _default_grobid_parser(self, file_path: Path) -> GrobidParseResult | None:
        from pdf_backend_grobid import get_configured_grobid_client

        client = get_configured_grobid_client()
        if client is None or not client.is_alive():
            return None
        try:
            return client.process_fulltext(file_path)
        finally:
            client.close()

    def _timed(self, conn: sqlite3.Connection, job_id: str, stage: str, fn):
        start = time.monotonic()
        try:
            return fn()
        finally:
            record_stage_timing(conn, job_id, stage, time.monotonic() - start)

    def _run_body_and_grobid(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality: PdfQualityReport,
        plan: ParsePlan,
    ) -> tuple[ParseDocument, GrobidParseResult | None]:
        body_start = time.monotonic()
        grobid_start = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as executor:
            grobid_future = executor.submit(self._grobid_parser, file_path)
            body_future = executor.submit(
                self._body_parser,
                file_path,
                paper_id,
                space_id,
                quality,
                plan,
            )

            document: ParseDocument | None = None
            grobid_result: GrobidParseResult | None = None
            pending = {body_future, grobid_future}
            while pending:
                done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                if not done:
                    continue

                if grobid_future in done:
                    try:
                        grobid_result = grobid_future.result()
                        if grobid_result is not None and grobid_result.metadata is not None:
                            apply_extracted_metadata(
                                conn,
                                paper_id=paper_id,
                                space_id=space_id,
                                metadata=grobid_result.metadata,
                            )
                    except Exception as exc:
                        append_job_warning(conn, job_id, f"grobid_failed:{exc}")
                    record_stage_timing(conn, job_id, "grobid", time.monotonic() - grobid_start)

                if body_future in done:
                    document = body_future.result()
                    record_stage_timing(conn, job_id, "body_parsing", time.monotonic() - body_start)

        if document is None:
            raise RuntimeError("body parser completed without a document")
        return document, grobid_result
```

Add fallback:

```python
    def _fallback_if_needed(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        file_path: Path,
        paper,
        quality: PdfQualityReport,
        plan: ParsePlan,
        document: ParseDocument,
    ) -> ParseDocument:
        decision = evaluate_parse_quality(document)
        if not decision.low_quality:
            return document

        append_job_warning(conn, job_id, f"low_quality:{','.join(decision.flags)}")
        if document.backend != "mineru" and plan.fallback_backend == "mineru":
            check_not_cancelled(conn, job_id)
            mineru_plan = plan.model_copy(update={"primary_backend": "mineru", "fallback_backend": "raw-pymupdf"})
            mineru_document = self._timed(
                conn,
                job_id,
                "mineru_fallback",
                lambda: self._body_parser(file_path, paper["id"], paper["space_id"], quality, mineru_plan),
            )
            mineru_decision = evaluate_parse_quality(mineru_document)
            if not mineru_decision.low_quality:
                return mineru_document
            append_job_warning(conn, job_id, f"mineru_low_quality:{','.join(mineru_decision.flags)}")

        if document.backend == "raw-pymupdf":
            return document

        raw_plan = plan.model_copy(update={"primary_backend": "raw-pymupdf", "fallback_backend": "raw-pymupdf"})
        return self._timed(
            conn,
            job_id,
            "raw_fallback",
            lambda: self._body_parser(file_path, paper["id"], paper["space_id"], quality, raw_plan),
        )

    def _final_status(self, document: ParseDocument, warnings: list[str]) -> str:
        if document.backend == "raw-pymupdf" or "raw_text_only_fallback" in warnings:
            return "completed_with_warnings"
        if "review_needed" in document.metadata.get("review_flags", []):
            return "review_needed"
        return "completed"
```

Add diagnostics:

```python
    def _attach_diagnostics(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        document: ParseDocument,
    ) -> ParseDocument:
        job = get_parse_job(conn, job_id)
        metadata = dict(document.metadata)
        metadata["parse_plan"] = json.loads(job["parse_plan_json"])
        metadata["stage_timings"] = json.loads(job["stage_timings_json"])
        metadata["review_flags"] = json.loads(job["review_flags_json"])
        metadata["parser_versions"] = self._parser_versions(document)
        return document.model_copy(update={"metadata": metadata})

    def _parser_versions(self, document: ParseDocument) -> dict[str, str]:
        versions: dict[str, str] = {"backend": document.backend}
        try:
            from importlib.metadata import version

            versions["pymupdf"] = version("PyMuPDF")
        except Exception:
            versions["pymupdf"] = "unavailable"
        try:
            from importlib.metadata import version

            versions["pymupdf4llm"] = version("pymupdf4llm")
        except Exception:
            versions["pymupdf4llm"] = "unavailable"
        try:
            from importlib.metadata import version

            versions["mineru"] = version("mineru")
        except Exception:
            try:
                from importlib.metadata import version

                versions["mineru"] = version("magic-pdf")
            except Exception:
                versions["mineru"] = "unavailable"
        versions["grobid"] = str(document.metadata.get("grobid_version", "unknown"))
        return versions
```

Add DB helpers:

```python
    def _load_paper(self, conn: sqlite3.Connection, paper_id: str, space_id: str):
        row = conn.execute(
            "SELECT * FROM papers WHERE id = ? AND space_id = ?",
            (paper_id, space_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"paper not found: {paper_id}")
        return row

    def _set_paper_status(self, conn: sqlite3.Connection, paper_id: str, status: str) -> None:
        conn.execute("UPDATE papers SET parse_status = ? WHERE id = ?", (status, paper_id))
        conn.commit()
```

Add `parse_worker` to `pyproject.toml`.

- [ ] **Step 6: Run worker tests**

```bash
.venv/bin/pytest -q tests/test_parse_worker.py tests/test_parse_jobs.py tests/test_pdf_quality_gate.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add parse_worker.py tests/test_parse_worker.py pyproject.toml
git commit -m "Add local parse worker"
```

---

## Task 14: Upload, Job API, Cancellation, And Parse Reuse

**Files:**
- Modify: `routes_papers.py`
- Modify: `parser.py`
- Create or modify: `tests/test_routes_papers.py`

- [ ] **Step 1: Write failing route tests**

Add tests in `tests/test_routes_papers.py` that assert:

```python
@pytest.mark.asyncio
async def test_upload_creates_pending_parse_job(client: AsyncClient) -> None:
    await _create_and_activate_space(client)

    response = await client.post(
        "/api/papers/upload",
        files={"file": ("paper.pdf", _make_minimal_pdf(), "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["parse_status"] == "pending"
    assert body["parse_job"]["status"] == "pending"
    assert body["parse_job"]["paper_id"] == body["id"]


@pytest.mark.asyncio
async def test_get_parse_job_status(client: AsyncClient) -> None:
    await _create_and_activate_space(client)
    upload = await client.post(
        "/api/papers/upload",
        files={"file": ("paper.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = upload.json()["id"]
    job_id = upload.json()["parse_job"]["id"]

    response = await client.get(f"/api/papers/{paper_id}/parse-jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["id"] == job_id


@pytest.mark.asyncio
async def test_cancel_parse_job_sets_cancelled(client: AsyncClient) -> None:
    await _create_and_activate_space(client)
    upload = await client.post(
        "/api/papers/upload",
        files={"file": ("paper.pdf", _make_minimal_pdf(), "application/pdf")},
    )
    paper_id = upload.json()["id"]
    job_id = upload.json()["parse_job"]["id"]

    response = await client.post(f"/api/papers/{paper_id}/parse-jobs/{job_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
```

- [ ] **Step 2: Run route tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_routes_papers.py
```

Expected: FAIL because upload does not create jobs and job endpoints do not exist.

- [ ] **Step 3: Export worker wrappers in `parser.py`**

Add lazy wrappers:

```python
def run_parse_job(job_id: str) -> Any:
    from parse_worker import LocalParseWorker

    return LocalParseWorker().run_job(job_id)
```

Add `run_parse_job` to `__all__`.

- [ ] **Step 4: Create parse jobs on upload**

In `routes_papers.py`, import:

```python
from parse_jobs import cancel_parse_job, create_parse_job, get_parse_job
from parser import run_parse_job
from pdf_persistence import clone_parse_result
```

After inserting the paper row:

```python
job = create_parse_job(conn, paper_id=paper_id, space_id=space_id)
result = _paper_row_to_dict(row)
result["parse_job"] = dict(job)
```

Keep same-space duplicate behavior as HTTP 409.

- [ ] **Step 5: Add job status and cancellation endpoints**

Add:

```python
@router.get("/{paper_id}/parse-jobs/{job_id}")
async def get_parse_job_status(paper_id: str, job_id: str) -> dict[str, Any]:
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        job = get_parse_job(conn, job_id)
        if job["paper_id"] != paper_id or job["space_id"] != space_id:
            raise HTTPException(status_code=404, detail="Parse job not found")
        return dict(job)
    finally:
        conn.close()


@router.post("/{paper_id}/parse-jobs/{job_id}/cancel")
async def cancel_parse_job_route(paper_id: str, job_id: str) -> dict[str, Any]:
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        job = get_parse_job(conn, job_id)
        if job["paper_id"] != paper_id or job["space_id"] != space_id:
            raise HTTPException(status_code=404, detail="Parse job not found")
        cancel_parse_job(conn, job_id)
        return dict(get_parse_job(conn, job_id))
    finally:
        conn.close()
```

- [ ] **Step 6: Rework `POST /{paper_id}/parse` to dispatch a job**

For V1, keep a synchronous compatibility endpoint that creates or reuses a pending job and runs it immediately in-process:

```python
@router.post("/{paper_id}/parse")
async def parse_paper(paper_id: str) -> dict[str, Any]:
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        row = conn.execute(
            "SELECT * FROM papers WHERE id = ? AND space_id = ?",
            (paper_id, space_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Paper not found")
        existing_job = conn.execute(
            """
            SELECT * FROM parse_jobs
            WHERE paper_id = ? AND space_id = ?
              AND status IN ('pending', 'failed', 'cancelled')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (paper_id, space_id),
        ).fetchone()
        job = existing_job or create_parse_job(conn, paper_id=paper_id, space_id=space_id)
    finally:
        conn.close()

    result = run_parse_job(job["id"])
    conn = get_connection()
    try:
        parse_run = (
            conn.execute(
                "SELECT backend, quality_score FROM parse_runs WHERE id = ?",
                (result.parse_run_id,),
            ).fetchone()
            if result.parse_run_id is not None
            else None
        )
    finally:
        conn.close()
    api_status = (
        "parsed"
        if result.status in {"completed", "completed_with_warnings", "review_needed"}
        else "error"
    )
    return {
        "status": api_status,
        "paper_id": paper_id,
        "parse_job_id": result.job_id,
        "passage_count": result.passage_count,
        "parse_run_id": result.parse_run_id,
        "backend": None if parse_run is None else parse_run["backend"],
        "quality_score": None if parse_run is None else parse_run["quality_score"],
        "warnings": result.warnings,
    }
```

The endpoint remains compatible for existing UI flows while the internal unit of work becomes `parse_jobs`.

- [ ] **Step 7: Add content-hash parse reuse helper**

In `routes_papers.py`, before creating a parse job on upload, query:

```python
def _find_reusable_parse_run(conn: Any, file_hash: str) -> str | None:
    row = conn.execute(
        """
        SELECT pr.id
        FROM papers p
        JOIN parse_runs pr ON pr.paper_id = p.id AND pr.space_id = p.space_id
        WHERE p.file_hash = ?
          AND pr.status IN ('completed', 'completed_with_warnings')
        ORDER BY pr.completed_at DESC, pr.started_at DESC
        LIMIT 1
        """,
        (file_hash,),
    ).fetchone()
    return None if row is None else str(row["id"])
```

When reusable output exists, call `clone_parse_result()` for the new paper, set `papers.parse_status = 'parsed'`, and return:

```python
result["parse_job"] = None
result["reused_parse_run_id"] = cloned_run_id
```

- [ ] **Step 8: Run route tests**

```bash
.venv/bin/pytest -q tests/test_routes_papers.py tests/test_parse_worker.py tests/test_pdf_persistence.py
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add routes_papers.py parser.py tests/test_routes_papers.py
git commit -m "Wire paper parsing through parse jobs"
```

---

## Task 15: Reference Paper Evaluation

**Files:**
- Create: `scripts/eval_reference_papers.py`
- Create: `tests/test_eval_reference_papers.py`

- [ ] **Step 1: Write failing eval tests**

Create `tests/test_eval_reference_papers.py`:

```python
import json
from pathlib import Path

from scripts.eval_reference_papers import discover_reference_papers, write_report


def test_discover_reference_papers_returns_sorted_pdfs(tmp_path: Path) -> None:
    (tmp_path / "b.pdf").write_bytes(b"%PDF")
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "notes.txt").write_text("skip")

    assert [path.name for path in discover_reference_papers(tmp_path)] == ["a.pdf", "b.pdf"]


def test_write_report_outputs_json(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    write_report(
        output,
        [
            {
                "file": "a.pdf",
                "selected_backend": "pymupdf4llm",
                "status": "completed",
                "warnings": [],
            }
        ],
    )

    data = json.loads(output.read_text())
    assert data["papers"][0]["file"] == "a.pdf"
    assert data["paper_count"] == 1
```

- [ ] **Step 2: Run tests and verify failure**

```bash
.venv/bin/pytest -q tests/test_eval_reference_papers.py
```

Expected: FAIL because `scripts/eval_reference_papers.py` does not exist.

- [ ] **Step 3: Implement evaluation helpers and CLI**

Create `scripts/eval_reference_papers.py`:

```python
"""Evaluate the local parser on untracked reference PDFs."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from db import init_db
from parse_jobs import create_parse_job
from parse_worker import LocalParseWorker


def discover_reference_papers(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.iterdir() if path.suffix.lower() == ".pdf")


def write_report(output: Path, rows: list[dict[str, Any]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"paper_count": len(rows), "papers": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def evaluate_file(pdf_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmpdir:
        conn = init_db(Path(tmpdir) / "eval.db")
        space_id = "eval-space"
        paper_id = str(uuid.uuid4())
        conn.execute("INSERT INTO spaces (id, name) VALUES (?, ?)", (space_id, "Evaluation"))
        conn.execute(
            """
            INSERT INTO papers (id, space_id, file_path, file_hash, parse_status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (paper_id, space_id, str(pdf_path), pdf_path.name),
        )
        conn.commit()
        job = create_parse_job(conn, paper_id=paper_id, space_id=space_id)
        start = time.monotonic()
        result = LocalParseWorker(conn_factory=lambda: conn).run_job(job["id"])
        elapsed = round(time.monotonic() - start, 4)
        run = conn.execute(
            "SELECT * FROM parse_runs WHERE id = ?",
            (result.parse_run_id,),
        ).fetchone() if result.parse_run_id else None
        return {
            "file": pdf_path.name,
            "status": result.status,
            "selected_backend": None if run is None else run["backend"],
            "passage_count": result.passage_count,
            "quality_score": None if run is None else run["quality_score"],
            "warnings": result.warnings,
            "elapsed_seconds": elapsed,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="reference_paper")
    parser.add_argument("--output", default="/tmp/paper-parser-eval/report.json")
    args = parser.parse_args()

    rows = [evaluate_file(path) for path in discover_reference_papers(Path(args.root))]
    write_report(Path(args.output), rows)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run eval tests**

```bash
.venv/bin/pytest -q tests/test_eval_reference_papers.py
```

Expected: PASS.

- [ ] **Step 5: Run the local reference corpus**

```bash
.venv/bin/python scripts/eval_reference_papers.py --root reference_paper --output /tmp/paper-parser-eval/report.json
```

Expected: command exits 0 and `/tmp/paper-parser-eval/report.json` includes one row per local PDF. Papers that require unavailable MinerU should show a warning or fallback status rather than crash the whole run.

- [ ] **Step 6: Commit**

```bash
git add scripts/eval_reference_papers.py tests/test_eval_reference_papers.py
git commit -m "Add reference paper parser evaluation"
```

---

## Task 16: Documentation And Full Verification

**Files:**
- Modify: `docs/pdf-ingestion.md`

- [ ] **Step 1: Update parser architecture documentation**

Add a section to `docs/pdf-ingestion.md`:

```markdown
## Local Paper Parser Pipeline

The parser is local-only and paper-specific:

1. Upload validates, hashes, stores the PDF, and creates a `parse_jobs` row.
2. The worker profiles with PyMuPDF and builds a `ParsePlan`.
3. GROBID metadata/reference extraction runs with a 60 second timeout and one retry.
4. The body parser uses PyMuPDF4LLM for normal papers and MinerU for scanned or complex papers.
5. Parser output normalizes to `ParseDocument`.
6. The quality gate reruns MinerU for low-quality PyMuPDF4LLM output.
7. If structured parsing remains poor, raw PyMuPDF text is persisted and the job is marked `completed_with_warnings`.
8. Academic enrichment stores references, binds captions, and preserves formula blocks. In-text citation linking is disabled in V1.
9. `pdf_chunker` and `pdf_persistence` handle chunking, FTS, and optional embeddings.

`reference_paper/` is a local-only evaluation corpus and is ignored by git.
```

- [ ] **Step 2: Run focused parser tests**

```bash
.venv/bin/pytest -q tests/test_pdf_parse_plan.py tests/test_pdf_quality_gate.py tests/test_pdf_backend_raw.py tests/test_pdf_backend_mineru.py tests/test_pdf_router.py tests/test_pdf_backend_grobid.py tests/test_pdf_enrichment.py tests/test_parse_jobs.py tests/test_parse_worker.py tests/test_paper_metadata.py
```

Expected: PASS.

- [ ] **Step 3: Run persistence and route tests**

```bash
.venv/bin/pytest -q tests/test_db_migrations.py tests/test_pdf_persistence.py tests/test_routes_papers.py tests/test_eval_reference_papers.py
```

Expected: PASS.

- [ ] **Step 4: Run the existing parser regression suite**

```bash
.venv/bin/pytest -q tests/test_parser.py tests/test_pdf_chunker.py tests/eval/test_pdf_parse_quality.py
```

Expected: PASS.

- [ ] **Step 5: Run the full test suite**

```bash
.venv/bin/pytest -q tests
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/pdf-ingestion.md
git commit -m "Document local paper parser pipeline"
```

---

## Execution Notes

- Implement tasks in order. Tasks 2 through 5 establish contracts used by later worker and route tasks.
- Do not delete `pdf_backend_llamaparse.py` during this plan. The required behavioral change is that the router no longer imports or uses it.
- Do not commit files under `reference_paper/`.
- If real MinerU is not installed locally, unit tests still pass through the injected runner in `MinerUBackend`; the reference corpus run should report fallback behavior instead of blocking implementation.
