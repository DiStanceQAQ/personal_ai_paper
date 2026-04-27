"""Tests for SQLite schema migration helpers."""

import tempfile
from pathlib import Path

from db import SCHEMA_SQL, get_connection, init_db
from db_migrations import apply_migrations, get_schema_version, set_schema_version


SCHEMA_VERSION_KEY = "schema_version"


def schema_version_rows(conn):
    """Return all schema version app_state rows."""
    return conn.execute(
        "SELECT key, value FROM app_state WHERE key = ?", (SCHEMA_VERSION_KEY,)
    ).fetchall()


def test_apply_migrations_creates_initial_schema_version_row() -> None:
    """Fresh initialized databases default to schema version 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = get_connection(database_path=db_path)
        conn.executescript(SCHEMA_SQL)
        conn.commit()

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
        conn = get_connection(database_path=db_path)
        conn.executescript(SCHEMA_SQL)
        conn.commit()

        set_schema_version(conn, 1)
        set_schema_version(conn, 2)

        rows = schema_version_rows(conn)
        assert get_schema_version(conn) == 2
        assert len(rows) == 1
        assert rows[0]["value"] == "2"

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
