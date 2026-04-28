"""Local MCP Server for the Paper Knowledge Engine.

Start with: paper-engine-mcp
Configure in agent's MCP settings with stdio transport.
"""

import uuid
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from paper_engine.storage.database import get_connection, init_db
from paper_engine.retrieval.lexical import search_passages

mcp = FastMCP("paper-knowledge-engine")

ACTIVE_SPACE_KEY = "active_space"
AGENT_ACCESS_KEY = "agent_access"
CARD_TYPES = {
    "Problem", "Claim", "Evidence", "Method",
    "Object", "Variable", "Metric", "Result",
    "Failure Mode", "Interpretation", "Limitation", "Practical Tip",
}


def _json_string_list(value: Any) -> list[str]:
    """Decode a JSON array into strings, tolerating legacy empty/null values."""
    if value in (None, ""):
        return []
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, str) and item]


def _passage_id_from_result(result: dict[str, Any]) -> str | None:
    raw_id = result.get("passage_id", result.get("id"))
    if raw_id is None:
        return None
    passage_id = str(raw_id)
    return passage_id or None


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _load_passage_source_metadata(
    passage_ids: list[str],
    space_id: str,
) -> dict[str, dict[str, Any]]:
    """Load source metadata for passages, scoped to the active MCP space."""
    unique_ids = _dedupe_preserving_order(passage_ids)
    if not unique_ids:
        return {}

    placeholders = ",".join("?" * len(unique_ids))
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT id, paper_id, parse_run_id, heading_path_json,
                   parser_backend, quality_flags_json
            FROM passages
            WHERE space_id = ?
              AND id IN ({placeholders})
            """,
            [space_id, *unique_ids],
        ).fetchall()
    finally:
        conn.close()

    metadata: dict[str, dict[str, Any]] = {}
    for row in rows:
        passage_id = str(row["id"])
        metadata[passage_id] = {
            "passage_id": passage_id,
            "paper_id": str(row["paper_id"]),
            "parse_run_id": row["parse_run_id"],
            "heading_path": _json_string_list(row["heading_path_json"]),
            "parser_backend": str(row["parser_backend"] or ""),
            "quality_flags": _json_string_list(row["quality_flags_json"]),
        }
    return metadata


def _public_passage_source_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "passage_id": metadata["passage_id"],
        "parse_run_id": metadata["parse_run_id"],
        "heading_path": metadata["heading_path"],
        "parser_backend": metadata["parser_backend"],
        "quality_flags": metadata["quality_flags"],
    }


def _enrich_passage_results(
    results: list[dict[str, Any]],
    space_id: str,
) -> list[dict[str, Any]]:
    """Add source provenance fields to MCP passage-shaped results."""
    passage_ids = [
        passage_id
        for result in results
        if (passage_id := _passage_id_from_result(result)) is not None
    ]
    metadata_by_id = _load_passage_source_metadata(passage_ids, space_id)
    for result in results:
        passage_id = _passage_id_from_result(result)
        if passage_id is None:
            continue
        metadata = metadata_by_id.get(passage_id)
        if metadata is None:
            continue
        result["parse_run_id"] = metadata["parse_run_id"]
        result["heading_path"] = metadata["heading_path"]
        result["parser_backend"] = metadata["parser_backend"]
        result["quality_flags"] = metadata["quality_flags"]
        result["source_passage_ids"] = [passage_id]
    return results


def _source_ids_from_evidence_json(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, dict):
        return []
    return _json_string_list(json.dumps(decoded.get("source_passage_ids", [])))


def _load_card_source_ids(
    card_ids: list[str],
    space_id: str,
) -> dict[str, list[str]]:
    unique_card_ids = _dedupe_preserving_order(card_ids)
    if not unique_card_ids:
        return {}

    placeholders = ",".join("?" * len(unique_card_ids))
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT card_id, passage_id
            FROM knowledge_card_sources
            WHERE space_id = ?
              AND card_id IN ({placeholders})
            ORDER BY created_at, id
            """,
            [space_id, *unique_card_ids],
        ).fetchall()
    finally:
        conn.close()

    source_ids: dict[str, list[str]] = {card_id: [] for card_id in unique_card_ids}
    for row in rows:
        card_id = str(row["card_id"])
        passage_id = str(row["passage_id"])
        source_ids.setdefault(card_id, []).append(passage_id)
    return {
        card_id: _dedupe_preserving_order(ids)
        for card_id, ids in source_ids.items()
    }


def _candidate_card_source_ids(
    card: dict[str, Any],
    source_ids_by_card: dict[str, list[str]],
) -> list[str]:
    card_id = str(card.get("id", ""))
    candidates = list(source_ids_by_card.get(card_id, []))
    candidates.extend(_source_ids_from_evidence_json(card.get("evidence_json")))
    source_passage_id = card.get("source_passage_id")
    if source_passage_id:
        candidates.append(str(source_passage_id))
    return _dedupe_preserving_order(candidates)


