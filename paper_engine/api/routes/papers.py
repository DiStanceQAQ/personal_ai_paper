"""API routes for importing and managing papers."""

import hashlib
import uuid
import traceback
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, UploadFile

from paper_engine.core import config
from paper_engine.storage.database import get_connection
from pdf_backend_base import ParserBackendError
from pdf_persistence import embed_passages_for_parse_run
from parser import (
    chunk_parse_document,
    inspect_pdf,
    persist_parse_result,
    route_parse,
)
from search import FTS_TABLE

router = APIRouter(prefix="/api/papers", tags=["papers"])

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


def _paper_row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _require_paper_in_space(conn: Any, paper_id: str, space_id: str) -> None:
    row = conn.execute(
        "SELECT id FROM papers WHERE id = ? AND space_id = ?",
        (paper_id, space_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Paper not found in active space")


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _papers_dir(space_id: str) -> Path:
    """Get the papers directory for a space, creating it if needed."""
    p = config.SPACES_DIR / space_id / "papers"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _parse_response(
    *,
    status: str,
    paper_id: str,
    passage_count: int,
    parse_run_id: str | None,
    backend: str | None,
    quality_score: float | None,
    warnings: list[str] | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "paper_id": paper_id,
        "passage_count": passage_count,
        "parse_run_id": parse_run_id,
        "backend": backend,
        "quality_score": quality_score,
        "warnings": warnings or [],
    }


def _parser_error_warnings(
    warnings: list[str] | None,
    exc: BaseException,
) -> list[str]:
    merged = list(warnings or [])
    error_detail = " ".join(str(exc).split())
    if error_detail:
        merged.append(f"parser_error:{error_detail}")
    return merged


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


@router.get("/{paper_id}/parse-runs")
async def list_parse_runs(paper_id: str) -> list[dict[str, Any]]:
    """List parse runs for a paper in the active space."""
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        rows = conn.execute(
            """
            SELECT
                id,
                paper_id,
                space_id,
                backend,
                extraction_method,
                status,
                quality_score,
                started_at,
                started_at AS created_at,
                completed_at,
                warnings_json,
                config_json,
                metadata_json
            FROM parse_runs
            WHERE paper_id = ?
              AND space_id = ?
            ORDER BY started_at DESC, id DESC
            """,
            (paper_id, space_id),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/{paper_id}/elements")
async def list_document_elements(
    paper_id: str,
    element_type: str | None = Query(None, alias="type"),
    page: int | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """List structured document elements for a paper in the active space."""
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)

        query = """
            SELECT *
            FROM document_elements
            WHERE paper_id = ?
              AND space_id = ?
        """
        params: list[Any] = [paper_id, space_id]
        if element_type is not None:
            query += " AND element_type = ?"
            params.append(element_type)
        if page is not None:
            query += " AND page_number = ?"
            params.append(page)
        query += " ORDER BY element_index LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/{paper_id}/tables")
async def list_document_tables(paper_id: str) -> list[dict[str, Any]]:
    """List structured document tables for a paper in the active space."""
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        rows = conn.execute(
            """
            SELECT *
            FROM document_tables
            WHERE paper_id = ?
              AND space_id = ?
            ORDER BY page_number, table_index
            """,
            (paper_id, space_id),
        ).fetchall()
        return [dict(r) for r in rows]
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
        params: list[Any] = []

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
        quality = inspect_pdf(file_path)
        try:
            document = route_parse(file_path, paper_id, space_id, quality)
        except ParserBackendError as exc:
            conn.rollback()
            conn.execute(
                "UPDATE papers SET parse_status = 'error' WHERE id = ?",
                (paper_id,),
            )
            conn.commit()
            return _parse_response(
                status="error",
                paper_id=paper_id,
                passage_count=0,
                parse_run_id=None,
                backend=None,
                quality_score=quality.quality_score,
                warnings=_parser_error_warnings(quality.warnings, exc),
            )

        passages = chunk_parse_document(document)

        if not passages:
            conn.rollback()
            conn.execute(
                "UPDATE papers SET parse_status = 'error' WHERE id = ?",
                (paper_id,),
            )
            conn.commit()
            return _parse_response(
                status="error",
                paper_id=paper_id,
                passage_count=0,
                parse_run_id=None,
                backend=document.backend,
                quality_score=document.quality.quality_score,
                warnings=document.quality.warnings,
            )

        parse_run_id = persist_parse_result(
            conn,
            paper_id,
            space_id,
            document,
            passages,
        )
        embedding_warnings = embed_passages_for_parse_run(conn, parse_run_id)

        conn.execute(
            "UPDATE papers SET parse_status = 'parsed' WHERE id = ?",
            (paper_id,),
        )
        conn.commit()

        return _parse_response(
            status="parsed",
            paper_id=paper_id,
            passage_count=len(passages),
            parse_run_id=parse_run_id,
            backend=document.backend,
            quality_score=document.quality.quality_score,
            warnings=[*document.quality.warnings, *embedding_warnings],
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error parsing paper {paper_id}:")
        traceback.print_exc()
        conn.rollback()
        conn.execute(
            "UPDATE papers SET parse_status = 'error' WHERE id = ?",
            (paper_id,),
        )
        conn.commit()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.get("/{paper_id}/passages")
async def list_passages(paper_id: str) -> list[dict[str, Any]]:
    """List all passages for a paper."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Paper not found")

        rows = conn.execute(
            "SELECT * FROM passages WHERE paper_id = ? ORDER BY page_number, paragraph_index",
            (paper_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.delete("/{paper_id}")
async def delete_paper(paper_id: str) -> dict[str, str]:
    """Delete a paper and all associated data."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT file_path FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Paper not found")

        file_path = Path(row["file_path"])

        # 1. Clear linked data
        conn.execute("DELETE FROM knowledge_cards WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM notes WHERE paper_id = ?", (paper_id,))
        conn.execute(f"DELETE FROM {FTS_TABLE} WHERE paper_id = ?", (paper_id,))
        conn.execute("DELETE FROM passages WHERE paper_id = ?", (paper_id,))
        
        # 2. Delete the paper record
        conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        conn.commit()

        # 3. Delete the file from disk
        if file_path.exists():
            file_path.unlink()

        return {"status": "deleted", "paper_id": paper_id}
    finally:
        conn.close()
