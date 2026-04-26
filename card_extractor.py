"""Utility functions for paper metadata and card processing.
Heuristic card extraction logic has been moved to external Agents via MCP.
"""

from typing import Any

def extract_metadata_from_passages(passages: list[dict[str, Any]]) -> dict[str, str]:
    """Basic metadata extractor to provide initial info before Agent analysis."""
    meta = {"title": "", "authors": ""}
    if not passages:
        return meta

    # Very simple heuristic: first substantial block as potential title
    for p in passages[:5]:
        text = p.get("original_text", "").strip()
        if len(text) > 30 and not text.endswith("."):
            meta["title"] = text[:300]
            break
            
    return meta

# extract_cards_from_passages is now deprecated in favor of Agent-based extraction.
# We keep a stub for backward compatibility if needed, but it returns empty.
def extract_cards_from_passages(
    passages: list[dict[str, Any]],
    paper_id: str,
    space_id: str,
) -> list[dict[str, Any]]:
    return []
