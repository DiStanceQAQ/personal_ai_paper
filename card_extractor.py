"""Rules-based, domain-neutral heuristic knowledge card extraction from passages."""

import uuid
from typing import Any

METHOD_KEYWORDS = [
    "method", "approach", "architecture", "algorithm", "protocol",
    "pipeline", "framework", "model", "we propose", "we present",
    "we introduce", "we develop", "we design", "procedure", "workflow",
    "intervention", "assay", "synthesis", "experiment", "experimental setup",
]

RESULT_KEYWORDS = [
    "result", "achieve", "outperform", "accuracy", "performance",
    "improve", "increase", "decrease", "score", "f1", "bleu",
    "we obtain", "we report", "shows that", "demonstrates", "significant",
    "statistical test", "yield", "stability", "effect size", "outcome",
]

LIMITATION_KEYWORDS = [
    "limitation", "limited", "future work", "we acknowledge",
    "drawback", "shortcoming", "does not", "fail", "however",
    "although", "despite", "constraint", "bias", "confounder",
    "uncertainty", "threat to validity",
]

METRIC_KEYWORDS = [
    "metric", "measure", "evaluate", "accuracy", "precision",
    "recall", "f1", "bleu", "rouge", "perplexity", "auc",
    "rmse", "mae", "map", "ndcg", "measurement", "endpoint",
    "statistical test", "p-value", "confidence interval", "sample",
    "cohort", "yield", "stability",
]

HEURISTIC_CONFIDENCE = {
    "Method": 0.55,
    "Result": 0.5,
    "Limitation": 0.5,
    "Metric": 0.5,
}


def extract_cards_from_passages(
    passages: list[dict[str, Any]],
    paper_id: str,
    space_id: str,
) -> list[dict[str, Any]]:
    """Extract low-confidence heuristic cards from passages.

    The extractor is domain-neutral. It does not claim to understand a scientific
    field; users should review and edit generated cards.
    """
    cards: list[dict[str, Any]] = []

    for passage in passages:
        text = passage.get("original_text", "")
        text_lower = text.lower()
        passage_id = str(passage["id"])

        if any(kw in text_lower for kw in METHOD_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Method",
                _first_sentence(text), HEURISTIC_CONFIDENCE["Method"],
            ))

        if any(kw in text_lower for kw in RESULT_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Result",
                _first_sentence(text), HEURISTIC_CONFIDENCE["Result"],
            ))

        if any(kw in text_lower for kw in LIMITATION_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Limitation",
                _first_sentence(text), HEURISTIC_CONFIDENCE["Limitation"],
            ))

        if any(kw in text_lower for kw in METRIC_KEYWORDS):
            cards.append(_make_card(
                space_id, paper_id, passage_id, "Metric",
                _first_sentence(text), HEURISTIC_CONFIDENCE["Metric"],
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
