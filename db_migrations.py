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
LATEST_SCHEMA_VERSION = 0

Migration = Callable[[sqlite3.Connection], None]
MIGRATIONS: dict[int, Migration] = {}


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


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending schema migrations and persist the current version."""
    current_version = get_schema_version(conn)
    if current_version > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            "Database schema version "
            f"{current_version} is newer than supported version {LATEST_SCHEMA_VERSION}"
        )

    if not _schema_version_row_exists(conn):
        set_schema_version(conn, current_version)

    for version in range(current_version + 1, LATEST_SCHEMA_VERSION + 1):
        migration = MIGRATIONS[version]
        migration(conn)
        set_schema_version(conn, version)

    conn.commit()
