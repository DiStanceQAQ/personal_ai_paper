"""HTTP routes for knowledge cards."""

from typing import Any

from fastapi import APIRouter, Body

from paper_engine.cards import service

router = APIRouter(prefix="/api/cards", tags=["cards"])
CARD_TYPES = service.CARD_TYPES


@router.post("")
async def create_card(
    paper_id: str = Body(...),
    card_type: str = Body(...),
    summary: str = Body(""),
    source_passage_id: str | None = Body(None),
    confidence: float = Body(1.0),
) -> dict[str, Any]:
    return await service.create_card(
        paper_id=paper_id,
        card_type=card_type,
        summary=summary,
        source_passage_id=source_passage_id,
        confidence=confidence,
    )


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


@router.patch("/{card_id}")
async def update_card(
    card_id: str,
    summary: str | None = Body(None),
    card_type: str | None = Body(None),
    confidence: float | None = Body(None),
) -> dict[str, Any]:
    return await service.update_card(
        card_id=card_id,
        summary=summary,
        card_type=card_type,
        confidence=confidence,
    )


@router.delete("/{card_id}")
async def delete_card(card_id: str) -> dict[str, str]:
    return await service.delete_card(card_id)
