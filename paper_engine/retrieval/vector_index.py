"""Optional sqlite-vec helpers for accelerating passage embedding lookup."""

from __future__ import annotations

from collections.abc import Sequence
import json
import re
import sqlite3
from typing import Any

VECTOR_INDEX_TABLE_PREFIX = "passage_embedding_vec"
VECTOR_INDEX_METADATA_TABLE = "passage_embedding_vec_meta"
VECTOR_INDEX_TABLE_RE = re.compile(rf"^{VECTOR_INDEX_TABLE_PREFIX}_[0-9]+$")


def is_sqlite_vec_available(conn: sqlite3.Connection) -> bool:
    """Return whether sqlite-vec can be loaded for this connection."""
    try:
        _load_sqlite_vec(conn)
    except Exception:
        return False
    return True


def semantic_search_with_sqlite_vec(
    conn: sqlite3.Connection,
    *,
    query_vector: Sequence[float],
    space_id: str,
    provider: str,
    model: str,
    limit: int,
    candidate_passage_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Run vector search through sqlite-vec when the optional extension is usable."""
    if limit <= 0:
        return []
    vector = _coerce_vector(query_vector)
    if not vector:
        return []
    candidate_ids = _unique_non_empty(candidate_passage_ids)
    if candidate_passage_ids is not None and not candidate_ids:
        return []
    if conn.in_transaction:
        return []

    try:
        original_isolation_level = conn.isolation_level
        conn.isolation_level = None
        _load_sqlite_vec(conn)
        _ensure_metadata_table(conn)
        table_name = _ensure_vector_index(
            conn,
            dimension=len(vector),
            provider=provider,
            model=model,
        )
        _sync_vector_index(
            conn,
            table_name=table_name,
            space_id=space_id,
            provider=provider,
            model=model,
            dimension=len(vector),
            candidate_passage_ids=candidate_ids or None,
        )
        rows = _query_vector_index(
            conn,
            table_name=table_name,
            query_vector=vector,
            space_id=space_id,
            provider=provider,
            model=model,
            limit=limit,
            candidate_passage_ids=candidate_ids or None,
        )
    except Exception:
        return []
    finally:
        if "original_isolation_level" in locals():
            conn.isolation_level = original_isolation_level

    return [_row_to_result(row) for row in rows]


def upsert_passage_embedding_vector_index(
    conn: sqlite3.Connection,
    *,
    passage_id: str,
    provider: str,
    model: str,
    vector: Sequence[float],
) -> None:
    """Best-effort upsert of one stored passage embedding into sqlite-vec."""
    coerced = _coerce_vector(vector)
    if not coerced:
        return
    if conn.in_transaction:
        return
    try:
        original_isolation_level = conn.isolation_level
        conn.isolation_level = None
        _load_sqlite_vec(conn)
        _ensure_metadata_table(conn)
        table_name = _ensure_vector_index(
            conn,
            dimension=len(coerced),
            provider=provider,
            model=model,
        )
        _insert_vector_row(
            conn,
            table_name=table_name,
            rowid=_stable_rowid(passage_id, provider, model),
            passage_id=passage_id,
            provider=provider,
            model=model,
            vector=coerced,
        )
    except Exception:
        return
    finally:
        if "original_isolation_level" in locals():
            conn.isolation_level = original_isolation_level


def delete_passage_embedding_vector_index(
    conn: sqlite3.Connection,
    *,
    passage_ids: Sequence[str],
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Best-effort delete of stale rows from any sqlite-vec embedding tables."""
    clean_passage_ids = _unique_non_empty(passage_ids)
    if not clean_passage_ids:
        return
    if conn.in_transaction:
        return
    try:
        original_isolation_level = conn.isolation_level
        conn.isolation_level = None
        _load_sqlite_vec(conn)
        table_names = _vector_index_tables(conn)
        for table_name in table_names:
            conditions = ["passage_id IN (SELECT value FROM json_each(?))"]
            params: list[Any] = [json.dumps(clean_passage_ids, separators=(",", ":"))]
            if provider is not None:
                conditions.append("provider = ?")
                params.append(provider)
            if model is not None:
                conditions.append("model = ?")
                params.append(model)
            conn.execute(
                f"DELETE FROM {table_name} WHERE {' AND '.join(conditions)}",
                params,
            )
    except Exception:
        return
    finally:
        if "original_isolation_level" in locals():
            conn.isolation_level = original_isolation_level


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    import sqlite_vec  # type: ignore[import-untyped]

    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def _ensure_metadata_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {VECTOR_INDEX_METADATA_TABLE} (
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            dimension INTEGER NOT NULL CHECK(dimension > 0),
            table_name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (provider, model, dimension)
        )
        """
    )


def _ensure_vector_index(
    conn: sqlite3.Connection,
    *,
    dimension: int,
    provider: str,
    model: str,
) -> str:
    table_name = _vector_index_table_name(dimension)
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {table_name}
        USING vec0(
            embedding float[{dimension}],
            passage_id text,
            provider text,
            model text
        )
        """
    )
    conn.execute(
        f"""
        INSERT OR IGNORE INTO {VECTOR_INDEX_METADATA_TABLE} (
            provider, model, dimension, table_name
        )
        VALUES (?, ?, ?, ?)
        """,
        (provider, model, dimension, table_name),
    )
    return table_name


