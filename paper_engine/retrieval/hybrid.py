"""Hybrid passage retrieval using FTS and stored embeddings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Literal, TypeAlias

from paper_engine.storage.database import get_connection
from paper_engine.retrieval.embeddings import EmbeddingProvider, get_embedding_config, get_embedding_provider

SearchMode: TypeAlias = Literal["fts", "hybrid"]

RRF_RANK_CONSTANT = 60
SNIPPET_LENGTH = 240


def has_semantic_embeddings(
    space_id: str,
    database_path: Path | None = None,
) -> bool:
    """Return whether a space has stored passage embeddings."""
    conn = get_connection(database_path)
    try:
        return _has_semantic_embeddings(conn, space_id)
    finally:
        conn.close()


def semantic_vector_search(
    query: str,
    space_id: str,
    limit: int = 50,
    database_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Search passages by cosine similarity against stored passage embeddings."""
    if limit <= 0:
        return []

    conn = get_connection(database_path)
    try:
        return _semantic_vector_search_with_connection(conn, query, space_id, limit)
    finally:
        conn.close()


def reciprocal_rank_fusion(
    *,
    fts_results: Sequence[Mapping[str, Any]],
    semantic_results: Sequence[Mapping[str, Any]],
    limit: int = 50,
    rank_constant: int = RRF_RANK_CONSTANT,
) -> list[dict[str, Any]]:
    """Fuse FTS and semantic rankings with reciprocal rank fusion."""
    if limit <= 0:
        return []

    scores: dict[str, float] = {}
    fused: dict[str, dict[str, Any]] = {}

    for rank, row in enumerate(fts_results, start=1):
        passage_id = _passage_id(row)
        if passage_id is None:
            continue
        fused.setdefault(passage_id, dict(row))
        fused[passage_id]["fts_rank"] = rank
        fused[passage_id]["fts_score"] = row.get("score")
        scores[passage_id] = scores.get(passage_id, 0.0) + _rrf(rank, rank_constant)

    for rank, row in enumerate(semantic_results, start=1):
        passage_id = _passage_id(row)
        if passage_id is None:
            continue
        existing = fused.setdefault(passage_id, dict(row))
        _merge_missing_fields(existing, row)
        existing["semantic_rank"] = rank
        existing["semantic_score"] = row.get("semantic_score", row.get("score"))
        scores[passage_id] = scores.get(passage_id, 0.0) + _rrf(rank, rank_constant)

    for passage_id, row in fused.items():
        row["rrf_score"] = scores[passage_id]
        row["score"] = row["rrf_score"]
        row["search_mode"] = "hybrid"

    return sorted(
        fused.values(),
        key=lambda row: (
            -float(row["rrf_score"]),
            _best_rank(row),
            str(row.get("passage_id", "")),
        ),
    )[:limit]


def _has_semantic_embeddings(conn: sqlite3.Connection, space_id: str) -> bool:
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


def _semantic_vector_search_with_connection(
    conn: sqlite3.Connection,
    query: str,
    space_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    provider: EmbeddingProvider | None = None
    try:
        config = get_embedding_config(conn)
        provider = get_embedding_provider(config)
        if not provider.is_configured():
            return []
        query_vectors = provider.embed_texts([query])
    except Exception:
        return []
    finally:
        if provider is not None:
            _close_provider(provider)

    if not query_vectors:
        return []
    query_vector = _coerce_vector(query_vectors[0])
    if not query_vector:
        return []

    rows = conn.execute(
        """
        SELECT
            pe.embedding_json,
            p.id AS passage_id,
            p.paper_id,
            p.section,
            p.page_number,
            p.paragraph_index,
            p.original_text,
            papers.title AS paper_title
        FROM passage_embeddings pe
        JOIN passages p ON p.id = pe.passage_id
        JOIN papers ON papers.id = p.paper_id
        WHERE p.space_id = ?
          AND pe.provider = ?
          AND pe.model = ?
        """,
        (space_id, provider.provider, provider.model),
    ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        passage_vector = _vector_from_json(row["embedding_json"])
        score = _cosine_similarity(query_vector, passage_vector)
        if score is None:
            continue
        results.append(
            {
                "score": score,
                "semantic_score": score,
                "passage_id": row["passage_id"],
                "paper_id": row["paper_id"],
                "section": row["section"],
                "page_number": row["page_number"],
                "paragraph_index": row["paragraph_index"],
                "snippet": _plain_snippet(str(row["original_text"])),
                "original_text": row["original_text"],
                "paper_title": row["paper_title"],
            }
        )

    results.sort(key=lambda row: (-float(row["semantic_score"]), str(row["passage_id"])))
    return results[:limit]


def _close_provider(provider: EmbeddingProvider) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        close()


def _passage_id(row: Mapping[str, Any]) -> str | None:
    value = row.get("passage_id")
    if value is None:
        return None
    return str(value)


def _merge_missing_fields(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if key not in target or target[key] in (None, ""):
            target[key] = value


def _rrf(rank: int, rank_constant: int) -> float:
    return 1.0 / (rank_constant + rank)


def _best_rank(row: Mapping[str, Any]) -> int:
    ranks = [
        int(row[key])
        for key in ("fts_rank", "semantic_rank")
        if isinstance(row.get(key), int)
    ]
    return min(ranks, default=10**9)


def _vector_from_json(value: Any) -> list[float]:
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return _coerce_vector(decoded)


def _coerce_vector(value: Any) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return []


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or not left:
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _plain_snippet(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= SNIPPET_LENGTH:
        return compact
    return f"{compact[: SNIPPET_LENGTH - 3].rstrip()}..."
