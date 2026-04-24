"""Local MCP Server for the Paper Knowledge Engine.

Start with: python mcp_server.py
Configure in agent's MCP settings with stdio transport.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from db import get_connection, init_db
from search import search_passages

mcp = FastMCP("paper-knowledge-engine")

ACTIVE_SPACE_KEY = "active_space"
AGENT_ACCESS_KEY = "agent_access"


def _get_active_space_id() -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (ACTIVE_SPACE_KEY,),
        ).fetchone()
        return str(row["value"]) if row else ""
    finally:
        conn.close()


def _check_access() -> dict[str, Any] | None:
    """Return an error dict if agent access is disabled, else None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (AGENT_ACCESS_KEY,),
        ).fetchone()
        if row is None:
            # Default: access disabled until explicitly enabled
            return {"error": "Agent access is not configured. Enable it in the UI first."}
        if row["value"] != "enabled":
            return {"error": "Agent access is disabled. Enable it in the UI first."}
        return None
    finally:
        conn.close()


# ── Core Tools (US-014) ──────────────────────────────────────────────


@mcp.tool()
def list_spaces() -> list[dict[str, Any]]:
    """List all available idea spaces."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, description, status, created_at, updated_at FROM spaces WHERE status != 'deleted' ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@mcp.tool()
def get_active_space() -> dict[str, Any]:
    """Get the currently active idea space."""
    access_error = _check_access()
    if access_error:
        return access_error
    space_id = _get_active_space_id()
    if not space_id:
        return {"error": "No active space set"}
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM spaces WHERE id = ?", (space_id,)).fetchone()
        if row is None:
            return {"error": f"Active space {space_id} not found"}
        return dict(row)
    finally:
        conn.close()


@mcp.tool()
def list_papers(space_id: str = "") -> list[dict[str, Any]]:
    """List all papers in a space. Defaults to active space."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    sid = space_id or _get_active_space_id()
    if not sid:
        return [{"error": "No space specified and no active space set"}]
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, space_id, title, authors, year, doi, arxiv_id, pubmed_id,
                      venue, abstract, parse_status, imported_at, user_tags, relation_to_idea
               FROM papers WHERE space_id = ? ORDER BY imported_at DESC""",
            (sid,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@mcp.tool()
def search_literature(query: str, space_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Full-text search across passages. Returns paper, section, page, passage text, and card info."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    sid = space_id or _get_active_space_id()
    if not sid:
        return [{"error": "No space specified"}]
    results = search_passages(query, sid, limit)
    for r in results:
        if "snippet" in r:
            r["snippet"] = str(r["snippet"])
    return results


@mcp.tool()
def get_paper_summary(paper_id: str) -> dict[str, Any]:
    """Get a structured summary of a paper including metadata, passages, and cards."""
    access_error = _check_access()
    if access_error:
        return access_error
    conn = get_connection()
    try:
        paper = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if paper is None:
            return {"error": "Paper not found"}
        passages = conn.execute(
            "SELECT * FROM passages WHERE paper_id = ? ORDER BY page_number, paragraph_index",
            (paper_id,),
        ).fetchall()
        cards = conn.execute(
            "SELECT * FROM knowledge_cards WHERE paper_id = ?", (paper_id,)
        ).fetchall()
        return {
            "paper": dict(paper),
            "passage_count": len(passages),
            "card_count": len(cards),
            "cards": [dict(c) for c in cards],
        }
    finally:
        conn.close()


@mcp.tool()
def get_citation(paper_id: str) -> dict[str, str]:
    """Get citation information for a paper."""
    access_error = _check_access()
    if access_error:
        return access_error
    conn = get_connection()
    try:
        paper = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if paper is None:
            return {"error": "Paper not found"}
        return {
            "title": str(paper["title"]),
            "authors": str(paper["authors"]),
            "year": str(paper["year"] or ""),
            "doi": str(paper["doi"]),
            "arxiv_id": str(paper["arxiv_id"]),
            "pubmed_id": str(paper["pubmed_id"]),
            "venue": str(paper["venue"]),
            "citation": str(paper["citation"]),
        }
    finally:
        conn.close()


# ── Specialized Evidence Tools (US-015) ───────────────────────────────


def _get_cards_by_type(card_type: str, space_id: str = "") -> list[dict[str, Any]]:
    sid = space_id or _get_active_space_id()
    if not sid:
        return [{"error": "No space specified"}]
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT c.*, p.title as paper_title
               FROM knowledge_cards c
               JOIN papers p ON p.id = c.paper_id
               WHERE c.space_id = ? AND c.card_type = ?
               ORDER BY c.created_at DESC""",
            (sid, card_type),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@mcp.tool()
