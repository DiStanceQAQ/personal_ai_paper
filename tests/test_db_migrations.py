"""Tests for SQLite schema migration helpers."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

import db_migrations
from db import SCHEMA_SQL, get_connection, get_table_names, init_db
from db_migrations import apply_migrations, get_schema_version, set_schema_version


SCHEMA_VERSION_KEY = "schema_version"
EXPECTED_SCHEMA_VERSION = 2


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


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Return column names for a table."""
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def table_column_info(
    conn: sqlite3.Connection, table_name: str
) -> dict[str, sqlite3.Row]:
    """Return PRAGMA table_info rows keyed by column name."""
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"]: row for row in rows}


def index_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    """Return index names for a table."""
    rows = conn.execute(f"PRAGMA index_list({table_name})").fetchall()
    return {row["name"] for row in rows}


def index_columns(conn: sqlite3.Connection, index_name: str) -> tuple[str, ...]:
    """Return indexed column names in index order."""
    rows = conn.execute(f"PRAGMA index_info({index_name})").fetchall()
    return tuple(row["name"] for row in rows)


def assert_index_columns(
    conn: sqlite3.Connection, expected_columns: dict[str, tuple[str, ...]]
) -> None:
    """Assert indexed column names in index order for each index."""
    for index_name, columns in expected_columns.items():
        assert index_columns(conn, index_name) == columns


def insert_space(conn: sqlite3.Connection, space_id: str) -> None:
    """Insert a minimal space row."""
    conn.execute(
        "INSERT INTO spaces (id, name) VALUES (?, ?)",
        (space_id, space_id),
    )


def insert_paper(conn: sqlite3.Connection, paper_id: str, space_id: str) -> None:
    """Insert a minimal paper row."""
    conn.execute(
        "INSERT INTO papers (id, space_id, title) VALUES (?, ?, ?)",
        (paper_id, space_id, paper_id),
    )


def test_apply_migrations_creates_initial_schema_version_row() -> None:
    """Fresh initialized databases advance to the latest schema version."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = create_schema_connection(db_path)

        assert get_schema_version(conn) == 0
        assert schema_version_rows(conn) == []

        apply_migrations(conn)

        rows = schema_version_rows(conn)
        assert get_schema_version(conn) == EXPECTED_SCHEMA_VERSION
        assert len(rows) == 1
        assert rows[0]["value"] == str(EXPECTED_SCHEMA_VERSION)

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
        assert get_schema_version(conn) == EXPECTED_SCHEMA_VERSION
        assert len(rows) == 1
        assert rows[0]["value"] == str(EXPECTED_SCHEMA_VERSION)

        conn.close()


def test_migration_one_creates_parse_run_and_document_element_tables() -> None:
    """Migration 1 creates parse storage tables with JSON text columns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        tables = set(get_table_names(conn))
        assert {
            "parse_runs",
            "document_elements",
            "document_tables",
            "document_assets",
        }.issubset(tables)
        assert get_schema_version(conn) == EXPECTED_SCHEMA_VERSION

        assert {
            "warnings_json",
            "config_json",
            "metadata_json",
        }.issubset(table_columns(conn, "parse_runs"))
        assert {
            "bbox_json",
            "heading_path_json",
            "metadata_json",
        }.issubset(table_columns(conn, "document_elements"))
        assert {
            "cells_json",
            "bbox_json",
            "metadata_json",
        }.issubset(table_columns(conn, "document_tables"))
        assert {
            "bbox_json",
            "metadata_json",
        }.issubset(table_columns(conn, "document_assets"))

        conn.close()