def _enrich_cards(
    cards: list[dict[str, Any]],
    space_id: str,
) -> list[dict[str, Any]]:
    """Add validated source passage IDs and provenance to MCP card results."""
    if not cards:
        return cards

    card_ids = [
        str(card["id"])
        for card in cards
        if card.get("id") is not None
    ]
    source_ids_by_card = _load_card_source_ids(card_ids, space_id)
    candidates_by_card = {
        str(card.get("id", "")): _candidate_card_source_ids(card, source_ids_by_card)
        for card in cards
    }
    all_source_ids = [
        source_id
        for source_ids in candidates_by_card.values()
        for source_id in source_ids
    ]
    metadata_by_id = _load_passage_source_metadata(all_source_ids, space_id)

    for card in cards:
        card["quality_flags"] = _json_string_list(card.get("quality_flags_json"))
        card_paper_id = str(card.get("paper_id", ""))
        validated_sources: list[dict[str, Any]] = []
        for source_id in candidates_by_card.get(str(card.get("id", "")), []):
            metadata = metadata_by_id.get(source_id)
            if metadata is None or metadata["paper_id"] != card_paper_id:
                continue
            validated_sources.append(_public_passage_source_metadata(metadata))
        card["source_passage_ids"] = [
            str(source["passage_id"]) for source in validated_sources
        ]
        card["source_passages"] = validated_sources
    return cards


def _get_active_space_id() -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT s.id
               FROM spaces s
               JOIN app_state a ON a.value = s.id
               WHERE a.key = ? AND s.status = 'active'""",
            (ACTIVE_SPACE_KEY,),
        ).fetchone()
        return str(row["id"]) if row else ""
    finally:
        conn.close()


def _resolve_active_space(requested_space_id: str = "") -> tuple[str, dict[str, str] | None]:
    """Resolve an optional requested space to the active space only."""
    active_space_id = _get_active_space_id()
    if not active_space_id:
        return "", {"error": "No active space set"}
    if requested_space_id and requested_space_id != active_space_id:
        return "", {"error": "MCP access is limited to the active space"}
    return active_space_id, None


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
    """Return the active idea space exposed to agents."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    space_id, space_error = _resolve_active_space()
    if space_error:
        return [space_error]
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, name, description, status, created_at, updated_at
               FROM spaces
               WHERE id = ? AND status != 'deleted'""",
            (space_id,),
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
    sid, space_error = _resolve_active_space(space_id)
    if space_error:
        return [space_error]
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
    sid, space_error = _resolve_active_space(space_id)
    if space_error:
        return [space_error]
    results = search_passages(query, sid, limit)
    for r in results:
        if "snippet" in r:
            r["snippet"] = str(r["snippet"])
    return _enrich_passage_results(results, sid)


@mcp.tool()
def get_paper_summary(paper_id: str) -> dict[str, Any]:
    """Get a structured summary of a paper including metadata, passages, and cards."""
    access_error = _check_access()
    if access_error:
        return access_error
    sid, space_error = _resolve_active_space()
    if space_error:
        return space_error
    conn = get_connection()
    try:
        paper = conn.execute(
            "SELECT * FROM papers WHERE id = ? AND space_id = ?",
            (paper_id, sid),
        ).fetchone()
        if paper is None:
            return {"error": "Paper not found"}
        passages = conn.execute(
            """SELECT * FROM passages
               WHERE paper_id = ? AND space_id = ?
               ORDER BY page_number, paragraph_index""",
            (paper_id, sid),
        ).fetchall()
        cards = conn.execute(
            "SELECT * FROM knowledge_cards WHERE paper_id = ? AND space_id = ?",
            (paper_id, sid),
        ).fetchall()
        return {
            "paper": dict(paper),
            "passage_count": len(passages),
            "card_count": len(cards),
            "cards": _enrich_cards([dict(c) for c in cards], sid),
        }
    finally:
        conn.close()


@mcp.tool()
def get_citation(paper_id: str) -> dict[str, str]:
    """Get citation information for a paper."""
    access_error = _check_access()
    if access_error:
        return access_error
    sid, space_error = _resolve_active_space()
    if space_error:
        return space_error
    conn = get_connection()
    try:
        paper = conn.execute(
            "SELECT * FROM papers WHERE id = ? AND space_id = ?",
            (paper_id, sid),
        ).fetchone()
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
    sid, space_error = _resolve_active_space(space_id)
    if space_error:
        return [space_error]
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
        return _enrich_cards([dict(r) for r in rows], sid)
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
    sid, space_error = _resolve_active_space(space_id)
    if space_error:
        return [space_error]
    results = search_passages(query, sid, limit)
    for r in results:
        if "snippet" in r:
            r["snippet"] = str(r["snippet"])
    return _enrich_passage_results(results, sid)


@mcp.tool()
def compare_with_literature(
    observation: str, space_id: str = "", limit: int = 10
) -> dict[str, Any]:
    """Compare an observation with literature evidence. Returns matching passages and related cards."""
    access_error = _check_access()
    if access_error:
        return access_error
    sid, space_error = _resolve_active_space(space_id)
    if space_error:
        return space_error

    passages = search_passages(observation, sid, limit)
    card_results: list[dict[str, Any]] = []

    conn = get_connection()
    try:
        paper_ids = list({p.get("paper_id", "") for p in passages if p.get("paper_id")})
        if paper_ids:
            placeholders = ",".join("?" * len(paper_ids))
            card_rows = conn.execute(
                f"""SELECT * FROM knowledge_cards
                    WHERE space_id = ? AND paper_id IN ({placeholders})""",
                [sid, *paper_ids],
            ).fetchall()
            card_results = _enrich_cards([dict(r) for r in card_rows], sid)
    finally:
        conn.close()

    for p in passages:
        if "snippet" in p:
            p["snippet"] = str(p["snippet"])
    _enrich_passage_results(passages, sid)

    return {"passages": passages, "related_cards": card_results}


@mcp.tool()
def get_evidence_for_claim(
    claim: str, space_id: str = "", limit: int = 10
) -> list[dict[str, Any]]:
    """Find evidence for a scientific claim in the literature."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    sid, space_error = _resolve_active_space(space_id)
    if space_error:
        return [space_error]

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
    _enrich_passage_results(passages, sid)

    return [
        {
            "passages": passages,
            "evidence_cards": _enrich_cards([dict(c) for c in ev_cards], sid),
        }
    ]


