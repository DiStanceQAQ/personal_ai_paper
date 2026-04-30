from __future__ import annotations

import sqlite3
from pathlib import Path

from paper_engine.pdf.jobs import queue_parse_run
from paper_engine.retrieval.embedding_jobs import (
    claim_next_embedding_run,
    complete_embedding_run,
    fail_embedding_run,
    queue_embedding_run,
    recover_stale_embedding_runs,
)
from paper_engine.storage.database import init_db


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(database_path=tmp_path / "test.db")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, file_path, file_hash, parse_status)
        VALUES ('paper-1', 'space-1', ?, 'hash', 'parsed')
        """,
        (str(pdf),),
    )
    parse_run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={},
    )
    conn.execute(
        """
        UPDATE parse_runs
        SET status = 'completed', completed_at = datetime('now')
        WHERE id = ?
        """,
        (parse_run_id,),
    )
    conn.commit()
    return conn


def test_queue_embedding_run_reuses_active_run(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    parse_run_id = conn.execute("SELECT id FROM parse_runs").fetchone()["id"]

    first = queue_embedding_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parse_run_id=parse_run_id,
    )
    second = queue_embedding_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parse_run_id=parse_run_id,
    )

    assert second == first
    paper = conn.execute(
        "SELECT embedding_status FROM papers WHERE id = 'paper-1'"
    ).fetchone()
    assert paper["embedding_status"] == "pending"
    assert conn.execute("SELECT COUNT(*) FROM embedding_runs").fetchone()[0] == 1


def test_claim_and_complete_embedding_run_updates_paper_status(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    parse_run_id = conn.execute("SELECT id FROM parse_runs").fetchone()["id"]
    embedding_run_id = queue_embedding_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parse_run_id=parse_run_id,
    )

    job = claim_next_embedding_run(conn, worker_id="worker-1")
    assert job is not None
    assert job.id == embedding_run_id
    assert job.attempt_count == 1

    complete_embedding_run(
        conn,
        embedding_run_id,
        paper_id="paper-1",
        space_id="space-1",
        worker_id="worker-1",
        passage_count=2,
        embedded_count=1,
        reused_count=1,
        skipped_count=0,
        batch_count=1,
        warnings=[],
        metadata={"batch_size": 16},
    )

    run = conn.execute("SELECT * FROM embedding_runs WHERE id = ?", (embedding_run_id,)).fetchone()
    paper = conn.execute(
        "SELECT embedding_status FROM papers WHERE id = 'paper-1'"
    ).fetchone()
    assert run["status"] == "completed"
    assert run["embedded_count"] == 1
    assert run["reused_count"] == 1
    assert paper["embedding_status"] == "completed"


def test_fail_embedding_run_does_not_change_parse_status(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    parse_run_id = conn.execute("SELECT id FROM parse_runs").fetchone()["id"]
    embedding_run_id = queue_embedding_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parse_run_id=parse_run_id,
    )
    assert claim_next_embedding_run(conn, worker_id="worker-1") is not None

    fail_embedding_run(
        conn,
        embedding_run_id,
        paper_id="paper-1",
        space_id="space-1",
        worker_id="worker-1",
        error="boom",
        warnings=["boom"],
    )

    paper = conn.execute(
        """
        SELECT parse_status, embedding_status
        FROM papers
        WHERE id = 'paper-1'
        """
    ).fetchone()
    run = conn.execute("SELECT status, last_error FROM embedding_runs").fetchone()
    assert paper["parse_status"] == "parsed"
    assert paper["embedding_status"] == "failed"
    assert run["status"] == "failed"
    assert run["last_error"] == "boom"


def test_recover_stale_embedding_run_requeues_until_max_attempts(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    parse_run_id = conn.execute("SELECT id FROM parse_runs").fetchone()["id"]
    embedding_run_id = queue_embedding_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parse_run_id=parse_run_id,
    )
    assert claim_next_embedding_run(conn, worker_id="worker-1") is not None
    conn.execute(
        """
        UPDATE embedding_runs
        SET heartbeat_at = datetime('now', '-20 minutes')
        WHERE id = ?
        """,
        (embedding_run_id,),
    )
    conn.commit()

    recovered = recover_stale_embedding_runs(
        conn,
        stale_after_seconds=60,
        max_attempts=3,
    )

    run = conn.execute("SELECT status, worker_id, last_error FROM embedding_runs").fetchone()
    paper = conn.execute(
        "SELECT embedding_status FROM papers WHERE id = 'paper-1'"
    ).fetchone()
    assert recovered == 1
    assert run["status"] == "queued"
    assert run["worker_id"] is None
    assert run["last_error"] == "worker_heartbeat_timeout"
    assert paper["embedding_status"] == "pending"
