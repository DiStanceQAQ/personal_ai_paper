"""Full-text search using SQLite FTS5."""

from pathlib import Path
from typing import Any

from db import get_connection

FTS_TABLE = "passages_fts"


def ensure_fts_index(database_path: Path | None = None) -> None:
    """Create the FTS5 index if it does not exist."""
    conn = get_connection(database_path)
    try:
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE}
            USING fts5(
                passage_id,
                paper_id,
                space_id,
                section,
                original_text
            )
        """)
        conn.commit()
    finally:
        conn.close()


def rebuild_fts_index(database_path: Path | None = None) -> None:
    """Rebuild the FTS5 index from all passages."""
    conn = get_connection(database_path)
    try:
        # Delete all entries and re-insert from passages
        conn.execute(f"DELETE FROM {FTS_TABLE}")
        conn.execute(f"""
            INSERT INTO {FTS_TABLE} (passage_id, paper_id, space_id, section, original_text)
            SELECT id, paper_id, space_id, section, original_text FROM passages
        """)
        conn.commit()
    finally:
        conn.close()


def search_passages(
    query: str,
    space_id: str,
    limit: int = 50,
    database_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Search passages in a space using FTS5.

    Returns results with paper title, section, page number, snippet, and match score.
    """
    conn = get_connection(database_path)
    try:
        rows = conn.execute(
            f"""
            SELECT
                fts.rank AS score,
                p.id AS passage_id,
                p.paper_id,
                p.section,
                p.page_number,
                p.paragraph_index,
                snippet({FTS_TABLE}, 1, '<mark>', '</mark>', '...', 32) AS snippet,
                p.original_text,
                papers.title AS paper_title
            FROM {FTS_TABLE} fts
            JOIN passages p ON p.id = fts.passage_id
            JOIN papers ON papers.id = p.paper_id
            WHERE {FTS_TABLE} MATCH ?
              AND fts.space_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, space_id, limit),
        ).fetchall()

        return [dict(r) for r in rows]
    finally:
        conn.close()