@mcp.tool()
def get_full_paper_text(paper_id: str) -> list[dict[str, Any]]:
    """Get all text passages of a paper in order. Useful for full-paper analysis."""
    access_error = _check_access()
    if access_error:
        return [access_error]
    sid, space_error = _resolve_active_space()
    if space_error:
        return [space_error]
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, section, page_number, original_text FROM passages WHERE paper_id = ? AND space_id = ? ORDER BY page_number, paragraph_index",
            (paper_id, sid),
        ).fetchall()
        return _enrich_passage_results([dict(r) for r in rows], sid)
    finally:
        conn.close()


@mcp.tool()
def update_paper_metadata(
    paper_id: str,
    title: str = "",
    authors: str = "",
    year: int = 0,
    abstract: str = "",
    relation_to_idea: str = "",
) -> dict[str, Any]:
    """Update paper metadata. Call this after analyzing a paper to fill in missing info."""
    access_error = _check_access()
    if access_error:
        return access_error
    sid, space_error = _resolve_active_space()
    if space_error:
        return space_error
    
    conn = get_connection()
    try:
        fields: list[str] = []
        params: list[Any] = []
        if title: fields.append("title = ?"); params.append(title)
        if authors: fields.append("authors = ?"); params.append(authors)
        if year: fields.append("year = ?"); params.append(year)
        if abstract: fields.append("abstract = ?"); params.append(abstract)
        if relation_to_idea: fields.append("relation_to_idea = ?"); params.append(relation_to_idea)
        
        if not fields:
            return {"error": "No fields to update"}
            
        params.append(paper_id)
        params.append(sid)
        conn.execute(
            f"UPDATE papers SET {', '.join(fields)} WHERE id = ? AND space_id = ?",
            params
        )
        conn.commit()
        return {"status": "success", "updated_paper_id": paper_id}
    finally:
        conn.close()


@mcp.tool()
def add_knowledge_card(
    paper_id: str,
    card_type: str,
    summary: str,
    source_passage_id: str = "",
) -> dict[str, Any]:
    """Create a new knowledge card for a paper. 
    Valid types: Method, Metric, Result, Failure Mode, Limitation, Claim, Evidence, Problem, Object, Variable, Interpretation, Practical Tip.
    """
    access_error = _check_access()
    if access_error:
        return access_error
    sid, space_error = _resolve_active_space()
    if space_error:
        return space_error

    if card_type not in CARD_TYPES:
        return {"error": f"Invalid card type: {card_type}"}

    conn = get_connection()
    try:
        paper = conn.execute(
            "SELECT id FROM papers WHERE id = ? AND space_id = ?",
            (paper_id, sid),
        ).fetchone()
        if paper is None:
            return {"error": "Paper not found in active space"}

        source_id = source_passage_id or None
        if source_id is not None:
            source = conn.execute(
                """SELECT id FROM passages
                   WHERE id = ? AND paper_id = ? AND space_id = ?""",
                (source_id, paper_id, sid),
            ).fetchone()
            if source is None:
                return {
                    "error": "source_passage_id must belong to the same paper and active space"
                }

        card_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO knowledge_cards (id, space_id, paper_id, source_passage_id, card_type, summary, confidence, user_edited)
               VALUES (?, ?, ?, ?, ?, ?, 1.0, 0)""",
            (card_id, sid, paper_id, source_id, card_type, summary),
        )
        conn.commit()
        return {"status": "success", "card_id": card_id}
    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    """Start the MCP server with stdio transport."""
    init_db()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
