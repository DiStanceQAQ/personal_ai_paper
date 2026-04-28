"""Durable parse run job helpers."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParseRunJob:
    """A claimed parse run ready for worker execution."""

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
    commit: bool = True,
) -> str:
    """Create a queued parse run with a parser config snapshot."""
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
    if commit:
        conn.commit()
    return parse_run_id


def claim_next_parse_run(
    conn: sqlite3.Connection,
    *,
    worker_id: str,
) -> ParseRunJob | None:
    """Atomically claim one queued parse run without same-paper overlap."""
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
                    """
                    UPDATE papers
                    SET parse_status = 'parsing'
                    WHERE id = ? AND space_id = ?
                    """,
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


def heartbeat_parse_run(conn: sqlite3.Connection, parse_run_id: str) -> None:
    """Refresh heartbeat for an active parse run."""
    conn.execute(
        """
        UPDATE parse_runs
        SET heartbeat_at = datetime('now')
        WHERE id = ? AND status = 'running'
        """,
        (parse_run_id,),
    )
    conn.commit()


def recover_stale_parse_runs(
    conn: sqlite3.Connection,
    *,
    stale_after_seconds: int,
    max_attempts: int,
) -> int:
    """Requeue or fail running parse jobs whose heartbeat is stale."""
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


def complete_parse_run(
    conn: sqlite3.Connection,
    parse_run_id: str,
    *,
    paper_id: str,
    warnings: list[str],
) -> None:
    """Mark a parse run and paper as successfully parsed."""
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


def fail_parse_run(
    conn: sqlite3.Connection,
    parse_run_id: str,
    *,
    paper_id: str,
    error: str,
    warnings: list[str],
) -> None:
    """Mark a parse run failed and update paper status when no parse exists."""
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
        """
        SELECT 1
        FROM parse_runs
        WHERE paper_id = ? AND status = 'completed'
        LIMIT 1
        """,
        (paper_id,),
    ).fetchone()
    next_status = "error" if completed is None else "parsed"
    conn.execute(
        "UPDATE papers SET parse_status = ? WHERE id = ?",
        (next_status, paper_id),
    )
    conn.commit()
