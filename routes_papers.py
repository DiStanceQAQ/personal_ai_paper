"""API routes for importing and managing papers."""

import hashlib
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, UploadFile

from config import SPACES_DIR
from db import get_connection
from parser import extract_passages_from_pdf

router = APIRouter(prefix="/api/papers", tags=["papers"])

ACTIVE_SPACE_KEY = "active_space"


def _get_active_space_id() -> str:
    """Get the currently active space ID, or raise 400."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (ACTIVE_SPACE_KEY,),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=400,
                detail="No active space selected. Please open a space first.",
            )
        return str(row["value"])
    finally:
        conn.close()


def _paper_row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _papers_dir(space_id: str) -> Path:
    """Get the papers directory for a space, creating it if needed."""
    p = SPACES_DIR / space_id / "papers"
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.post("/upload")
async def upload_paper(file: UploadFile) -> dict[str, Any]:
    """Upload a PDF paper to the active space."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400, detail="Only PDF files are accepted"
        )

    space_id = _get_active_space_id()

    # Read file content
    content = await file.read()

    # Save to temp file first to compute hash
    paper_id = str(uuid.uuid4())
    papers_dir = _papers_dir(space_id)
    dest_path = papers_dir / f"{paper_id}.pdf"

    with open(dest_path, "wb") as f:
        f.write(content)

    file_hash = _compute_sha256(dest_path)

    # Check for duplicate by hash in the same space
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id, file_path FROM papers WHERE space_id = ? AND file_hash = ?",
            (space_id, file_hash),
        ).fetchone()
        if existing is not None:
            # Remove the just-saved duplicate file
            dest_path.unlink()
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate PDF detected (paper id: {existing['id']}). "
                       "This PDF has already been imported in this space.",
            )

        conn.execute(
            """INSERT INTO papers (id, space_id, file_path, file_hash, parse_status)
               VALUES (?, ?, ?, ?, 'pending')""",
            (paper_id, space_id, str(dest_path), file_hash),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="Failed to create paper record")
        result = _paper_row_to_dict(row)
    finally:
        conn.close()

    return result


@router.get("")
async def list_papers(space_id: str | None = None) -> list[dict[str, Any]]:
    """List papers in a space. Defaults to active space."""
    if space_id is None:
        space_id = _get_active_space_id()

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM papers WHERE space_id = ? ORDER BY imported_at DESC",
            (space_id,),
        ).fetchall()
        return [_paper_row_to_dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/{paper_id}")
async def get_paper(paper_id: str) -> dict[str, Any]:
    """Get a single paper by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Paper not found")
        return _paper_row_to_dict(row)
    finally:
        conn.close()


@router.patch("/{paper_id}")
async def update_paper(
    paper_id: str,
    title: str | None = Body(None),
    authors: str | None = Body(None),
    year: int | None = Body(None),
    doi: str | None = Body(None),
    arxiv_id: str | None = Body(None),
    pubmed_id: str | None = Body(None),
    venue: str | None = Body(None),
    abstract: str | None = Body(None),
    citation: str | None = Body(None),
    user_tags: str | None = Body(None),
    relation_to_idea: str | None = Body(None),
) -> dict[str, Any]:
    """Update paper metadata fields."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Paper not found")

        field_map: dict[str, str | int | None] = {
            "title": title,
            "authors": authors,
            "year": year,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "pubmed_id": pubmed_id,
            "venue": venue,
            "abstract": abstract,
            "citation": citation,
            "user_tags": user_tags,
            "relation_to_idea": relation_to_idea,
        }

        updates: list[str] = []
        params: list[str | int] = []

        for field, value in field_map.items():
            if value is not None:
                updates.append(f"{field} = ?")
                params.append(value)

        if updates:
            params.append(paper_id)
            conn.execute(
                f"UPDATE papers SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
    finally:
        conn.close()

    return await get_paper(paper_id)


@router.post("/{paper_id}/parse")
async def parse_paper(paper_id: str) -> dict[str, Any]:
    """Trigger PDF parsing for a paper."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Paper not found")

        file_path = Path(row["file_path"])
        if not file_path.exists():
            conn.execute(
                "UPDATE papers SET parse_status = 'error' WHERE id = ?",
                (paper_id,),
            )
            conn.commit()
            raise HTTPException(
                status_code=400, detail="PDF file not found on disk"
            )

        # Set status to parsing
        conn.execute(
            "UPDATE papers SET parse_status = 'parsing' WHERE id = ?",
            (paper_id,),
        )
        conn.commit()

        space_id = str(row["space_id"])
        passages = extract_passages_from_pdf(file_path, paper_id, space_id)

        if not passages:
            # Parsing produced no passages
            conn.execute(
                "UPDATE papers SET parse_status = 'error' WHERE id = ?",
                (paper_id,),
            )
            conn.commit()
            return {"status": "error", "paper_id": paper_id, "passage_count": 0}

        # Insert passages
        for p in passages:
            conn.execute(
                """INSERT OR REPLACE INTO passages
                   (id, paper_id, space_id, section, page_number,
                    paragraph_index, original_text, parse_confidence, passage_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    p["id"], p["paper_id"], p["space_id"], p["section"],
                    p["page_number"], p["paragraph_index"], p["original_text"],
                    p["parse_confidence"], p["passage_type"],
                ),
            )

        conn.execute(
            "UPDATE papers SET parse_status = 'parsed' WHERE id = ?",
            (paper_id,),
        )
        conn.commit()

        return {
            "status": "parsed",
            "paper_id": paper_id,
            "passage_count": len(passages),
        }
    except HTTPException:
        raise
    except Exception:
        conn.execute(
            "UPDATE papers SET parse_status = 'error' WHERE id = ?",
            (paper_id,),
        )
        conn.commit()
        raise HTTPException(status_code=500, detail="PDF parsing failed")
    finally:
        conn.close()