def get_methods(space_id: str = "") -> list[dict[str, Any]]:
    """Get method cards from the active space, each with paper title and source passage."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    return _get_cards_by_type("Method", space_id)


@mcp.tool()
def get_metrics(space_id: str = "") -> list[dict[str, Any]]:
    """Get metric cards from the active space."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    return _get_cards_by_type("Metric", space_id)


@mcp.tool()
def get_limitations(space_id: str = "") -> list[dict[str, Any]]:
    """Get limitation cards from the active space."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    return _get_cards_by_type("Limitation", space_id)


@mcp.tool()
def find_failure_modes(space_id: str = "") -> list[dict[str, Any]]:
    """Find failure mode cards from the active space."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    return _get_cards_by_type("Failure Mode", space_id)


@mcp.tool()
def find_similar_results(query: str, space_id: str = "", limit: int = 10) -> list[dict[str, Any]]:
    """Find results similar to a query using full-text search."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    sid = space_id or _get_active_space_id()
    if not sid:
        return [{"error": "No space specified"}]
    results = search_passages(query, sid, limit)
    for r in results:
        if "snippet" in r:
            r["snippet"] = str(r["snippet"])
    return results


@mcp.tool()
def compare_with_literature(
    observation: str, space_id: str = "", limit: int = 10
) -> dict[str, Any]:
    """Compare an observation with literature evidence. Returns matching passages and related cards."""
    access_error = _check_access()
    if access_error:
        return access_error
    sid = space_id or _get_active_space_id()
    if not sid:
        return {"error": "No space specified"}

    passages = search_passages(observation, sid, limit)
    card_results: list[dict[str, Any]] = []

    conn = get_connection()
    try:
        paper_ids = list({p.get("paper_id", "") for p in passages if p.get("paper_id")})
        if paper_ids:
            placeholders = ",".join("?" * len(paper_ids))
            card_rows = conn.execute(
                f"SELECT * FROM knowledge_cards WHERE paper_id IN ({placeholders})",
                paper_ids,
            ).fetchall()
            card_results = [dict(r) for r in card_rows]
    finally:
        conn.close()

    for p in passages:
        if "snippet" in p:
            p["snippet"] = str(p["snippet"])

    return {"passages": passages, "related_cards": card_results}


@mcp.tool()
def get_evidence_for_claim(
    claim: str, space_id: str = "", limit: int = 10
) -> list[dict[str, Any]]:
    """Find evidence for a scientific claim in the literature."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    sid = space_id or _get_active_space_id()
    if not sid:
        return [{"error": "No space specified"}]

    passages = search_passages(claim, sid, limit)

    conn = get_connection()
    try:
        ev_cards = conn.execute(
            "SELECT * FROM knowledge_cards WHERE space_id = ? AND card_type = 'Evidence' ORDER BY created_at DESC LIMIT ?",
            (sid, limit),
        ).fetchall()
    finally:
        conn.close()

    for p in passages:
        if "snippet" in p:
            p["snippet"] = str(p["snippet"])

    return [{"passages": passages, "evidence_cards": [dict(c) for c in ev_cards]}]


# ── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    """Start the MCP server with stdio transport."""
    init_db()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
