"""Service helpers for literature search."""

from collections.abc import Sequence
from datetime import datetime, timezone
import math
import threading
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from paper_engine.storage.database import get_connection
from paper_engine.retrieval.embeddings import (
    EmbeddingConfig,
    EmbeddingProvider,
    format_embedding_texts,
    get_embedding_config,
    get_embedding_provider,
)
from paper_engine.retrieval.lexical import search_passages
from paper_engine.retrieval.vector_index import semantic_search_with_sqlite_vec

SearchModeParam = Literal["fts", "hybrid"]
SearchWarmupStatus = Literal["idle", "warming", "ready", "failed", "skipped"]

router = APIRouter(prefix="/api/search", tags=["search"])

ACTIVE_SPACE_KEY = "active_space"
WARMUP_QUERY = "semantic search warmup"

_SEARCH_WARMUP_LOCK = threading.Lock()
_SEARCH_WARMUP_STATE: dict[str, dict[str, Any]] = {}


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


def _resolve_search_space_id(space_id: str | None) -> str:
    active_space_id = _get_active_space_id()
    if space_id is not None and space_id != active_space_id:
        raise HTTPException(
            status_code=403,
            detail="Search is limited to the active space.",
        )
    return active_space_id


def get_search_warmup_status(space_id: str | None = None) -> dict[str, Any]:
    """Return semantic-search warmup state for the active space."""
    resolved_space_id = _resolve_search_space_id(space_id)
    signature, has_embeddings = _current_warmup_context(resolved_space_id)

    with _SEARCH_WARMUP_LOCK:
        state = _SEARCH_WARMUP_STATE.get(signature)
        if state is not None:
            return dict(state)

    if not has_embeddings:
        return _warmup_state(
            space_id=resolved_space_id,
            status="skipped",
            message="当前空间还没有语义索引，深度检索会退回关键词结果。",
        )

    return _warmup_state(
        space_id=resolved_space_id,
        status="idle",
        message="语义检索尚未预热。",
    )


def start_search_warmup(space_id: str | None = None) -> dict[str, Any]:
    """Start a non-blocking semantic-search warmup job if needed."""
    resolved_space_id = _resolve_search_space_id(space_id)
    signature, has_embeddings = _current_warmup_context(resolved_space_id)

    if not has_embeddings:
        state = _warmup_state(
            space_id=resolved_space_id,
            status="skipped",
            message="当前空间还没有语义索引，深度检索会退回关键词结果。",
        )
        with _SEARCH_WARMUP_LOCK:
            _SEARCH_WARMUP_STATE[signature] = state
        return dict(state)

    with _SEARCH_WARMUP_LOCK:
        existing = _SEARCH_WARMUP_STATE.get(signature)
        if existing is not None and existing["status"] in {"warming", "ready"}:
            return dict(existing)

        state = _warmup_state(
            space_id=resolved_space_id,
            status="warming",
            message="正在准备语义模型和向量索引。",
            started_at=_utc_now(),
        )
        _SEARCH_WARMUP_STATE[signature] = state

    thread = threading.Thread(
        target=_run_search_warmup,
        args=(resolved_space_id, signature),
        daemon=True,
    )
    thread.start()
    return dict(state)


def _current_warmup_context(space_id: str) -> tuple[str, bool]:
    conn = get_connection()
    try:
        config = get_embedding_config(conn)
        has_embeddings = _space_has_embeddings(conn, space_id)
    finally:
        conn.close()
    return _warmup_signature(space_id, config), has_embeddings


def _space_has_embeddings(conn: Any, space_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM passage_embeddings pe
        JOIN passages p ON p.id = pe.passage_id
        WHERE p.space_id = ?
        LIMIT 1
        """,
        (space_id,),
    ).fetchone()
    return row is not None


def _run_search_warmup(space_id: str, signature: str) -> None:
    started_at = time.perf_counter()
    provider: EmbeddingProvider | None = None
    try:
        conn = get_connection()
        try:
            if not _space_has_embeddings(conn, space_id):
                _set_warmup_state(
                    signature,
                    _warmup_state(
                        space_id=space_id,
                        status="skipped",
                        message="当前空间还没有语义索引，深度检索会退回关键词结果。",
                        completed_at=_utc_now(),
                        elapsed_ms=_elapsed_ms(started_at),
                    ),
                )
                return

            config = get_embedding_config(conn)
            provider = get_embedding_provider(config)
            if not provider.is_configured():
                raise RuntimeError("Embedding provider is not configured.")

            vector = _embed_warmup_query(provider)
            semantic_search_with_sqlite_vec(
                conn,
                query_vector=vector,
                space_id=space_id,
                provider=provider.provider,
                model=provider.model,
                limit=1,
            )
        finally:
            if provider is not None:
                _close_provider(provider)
            conn.close()

        _set_warmup_state(
            signature,
            _warmup_state(
                space_id=space_id,
                status="ready",
                message="语义检索已准备好。",
                completed_at=_utc_now(),
                elapsed_ms=_elapsed_ms(started_at),
            ),
        )
    except Exception as exc:
        _set_warmup_state(
            signature,
            _warmup_state(
                space_id=space_id,
                status="failed",
                message=f"语义检索预热失败：{_compact_error(exc)}",
                completed_at=_utc_now(),
                elapsed_ms=_elapsed_ms(started_at),
            ),
        )


def _embed_warmup_query(provider: EmbeddingProvider) -> list[float]:
    formatted = format_embedding_texts(
        [WARMUP_QUERY],
        model=provider.model,
        input_type="query",
    )
    vectors = provider.embed_texts(formatted)
    if not vectors:
        raise RuntimeError("Embedding provider returned no warmup vector.")
    vector = _coerce_vector(vectors[0])
    if not vector:
        raise RuntimeError("Embedding provider returned an invalid warmup vector.")
    return vector


def _warmup_signature(space_id: str, config: EmbeddingConfig) -> str:
    return "|".join(
        [
            space_id,
            config.provider,
            config.base_url,
            config.model,
            str(config.dimension or ""),
        ]
    )


def _warmup_state(
    *,
    space_id: str,
    status: SearchWarmupStatus,
    message: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "space_id": space_id,
        "status": status,
        "message": message,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_ms": elapsed_ms,
    }


def _set_warmup_state(signature: str, state: dict[str, Any]) -> None:
    with _SEARCH_WARMUP_LOCK:
        _SEARCH_WARMUP_STATE[signature] = state


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started_at: float) -> int:
    return max(0, math.floor((time.perf_counter() - started_at) * 1000))


def _coerce_vector(value: Sequence[float]) -> list[float]:
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return []


def _close_provider(provider: EmbeddingProvider) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        close()


def _compact_error(exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    return detail or exc.__class__.__name__


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
    space_id = _resolve_search_space_id(space_id)

    results = search_passages(q, space_id, limit, mode=mode)
    return results
