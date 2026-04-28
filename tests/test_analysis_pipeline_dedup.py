"""Tests for AI card deduplication and ranking."""

from typing import Any

import paper_engine.analysis.pipeline as analysis_pipeline
from paper_engine.analysis.models import CardExtraction
from paper_engine.analysis.verifier import RejectedCardDiagnostic


def _passage(
    passage_id: str,
    *,
    passage_type: str = "body",
    heading_path: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": passage_id,
        "paper_id": "paper-1",
        "space_id": "space-1",
        "section": (heading_path or [passage_type.title()])[-1],
        "page_number": 1,
        "paragraph_index": 0,
        "original_text": f"{passage_type} evidence for {passage_id}",
        "passage_type": passage_type,
        "heading_path": heading_path or [passage_type.title()],
    }


def _card(
    summary: str,
    source_passage_ids: list[str],
    *,
    card_type: str = "Method",
    confidence: float = 0.8,
) -> CardExtraction:
    return CardExtraction(
        card_type=card_type,
        summary=summary,
        source_passage_ids=source_passage_ids,
        evidence_quote="grounded evidence",
        confidence=confidence,
        reasoning_summary="The cited passage supports the extracted card.",
    )


def _batches(
    passages: list[dict[str, Any]],
) -> list[analysis_pipeline.AnalysisPassageBatch]:
    return analysis_pipeline.select_analysis_passage_batches(
        passages,
        max_batch_tokens=500,
    )


def test_deduplicates_similar_same_type_cards_with_overlapping_sources() -> None:
    """Duplicate cards collapse only when type, summary, and sources agree."""
    passages = [
        _passage("method-1", passage_type="method", heading_path=["Methods"]),
        _passage("method-2", passage_type="method", heading_path=["Methods"]),
        _passage("method-3", passage_type="method", heading_path=["Methods"]),
        _passage("result-1", passage_type="result", heading_path=["Results"]),
    ]
    weaker_duplicate = _card(
        "The parser uses heading-aware chunking to preserve source structure.",
        ["method-1"],
        confidence=0.72,
    )
    stronger_duplicate = _card(
        "Parser uses heading aware chunking and preserves source structure.",
        ["method-1", "method-2"],
        confidence=0.88,
    )
    same_summary_different_type = _card(
        "The parser uses heading-aware chunking to preserve source structure.",
        ["result-1"],
        card_type="Result",
        confidence=0.8,
    )
    similar_summary_without_source_overlap = _card(
        "The parser uses heading aware chunking to preserve source structure.",
        ["method-3"],
        confidence=0.81,
    )

    result = analysis_pipeline.deduplicate_and_rank_cards_stage(
        [
            weaker_duplicate,
            stronger_duplicate,
            same_summary_different_type,
            similar_summary_without_source_overlap,
        ],
        batches=_batches(passages),
    )

    typed_summaries = [(card.card_type, card.summary) for card in result.cards]
    assert (stronger_duplicate.card_type, stronger_duplicate.summary) in typed_summaries
    assert (weaker_duplicate.card_type, weaker_duplicate.summary) not in typed_summaries
    assert (
        same_summary_different_type.card_type,
        same_summary_different_type.summary,
    ) in typed_summaries
    assert (
        similar_summary_without_source_overlap.card_type,
        similar_summary_without_source_overlap.summary,
    ) in typed_summaries
    assert result.diagnostics["duplicate_card_count"] == 1
    assert result.diagnostics["duplicate_cards"][0]["kept"]["summary"] == (
        stronger_duplicate.summary
    )
    assert result.diagnostics["duplicate_cards"][0]["dropped"]["summary"] == (
        weaker_duplicate.summary
    )


def test_ranks_by_confidence_source_coverage_and_key_sections() -> None:
    """Ranking should prefer confidence, then source coverage, then key sections."""
    passages = [
        _passage("body-1", passage_type="body", heading_path=["Background"]),
        _passage("method-1", passage_type="method", heading_path=["Methods"]),
        _passage("method-2", passage_type="method", heading_path=["Methods"]),
        _passage(
            "intro-1",
            passage_type="introduction",
            heading_path=["Introduction"],
        ),
        _passage(
            "intro-2",
            passage_type="introduction",
            heading_path=["Introduction"],
        ),
    ]
    high_confidence = _card(
        "A high-confidence background claim is retained first.",
        ["body-1"],
        card_type="Claim",
        confidence=0.95,
    )
    method_with_coverage = _card(
        "The method combines parser routing with source-aware chunks.",
        ["method-1", "method-2"],
        confidence=0.82,
    )
    intro_with_same_coverage = _card(
        "The introduction motivates local-first paper analysis.",
        ["intro-1", "intro-2"],
        card_type="Claim",
        confidence=0.82,
    )

    result = analysis_pipeline.deduplicate_and_rank_cards_stage(
        [intro_with_same_coverage, method_with_coverage, high_confidence],
        batches=_batches(passages),
    )

    assert [card.summary for card in result.cards] == [
        high_confidence.summary,
        method_with_coverage.summary,
        intro_with_same_coverage.summary,
    ]


def test_limits_to_twenty_cards_and_records_overflow_diagnostics() -> None:
    """Overflow cards should be excluded from final output but retained as diagnostics."""
    cards = [
        _card(
            f"Unique ranked result {index}",
            [f"result-{index}"],
            card_type="Result",
            confidence=1.0 - (index * 0.01),
        )
        for index in range(22)
    ]
    rejected = RejectedCardDiagnostic(
        card_index=99,
        reason="evidence_mismatch",
        message="Unsupported evidence quote.",
        source_passage_ids=["result-99"],
    )

    result = analysis_pipeline.deduplicate_and_rank_cards_stage(
        cards,
        rejected_cards=[rejected],
    )

    assert len(result.cards) == 20
    assert [card.summary for card in result.cards[-2:]] == [
        "Unique ranked result 18",
        "Unique ranked result 19",
    ]
    assert result.rejected_cards == [rejected]
    assert result.diagnostics["overflow_card_count"] == 2
    assert [
        item["dropped"]["summary"] for item in result.diagnostics["overflow_cards"]
    ] == [
        "Unique ranked result 20",
        "Unique ranked result 21",
    ]
    assert result.diagnostics["rejected_cards"][0]["reason"] == "evidence_mismatch"
