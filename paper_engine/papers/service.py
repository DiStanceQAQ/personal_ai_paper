"""API routes for importing and managing papers."""

import hashlib
import json
import os
import uuid
import traceback
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from paper_engine.core import config
from paper_engine.storage.database import get_connection
from paper_engine.analysis.jobs import cancel_analysis_run as cancel_analysis_run_job
from paper_engine.analysis.jobs import queue_analysis_run
from paper_engine.cards.service import CARD_TYPES
from paper_engine.pdf.jobs import queue_parse_run
from paper_engine.pdf.settings import get_parser_settings
from paper_engine.papers.metadata import (
    CORE_METADATA_FIELDS,
    filename_fallback_title,
    mark_user_edited_metadata_fields,
)
from paper_engine.retrieval.lexical import FTS_TABLE

router = APIRouter(prefix="/api/papers", tags=["papers"])

ACTIVE_SPACE_KEY = "active_space"
RELATION_TO_IDEA_VALUES = {
    "supports",
    "refutes",
    "inspires",
    "baseline",
    "method_source",
    "background",
    "result_comparison",
    "unclassified",
}
DEFAULT_BATCH_UPLOAD_MAX_FILES = 20
DEFAULT_UPLOAD_MAX_BYTES = 200 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
BATCH_UPLOAD_MAX_FILES_ENV = "PAPER_ENGINE_BATCH_UPLOAD_MAX_FILES"
UPLOAD_MAX_BYTES_ENV = "PAPER_ENGINE_UPLOAD_MAX_BYTES"


def _get_active_space_id_from_conn(conn: Any) -> str:
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


def _get_active_space_id() -> str:
    """Get the currently active space ID, or raise 400."""
    conn = get_connection()
    try:
        return _get_active_space_id_from_conn(conn)
    finally:
        conn.close()


def _resolve_active_space_scope(space_id: str | None = None) -> str:
    active_space_id = _get_active_space_id()
    if space_id is not None and space_id != active_space_id:
        raise HTTPException(
            status_code=403,
            detail="Access is limited to the active space.",
        )
    return active_space_id


