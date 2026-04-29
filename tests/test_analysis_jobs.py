"""Tests for durable background analysis jobs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from paper_engine.analysis.jobs import (
    AnalysisRunCancelled,
    cancel_analysis_run,
    queue_analysis_run,
)
from paper_engine.analysis.worker import AnalysisWorker
from paper_engine.storage.database import init_db


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(database_path=tmp_path / "test.db")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, title, parse_status)
        VALUES ('paper-1', 'space-1', 'Paper', 'parsed')
        """
    )
    conn.execute(
        """
        INSERT INTO passages (id, paper_id, space_id, original_text)
        VALUES ('passage-1', 'paper-1', 'space-1', 'Parsed text')
        """
    )
    conn.commit()
    return conn


def test_analysis_worker_completes_claimed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn(tmp_path)
    run_id = queue_analysis_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
    )

    async def fake_run_paper_analysis(
        paper_id: str,
        space_id: str,
        *,
        analysis_run_id: str | None = None,
    ) -> object:
        assert (paper_id, space_id, analysis_run_id) == (
            "paper-1",
            "space-1",
            run_id,
        )
        conn.execute(
            """
            UPDATE analysis_runs
            SET status = 'completed',
                accepted_card_count = 2,
                completed_at = datetime('now'),
                worker_id = NULL
            WHERE id = ?
            """,
            (analysis_run_id,),
        )
        conn.commit()
        return object()

    monkeypatch.setattr(
        "paper_engine.analysis.worker.run_paper_analysis",
        fake_run_paper_analysis,
    )

    worker = AnalysisWorker(
        conn_factory=lambda: conn,
        worker_id="analysis-worker-1",
        close_connection=False,
    )

    assert worker.run_once() is True
    row = conn.execute("SELECT status, accepted_card_count FROM analysis_runs").fetchone()
    assert dict(row) == {"status": "completed", "accepted_card_count": 2}


def test_analysis_worker_marks_failed_run_without_touching_parse_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn(tmp_path)
    queue_analysis_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
    )

    async def failing_run_paper_analysis(*args: object, **kwargs: object) -> object:
        raise RuntimeError("LLM failed")

    monkeypatch.setattr(
        "paper_engine.analysis.worker.run_paper_analysis",
        failing_run_paper_analysis,
    )

    worker = AnalysisWorker(
        conn_factory=lambda: conn,
        worker_id="analysis-worker-1",
        close_connection=False,
    )

    assert worker.run_once() is True
    run = conn.execute("SELECT status, last_error FROM analysis_runs").fetchone()
    paper = conn.execute("SELECT parse_status FROM papers WHERE id = 'paper-1'").fetchone()
    assert run["status"] == "failed"
    assert "LLM failed" in run["last_error"]
    assert paper["parse_status"] == "parsed"


def test_cancelled_queued_run_is_not_claimed(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    run_id = queue_analysis_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
    )
    cancelled = cancel_analysis_run(
        conn,
        analysis_run_id=run_id,
        paper_id="paper-1",
        space_id="space-1",
    )
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"

    worker = AnalysisWorker(
        conn_factory=lambda: conn,
        worker_id="analysis-worker-1",
        close_connection=False,
    )

    assert worker.run_once() is False


def test_analysis_worker_preserves_cancelled_running_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn(tmp_path)
    run_id = queue_analysis_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
    )

    async def cancelled_run(
        paper_id: str,
        space_id: str,
        *,
        analysis_run_id: str | None = None,
    ) -> object:
        assert analysis_run_id == run_id
        cancel_analysis_run(
            conn,
            analysis_run_id=run_id,
            paper_id=paper_id,
            space_id=space_id,
        )
        raise AnalysisRunCancelled("cancelled")

    monkeypatch.setattr(
        "paper_engine.analysis.worker.run_paper_analysis",
        cancelled_run,
    )

    worker = AnalysisWorker(
        conn_factory=lambda: conn,
        worker_id="analysis-worker-1",
        close_connection=False,
    )

    assert worker.run_once() is True
    run = conn.execute("SELECT status, last_error FROM analysis_runs").fetchone()
    assert dict(run) == {"status": "cancelled", "last_error": "cancelled_by_user"}
