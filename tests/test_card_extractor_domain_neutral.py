"""Domain-neutral heuristic extraction tests."""

from paper_engine.cards.extraction import extract_cards_from_passages


def test_extracts_generic_protocol_and_measurement_language() -> None:
    passages = [
        {
            "id": "p1",
            "original_text": "The protocol measures sample stability after synthesis.",
        }
    ]

    cards = extract_cards_from_passages(passages, "paper-1", "space-1")
    card_types = {card["card_type"] for card in cards}

    assert "Method" in card_types
    assert "Metric" in card_types
    assert all(card["confidence"] <= 0.55 for card in cards)


def test_extracts_intervention_and_statistical_test_language() -> None:
    passages = [
        {
            "id": "p2",
            "original_text": "The cohort received an intervention and the statistical test showed a significant result.",
        }
    ]

    cards = extract_cards_from_passages(passages, "paper-1", "space-1")
    card_types = {card["card_type"] for card in cards}

    assert "Method" in card_types
    assert "Result" in card_types