def _sync_vector_index(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    space_id: str,
    provider: str,
    model: str,
    dimension: int,
    candidate_passage_ids: Sequence[str] | None = None,
) -> None:
    rows = _stored_embedding_rows(
        conn,
        space_id=space_id,
        provider=provider,
        model=model,
        dimension=dimension,
        candidate_passage_ids=candidate_passage_ids,
    )
    for row in rows:
        vector = _vector_from_json(row["embedding_json"])
        if len(vector) != dimension:
            continue
        _insert_vector_row(
            conn,
            table_name=table_name,
            rowid=_stable_rowid(str(row["passage_id"]), provider, model),
            passage_id=str(row["passage_id"]),
            provider=provider,
            model=model,
            vector=vector,
        )


def _stored_embedding_rows(
    conn: sqlite3.Connection,
    *,
    space_id: str,
    provider: str,
    model: str,
    dimension: int,
    candidate_passage_ids: Sequence[str] | None = None,
) -> list[sqlite3.Row]:
    candidate_clause = ""
    params: list[Any] = [space_id, provider, model, dimension]
    if candidate_passage_ids is not None:
        candidate_clause = "AND p.id IN (SELECT value FROM json_each(?))"
        params.append(json.dumps(list(candidate_passage_ids), separators=(",", ":")))
    return conn.execute(
        f"""
        SELECT pe.embedding_json, p.id AS passage_id
        FROM passage_embeddings pe
        JOIN passages p ON p.id = pe.passage_id
        WHERE p.space_id = ?
          AND pe.provider = ?
          AND pe.model = ?
          AND pe.dimension = ?
          {candidate_clause}
        """,
        params,
    ).fetchall()


def _insert_vector_row(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    rowid: int,
    passage_id: str,
    provider: str,
    model: str,
    vector: Sequence[float],
) -> None:
    import sqlite_vec

    conn.execute(
        f"""
        DELETE FROM {table_name}
        WHERE rowid = ?
           OR (passage_id = ? AND provider = ? AND model = ?)
        """,
        (rowid, passage_id, provider, model),
    )
    conn.execute(
        f"""
        INSERT INTO {table_name} (
            rowid, embedding, passage_id, provider, model
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            rowid,
            sqlite_vec.serialize_float32(list(vector)),
            passage_id,
            provider,
            model,
        ),
    )


def _query_vector_index(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    query_vector: Sequence[float],
    space_id: str,
    provider: str,
    model: str,
    limit: int,
    candidate_passage_ids: Sequence[str] | None = None,
) -> list[sqlite3.Row]:
    import sqlite_vec

    filters = ["vec.provider = ?", "vec.model = ?"]
    params: list[Any] = [
        sqlite_vec.serialize_float32(list(query_vector)),
        provider,
        model,
        space_id,
        provider,
        model,
    ]
    if candidate_passage_ids is not None:
        filters.append("vec.passage_id IN (SELECT value FROM json_each(?))")
        params.insert(3, json.dumps(list(candidate_passage_ids), separators=(",", ":")))

    return conn.execute(
        f"""
        SELECT
            vec_distance_cosine(vec.embedding, ?) AS distance,
            p.id AS passage_id,
            p.paper_id,
            p.section,
            p.page_number,
            p.paragraph_index,
            p.original_text,
            papers.title AS paper_title
        FROM {table_name} vec
        JOIN passages p ON p.id = vec.passage_id
        JOIN papers ON papers.id = p.paper_id
        JOIN passage_embeddings pe ON pe.passage_id = p.id
        WHERE {' AND '.join(filters)}
          AND p.space_id = ?
          AND pe.provider = ?
          AND pe.model = ?
        ORDER BY distance ASC, p.id ASC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()


def _row_to_result(row: sqlite3.Row) -> dict[str, Any]:
    distance = float(row["distance"])
    score = 1.0 - distance
    return {
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


def _vector_index_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name LIKE ?
        """,
        (f"{VECTOR_INDEX_TABLE_PREFIX}_%",),
    ).fetchall()
    return [
        name
        for row in rows
        if VECTOR_INDEX_TABLE_RE.match(name := str(row["name"])) is not None
    ]


def _vector_index_table_name(dimension: int) -> str:
    return f"{VECTOR_INDEX_TABLE_PREFIX}_{dimension}"


def _stable_rowid(passage_id: str, provider: str, model: str) -> int:
    text = f"{passage_id}\0{provider}\0{model}".encode("utf-8")
    value = 1469598103934665603
    for byte in text:
        value ^= byte
        value = (value * 1099511628211) & 0x7FFFFFFFFFFFFFFF
    return value or 1


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


def _unique_non_empty(values: Sequence[str] | None) -> list[str]:
    if values is None:
        return []
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _plain_snippet(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= 240:
        return compact
    return f"{compact[:237].rstrip()}..."
