"""Idempotent SQLite schema migration helpers."""

import sqlite3
from collections.abc import Callable

__all__ = [
    "LATEST_SCHEMA_VERSION",
    "SCHEMA_VERSION_KEY",
    "apply_migrations",
    "get_schema_version",
    "set_schema_version",
]

SCHEMA_VERSION_KEY = "schema_version"
LATEST_SCHEMA_VERSION = 2

Migration = Callable[[sqlite3.Connection], None]


def _create_parse_run_document_tables(conn: sqlite3.Connection) -> None:
    """Create parse run and structured document storage tables."""
    statements = (
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_id_space_id_unique
            ON papers(id, space_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS parse_runs (
            id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            backend TEXT NOT NULL DEFAULT '',
            extraction_method TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed',
            quality_score REAL,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            config_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (paper_id, space_id)
                REFERENCES papers(id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (space_id) REFERENCES spaces(id)
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_parse_runs_id_paper_space_unique
            ON parse_runs(id, paper_id, space_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS document_elements (
            id TEXT PRIMARY KEY,
            parse_run_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            element_index INTEGER NOT NULL,
            element_type TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            page_number INTEGER NOT NULL DEFAULT 0,
            bbox_json TEXT,
            heading_path_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (parse_run_id, paper_id, space_id)
                REFERENCES parse_runs(id, paper_id, space_id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_document_elements_id_parse_scope_unique
            ON document_elements(id, parse_run_id, paper_id, space_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS document_tables (
            id TEXT PRIMARY KEY,
            parse_run_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            element_id TEXT,
            table_index INTEGER NOT NULL DEFAULT 0,
            page_number INTEGER NOT NULL DEFAULT 0,
            caption TEXT NOT NULL DEFAULT '',
            cells_json TEXT NOT NULL DEFAULT '[]',
            bbox_json TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (parse_run_id, paper_id, space_id)
                REFERENCES parse_runs(id, paper_id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (element_id, parse_run_id, paper_id, space_id)
                REFERENCES document_elements(id, parse_run_id, paper_id, space_id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS document_assets (
            id TEXT PRIMARY KEY,
            parse_run_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            element_id TEXT,
            asset_type TEXT NOT NULL DEFAULT '',
            page_number INTEGER NOT NULL DEFAULT 0,
            uri TEXT NOT NULL DEFAULT '',
            bbox_json TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (parse_run_id, paper_id, space_id)
                REFERENCES parse_runs(id, paper_id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (element_id, parse_run_id, paper_id, space_id)
                REFERENCES document_elements(id, parse_run_id, paper_id, space_id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_parse_runs_paper_id
            ON parse_runs(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_parse_runs_space_id
            ON parse_runs(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_elements_paper_id
            ON document_elements(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_elements_space_id
            ON document_elements(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_elements_parse_run_id
            ON document_elements(parse_run_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_elements_paper_element_index
            ON document_elements(paper_id, element_index)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_tables_paper_id
            ON document_tables(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_tables_space_id
            ON document_tables(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_tables_parse_run_id
            ON document_tables(parse_run_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_tables_element_id
            ON document_tables(element_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_assets_paper_id
            ON document_assets(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_assets_space_id
            ON document_assets(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_assets_parse_run_id
            ON document_assets(parse_run_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_assets_element_id
            ON document_assets(element_id)
        """,
    )
    for statement in statements:
        conn.execute(statement)


def _extend_passages_with_provenance_columns(conn: sqlite3.Connection) -> None:
    """Add passage provenance fields for structured parse persistence."""
    statements = (
        """
        ALTER TABLE passages
        ADD COLUMN parse_run_id TEXT REFERENCES parse_runs(id) ON DELETE SET NULL
        """,
        """
        ALTER TABLE passages
        ADD COLUMN element_ids_json TEXT NOT NULL DEFAULT '[]'
        """,
        """
        ALTER TABLE passages
        ADD COLUMN heading_path_json TEXT NOT NULL DEFAULT '[]'
        """,
        """
        ALTER TABLE passages
        ADD COLUMN bbox_json TEXT
        """,
        """
        ALTER TABLE passages
        ADD COLUMN token_count INTEGER
        """,
        """
        ALTER TABLE passages
        ADD COLUMN char_count INTEGER
        """,
        """
        ALTER TABLE passages
        ADD COLUMN content_hash TEXT
        """,
        """
        ALTER TABLE passages
        ADD COLUMN parser_backend TEXT NOT NULL DEFAULT ''
        """,
        """
        ALTER TABLE passages
        ADD COLUMN extraction_method TEXT NOT NULL DEFAULT ''
        """,
        """
        ALTER TABLE passages
        ADD COLUMN quality_flags_json TEXT NOT NULL DEFAULT '[]'
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_passages_paper_content_hash_unique
            ON passages(paper_id, content_hash)
            WHERE content_hash IS NOT NULL
        """,
    )
    for statement in statements:
        conn.execute(statement)


MIGRATIONS: dict[int, Migration] = {
    1: _create_parse_run_document_tables,
    2: _extend_passages_with_provenance_columns,
}


def _schema_version_row_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM app_state WHERE key = ?", (SCHEMA_VERSION_KEY,)
    ).fetchone()
    return row is not None


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or 0 when no version row exists."""
    row = conn.execute(
        "SELECT value FROM app_state WHERE key = ?", (SCHEMA_VERSION_KEY,)
    ).fetchone()
    if row is None:
        return 0
    return int(row[0])


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Store the schema version in app_state as a single upserted row."""
    conn.execute(
        """
        INSERT INTO app_state (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (SCHEMA_VERSION_KEY, str(version)),
    )


def _pending_versions(current_version: int) -> range:
    return range(current_version + 1, LATEST_SCHEMA_VERSION + 1)


def _get_migration(version: int) -> Migration:
    try:
        return MIGRATIONS[version]
    except KeyError as exc:
        raise RuntimeError(f"No migration registered for version {version}") from exc


def _apply_migration(
    conn: sqlite3.Connection, version: int, migration: Migration
) -> None:
    savepoint = f"schema_migration_{version}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        migration(conn)
        set_schema_version(conn, version)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending schema migrations and persist the current version."""
    current_version = get_schema_version(conn)
    if current_version > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            "Database schema version "
            f"{current_version} is newer than supported version {LATEST_SCHEMA_VERSION}"
        )

    migrations: dict[int, Migration] = {}
    for version in _pending_versions(current_version):
        migrations[version] = _get_migration(version)

    if not _schema_version_row_exists(conn):
        set_schema_version(conn, current_version)
        conn.commit()

    for version, migration in migrations.items():
        _apply_migration(conn, version, migration)
        conn.commit()