def test_migration_one_creates_parse_storage_indexes() -> None:
    """Migration 1 adds predictable indexes for parse storage lookups."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        assert {
            "idx_parse_runs_paper_id",
            "idx_parse_runs_space_id",
            "idx_parse_runs_id_paper_space_unique",
        }.issubset(index_names(conn, "parse_runs"))
        assert {
            "idx_document_elements_paper_id",
            "idx_document_elements_space_id",
            "idx_document_elements_parse_run_id",
            "idx_document_elements_paper_element_index",
            "idx_document_elements_id_parse_scope_unique",
        }.issubset(index_names(conn, "document_elements"))
        assert {
            "idx_document_tables_paper_id",
            "idx_document_tables_space_id",
            "idx_document_tables_parse_run_id",
            "idx_document_tables_element_id",
        }.issubset(index_names(conn, "document_tables"))
        assert {
            "idx_document_assets_paper_id",
            "idx_document_assets_space_id",
            "idx_document_assets_parse_run_id",
            "idx_document_assets_element_id",
        }.issubset(index_names(conn, "document_assets"))

        assert_index_columns(
            conn,
            {
                "idx_parse_runs_paper_id": ("paper_id",),
                "idx_parse_runs_space_id": ("space_id",),
                "idx_parse_runs_id_paper_space_unique": (
                    "id",
                    "paper_id",
                    "space_id",
                ),
                "idx_document_elements_paper_id": ("paper_id",),
                "idx_document_elements_space_id": ("space_id",),
                "idx_document_elements_parse_run_id": ("parse_run_id",),
                "idx_document_elements_paper_element_index": (
                    "paper_id",
                    "element_index",
                ),
                "idx_document_elements_id_parse_scope_unique": (
                    "id",
                    "parse_run_id",
                    "paper_id",
                    "space_id",
                ),
                "idx_document_tables_paper_id": ("paper_id",),
                "idx_document_tables_space_id": ("space_id",),
                "idx_document_tables_parse_run_id": ("parse_run_id",),
                "idx_document_tables_element_id": ("element_id",),
                "idx_document_assets_paper_id": ("paper_id",),
                "idx_document_assets_space_id": ("space_id",),
                "idx_document_assets_parse_run_id": ("parse_run_id",),
                "idx_document_assets_element_id": ("element_id",),
            },
        )

        conn.close()


def test_migration_two_extends_existing_passages_with_provenance_defaults() -> None:
    """Migration 2 adds nullable/defaulted passage provenance columns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = create_schema_connection(db_path)
        insert_space(conn, "space-1")
        insert_paper(conn, "paper-1", "space-1")
        conn.execute(
            """
            INSERT INTO passages (
                id, paper_id, space_id, section, page_number, paragraph_index,
                original_text, parse_confidence, passage_type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "passage-1",
                "paper-1",
                "space-1",
                "method",
                3,
                5,
                "existing passage",
                0.9,
                "method",
            ),
        )
        conn.commit()

        apply_migrations(conn)

        assert get_schema_version(conn) == EXPECTED_SCHEMA_VERSION

        column_info = table_column_info(conn, "passages")
        expected_columns = {
            "parse_run_id": ("TEXT", 0, None),
            "element_ids_json": ("TEXT", 1, "'[]'"),
            "heading_path_json": ("TEXT", 1, "'[]'"),
            "bbox_json": ("TEXT", 0, None),
            "token_count": ("INTEGER", 0, None),
            "char_count": ("INTEGER", 0, None),
            "content_hash": ("TEXT", 0, None),
            "parser_backend": ("TEXT", 1, "''"),
            "extraction_method": ("TEXT", 1, "''"),
            "quality_flags_json": ("TEXT", 1, "'[]'"),
        }
        assert expected_columns.keys() <= column_info.keys()
        for column_name, (expected_type, expected_notnull, expected_default) in (
            expected_columns.items()
        ):
            assert column_info[column_name]["type"] == expected_type
            assert column_info[column_name]["notnull"] == expected_notnull
            assert column_info[column_name]["dflt_value"] == expected_default

        existing_row = conn.execute(
            """
            SELECT parse_run_id, element_ids_json, heading_path_json, bbox_json,
                   token_count, char_count, content_hash, parser_backend,
                   extraction_method, quality_flags_json
            FROM passages
            WHERE id = ?
            """,
            ("passage-1",),
        ).fetchone()
        assert dict(existing_row) == {
            "parse_run_id": None,
            "element_ids_json": "[]",
            "heading_path_json": "[]",
            "bbox_json": None,
            "token_count": None,
            "char_count": None,
            "content_hash": None,
            "parser_backend": "",
            "extraction_method": "",
            "quality_flags_json": "[]",
        }

        conn.execute(
            """
            INSERT INTO passages (id, paper_id, space_id, original_text)
            VALUES (?, ?, ?, ?)
            """,
            ("passage-2", "paper-1", "space-1", "legacy insert still works"),
        )
        inserted_row = conn.execute(
            """
            SELECT parse_run_id, element_ids_json, heading_path_json, bbox_json,
                   token_count, char_count, content_hash, parser_backend,
                   extraction_method, quality_flags_json
            FROM passages
            WHERE id = ?
            """,
            ("passage-2",),
        ).fetchone()
        assert dict(inserted_row) == dict(existing_row)

        conn.close()


def test_migration_two_adds_partial_unique_content_hash_index() -> None:
    """Passages may share NULL hashes but not duplicate hashes per paper."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)

        index_rows = {
            row["name"]: row for row in conn.execute("PRAGMA index_list(passages)")
        }
        index_row = index_rows["idx_passages_paper_content_hash_unique"]
        assert index_row["unique"] == 1
        assert index_row["partial"] == 1
        assert index_columns(conn, "idx_passages_paper_content_hash_unique") == (
            "paper_id",
            "content_hash",
        )
        index_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?",
            ("idx_passages_paper_content_hash_unique",),
        ).fetchone()["sql"]
        assert "WHERE content_hash IS NOT NULL" in index_sql

        insert_space(conn, "space-1")
        insert_paper(conn, "paper-1", "space-1")
        insert_paper(conn, "paper-2", "space-1")

        def insert_passage(
            passage_id: str, paper_id: str, content_hash: str | None
        ) -> None:
            conn.execute(
                """
                INSERT INTO passages (
                    id, paper_id, space_id, original_text, content_hash
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (passage_id, paper_id, "space-1", passage_id, content_hash),
            )

        insert_passage("passage-1", "paper-1", "hash-1")
        insert_passage("passage-2", "paper-2", "hash-1")
        insert_passage("passage-3", "paper-1", None)
        insert_passage("passage-4", "paper-1", None)

        with pytest.raises(sqlite3.IntegrityError):
            insert_passage("passage-5", "paper-1", "hash-1")

        conn.close()


def test_parse_run_rejects_paper_space_mismatch() -> None:
    """Parse runs must use the same space_id as their paper."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)
        insert_space(conn, "space-1")
        insert_space(conn, "space-2")
        insert_paper(conn, "paper-1", "space-1")
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO parse_runs (id, paper_id, space_id)
                VALUES (?, ?, ?)
                """,
                ("parse-run-1", "paper-1", "space-2"),
            )

        conn.close()


def test_document_rows_reject_parse_scope_mismatch() -> None:
    """Document rows must use the same paper and space as their parse run."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)
        insert_space(conn, "space-1")
        insert_space(conn, "space-2")
        insert_paper(conn, "paper-1", "space-1")
        insert_paper(conn, "paper-2", "space-2")
        conn.execute(
            """
            INSERT INTO parse_runs (id, paper_id, space_id)
            VALUES (?, ?, ?)
            """,
            ("parse-run-1", "paper-1", "space-1"),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO document_elements (
                    id, parse_run_id, paper_id, space_id, element_index, element_type
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("element-1", "parse-run-1", "paper-2", "space-2", 0, "paragraph"),
            )

        conn.execute(
            """
            INSERT INTO document_elements (
                id, parse_run_id, paper_id, space_id, element_index, element_type
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("element-2", "parse-run-1", "paper-1", "space-1", 0, "table"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO document_tables (
                    id, parse_run_id, paper_id, space_id, element_id
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("table-1", "parse-run-1", "paper-2", "space-2", "element-2"),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO document_assets (
                    id, parse_run_id, paper_id, space_id, element_id
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("asset-1", "parse-run-1", "paper-2", "space-2", "element-2"),
            )

        conn.close()


def test_tables_and_assets_reject_element_from_other_parse_run() -> None:
    """Element FKs must match the row's parse run, paper, and space scope."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)
        insert_space(conn, "space-1")
        insert_paper(conn, "paper-1", "space-1")
        conn.execute(
            """
            INSERT INTO parse_runs (id, paper_id, space_id)
            VALUES (?, ?, ?)
            """,
            ("parse-run-1", "paper-1", "space-1"),
        )
        conn.execute(
            """
            INSERT INTO parse_runs (id, paper_id, space_id)
            VALUES (?, ?, ?)
            """,
            ("parse-run-2", "paper-1", "space-1"),
        )
        conn.execute(
            """
            INSERT INTO document_elements (
                id, parse_run_id, paper_id, space_id, element_index, element_type
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("element-1", "parse-run-1", "paper-1", "space-1", 0, "table"),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO document_tables (
                    id, parse_run_id, paper_id, space_id, element_id
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("table-1", "parse-run-2", "paper-1", "space-1", "element-1"),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO document_assets (
                    id, parse_run_id, paper_id, space_id, element_id
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                ("asset-1", "parse-run-2", "paper-1", "space-1", "element-1"),
            )

        conn.close()


def test_deleting_paper_cascades_parse_storage_rows() -> None:
    """Deleting a paper removes parse runs and all child document rows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_db(database_path=db_path)
        insert_space(conn, "space-1")
        insert_paper(conn, "paper-1", "space-1")
        conn.execute(
            """
            INSERT INTO parse_runs (id, paper_id, space_id)
            VALUES (?, ?, ?)
            """,
            ("parse-run-1", "paper-1", "space-1"),
        )
        conn.execute(
            """
            INSERT INTO document_elements (
                id, parse_run_id, paper_id, space_id, element_index, element_type
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("element-1", "parse-run-1", "paper-1", "space-1", 0, "table"),
        )
        conn.execute(
            """
            INSERT INTO document_tables (
                id, parse_run_id, paper_id, space_id, element_id
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            ("table-1", "parse-run-1", "paper-1", "space-1", "element-1"),
        )
        conn.execute(
            """
            INSERT INTO document_assets (
                id, parse_run_id, paper_id, space_id, element_id
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            ("asset-1", "parse-run-1", "paper-1", "space-1", "element-1"),
        )
        conn.commit()

        conn.execute("DELETE FROM papers WHERE id = ?", ("paper-1",))
        conn.commit()

        for table_name in (
            "parse_runs",
            "document_elements",
            "document_tables",
            "document_assets",
        ):
            row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            assert row_count[0] == 0

        conn.close()
