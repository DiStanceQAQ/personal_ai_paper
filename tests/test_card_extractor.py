"""Tests for rules-based card extraction."""

from card_extractor import extract_cards_from_passages


def test_extract_method_card() -> None:
    passages = [
        {
            "id": "p1", "paper_id": "paper-1", "space_id": "space-1",
            "section": "method", "page_number": 3, "paragraph_index": 0,
            "original_text": "We propose a novel transformer architecture for sequence modeling.",
            "passage_type": "method",
        }
    ]
    cards = extract_cards_from_passages(passages, "paper-1", "space-1")
    method_cards = [c for c in cards if c["card_type"] == "Method"]
    assert len(method_cards) >= 1
    assert method_cards[0]["source_passage_id"] == "p1"
    assert method_cards[0]["confidence"] > 0


def test_extract_result_card() -> None:
    passages = [
        {
            "id": "p2", "paper_id": "paper-1", "space_id": "space-1",
            "section": "result", "page_number": 5, "paragraph_index": 0,
            "original_text": "Our model achieves 95.3% accuracy on the benchmark.",
            "passage_type": "result",
        }
    ]
    cards = extract_cards_from_passages(passages, "paper-1", "space-1")
    assert any(c["card_type"] == "Result" for c in cards)


def test_extract_limitation_card() -> None:
    passages = [
        {
            "id": "p3", "paper_id": "paper-1", "space_id": "space-1",
            "section": "limitation", "page_number": 8, "paragraph_index": 0,
            "original_text": "Our approach has several limitations. Future work should address scalability.",
            "passage_type": "limitation",
        }
    ]
    cards = extract_cards_from_passages(passages, "paper-1", "space-1")
    assert any(c["card_type"] == "Limitation" for c in cards)


def test_extract_metric_card() -> None:
    passages = [
        {
            "id": "p4", "paper_id": "paper-1", "space_id": "space-1",
            "section": "method", "page_number": 4, "paragraph_index": 0,
            "original_text": "We evaluate using precision, recall, and F1 score.",
            "passage_type": "method",
        }
    ]
    cards = extract_cards_from_passages(passages, "paper-1", "space-1")
    assert any(c["card_type"] == "Metric" for c in cards)


def test_extraction_binds_source_passage() -> None:
    passages = [
        {
            "id": "p5", "paper_id": "paper-1", "space_id": "space-1",
            "section": "method", "page_number": 3, "paragraph_index": 0,
            "original_text": "We present a new method for data augmentation.",
            "passage_type": "method",
        }
    ]
    cards = extract_cards_from_passages(passages, "paper-1", "space-1")
    for c in cards:
        assert c["source_passage_id"] is not None
        assert c["paper_id"] == "paper-1"


def test_extraction_empty_passages() -> None:
    """Test that empty passage list returns no cards."""
    cards = extract_cards_from_passages([], "paper-1", "space-1")
    assert cards == []