def _paper_row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _require_paper_in_space(conn: Any, paper_id: str, space_id: str) -> None:
    row = conn.execute(
        "SELECT id FROM papers WHERE id = ? AND space_id = ?",
        (paper_id, space_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Paper not found in active space")


def _get_paper_in_active_space(conn: Any, paper_id: str) -> Any:
    row = conn.execute(
        "SELECT * FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Paper not found")

    active_space_id = _get_active_space_id_from_conn(conn)
    if str(row["space_id"]) != active_space_id:
        raise HTTPException(status_code=404, detail="Paper not found")
    return row


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


def _positive_int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _batch_upload_max_files() -> int:
    return _positive_int_env(BATCH_UPLOAD_MAX_FILES_ENV, DEFAULT_BATCH_UPLOAD_MAX_FILES)


def _upload_max_bytes() -> int:
    return _positive_int_env(UPLOAD_MAX_BYTES_ENV, DEFAULT_UPLOAD_MAX_BYTES)


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


def _analysis_run_row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _embedding_run_row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _card_row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _validate_card_type(card_type: str) -> None:
    if card_type not in CARD_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid card type. Must be one of: {', '.join(CARD_TYPES)}",
        )


def _validate_confidence(confidence: float) -> None:
    if not 0.0 <= confidence <= 1.0:
        raise HTTPException(
            status_code=422,
            detail="confidence must be between 0 and 1",
        )


def _get_card_for_paper(
    conn: Any,
    *,
    paper_id: str,
    space_id: str,
    card_id: str,
) -> Any:
    row = conn.execute(
        """
        SELECT *
        FROM knowledge_cards
        WHERE id = ?
          AND paper_id = ?
          AND space_id = ?
        """,
        (card_id, paper_id, space_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return row


def _queue_parse_for_paper(
    conn: Any,
    *,
    paper_id: str,
    space_id: str,
) -> tuple[str, str]:
    settings = get_parser_settings(conn)
    parser_config = {
        "parser_backend": settings.pdf_parser_backend,
        "mineru_base_url": settings.mineru_base_url,
        "worker_version": "pdf-parser-selection-v1",
    }
    parse_run_id = queue_parse_run(
        conn,
        paper_id=paper_id,
        space_id=space_id,
        parser_backend=settings.pdf_parser_backend,
        parser_config=parser_config,
        commit=False,
    )
    return parse_run_id, settings.pdf_parser_backend


@router.post("/upload")
async def upload_paper(file: UploadFile) -> dict[str, Any]:
    """Upload a PDF paper to the active space."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400, detail="Only PDF files are accepted"
        )

    space_id = _get_active_space_id()
    return await _store_uploaded_pdf(file, space_id=space_id)


async def upload_papers_batch(files: list[UploadFile]) -> dict[str, Any]:
    """Upload multiple PDF papers to the active space with per-file results."""
    if not files:
        raise HTTPException(status_code=400, detail="At least one PDF file is required")
    max_files = _batch_upload_max_files()
    if len(files) > max_files:
        raise HTTPException(
            status_code=413,
            detail=f"Batch upload accepts at most {max_files} files.",
        )

    space_id = _get_active_space_id()
    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0

    for file in files:
        filename = file.filename or ""
        if not filename.lower().endswith(".pdf"):
            failed += 1
            results.append(
                {
                    "filename": filename,
                    "status": "failed",
                    "error": "Only PDF files are accepted",
                }
            )
            continue

        try:
            paper = await _store_uploaded_pdf(file, space_id=space_id)
        except Exception as exc:
            failed += 1
            results.append(
                {
                    "filename": filename,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            continue

        succeeded += 1
        results.append(
            {
                "filename": filename,
                "status": "success",
                "paper": paper,
            }
        )

    return {
        "total": len(files),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


async def _store_uploaded_pdf(file: UploadFile, *, space_id: str) -> dict[str, Any]:
    """Store one uploaded PDF and queue a parse run."""
    paper_id = str(uuid.uuid4())
    papers_dir = _papers_dir(space_id)
    dest_path = papers_dir / f"{paper_id}.pdf"
    try:
        await _write_upload_file(file, dest_path)
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise

    file_hash = _compute_sha256(dest_path)
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id, file_path FROM papers WHERE space_id = ? AND file_hash = ?",
            (space_id, file_hash),
        ).fetchone()
        if existing is not None:
            dest_path.unlink(missing_ok=True)
            parse_run_id, _backend = _queue_parse_for_paper(
                conn,
                paper_id=str(existing["id"]),
                space_id=space_id,
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM papers WHERE id = ?", (existing["id"],)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=500, detail="Failed to load paper")
            result = _paper_row_to_dict(row)
            result["queued_parse_run_id"] = parse_run_id
            return result

        fallback_title = filename_fallback_title(file.filename)
        metadata_sources = {"title": "filename.fallback"} if fallback_title else {}
        metadata_confidence = {"title": 0.1} if fallback_title else {}
        conn.execute(
            """INSERT INTO papers (
                   id, space_id, title, file_path, file_hash, parse_status,
                   embedding_status, metadata_sources_json, metadata_confidence_json
               )
               VALUES (?, ?, ?, ?, ?, 'pending', 'pending', ?, ?)""",
            (
                paper_id,
                space_id,
                fallback_title,
                str(dest_path),
                file_hash,
                json.dumps(metadata_sources, ensure_ascii=False, sort_keys=True),
                json.dumps(metadata_confidence, ensure_ascii=False, sort_keys=True),
            ),
        )
        parse_run_id, _backend = _queue_parse_for_paper(
            conn,
            paper_id=paper_id,
            space_id=space_id,
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="Failed to create paper record")
        result = _paper_row_to_dict(row)
        result["queued_parse_run_id"] = parse_run_id
        return result
    finally:
        conn.close()


async def _write_upload_file(file: UploadFile, dest_path: Path) -> None:
    """Write an upload in chunks while enforcing the per-file size limit."""
    max_bytes = _upload_max_bytes()
    written = 0
    with open(dest_path, "wb") as destination:
        while True:
            chunk = await file.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "PDF file is too large. "
                        f"Maximum allowed size is {max_bytes} bytes."
                    ),
                )
            destination.write(chunk)


@router.get("")
async def list_papers(space_id: str | None = None) -> list[dict[str, Any]]:
    """List papers in a space. Defaults to active space."""
    space_id = _resolve_active_space_scope(space_id)

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
        row = _get_paper_in_active_space(conn, paper_id)
        return _paper_row_to_dict(row)
    finally:
        conn.close()


async def get_paper_pdf(paper_id: str) -> FileResponse:
    """Stream the original PDF for a paper in the active space."""
    conn = get_connection()
    try:
        row = _get_paper_in_active_space(conn, paper_id)
        file_path = Path(str(row["file_path"]))
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="PDF file not found")
        if file_path.suffix.lower() != ".pdf":
            raise HTTPException(status_code=415, detail="Stored file is not a PDF")

        filename = f"{row['title'] or row['id']}.pdf"
        return FileResponse(
            file_path,
            media_type="application/pdf",
            filename=filename,
            content_disposition_type="inline",
        )
    finally:
        conn.close()


async def get_paper_metadata(paper_id: str) -> dict[str, Any]:
    """Return core paper metadata with parsed provenance fields."""
    conn = get_connection()
    try:
        row = _get_paper_in_active_space(conn, paper_id)
        return {
            "paper_id": row["id"],
            "space_id": row["space_id"],
            "title": row["title"],
            "authors": row["authors"],
            "year": row["year"],
            "doi": row["doi"],
            "arxiv_id": row["arxiv_id"],
            "pubmed_id": row["pubmed_id"],
            "venue": row["venue"],
            "abstract": row["abstract"],
            "parse_status": row["parse_status"],
            "metadata_status": row["metadata_status"],
            "metadata_sources": _json_object(row["metadata_sources_json"]),
            "metadata_confidence": _json_object(row["metadata_confidence_json"]),
            "user_edited_fields": _json_list(row["user_edited_fields_json"]),
        }
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
                last_error,
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


async def list_embedding_runs(paper_id: str) -> list[dict[str, Any]]:
    """List embedding runs for a paper in the active space."""
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        rows = conn.execute(
            """
            SELECT *
            FROM embedding_runs
            WHERE paper_id = ?
              AND space_id = ?
            ORDER BY started_at DESC, id DESC
            """,
            (paper_id, space_id),
        ).fetchall()
        return [_embedding_run_row_to_dict(row) for row in rows]
    finally:
        conn.close()


async def create_analysis_run(paper_id: str) -> dict[str, Any]:
    """Queue a background AI analysis run for a parsed paper."""
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT parse_status FROM papers WHERE id = ? AND space_id = ?",
            (paper_id, space_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Paper not found in active space")
        if row["parse_status"] != "parsed":
            raise HTTPException(
                status_code=409,
                detail="PDF parsing has not completed yet. Please wait for parsing to finish.",
            )

        passage = conn.execute(
            """
            SELECT 1
            FROM passages
            WHERE paper_id = ? AND space_id = ?
            LIMIT 1
            """,
            (paper_id, space_id),
        ).fetchone()
        if passage is None:
            raise HTTPException(
                status_code=409,
                detail="PDF parsing has not produced passages yet.",
            )

        analysis_run_id = queue_analysis_run(
            conn,
            paper_id=paper_id,
            space_id=space_id,
            commit=False,
        )
        conn.commit()
        run = conn.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND paper_id = ? AND space_id = ?",
            (analysis_run_id, paper_id, space_id),
        ).fetchone()
        if run is None:
            raise HTTPException(status_code=500, detail="Failed to create analysis run")
        return _analysis_run_row_to_dict(run)
    finally:
        conn.close()


async def list_analysis_runs(paper_id: str) -> list[dict[str, Any]]:
    """List AI analysis runs for a paper in the active space."""
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        rows = conn.execute(
            """
            SELECT *
            FROM analysis_runs
            WHERE paper_id = ?
              AND space_id = ?
            ORDER BY started_at DESC, id DESC
            """,
            (paper_id, space_id),
        ).fetchall()
        return [_analysis_run_row_to_dict(row) for row in rows]
    finally:
        conn.close()


async def get_analysis_run(paper_id: str, run_id: str) -> dict[str, Any]:
    """Get one AI analysis run for a paper in the active space."""
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        row = conn.execute(
            """
            SELECT *
            FROM analysis_runs
            WHERE id = ?
              AND paper_id = ?
              AND space_id = ?
            """,
            (run_id, paper_id, space_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Analysis run not found")
        return _analysis_run_row_to_dict(row)
    finally:
        conn.close()


async def cancel_analysis_run(paper_id: str, run_id: str) -> dict[str, Any]:
    """Cancel a queued or running AI analysis run."""
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        row = conn.execute(
            """
            SELECT *
            FROM analysis_runs
            WHERE id = ?
              AND paper_id = ?
              AND space_id = ?
            """,
            (run_id, paper_id, space_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Analysis run not found")
        if row["status"] in {"completed", "failed"}:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot cancel an analysis run with status {row['status']}.",
            )
        if row["status"] == "cancelled":
            return _analysis_run_row_to_dict(row)

        cancelled = cancel_analysis_run_job(
            conn,
            analysis_run_id=run_id,
            paper_id=paper_id,
            space_id=space_id,
            commit=False,
        )
        conn.commit()
        if cancelled is None:
            raise HTTPException(status_code=404, detail="Analysis run not found")
        if cancelled["status"] in {"completed", "failed"}:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot cancel an analysis run with status {cancelled['status']}.",
            )
        return _analysis_run_row_to_dict(cancelled)
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
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
    if (
        relation_to_idea is not None
        and relation_to_idea not in RELATION_TO_IDEA_VALUES
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "relation_to_idea must be one of: "
                + ", ".join(sorted(RELATION_TO_IDEA_VALUES))
            ),
        )

    conn = get_connection()
    try:
        row = _get_paper_in_active_space(conn, paper_id)
        space_id = str(row["space_id"])

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
            edited_metadata_fields = [
                field
                for field, value in field_map.items()
                if value is not None and field in CORE_METADATA_FIELDS
            ]
            params.append(paper_id)
            params.append(space_id)
            conn.execute(
                f"UPDATE papers SET {', '.join(updates)} WHERE id = ? AND space_id = ?",
                params,
            )
            mark_user_edited_metadata_fields(
                conn,
                paper_id=paper_id,
                space_id=space_id,
                fields=edited_metadata_fields,
            )
            conn.commit()
    finally:
        conn.close()

    return await get_paper(paper_id)


@router.post("/{paper_id}/parse")
async def parse_paper(paper_id: str) -> dict[str, Any]:
    """Trigger PDF parsing for a paper."""
    conn = get_connection()
    space_id: str | None = None
    try:
        row = _get_paper_in_active_space(conn, paper_id)
        space_id = str(row["space_id"])

        file_path = Path(row["file_path"])
        if not file_path.exists():
            conn.execute(
                "UPDATE papers SET parse_status = 'error' WHERE id = ? AND space_id = ?",
                (paper_id, space_id),
            )
            conn.commit()
            raise HTTPException(
                status_code=400, detail="PDF file not found on disk"
            )

        parse_run_id, backend = _queue_parse_for_paper(
            conn,
            paper_id=paper_id,
            space_id=space_id,
        )
        conn.commit()
        return _parse_response(
            status="queued",
            paper_id=paper_id,
            passage_count=0,
            parse_run_id=parse_run_id,
            backend=backend,
            quality_score=None,
            warnings=[],
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error parsing paper {paper_id}:")
        traceback.print_exc()
        conn.rollback()
        if space_id is not None:
            conn.execute(
                "UPDATE papers SET parse_status = 'error' WHERE id = ? AND space_id = ?",
                (paper_id, space_id),
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
        row = _get_paper_in_active_space(conn, paper_id)
        space_id = str(row["space_id"])

        rows = conn.execute(
            """SELECT *
               FROM passages
               WHERE paper_id = ? AND space_id = ?
               ORDER BY page_number, paragraph_index""",
            (paper_id, space_id),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def create_paper_card(
    *,
    paper_id: str,
    card_type: str,
    summary: str = "",
    source_passage_id: str | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    """Create a user-owned knowledge card scoped to one paper."""
    _validate_card_type(card_type)
    _validate_confidence(confidence)

    space_id = _get_active_space_id()
    card_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)

        if source_passage_id is not None:
            source = conn.execute(
                """
                SELECT id
                FROM passages
                WHERE id = ?
                  AND paper_id = ?
                  AND space_id = ?
                """,
                (source_passage_id, paper_id, space_id),
            ).fetchone()
            if source is None:
                raise HTTPException(
                    status_code=422,
                    detail="source_passage_id must belong to the same paper and active space",
                )

        conn.execute(
            """
            INSERT INTO knowledge_cards (
                id, space_id, paper_id, source_passage_id, card_type,
                summary, confidence, created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'user')
            """,
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

        row = _get_card_for_paper(
            conn,
            paper_id=paper_id,
            space_id=space_id,
            card_id=card_id,
        )
        return _card_row_to_dict(row)
    finally:
        conn.close()


async def list_paper_cards(
    paper_id: str,
    card_type: str | None = None,
) -> list[dict[str, Any]]:
    """List knowledge cards for one paper in the active space."""
    if card_type is not None:
        _validate_card_type(card_type)

    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        query = """
            SELECT *
            FROM knowledge_cards
            WHERE paper_id = ?
              AND space_id = ?
        """
        params: list[Any] = [paper_id, space_id]
        if card_type is not None:
            query += " AND card_type = ?"
            params.append(card_type)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_card_row_to_dict(row) for row in rows]
    finally:
        conn.close()


async def get_paper_card(paper_id: str, card_id: str) -> dict[str, Any]:
    """Get one knowledge card scoped to one paper."""
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        row = _get_card_for_paper(
            conn,
            paper_id=paper_id,
            space_id=space_id,
            card_id=card_id,
        )
        return _card_row_to_dict(row)
    finally:
        conn.close()


async def update_paper_card(
    *,
    paper_id: str,
    card_id: str,
    summary: str | None = None,
    card_type: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    """Update one knowledge card scoped to one paper."""
    if card_type is not None:
        _validate_card_type(card_type)
    if confidence is not None:
        _validate_confidence(confidence)

    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        _get_card_for_paper(
            conn,
            paper_id=paper_id,
            space_id=space_id,
            card_id=card_id,
        )

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
            updates.extend(
                [
                    "user_edited = 1",
                    "created_by = 'user'",
                    "updated_at = datetime('now')",
                ]
            )
            params.extend([card_id, paper_id, space_id])
            conn.execute(
                f"""
                UPDATE knowledge_cards
                SET {', '.join(updates)}
                WHERE id = ?
                  AND paper_id = ?
                  AND space_id = ?
                """,
                params,
            )
            conn.commit()

        row = _get_card_for_paper(
            conn,
            paper_id=paper_id,
            space_id=space_id,
            card_id=card_id,
        )
        return _card_row_to_dict(row)
    finally:
        conn.close()


async def delete_paper_card(paper_id: str, card_id: str) -> dict[str, str]:
    """Delete one knowledge card scoped to one paper."""
    space_id = _get_active_space_id()
    conn = get_connection()
    try:
        _require_paper_in_space(conn, paper_id, space_id)
        _get_card_for_paper(
            conn,
            paper_id=paper_id,
            space_id=space_id,
            card_id=card_id,
        )
        conn.execute(
            """
            DELETE FROM knowledge_cards
            WHERE id = ?
              AND paper_id = ?
              AND space_id = ?
            """,
            (card_id, paper_id, space_id),
        )
        conn.commit()
        return {"status": "deleted", "card_id": card_id}
    finally:
        conn.close()


@router.delete("/{paper_id}")
async def delete_paper(paper_id: str) -> dict[str, str]:
    """Delete a paper and all associated data."""
    conn = get_connection()
    try:
        row = _get_paper_in_active_space(conn, paper_id)

        file_path = Path(row["file_path"])
        space_id = str(row["space_id"])

        # 1. Clear linked data
        conn.execute(
            "DELETE FROM knowledge_cards WHERE paper_id = ? AND space_id = ?",
            (paper_id, space_id),
        )
        conn.execute(
            "DELETE FROM notes WHERE paper_id = ? AND space_id = ?",
            (paper_id, space_id),
        )
        conn.execute(
            f"DELETE FROM {FTS_TABLE} WHERE paper_id = ? AND space_id = ?",
            (paper_id, space_id),
        )
        conn.execute(
            "DELETE FROM passages WHERE paper_id = ? AND space_id = ?",
            (paper_id, space_id),
        )
        
        # 2. Delete the paper record
        conn.execute(
            "DELETE FROM papers WHERE id = ? AND space_id = ?",
            (paper_id, space_id),
        )
        conn.commit()

        # 3. Delete the file from disk
        if file_path.exists():
            file_path.unlink()

        return {"status": "deleted", "paper_id": paper_id}
    finally:
        conn.close()
