"""Rules-based knowledge card extraction from passages."""

import uuid
from typing import Any

METHOD_KEYWORDS = [
    "method", "approach", "architecture", "algorithm", "protocol",
    "pipeline", "framework", "model", "we propose", "we present",
    "we introduce", "we develop", "we design",
]

RESULT_KEYWORDS = [
    "result", "achieve", "outperform", "accuracy", "performance",
    "improve", "increase", "decrease", "score", "f1", "bleu",
    "we obtain", "we report", "shows that", "demonstrates",
]

LIMITATION_KEYWORDS = [
    "limitation", "limited", "future work", "we acknowledge",
    "drawback", "shortcoming", "does not", "fail", "however",
    "although", "despite",
]

METRIC_KEYWORDS = [
    "metric", "measure", "evaluate", "accuracy", "precision",
    "recall", "f1", "bleu", "rouge", "perplexity", "auc",
    "rmse", "mae", "map", "ndcg",
]


def extract_cards_from_passages(
    passages: list[dict[str, Any]],
    paper_id: str,
    space_id: str,
) -> list[dict[str, Any]]:
    """Extract knowledge cards from a list of passages using rules-based heuristics.

    Returns a list of card dicts ready for database insertion.
    """
    cards: list[dict[str, Any]] = []

    for passage in passages:
        text = passage.get("original_text", "")
        text_lower = text.lower()
        passage_id = str(passage["id"])

        # Method cards
        if any(kw in text_lower for kw in METHOD_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Method",
                _first_sentence(text), 0.6,
            ))

        # Result cards
        if any(kw in text_lower for kw in RESULT_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Result",
                _first_sentence(text), 0.5,
            ))

        # Limitation cards
        if any(kw in text_lower for kw in LIMITATION_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Limitation",
                _first_sentence(text), 0.5,
            ))

        # Metric cards
        if any(kw in text_lower for kw in METRIC_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Metric",
                _first_sentence(text), 0.5,
            ))

    return cards


def _make_card(
    space_id: str,
    paper_id: str,
    passage_id: str,
    card_type: str,
    summary: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "space_id": space_id,
        "paper_id": paper_id,
        "source_passage_id": passage_id,
        "card_type": card_type,
        "summary": summary[:500],
        "confidence": confidence,
        "user_edited": 0,
    }


def _first_sentence(text: str) -> str:
    """Extract the first sentence from text."""
    for delim in [". ", ".\n", ".  "]:
        idx = text.find(delim)
        if idx > 0:
            return text[:idx + 1].strip()
    return text[:200].strip()
