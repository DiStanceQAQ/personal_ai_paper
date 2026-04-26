"""Utility functions for paper metadata and card processing."""

import uuid
from typing import Any

METHOD_KEYWORDS = [
    "method", "approach", "architecture", "algorithm", "protocol",
    "pipeline", "framework", "model", "we propose", "we present",
    "we introduce", "we develop", "we design", "procedure", "workflow",
    "intervention", "assay", "synthesis", "experiment", "experimental setup",
]

RESULT_KEYWORDS = [
    "result", "achieve", "achieves", "outperform", "accuracy", "performance",
    "improve", "increase", "decrease", "score", "f1", "bleu",
    "we obtain", "we report", "shows that", "demonstrates", "significant",
    "statistical test", "yield", "stability", "effect size", "outcome",
]

LIMITATION_KEYWORDS = [
    "limitation", "limitations", "limited", "future work", "we acknowledge",
    "drawback", "shortcoming", "does not", "fail", "however",
    "although", "despite", "constraint", "bias", "confounder",
    "uncertainty", "threat to validity",
]

METRIC_KEYWORDS = [
    "metric", "measure", "measures", "evaluate", "accuracy", "precision",
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

def extract_cards_from_passages(
    passages: list[dict[str, Any]],
    paper_id: str,
    space_id: str,
) -> list[dict[str, Any]]:
    """Extract low-confidence, domain-neutral heuristic cards from passages."""
    cards: list[dict[str, Any]] = []

    for passage in passages:
        text = str(passage.get("original_text", ""))
        text_lower = text.lower()
        passage_id = str(passage.get("id", ""))

        if any(keyword in text_lower for keyword in METHOD_KEYWORDS):
            cards.append(_make_card(
                space_id,
                paper_id,
                passage_id,
                "Method",
                _first_sentence(text),
                HEURISTIC_CONFIDENCE["Method"],
            ))

        if any(keyword in text_lower for keyword in RESULT_KEYWORDS):
            cards.append(_make_card(
                space_id,
                paper_id,
                passage_id,
                "Result",
                _first_sentence(text),
                HEURISTIC_CONFIDENCE["Result"],
            ))

        if any(keyword in text_lower for keyword in LIMITATION_KEYWORDS):
            cards.append(_make_card(
                space_id,
                paper_id,
                passage_id,
                "Limitation",
                _first_sentence(text),
                HEURISTIC_CONFIDENCE["Limitation"],
            ))

        if any(keyword in text_lower for keyword in METRIC_KEYWORDS):
            cards.append(_make_card(
                space_id,
                paper_id,
                passage_id,
                "Metric",
                _first_sentence(text),
                HEURISTIC_CONFIDENCE["Metric"],
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
    for delimiter in [". ", ".\n", ".  "]:
        index = text.find(delimiter)
        if index > 0:
            return text[:index + 1].strip()
    return text[:200].strip()
