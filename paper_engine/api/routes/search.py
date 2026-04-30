"""HTTP routes for literature search."""

from typing import Any, Literal

from fastapi import APIRouter, Query

from paper_engine.retrieval import service

SearchModeParam = Literal["fts", "hybrid"]

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
async def search_literature(
    q: str = Query(..., min_length=1, description="Search query"),
    space_id: str | None = Query(None, description="Space ID (defaults to active space)"),
    limit: int = Query(50, ge=1, le=200),
    mode: SearchModeParam | None = Query(None),
) -> list[dict[str, Any]]:
    return await service.search_literature(
        q=q,
        space_id=space_id,
        limit=limit,
        mode=mode,
    )


@router.get("/warmup")
async def get_search_warmup_status(
    space_id: str | None = Query(None, description="Space ID (defaults to active space)"),
) -> dict[str, Any]:
    return service.get_search_warmup_status(space_id=space_id)


@router.post("/warmup")
async def start_search_warmup(
    space_id: str | None = Query(None, description="Space ID (defaults to active space)"),
) -> dict[str, Any]:
    return service.start_search_warmup(space_id=space_id)
