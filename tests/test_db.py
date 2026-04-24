"""Tests for database initialization and schema."""

import sqlite3
import tempfile
from pathlib import Path

from db import SCHEMA_SQL, get_connection, get_table_names, init_db


def test_init_db_creates_tables() -> None:
    """Test that init_db creates all expected tables."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        tables = get_table_names(conn)
        expected_tables = {
            "spaces", "papers", "passages",
            "knowledge_cards", "notes", "app_state",
        }
        assert expected_tables.issubset(set(tables))

        conn.close()


def test_init_db_idempotent() -> None:
    """Test that init_db can be called repeatedly without destroying data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        conn = init_db(database_path=db_path)
        # Insert test data
        conn.execute(
            "INSERT INTO spaces (id, name, description) VALUES (?, ?, ?)",
            ("test-space-1", "Test Space", "A test space"),
        )
        conn.commit()

        # Call init_db again
        conn2 = init_db(database_path=db_path)

        # Check that the data is still there
        rows = conn2.execute("SELECT * FROM spaces").fetchall()
        assert len(rows) == 1
        assert rows[0]["name"] == "Test Space"

        conn.close()
        conn2.close()


def test_foreign_keys_enabled() -> None:
    """Test that foreign keys are enforced."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        # Try inserting a paper with a non-existent space_id
        try:
            conn.execute(
                "INSERT INTO papers (id, space_id, title) VALUES (?, ?, ?)",
                ("paper-1", "nonexistent-space", "Test Paper"),
            )
            conn.commit()
            assert False, "Should have raised IntegrityError"
        except sqlite3.IntegrityError:
            pass  # Expected

        conn.close()


def test_space_id_on_scoped_tables() -> None:
    """Test that space-scoped tables all have space_id column."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        scoped_tables = ["papers", "passages", "knowledge_cards", "notes"]
        for table in scoped_tables:
            pragma = conn.execute(f"PRAGMA table_info({table})").fetchall()
            columns = {row["name"] for row in pragma}
            assert "space_id" in columns, f"{table} should have space_id column"

        conn.close()


def test_app_state_table() -> None:
    """Test that app_state table works for key-value storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        conn.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?)",
            ("active_space", "space-1"),
        )
        conn.commit()

        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", ("active_space",)
        ).fetchone()
        assert row is not None
        assert row["value"] == "space-1"

        conn.close()
