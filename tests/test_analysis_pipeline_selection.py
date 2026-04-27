"""Tests for full-paper passage selection and batching."""

from typing import Any

import analysis_pipeline
from pdf_chunker import count_text_tokens


def _passage(
    passage_id: str,
    text: str,
    *,
    page_number: int = 1,
    passage_type: str = "body",
    section: str = "",
    heading_path: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": passage_id,
        "paper_id": "paper-1",
        "space_id": "space-1",
        "section": section or (heading_path or [""])[-1],
        "page_number": page_number,
        "paragraph_index": page_number,
        "original_text": text,
        "passage_type": passage_type,
        "heading_path": heading_path or [],
    }


def _selected_source_ids(
    batches: list[analysis_pipeline.AnalysisPassageBatch],
) -> list[str]:
    return [
        source_id
        for batch in batches
        for source_id in batch.source_passage_ids
    ]


def test_selects_late_result_and_limitation_passages_from_full_paper() -> None:
    """Selection must scan the full paper rather than stopping at 60 passages."""
    early_body = [
        _passage(
            f"passage-body-{index}",
            f"Background filler passage {index} with ordinary context.",
            page_number=index + 1,
            heading_path=["Background"],
        )
        for index in range(65)
    ]
    late_result = _passage(
        "passage-late-result",
        "The late results section reports a 14 percent source coverage gain.",
        page_number=66,
        passage_type="result",
        heading_path=["Results"],
    )
    late_limitation = _passage(
        "passage-late-limitation",
        "The late limitations section says scanned papers remain difficult.",
        page_number=67,
        passage_type="limitation",
        heading_path=["Limitations"],
    )
    reference = _passage(
        "passage-reference",
        "Smith, A. 2025. Bibliographic entry.",
        page_number=68,
        passage_type="reference",
        heading_path=["References"],
    )

    batches = analysis_pipeline.select_analysis_passage_batches(
        [*early_body, late_result, late_limitation, reference],
        max_batch_tokens=220,
    )

    source_ids = _selected_source_ids(batches)
    assert "passage-late-result" in source_ids
    assert "passage-late-limitation" in source_ids
    assert "passage-reference" not in source_ids


def test_groups_by_heading_and_type_and_prioritizes_paper_sections() -> None:
    """Batches should be grouped by source structure and ordered by analysis value."""
    passages = [
        _passage("body-1", "General background.", heading_path=["Related Work"]),
        _passage(
            "result-1",
            "The method improves retrieval quality.",
            passage_type="result",
            heading_path=["Results"],
        ),
        _passage(
            "abstract-1",
            "The abstract states the core contribution.",
            passage_type="abstract",
            heading_path=["Abstract"],
        ),
        _passage(
            "method-1",
            "The parser uses heading-aware chunking.",
            passage_type="method",
            heading_path=["Methods", "Chunking"],
        ),
        _passage(
            "method-2",
            "The tokenizer budget is shared with PDF chunking.",
            passage_type="method",
            heading_path=["Methods", "Chunking"],
        ),
        _passage(
            "discussion-1",
            "The discussion compares local and cloud parsing.",
            passage_type="discussion",
            heading_path=["Discussion"],
        ),
        _passage(
            "intro-1",
            "The introduction motivates local-first paper analysis.",
            passage_type="introduction",
            heading_path=["Introduction"],
        ),
        _passage(
            "limitation-1",
            "The limitation is OCR quality on scanned documents.",
            passage_type="limitation",
            heading_path=["Limitations"],
        ),
    ]

    batches = analysis_pipeline.select_analysis_passage_batches(
        passages,
        max_batch_tokens=500,
    )

    ordered_groups = [
        (batch.passage_type, list(batch.heading_path))
        for batch in batches
    ]
    assert ordered_groups[:6] == [
        ("abstract", ["Abstract"]),
        ("introduction", ["Introduction"]),
        ("method", ["Methods", "Chunking"]),
        ("result", ["Results"]),
        ("discussion", ["Discussion"]),
        ("limitation", ["Limitations"]),
    ]
    method_batch = next(batch for batch in batches if batch.passage_type == "method")
    assert method_batch.source_passage_ids == ("method-1", "method-2")


def test_batches_respect_token_budget_with_pdf_chunker_counter() -> None:
    """Each LLM request batch should stay under the configured token budget."""
    passages = [
        _passage(
            f"method-{index}",
            " ".join([f"calibrated extraction signal {index}"] * 5),
            passage_type="method",
            heading_path=["Methods"],
        )
        for index in range(8)
    ]
    budget = 80

    batches = analysis_pipeline.select_analysis_passage_batches(
        passages,
        max_batch_tokens=budget,
    )

    assert len(batches) > 1
    assert all(batch.token_count <= budget for batch in batches)
    assert sum(len(batch.passages) for batch in batches) == len(passages)

    counted_prompt_payload = "\n".join(
        f"{batch.group_key}:{','.join(batch.source_passage_ids)}"
        for batch in batches
    )
    assert count_text_tokens(counted_prompt_payload) > 0


def test_references_can_be_included_for_citation_analysis() -> None:
    """Citation-specific callers should be able to include reference passages."""
    reference = _passage(
        "passage-reference",
        "Smith, A. 2025. Bibliographic entry.",
        passage_type="reference",
        heading_path=["References"],
    )

    batches = analysis_pipeline.select_analysis_passage_batches(
        [reference],
        max_batch_tokens=120,
        include_references=True,
    )

    assert _selected_source_ids(batches) == ["passage-reference"]
