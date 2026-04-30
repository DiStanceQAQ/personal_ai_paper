"""Durable embedding run job helpers."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from paper_engine.retrieval.embeddings import get_embedding_config


@dataclass(frozen=True)
class EmbeddingRunJob:
    """A claimed embedding run ready for worker execution."""

    id: str
    paper_id: str
    space_id: str
    parse_run_id: str
    provider: str
    model: str
    attempt_count: int


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _embedding_identity(conn: sqlite3.Connection) -> tuple[str, str]:
    config = get_embedding_config(conn)
    provider = config.provider.strip() or "local"
    model = config.model.strip()
    return provider, model


def queue_embedding_run(
    conn: sqlite3.Connection,
    *,
    paper_id: str,
    space_id: str,
    parse_run_id: str,
    commit: bool = True,
) -> str:
    """Create or reuse a queued/running embedding run for a parse run."""
    existing = conn.execute(
        """
        SELECT id
        FROM embedding_runs
        WHERE paper_id = ?
          AND space_id = ?
          AND parse_run_id = ?
          AND status IN ('queued', 'running')
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        (paper_id, space_id, parse_run_id),
    ).fetchone()
    if existing is not None:
        return str(existing["id"])

    provider, model = _embedding_identity(conn)
    embedding_run_id = f"embedding-run-{uuid.uuid4()}"
    conn.execute(
        """
        INSERT INTO embedding_runs (
            id, paper_id, space_id, parse_run_id, status, provider, model,
            warnings_json, metadata_json
        )
        VALUES (?, ?, ?, ?, 'queued', ?, ?, '[]', '{}')
        """,
        (embedding_run_id, paper_id, space_id, parse_run_id, provider, model),
    )
    conn.execute(
        """
        UPDATE papers
        SET embedding_status = 'pending'
        WHERE id = ? AND space_id = ?
        """,
        (paper_id, space_id),
    )
    if commit:
        conn.commit()
    return embedding_run_id


def claim_next_embedding_run(
    conn: sqlite3.Connection,
    *,
    worker_id: str,
) -> EmbeddingRunJob | None:
    """Atomically claim one queued embedding run without same-paper overlap."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        candidates = conn.execute(
            """
            SELECT id, paper_id, space_id, parse_run_id, provider, model,
                   attempt_count
            FROM embedding_runs
            WHERE status = 'queued'
            ORDER BY started_at, id
            LIMIT 20
            """
        ).fetchall()
        for row in candidates:
            result = conn.execute(
                """
                UPDATE embedding_runs
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
                    FROM embedding_runs active
                    WHERE active.paper_id = embedding_runs.paper_id
                      AND active.status = 'running'
                  )
                """,
                (worker_id, row["id"]),
            )
            if result.rowcount == 1:
                conn.execute(
                    """
                    UPDATE papers
                    SET embedding_status = 'running'
                    WHERE id = ? AND space_id = ?
                    """,
                    (row["paper_id"], row["space_id"]),
                )
                conn.commit()
                return EmbeddingRunJob(
                    id=str(row["id"]),
                    paper_id=str(row["paper_id"]),
                    space_id=str(row["space_id"]),
                    parse_run_id=str(row["parse_run_id"]),
                    provider=str(row["provider"]),
                    model=str(row["model"]),
                    attempt_count=int(row["attempt_count"]) + 1,
                )
        conn.commit()
        return None
    except Exception:
        conn.rollback()
        raise


def heartbeat_embedding_run_for_worker(
    conn: sqlite3.Connection,
    embedding_run_id: str,
    *,
    worker_id: str,
) -> None:
    """Refresh heartbeat only if the current worker still owns the run."""
    conn.execute(
        """
        UPDATE embedding_runs
        SET heartbeat_at = datetime('now')
        WHERE id = ?
          AND status = 'running'
          AND worker_id = ?
        """,
        (embedding_run_id, worker_id),
    )
    conn.commit()


def complete_embedding_run(
    conn: sqlite3.Connection,
    embedding_run_id: str,
    *,
    paper_id: str,
    space_id: str,
    worker_id: str | None = None,
    passage_count: int,
    embedded_count: int,
    reused_count: int,
    skipped_count: int,
    batch_count: int,
    warnings: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Mark an embedding run and paper as successfully embedded."""
    worker_clause = " AND worker_id = ?" if worker_id is not None else ""
    params: list[Any] = [
        passage_count,
        embedded_count,
        reused_count,
        skipped_count,
        batch_count,
        _json(warnings or []),
        _json(metadata or {}),
        embedding_run_id,
    ]
    if worker_id is not None:
        params.append(worker_id)
    result = conn.execute(
        """
        UPDATE embedding_runs
        SET status = 'completed',
            completed_at = datetime('now'),
            heartbeat_at = datetime('now'),
            worker_id = NULL,
            passage_count = ?,
            embedded_count = ?,
            reused_count = ?,
            skipped_count = ?,
            batch_count = ?,
            warnings_json = ?,
            metadata_json = ?
        WHERE id = ?
          AND status = 'running'
        """
        + worker_clause,
        params,
    )
    if result.rowcount != 1:
        raise RuntimeError(
            f"embedding run {embedding_run_id} is no longer running for this worker"
        )
    next_status = "completed" if passage_count > 0 else "skipped"
    conn.execute(
        """
        UPDATE papers
        SET embedding_status = ?
        WHERE id = ? AND space_id = ?
        """,
        (next_status, paper_id, space_id),
    )
    conn.commit()


