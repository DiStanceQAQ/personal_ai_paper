"""API routes for knowledge cards."""

import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from paper_engine.storage.database import get_connection

router = APIRouter(prefix="/api/cards", tags=["cards"])

CARD_TYPES = [
    "Problem", "Claim", "Evidence", "Method",
    "Object", "Variable", "Metric", "Result",
    "Failure Mode", "Interpretation", "Limitation", "Practical Tip",
]


def _card_row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _get_active_space_id() -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT s.id
               FROM spaces s
               JOIN app_state a ON a.value = s.id
               WHERE a.key = ? AND s.status = 'active'""",
            ("active_space",),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=400,
                detail="No active space selected.",
            )
        return str(row["id"])
    finally:
        conn.close()


@router.post("")
async def create_card(
    paper_id: str = Body(...),
    card_type: str = Body(...),
    summary: str = Body(""),
    source_passage_id: str | None = Body(None),
    confidence: float = Body(1.0),
) -> dict[str, Any]:
    """Create a new knowledge card."""
    if card_type not in CARD_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid card type. Must be one of: {', '.join(CARD_TYPES)}",
        )

    space_id = _get_active_space_id()
    card_id = str(uuid.uuid4())

    conn = get_connection()
    try:
        # Verify paper exists in the space
        paper = conn.execute(
            "SELECT id FROM papers WHERE id = ? AND space_id = ?",
            (paper_id, space_id),
        ).fetchone()
        if paper is None:
            raise HTTPException(status_code=404, detail="Paper not found in active space")

        if source_passage_id is not None:
            source = conn.execute(
                """SELECT id FROM passages
                   WHERE id = ? AND paper_id = ? AND space_id = ?""",
                (source_passage_id, paper_id, space_id),
            ).fetchone()
            if source is None:
                raise HTTPException(
                    status_code=422,
                    detail="source_passage_id must belong to the same paper and active space",
                )

        conn.execute(
            """INSERT INTO knowledge_cards
               (id, space_id, paper_id, source_passage_id, card_type, summary,
                confidence, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'user')""",
            (
                card_id,
                space_id,
                paper_id,
                source_passage_id,
                card_type,
                summary,
                confidence,
            ),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM knowledge_cards WHERE id = ?", (card_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="Failed to create card")
        return _card_row_to_dict(row)
    finally:
        conn.close()


@router.get("")
async def list_cards(
    paper_id: str | None = None,
    card_type: str | None = None,
    space_id_override: str | None = None,
) -> list[dict[str, Any]]:
    """List knowledge cards in the active space, optionally filtered."""
    if space_id_override is None:
        space_id_override = _get_active_space_id()

    query = "SELECT * FROM knowledge_cards WHERE space_id = ?"
    params: list[Any] = [space_id_override]

    if paper_id:
        query += " AND paper_id = ?"
        params.append(paper_id)
    if card_type:
        query += " AND card_type = ?"
        params.append(card_type)

    query += " ORDER BY created_at DESC"

    conn = get_connection()
    try:
        rows = conn.execute(query, params).fetchall()
        return [_card_row_to_dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/{card_id}")
async def get_card(card_id: str) -> dict[str, Any]:
    """Get a single knowledge card by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_cards WHERE id = ?", (card_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Card not found")
        return _card_row_to_dict(row)
    finally:
        conn.close()


@router.patch("/{card_id}")
async def update_card(
    card_id: str,
    summary: str | None = Body(None),
    card_type: str | None = Body(None),
    confidence: float | None = Body(None),
) -> dict[str, Any]:
    """Update a knowledge card."""
    if card_type is not None and card_type not in CARD_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid card type: {card_type}")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_cards WHERE id = ?", (card_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Card not found")

        updates: list[str] = []
        params: list[Any] = []

        if summary is not None:
            updates.append("summary = ?")
            params.append(summary)
        if card_type is not None:
            updates.append("card_type = ?")
            params.append(card_type)
        if confidence is not None:
            updates.append("confidence = ?")
            params.append(confidence)

        if updates:
            updates.append("user_edited = 1")
            updates.append("created_by = 'user'")
            updates.append("updated_at = datetime('now')")
            params.append(card_id)
            conn.execute(
                f"UPDATE knowledge_cards SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
    finally:
        conn.close()

    return await get_card(card_id)


@router.delete("/{card_id}")
async def delete_card(card_id: str) -> dict[str, str]:
    """Delete a knowledge card."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM knowledge_cards WHERE id = ?", (card_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Card not found")

        conn.execute("DELETE FROM knowledge_cards WHERE id = ?", (card_id,))
        conn.commit()
        return {"status": "deleted", "card_id": card_id}
    finally:
        conn.close()
