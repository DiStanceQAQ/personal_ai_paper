"""SQLite repository helpers for idea spaces."""

from __future__ import annotations

import sqlite3
from typing import Any

ACTIVE_SPACE_KEY = "active_space"


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def get_active_space(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT s.*
           FROM spaces s
           JOIN app_state a ON a.value = s.id
           WHERE a.key = ? AND s.status = 'active'""",
        (ACTIVE_SPACE_KEY,),
    ).fetchone()
    return None if row is None else row_to_dict(row)


def get_active_space_id(conn: sqlite3.Connection) -> str | None:
    space = get_active_space(conn)
    return None if space is None else str(space["id"])