def fail_embedding_run(
    conn: sqlite3.Connection,
    embedding_run_id: str,
    *,
    paper_id: str,
    space_id: str,
    worker_id: str | None = None,
    error: str,
    warnings: list[str] | None = None,
) -> None:
    """Mark an embedding run failed without changing parse status."""
    worker_clause = " AND worker_id = ?" if worker_id is not None else ""
    params: list[Any] = [
        error,
        _json(warnings or [error]),
        embedding_run_id,
    ]
    if worker_id is not None:
        params.append(worker_id)
    result = conn.execute(
        """
        UPDATE embedding_runs
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
    if result.rowcount == 1:
        conn.execute(
            """
            UPDATE papers
            SET embedding_status = 'failed'
            WHERE id = ? AND space_id = ?
            """,
            (paper_id, space_id),
        )
    conn.commit()


def recover_stale_embedding_runs(
    conn: sqlite3.Connection,
    *,
    stale_after_seconds: int,
    max_attempts: int,
) -> int:
    """Requeue or fail running embedding jobs whose heartbeat is stale."""
    cutoff = f"-{stale_after_seconds} seconds"
    stale_rows = conn.execute(
        """
        SELECT id, paper_id, space_id, attempt_count
        FROM embedding_runs
        WHERE status = 'running'
          AND (
            heartbeat_at IS NULL
            OR heartbeat_at < datetime('now', ?)
          )
        """,
        (cutoff,),
    ).fetchall()
    failed_rows = [
        row for row in stale_rows if int(row["attempt_count"]) >= max_attempts
    ]
    requeued_rows = [
        row for row in stale_rows if int(row["attempt_count"]) < max_attempts
    ]

    failed = _update_stale_embedding_runs(
        conn,
        [str(row["id"]) for row in failed_rows],
        status="failed",
        cutoff=cutoff,
        completed=True,
    )
    requeued = _update_stale_embedding_runs(
        conn,
        [str(row["id"]) for row in requeued_rows],
        status="queued",
        cutoff=cutoff,
        completed=False,
    )
    for row in failed_rows:
        _update_paper_embedding_status(
            conn,
            str(row["paper_id"]),
            str(row["space_id"]),
            fallback_status="failed",
        )
    for row in requeued_rows:
        conn.execute(
            """
            UPDATE papers
            SET embedding_status = 'pending'
            WHERE id = ? AND space_id = ?
            """,
            (row["paper_id"], row["space_id"]),
        )
    conn.commit()
    return int(failed + requeued)


def _update_stale_embedding_runs(
    conn: sqlite3.Connection,
    embedding_run_ids: list[str],
    *,
    status: str,
    cutoff: str,
    completed: bool,
) -> int:
    if not embedding_run_ids:
        return 0

    placeholders = ",".join("?" for _ in embedding_run_ids)
    completed_update = ", completed_at = datetime('now')" if completed else ""
    return int(
        conn.execute(
            f"""
            UPDATE embedding_runs
            SET status = ?,
                claimed_at = NULL,
                heartbeat_at = NULL,
                worker_id = NULL,
                last_error = 'worker_heartbeat_timeout'
                {completed_update}
            WHERE id IN ({placeholders})
              AND status = 'running'
              AND (
                heartbeat_at IS NULL
                OR heartbeat_at < datetime('now', ?)
              )
            """,
            (status, *embedding_run_ids, cutoff),
        ).rowcount
    )


def _update_paper_embedding_status(
    conn: sqlite3.Connection,
    paper_id: str,
    space_id: str,
    *,
    fallback_status: str,
) -> None:
    completed = conn.execute(
        """
        SELECT 1
        FROM embedding_runs
        WHERE paper_id = ?
          AND space_id = ?
          AND status = 'completed'
        LIMIT 1
        """,
        (paper_id, space_id),
    ).fetchone()
    next_status = "completed" if completed is not None else fallback_status
    conn.execute(
        """
        UPDATE papers
        SET embedding_status = ?
        WHERE id = ? AND space_id = ?
        """,
        (next_status, paper_id, space_id),
    )


__all__ = [
    "EmbeddingRunJob",
    "claim_next_embedding_run",
    "complete_embedding_run",
    "fail_embedding_run",
    "heartbeat_embedding_run_for_worker",
    "queue_embedding_run",
    "recover_stale_embedding_runs",
]
