from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import paper_engine.api.app as app_module
from paper_engine.storage.database import get_connection, init_db


@pytest.mark.asyncio
async def test_lifespan_recovers_stale_parse_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.storage.database as db_module

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(db_module, "DATABASE_PATH", Path(tmpdir) / "test.db")
        init_db(database_path=db_module.DATABASE_PATH)
        conn = get_connection(db_module.DATABASE_PATH)
        conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
        conn.execute(
            """
            INSERT INTO papers (id, space_id, file_path, file_hash)
            VALUES ('paper-1', 'space-1', ?, 'hash')
            """,
            (str(Path(tmpdir) / "paper.pdf"),),
        )
        conn.execute(
            """
            INSERT INTO parse_runs (
                id, paper_id, space_id, backend, status, heartbeat_at,
                attempt_count
            )
            VALUES (
                'run-1', 'paper-1', 'space-1', 'docling', 'running',
                datetime('now', '-20 minutes'), 1
            )
            """
        )
        conn.commit()
        conn.close()

        monkeypatch.setenv("PAPER_ENGINE_PARSE_WORKER_ENABLED", "0")
        async with app_module.app.router.lifespan_context(app_module.app):
            async with AsyncClient(
                transport=ASGITransport(app=app_module.app),
                base_url="http://test",
            ) as client:
                response = await client.get("/health")
                assert response.status_code == 200

        conn = get_connection(db_module.DATABASE_PATH)
        try:
            row = conn.execute(
                "SELECT status, worker_id, last_error FROM parse_runs WHERE id = 'run-1'"
            ).fetchone()
        finally:
            conn.close()
        assert row["status"] == "queued"
        assert row["worker_id"] is None
        assert row["last_error"] == "worker_heartbeat_timeout"


@pytest.mark.asyncio
async def test_lifespan_starts_parse_worker_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import paper_engine.storage.database as db_module

    monkeypatch.setattr(db_module, "DATABASE_PATH", tmp_path / "test.db")
    calls: list[dict[str, Any]] = []

    def fake_run_worker_loop(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(app_module, "run_worker_loop", fake_run_worker_loop)
    monkeypatch.setattr(app_module, "run_parse_recovery_loop", fake_run_worker_loop)
    monkeypatch.setenv("PAPER_ENGINE_PARSE_WORKER_ENABLED", "1")
    monkeypatch.setenv("PAPER_ENGINE_PARSE_POLL_SECONDS", "0.01")
    monkeypatch.setenv("PAPER_ENGINE_PARSE_RECOVERY_POLL_SECONDS", "0.02")

    async with app_module.app.router.lifespan_context(app_module.app):
        pass

    assert len(calls) == 2
    assert calls[0]["poll_interval_seconds"] == 0.01
    assert calls[0]["stop"]() is True
    assert calls[1]["poll_interval_seconds"] == 0.02
    assert calls[1]["stop"]() is True
