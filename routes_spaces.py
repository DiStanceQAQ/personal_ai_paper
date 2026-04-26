"""API routes for managing idea spaces."""

import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from db import get_connection

router = APIRouter(prefix="/api/spaces", tags=["spaces"])

ACTIVE_SPACE_KEY = "active_space"


def _space_row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a sqlite3.Row to a dict."""
    return dict(row)


def _clear_active_space_if_matches(conn: Any, space_id: str) -> None:
    """Clear active space state when that space is no longer active."""
    conn.execute(
        "DELETE FROM app_state WHERE key = ? AND value = ?",
        (ACTIVE_SPACE_KEY, space_id),
    )


@router.post("")
async def create_space(
    name: str = Body(...),
    description: str = Body(""),
) -> dict[str, Any]:
    """Create a new idea space."""
    space_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO spaces (id, name, description)
               VALUES (?, ?, ?)""",
            (space_id, name, description),
        )
        conn.commit()
    finally:
        conn.close()

    return await get_space(space_id)


@router.get("")
async def list_spaces() -> list[dict[str, Any]]:
    """List all idea spaces, excluding deleted ones."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM spaces WHERE status != 'deleted' ORDER BY updated_at DESC"
        ).fetchall()
        return [_space_row_to_dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/active")
async def get_active_space() -> dict[str, Any]:
    """Get the currently active idea space."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT s.id
               FROM spaces s
               JOIN app_state a ON a.value = s.id
               WHERE a.key = ? AND s.status = 'active'""",
            (ACTIVE_SPACE_KEY,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No active space set")
        space_id = row["id"]
        return await get_space(space_id)
    finally:
        conn.close()


@router.put("/active/{space_id}")
async def set_active_space(space_id: str) -> dict[str, Any]:
    """Set the active idea space."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM spaces WHERE id = ? AND status = 'active'",
            (space_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Space not found or not active",
            )

        conn.execute(
            """INSERT INTO app_state (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (ACTIVE_SPACE_KEY, space_id),
        )
        conn.commit()
        return {"active_space_id": space_id, "space": _space_row_to_dict(row)}
    finally:
        conn.close()


@router.get("/{space_id}")
async def get_space(space_id: str) -> dict[str, Any]:
    """Get a single idea space by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM spaces WHERE id = ?", (space_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Space not found")
        if row["status"] == "deleted":
            raise HTTPException(status_code=404, detail="Space not found")
        return _space_row_to_dict(row)
    finally:
        conn.close()


@router.patch("/{space_id}")
async def update_space(
    space_id: str,
    name: str | None = Body(None),
    description: str | None = Body(None),
) -> dict[str, Any]:
    """Update an idea space's name and/or description."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM spaces WHERE id = ?", (space_id,)
        ).fetchone()
        if row is None or row["status"] == "deleted":
            raise HTTPException(status_code=404, detail="Space not found")

        updates: list[str] = []
        params: list[str] = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if updates:
            updates.append("updated_at = datetime('now')")
            params.append(space_id)
            conn.execute(
                f"UPDATE spaces SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
    finally:
        conn.close()

    return await get_space(space_id)


@router.patch("/{space_id}/archive")
async def archive_space(space_id: str) -> dict[str, str]:
    """Archive an idea space (soft delete, data preserved)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM spaces WHERE id = ?", (space_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Space not found")
        if row["status"] == "deleted":
            raise HTTPException(status_code=404, detail="Space not found")

        conn.execute(
            "UPDATE spaces SET status = 'archived', updated_at = datetime('now') WHERE id = ?",
            (space_id,),
        )
        _clear_active_space_if_matches(conn, space_id)
        conn.commit()
        return {"status": "archived", "space_id": space_id}
    finally:
        conn.close()


@router.delete("/{space_id}")
async def delete_space(space_id: str) -> dict[str, str]:
    """Delete an idea space (marks as deleted, data preserved)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM spaces WHERE id = ?", (space_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Space not found")

        conn.execute(
            "UPDATE spaces SET status = 'deleted', updated_at = datetime('now') WHERE id = ?",
            (space_id,),
        )
        _clear_active_space_if_matches(conn, space_id)
        conn.commit()
        return {"status": "deleted", "space_id": space_id}
    finally:
        conn.close()
