"""API routes for literature search."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from paper_engine.storage.database import get_connection
from search import search_passages

SearchModeParam = Literal["fts", "hybrid"]

router = APIRouter(prefix="/api/search", tags=["search"])

ACTIVE_SPACE_KEY = "active_space"


def _get_active_space_id() -> str:
    """Get the currently active space ID, or raise 400."""
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
            raise HTTPException(
                status_code=400,
                detail="No active space selected. Please open an active space first.",
            )
        return str(row["id"])
    finally:
        conn.close()


@router.get("")
async def search_literature(
    q: str = Query(..., min_length=1, description="Search query"),
    space_id: str | None = Query(None, description="Space ID (defaults to active space)"),
    limit: int = Query(50, ge=1, le=200),
    mode: SearchModeParam | None = Query(
        None,
        description="Search mode: fts or hybrid. Defaults based on embedding availability.",
    ),
) -> list[dict[str, Any]]:
    """Full-text search across passages in a space."""
    if space_id is None:
        space_id = _get_active_space_id()

    results = search_passages(q, space_id, limit, mode=mode)
    return results
