"""Tests for SQLite schema migration helpers."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

import db_migrations
from db import SCHEMA_SQL, get_connection, get_table_names, init_db
from db_migrations import apply_migrations, get_schema_version, set_schema_version


SCHEMA_VERSION_KEY = "schema_version"


def schema_version_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all schema version app_state rows."""
    return conn.execute(
        "SELECT key, value FROM app_state WHERE key = ?", (SCHEMA_VERSION_KEY,)
    ).fetchall()


def create_schema_connection(db_path: Path) -> sqlite3.Connection:
    """Create a connection with the base schema installed."""
    conn = get_connection(database_path=db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def test_apply_migrations_creates_initial_schema_version_row() -> None:
    """Fresh initialized databases default to schema version 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = create_schema_connection(db_path)

        assert get_schema_version(conn) == 0
        assert schema_version_rows(conn) == []

        apply_migrations(conn)

        rows = schema_version_rows(conn)
        assert get_schema_version(conn) == 0
        assert len(rows) == 1
        assert rows[0]["value"] == "0"

        conn.close()


def test_set_schema_version_upserts_one_value() -> None:
    """Setting schema version updates the existing app_state row."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = create_schema_connection(db_path)

        set_schema_version(conn, 1)
        set_schema_version(conn, 2)

        rows = schema_version_rows(conn)
        assert get_schema_version(conn) == 2
        assert len(rows) == 1
        assert rows[0]["value"] == "2"

        conn.close()


def test_apply_migrations_runs_numbered_migration_once_and_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registered migrations run once and persist the advanced version."""
    calls = []

    def migration_one(conn: sqlite3.Connection) -> None:
        calls.append("migration-1")
        conn.execute("CREATE TABLE migration_one (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(db_migrations, "LATEST_SCHEMA_VERSION", 1)
    monkeypatch.setattr(db_migrations, "MIGRATIONS", {1: migration_one})

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        conn = init_db(database_path=db_path)
        assert calls == ["migration-1"]
        assert get_schema_version(conn) == 1
        assert "migration_one" in get_table_names(conn)
        conn.close()

        conn = init_db(database_path=db_path)
        assert calls == ["migration-1"]
        assert get_schema_version(conn) == 1
        assert "migration_one" in get_table_names(conn)
        conn.close()


def test_failed_migration_rolls_back_schema_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed migration DDL is rolled back with the version unchanged."""

    def failing_migration(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE migration_partial (id INTEGER PRIMARY KEY)")
        raise RuntimeError("migration failed")

    monkeypatch.setattr(db_migrations, "LATEST_SCHEMA_VERSION", 1)
    monkeypatch.setattr(db_migrations, "MIGRATIONS", {1: failing_migration})

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = create_schema_connection(db_path)
        set_schema_version(conn, 0)
        conn.commit()

        with pytest.raises(RuntimeError, match="migration failed"):
            apply_migrations(conn)

        assert get_schema_version(conn) == 0
        assert "migration_partial" not in get_table_names(conn)

        conn.close()


def test_missing_registered_migration_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing migration registrations fail with a clear RuntimeError."""
    monkeypatch.setattr(db_migrations, "LATEST_SCHEMA_VERSION", 1)
    monkeypatch.setattr(db_migrations, "MIGRATIONS", {})

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = create_schema_connection(db_path)

        with pytest.raises(RuntimeError, match="No migration registered for version 1"):
            apply_migrations(conn)

        conn.close()


def test_init_db_runs_migrations_idempotently() -> None:
    """Repeated init_db leaves exactly one schema version value."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        conn = init_db(database_path=db_path)
        conn.close()

        conn = init_db(database_path=db_path)

        rows = schema_version_rows(conn)
        assert get_schema_version(conn) == 0
        assert len(rows) == 1
        assert rows[0]["value"] == "0"

        conn.close()
