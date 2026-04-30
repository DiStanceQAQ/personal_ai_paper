"""Read-only HTTP routes for active-space knowledge cards."""

from typing import Any

from fastapi import APIRouter

from paper_engine.cards import service

router = APIRouter(prefix="/api/cards", tags=["cards"])
CARD_TYPES = service.CARD_TYPES


@router.get("")
async def list_cards(
    paper_id: str | None = None,
    card_type: str | None = None,
) -> list[dict[str, Any]]:
    return await service.list_cards(
        paper_id=paper_id,
        card_type=card_type,
    )


@router.get("/{card_id}")
async def get_card(card_id: str) -> dict[str, Any]:
    return await service.get_card(card_id)
