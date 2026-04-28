"""HTTP routes for literature search."""

from typing import Any, Literal

from fastapi import APIRouter, Query

from paper_engine.retrieval import service

SearchModeParam = Literal["fts", "hybrid"]

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
async def search_literature(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    mode: SearchModeParam | None = Query(None),
) -> list[dict[str, Any]]:
    return await service.search_literature(q=q, space_id=None, limit=limit, mode=mode)
