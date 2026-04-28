"""HTTP routes for managing idea spaces."""

from typing import Any

from fastapi import APIRouter, Body

from paper_engine.spaces import service

router = APIRouter(prefix="/api/spaces", tags=["spaces"])


@router.post("")
async def create_space(
    name: str = Body(...),
    description: str = Body(""),
) -> dict[str, Any]:
    return await service.create_space(name=name, description=description)


@router.get("")
async def list_spaces() -> list[dict[str, Any]]:
    return await service.list_spaces()


@router.get("/active")
async def get_active_space() -> dict[str, Any]:
    return await service.get_active_space()


@router.put("/active/{space_id}")
async def set_active_space(space_id: str) -> dict[str, Any]:
    return await service.set_active_space(space_id)


@router.get("/{space_id}")
async def get_space(space_id: str) -> dict[str, Any]:
    return await service.get_space(space_id)


@router.patch("/{space_id}")
async def update_space(
    space_id: str,
    name: str | None = Body(None),
    description: str | None = Body(None),
) -> dict[str, Any]:
    return await service.update_space(
        space_id=space_id,
        name=name,
        description=description,
    )


@router.patch("/{space_id}/archive")
async def archive_space(space_id: str) -> dict[str, str]:
    return await service.archive_space(space_id)


@router.delete("/{space_id}")
async def delete_space(space_id: str) -> dict[str, str]:
    return await service.delete_space(space_id)
