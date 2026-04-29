from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from paper_engine.pdf.jobs import (
    claim_next_parse_run,
    complete_parse_run,
    fail_parse_run,
    heartbeat_parse_run,
    heartbeat_parse_run_for_worker,
    queue_parse_run,
    recover_stale_parse_runs,
)
from paper_engine.storage.database import init_db


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(database_path=tmp_path / "test.db")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, file_path, file_hash)
        VALUES ('paper-1', 'space-1', '/tmp/paper.pdf', 'hash')
        """
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
        parser_config={
            "parser_backend": "mineru",
            "mineru_base_url": "http://mineru.test",
        },
    )

    row = conn.execute(
        "SELECT status, backend, config_json FROM parse_runs WHERE id = ?",
        (parse_run_id,),
    ).fetchone()
    assert row["status"] == "queued"
    assert row["backend"] == "mineru"
    assert json.loads(row["config_json"])["mineru_base_url"] == "http://mineru.test"


def test_claim_next_parse_run_prevents_two_running_for_same_paper(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    first = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )
    second = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )

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


def test_heartbeat_updates_running_parse_run(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None
    conn.execute(
        "UPDATE parse_runs SET heartbeat_at = datetime('now', '-5 minutes') WHERE id = ?",
        (run_id,),
    )
    old_heartbeat = conn.execute(
        "SELECT heartbeat_at FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()["heartbeat_at"]

    heartbeat_parse_run(conn, run_id)

    new_heartbeat = conn.execute(
        "SELECT heartbeat_at FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()["heartbeat_at"]
    assert new_heartbeat > old_heartbeat


def test_worker_heartbeat_does_not_refresh_stolen_parse_run(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None
    conn.execute(
        "UPDATE parse_runs SET heartbeat_at = datetime('now', '-5 minutes') WHERE id = ?",
        (run_id,),
    )
    conn.commit()
    old_heartbeat = conn.execute(
        "SELECT heartbeat_at FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()["heartbeat_at"]

    heartbeat_parse_run_for_worker(conn, run_id, worker_id="worker-2")

    unchanged_heartbeat = conn.execute(
        "SELECT heartbeat_at FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()["heartbeat_at"]
    assert unchanged_heartbeat == old_heartbeat

    heartbeat_parse_run_for_worker(conn, run_id, worker_id="worker-1")
    new_heartbeat = conn.execute(
        "SELECT heartbeat_at FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()["heartbeat_at"]
    assert new_heartbeat > old_heartbeat


def test_complete_parse_run_requires_current_worker_when_supplied(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None

    with pytest.raises(RuntimeError, match="no longer running"):
        complete_parse_run(
            conn,
            run_id,
            paper_id="paper-1",
            space_id="space-1",
            worker_id="worker-2",
            warnings=[],
        )

    row = conn.execute(
        "SELECT status, worker_id FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["status"] == "running"
    assert row["worker_id"] == "worker-1"

    complete_parse_run(
        conn,
        run_id,
        paper_id="paper-1",
        space_id="space-1",
        worker_id="worker-1",
        warnings=["ok"],
    )
    row = conn.execute(
        "SELECT status, worker_id FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["status"] == "completed"
    assert row["worker_id"] is None


def test_fail_parse_run_ignores_non_owner_when_supplied(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None

    fail_parse_run(
        conn,
        run_id,
        paper_id="paper-1",
        space_id="space-1",
        worker_id="worker-2",
        error="wrong worker",
        warnings=["wrong worker"],
    )

    row = conn.execute(
        "SELECT status, worker_id, last_error FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["status"] == "running"
    assert row["worker_id"] == "worker-1"
    assert row["last_error"] is None

    fail_parse_run(
        conn,
        run_id,
        paper_id="paper-1",
        space_id="space-1",
        worker_id="worker-1",
        error="owned failure",
        warnings=["owned failure"],
    )
    row = conn.execute(
        "SELECT status, worker_id, last_error FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["status"] == "failed"
    assert row["worker_id"] is None
    assert row["last_error"] == "owned failure"


def test_recover_stale_parse_runs_requeues_stale_running_runs(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None
    conn.execute(
        "UPDATE parse_runs SET heartbeat_at = datetime('now', '-20 minutes') WHERE id = ?",
        (run_id,),
    )
    conn.commit()

    recovered = recover_stale_parse_runs(
        conn,
        stale_after_seconds=600,
        max_attempts=3,
    )

    assert recovered == 1
    row = conn.execute(
        "SELECT status, worker_id, last_error FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["status"] == "queued"
    assert row["worker_id"] is None
    assert row["last_error"] == "worker_heartbeat_timeout"
    paper = conn.execute(
        "SELECT parse_status FROM papers WHERE id = 'paper-1'"
    ).fetchone()
    assert paper["parse_status"] == "pending"


def test_recover_stale_parse_runs_recovers_missing_heartbeat(
    tmp_path: Path,
) -> None:
    """Migrated running jobs without heartbeat state should not stay stuck."""
    conn = _conn(tmp_path)
    run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None
    conn.execute(
        "UPDATE parse_runs SET heartbeat_at = NULL WHERE id = ?",
        (run_id,),
    )
    conn.commit()

    recovered = recover_stale_parse_runs(
        conn,
        stale_after_seconds=600,
        max_attempts=3,
    )

    assert recovered == 1
    row = conn.execute(
        "SELECT status, heartbeat_at, worker_id, last_error FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    paper = conn.execute(
        "SELECT parse_status FROM papers WHERE id = 'paper-1'"
    ).fetchone()
    assert row["status"] == "queued"
    assert row["heartbeat_at"] is None
    assert row["worker_id"] is None
    assert row["last_error"] == "worker_heartbeat_timeout"
    assert paper["parse_status"] == "pending"


def test_recover_stale_parse_runs_fails_after_max_attempts(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None
    conn.execute(
        """
        UPDATE parse_runs
        SET heartbeat_at = datetime('now', '-20 minutes'),
            attempt_count = 3
        WHERE id = ?
        """,
        (run_id,),
    )
    conn.commit()

    recovered = recover_stale_parse_runs(
        conn,
        stale_after_seconds=600,
        max_attempts=3,
    )

    assert recovered == 1
    row = conn.execute(
        "SELECT status, worker_id, last_error FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert row["status"] == "failed"
    assert row["worker_id"] is None
    assert row["last_error"] == "worker_heartbeat_timeout"
    paper = conn.execute(
        "SELECT parse_status FROM papers WHERE id = 'paper-1'"
    ).fetchone()
    assert paper["parse_status"] == "error"


def test_complete_and_fail_update_run_and_paper_status(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    run_id = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None

    complete_parse_run(conn, run_id, paper_id="paper-1", warnings=["ok"])
    row = conn.execute(
        "SELECT status, warnings_json FROM parse_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    paper = conn.execute(
        "SELECT parse_status FROM papers WHERE id = 'paper-1'"
    ).fetchone()
    assert row["status"] == "completed"
    assert json.loads(row["warnings_json"]) == ["ok"]
    assert paper["parse_status"] == "parsed"

    failed_run = queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )
    assert claim_next_parse_run(conn, worker_id="worker-1") is not None
    fail_parse_run(conn, failed_run, paper_id="paper-1", error="boom", warnings=["bad"])
    paper_after_failure = conn.execute(
        "SELECT parse_status FROM papers WHERE id = 'paper-1'"
    ).fetchone()
    assert paper_after_failure["parse_status"] == "parsed"
