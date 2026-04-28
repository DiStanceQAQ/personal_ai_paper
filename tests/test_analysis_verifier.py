"""Tests for AI card source-grounding verification."""

from typing import Any

from paper_engine.analysis.models import CardExtraction
from paper_engine.analysis.verifier import (
    verify_card_sources,
    verify_extraction_batch_sources,
)


def _passage(
    passage_id: str,
    text: str,
    *,
    paper_id: str = "paper-1",
    space_id: str = "space-1",
) -> dict[str, Any]:
    return {
        "id": passage_id,
        "paper_id": paper_id,
        "space_id": space_id,
        "page_number": 1,
        "original_text": text,
    }


def _card(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "card_type": "Method",
        "summary": "The parser chunks passages by source-aware headings.",
        "source_passage_ids": ["passage-1"],
        "evidence_quote": "source-aware headings",
        "confidence": 0.84,
        "reasoning_summary": "The cited passage explicitly describes the method.",
    }
    payload.update(overrides)
    return payload


def test_accepts_cards_with_exact_normalized_evidence_quote() -> None:
    """Exact evidence should match despite case and whitespace differences."""
    result = verify_card_sources(
        [_card(evidence_quote="SOURCE-AWARE   HEADINGS")],
        [
            _passage(
                "passage-1",
                "The parser chunks passages by source-aware headings.",
            ),
        ],
        paper_id="paper-1",
    )

    assert [card.summary for card in result.accepted_cards] == [
        "The parser chunks passages by source-aware headings.",
    ]
    assert result.rejected_cards == []


def test_accepts_cards_with_high_overlap_evidence_tokens() -> None:
    """Evidence can be accepted when token overlap is high but not contiguous."""
    result = verify_card_sources(
        [
            _card(
                card_type="Result",
                summary="The method improves source coverage by 14 percent.",
                evidence_quote="source coverage improves by 14 percent",
            ),
        ],
        [
            _passage(
                "passage-1",
                "The late results section reports a 14 percent source coverage gain.",
            ),
        ],
        paper_id="paper-1",
    )

    assert len(result.accepted_cards) == 1
    assert result.accepted_cards[0].card_type == "Result"
    assert result.rejected_cards == []


def test_rejects_hallucinated_source_passage_ids() -> None:
    """A card cannot cite a source ID that is absent from the paper catalog."""
    result = verify_card_sources(
        [_card(source_passage_ids=["missing-passage"])],
        [_passage("passage-1", "The parser chunks passages by headings.")],
        paper_id="paper-1",
    )

    assert result.accepted_cards == []
    assert [diagnostic.reason for diagnostic in result.rejected_cards] == [
        "missing_source",
    ]
    assert result.rejected_cards[0].source_passage_ids == ["missing-passage"]


def test_rejects_source_passage_ids_from_other_papers() -> None:
    """A source ID from another paper should not satisfy paper-level grounding."""
    result = verify_card_sources(
        [_card(source_passage_ids=["foreign-passage"])],
        [
            _passage(
                "foreign-passage",
                "The parser chunks passages by source-aware headings.",
                paper_id="paper-2",
            ),
        ],
        paper_id="paper-1",
    )

    assert result.accepted_cards == []
    assert result.rejected_cards[0].reason == "missing_source"


def test_rejects_unsupported_card_payloads_without_raising() -> None:
    """Verifier diagnostics should capture schema-level card failures."""
    result = verify_card_sources(
        [
            _card(card_type="Background"),
            _card(summary="   "),
            _card(source_passage_ids=[]),
        ],
        [_passage("passage-1", "The parser chunks passages by headings.")],
        paper_id="paper-1",
    )

    assert result.accepted_cards == []
    assert [diagnostic.reason for diagnostic in result.rejected_cards] == [
        "invalid_card",
        "invalid_card",
        "invalid_card",
    ]
    assert all("validation" in diagnostic.message.lower() for diagnostic in result.rejected_cards)


def test_rejects_evidence_not_supported_by_cited_sources() -> None:
    """A card is rejected when the quote is absent from all cited passages."""
    result = verify_card_sources(
        [
            _card(
                evidence_quote="The system performs perfect OCR on scanned papers.",
            ),
        ],
        [
            _passage(
                "passage-1",
                "The limitation is OCR quality on scanned documents.",
            ),
        ],
        paper_id="paper-1",
    )

    assert result.accepted_cards == []
    assert result.rejected_cards[0].reason == "evidence_mismatch"
    assert "evidence_quote" in result.rejected_cards[0].message


def test_verifies_card_extraction_batch_sources() -> None:
    """Batch verification should preserve accepted cards and rejected diagnostics."""
    result = verify_extraction_batch_sources(
        {
            "paper_id": "paper-1",
            "space_id": "space-1",
            "batch_index": 0,
            "source_passage_ids": ["passage-1", "passage-2"],
            "cards": [
                _card(source_passage_ids=["passage-1"]),
                _card(
                    card_type="Claim",
                    summary="The method uses an unavailable source.",
                    source_passage_ids=["passage-2"],
                    evidence_quote="unavailable source",
                ),
            ],
        },
        [
            _passage(
                "passage-1",
                "The parser chunks passages by source-aware headings.",
            ),
            _passage(
                "passage-2",
                "The cited passage discusses a different implementation detail.",
            ),
        ],
    )

    assert [card.card_type for card in result.accepted_cards] == ["Method"]
    assert [diagnostic.reason for diagnostic in result.rejected_cards] == [
        "evidence_mismatch",
    ]
    assert result.rejected_cards[0].batch_index == 0


def test_accepts_prevalidated_card_models() -> None:
    """Callers may pass CardExtraction models directly."""
    result = verify_card_sources(
        [
            CardExtraction(
                **_card(
                    evidence_quote="source-aware headings",
                ),
            ),
        ],
        [
            _passage(
                "passage-1",
                "The parser chunks passages by source-aware headings.",
            ),
        ],
        paper_id="paper-1",
    )

    assert len(result.accepted_cards) == 1
