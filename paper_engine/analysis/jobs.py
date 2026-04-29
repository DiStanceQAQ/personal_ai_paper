"""Durable AI analysis run job helpers."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnalysisRunJob:
    """A claimed analysis run ready for worker execution."""

    id: str
    paper_id: str
    space_id: str
    attempt_count: int


class AnalysisRunCancelled(RuntimeError):
    """Raised when a running analysis should stop because the run was cancelled."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_llm_identity(conn: sqlite3.Connection) -> tuple[str, str]:
    rows = conn.execute(
        "SELECT key, value FROM app_state WHERE key IN (?, ?)",
        ("llm_provider", "llm_model"),
    ).fetchall()
    config = {str(row["key"]): str(row["value"]) for row in rows}
    provider = config.get("llm_provider", "openai").strip() or "openai"
    model = config.get("llm_model", "gpt-4o").strip() or "gpt-4o"
    return provider, model


def queue_analysis_run(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    space_id: str,
    commit: bool = True,
) -> str:
    """Create or reuse a queued/running analysis run for a paper."""
    existing = conn.execute(
        """
        SELECT id
        FROM analysis_runs
        WHERE paper_id = ?
          AND space_id = ?
          AND status IN ('queued', 'running')
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        (paper_id, space_id),
    ).fetchone()
    if existing is not None:
        return str(existing["id"])

    provider, model = _load_llm_identity(conn)
    analysis_run_id = f"analysis-run-{uuid.uuid4()}"
    conn.execute(
        """
        INSERT INTO analysis_runs (
            id, paper_id, space_id, status, model, provider,
            extractor_version, warnings_json, diagnostics_json, metadata_json
        )
        VALUES (?, ?, ?, 'queued', ?, ?, 'analysis-v2', '[]', '{}', '{}')
        """,
        (analysis_run_id, paper_id, space_id, model, provider),
    )
    if commit:
        conn.commit()
    return analysis_run_id


def claim_next_analysis_run(
    conn: sqlite3.Connection,
    *,
    worker_id: str,
) -> AnalysisRunJob | None:
    """Atomically claim one queued analysis run without same-paper overlap."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        candidates = conn.execute(
            """
            SELECT id, paper_id, space_id, attempt_count
            FROM analysis_runs
            WHERE status = 'queued'
            ORDER BY started_at, id
            LIMIT 20
            """
        ).fetchall()
        for row in candidates:
            result = conn.execute(
                """
                UPDATE analysis_runs
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
                    FROM analysis_runs active
                    WHERE active.paper_id = analysis_runs.paper_id
                      AND active.status = 'running'
                  )
                """,
                (worker_id, row["id"]),
            )
            if result.rowcount == 1:
                conn.commit()
                return AnalysisRunJob(
                    id=str(row["id"]),
                    paper_id=str(row["paper_id"]),
                    space_id=str(row["space_id"]),
                    attempt_count=int(row["attempt_count"]) + 1,
                )
        conn.commit()
        return None
    except Exception:
        conn.rollback()
        raise


def heartbeat_analysis_run_for_worker(
    conn: sqlite3.Connection,
    analysis_run_id: str,
    *,
    worker_id: str,
) -> None:
    """Refresh heartbeat only if the current worker still owns the run."""
    conn.execute(
        """
        UPDATE analysis_runs
        SET heartbeat_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
          AND worker_id = ?
        """,
        (analysis_run_id, worker_id),
    )
    conn.commit()


def is_analysis_run_cancelled(
    conn: sqlite3.Connection,
    analysis_run_id: str,
) -> bool:
    """Return whether an analysis run has been cancelled."""
    row = conn.execute(
        "SELECT status FROM analysis_runs WHERE id = ?",
        (analysis_run_id,),
    ).fetchone()
    return row is not None and row["status"] == "cancelled"


def cancel_analysis_run(
    conn: sqlite3.Connection,
    *,
    analysis_run_id: str,
    paper_id: str,
    space_id: str,
    commit: bool = True,
) -> sqlite3.Row | None:
    """Cancel a queued or running analysis run and return its latest row."""
    row = conn.execute(
        """
        SELECT *
        FROM analysis_runs
        WHERE id = ?
          AND paper_id = ?
          AND space_id = ?
        """,
        (analysis_run_id, paper_id, space_id),
    ).fetchone()
    if row is None:
        return None

    if row["status"] == "cancelled":
        return row

    conn.execute(
        """
        UPDATE analysis_runs
        SET status = 'cancelled',
            completed_at = datetime('now'),
            heartbeat_at = datetime('now'),
            worker_id = NULL,
            last_error = 'cancelled_by_user'
        WHERE id = ?
          AND paper_id = ?
          AND space_id = ?
          AND status IN ('queued', 'running')
        """,
        (analysis_run_id, paper_id, space_id),
    )
    if commit:
        conn.commit()

    return conn.execute(
        """
        SELECT *
        FROM analysis_runs
        WHERE id = ?
          AND paper_id = ?
          AND space_id = ?
        """,
        (analysis_run_id, paper_id, space_id),
    ).fetchone()


def fail_analysis_run(
    conn: sqlite3.Connection,
    analysis_run_id: str,
    *,
    worker_id: str | None = None,
    error: str,
    warnings: list[str] | None = None,
) -> None:
    """Mark a running analysis run failed without changing paper parse status."""
    worker_clause = " AND worker_id = ?" if worker_id is not None else ""
    params: list[Any] = [
        error,
        _json(warnings or [error]),
        analysis_run_id,
    ]
    if worker_id is not None:
        params.append(worker_id)
    conn.execute(
        """
        UPDATE analysis_runs
        SET status = 'failed',
            completed_at = datetime('now'),
            heartbeat_at = datetime('now'),
            worker_id = NULL,
            last_error = ?,
            warnings_json = ?
        WHERE id = ?
          AND status = 'running'
        """
        + worker_clause,
        params,
    )
    conn.commit()


def recover_stale_analysis_runs(
    conn: sqlite3.Connection,
    *,
    stale_after_seconds: int,
    max_attempts: int,
) -> int:
    """Requeue or fail running analysis jobs whose heartbeat is stale."""
    cutoff = f"-{stale_after_seconds} seconds"
    stale_rows = conn.execute(
        """
        SELECT id, attempt_count
        FROM analysis_runs
        WHERE status = 'running'
          AND (
            heartbeat_at IS NULL
            OR heartbeat_at < datetime('now', ?)
          )
        """,
        (cutoff,),
    ).fetchall()
    failed_ids = [
        str(row["id"])
        for row in stale_rows
        if int(row["attempt_count"]) >= max_attempts
    ]
    requeued_ids = [
        str(row["id"])
        for row in stale_rows
        if int(row["attempt_count"]) < max_attempts
    ]
    failed = _update_stale_analysis_runs(
        conn,
        failed_ids,
        status="failed",
        cutoff=cutoff,
        completed=True,
    )
    requeued = _update_stale_analysis_runs(
        conn,
        requeued_ids,
        status="queued",
        cutoff=cutoff,
        completed=False,
    )
    conn.commit()
    return int(failed + requeued)


def _update_stale_analysis_runs(
    conn: sqlite3.Connection,
    analysis_run_ids: list[str],
    *,
    status: str,
    cutoff: str,
    completed: bool,
) -> int:
    if not analysis_run_ids:
        return 0

    placeholders = ",".join("?" for _ in analysis_run_ids)
    completed_update = ", completed_at = datetime('now')" if completed else ""
    return int(
        conn.execute(
            f"""
            UPDATE analysis_runs
            SET status = ?,
                claimed_at = NULL,
                heartbeat_at = NULL,
                worker_id = NULL,
                last_error = 'analysis_worker_heartbeat_timeout'
                {completed_update}
            WHERE id IN ({placeholders})
              AND status = 'running'
              AND (
                heartbeat_at IS NULL
                OR heartbeat_at < datetime('now', ?)
              )
            """,
            (status, *analysis_run_ids, cutoff),
        ).rowcount
    )


__all__ = [
    "AnalysisRunJob",
    "AnalysisRunCancelled",
    "cancel_analysis_run",
    "claim_next_analysis_run",
    "fail_analysis_run",
    "heartbeat_analysis_run_for_worker",
    "is_analysis_run_cancelled",
    "queue_analysis_run",
    "recover_stale_analysis_runs",
]
