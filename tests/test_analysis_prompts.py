"""Tests for source-grounded AI analysis prompt builders."""

import tomllib
from pathlib import Path
from typing import Any

from analysis_models import CardExtraction
from analysis_prompts import (
    SourcePassageInput,
    build_card_batch_extraction_prompt,
    build_merge_dedup_prompt,
    build_metadata_extraction_prompt,
    build_section_summary_prompt,
)


RAW_DB_INTERNAL_MARKERS = (
    "parse_run_id",
    "element_ids_json",
    "bbox_json",
    "content_hash",
    "parser_backend",
    "extraction_method",
    "quality_flags_json",
    "raw-run-1",
    "sha256:abc",
    "[1, 2, 3, 4]",
)


def assert_prompt_is_grounded_and_sanitized(prompt_text: str) -> None:
    """Every prompt should expose source/page evidence without DB internals."""
    assert "source_id" in prompt_text
    assert "page_number" in prompt_text
    assert "Only use facts supported by source_id values" in prompt_text
    assert "unsupported" in prompt_text.lower()
    for marker in RAW_DB_INTERNAL_MARKERS:
        assert marker not in prompt_text


def test_analysis_prompts_is_in_packaged_runtime_modules() -> None:
    """Prompt builders should ship with packaged runtime modules."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert "analysis_prompts" in pyproject["tool"]["setuptools"]["py-modules"]


def test_metadata_prompt_lists_source_ids_pages_and_grounding_rules() -> None:
    """Metadata extraction prompt includes source IDs, pages, and evidence rules."""
    prompt = build_metadata_extraction_prompt(
        [
            SourcePassageInput(
                id="passage-title",
                page_number=1,
                text="Grounded PDF Analysis\nAda Lovelace and Grace Hopper",
                section="title",
                heading_path=["Title"],
            ),
            SourcePassageInput(
                id="passage-abstract",
                page_number=2,
                text="We introduce a local-first academic PDF analysis pipeline.",
                section="abstract",
                heading_path=["Abstract"],
            ),
        ],
        grobid_metadata={"doi": "10.1234/example", "authors": ["Ada Lovelace"]},
    )

    combined = f"{prompt.system_prompt}\n{prompt.user_prompt}"
    assert prompt.schema_name == "paper_metadata_extraction"
    assert prompt.schema["title"] == "PaperMetadataExtraction"
    assert "passage-title" in prompt.user_prompt
    assert "passage-abstract" in prompt.user_prompt
    assert '"page_number":1' in prompt.user_prompt
    assert '"page_number":2' in prompt.user_prompt
    assert "10.1234/example" in prompt.user_prompt
    assert_prompt_is_grounded_and_sanitized(combined)


def test_section_summary_prompt_sanitizes_database_passage_rows() -> None:
    """Prompt builders should convert DB-like rows into minimal evidence inputs."""
    database_row: dict[str, Any] = {
        "id": "passage-method",
        "page_number": 7,
        "original_text": "The method uses heading-aware chunks with source IDs.",
        "section": "Method",
        "heading_path": ["Methods", "Chunking"],
        "parse_run_id": "raw-run-1",
        "element_ids_json": '["element-1"]',
        "bbox_json": "[1, 2, 3, 4]",
        "content_hash": "sha256:abc",
        "parser_backend": "pymupdf4llm",
        "extraction_method": "native_text",
        "quality_flags_json": "[]",
    }

    prompt = build_section_summary_prompt("Methods", [database_row])

    combined = f"{prompt.system_prompt}\n{prompt.user_prompt}"
    assert prompt.schema_name == "section_summary_extraction"
    assert "passage-method" in prompt.user_prompt
    assert '"page_number":7' in prompt.user_prompt
    assert "The method uses heading-aware chunks" in prompt.user_prompt
    assert "Methods" in prompt.user_prompt
    assert "Chunking" in prompt.user_prompt
    assert_prompt_is_grounded_and_sanitized(combined)


def test_card_batch_prompt_uses_source_grounding_and_card_schema() -> None:
    """Card extraction prompts should use strict batch schema and allowed types."""
    prompt = build_card_batch_extraction_prompt(
        paper_id="paper-1",
        space_id="space-1",
        batch_index=3,
        passages=[
            {
                "id": "passage-result",
                "page_number": 9,
                "original_text": "The system improves source coverage by 18 percent.",
                "section": "Results",
                "heading_path": ["Results"],
                "parse_run_id": "raw-run-1",
            },
        ],
    )

    combined = f"{prompt.system_prompt}\n{prompt.user_prompt}"
    assert prompt.schema_name == "card_extraction_batch"
    assert prompt.schema["title"] == "CardExtractionBatch"
    assert "paper-1" in prompt.user_prompt
    assert "space-1" in prompt.user_prompt
    assert '"batch_index":3' in prompt.user_prompt
    assert "passage-result" in prompt.user_prompt
    assert '"page_number":9' in prompt.user_prompt
    assert "Problem" in prompt.system_prompt
    assert "Practical Tip" in prompt.system_prompt
    assert_prompt_is_grounded_and_sanitized(combined)


def test_merge_dedup_prompt_includes_candidate_sources_and_source_pages() -> None:
    """Merge prompts should cite candidate sources against a page-aware catalog."""
    prompt = build_merge_dedup_prompt(
        paper_id="paper-1",
        space_id="space-1",
        cards=[
            CardExtraction(
                card_type="Method",
                summary="The parser chunks by headings.",
                source_passage_ids=["passage-method"],
                evidence_quote="The parser chunks by headings.",
                confidence=0.7,
                reasoning_summary="The passage explicitly states the chunking method.",
            ),
            {
                "card_type": "Method",
                "summary": "Heading-aware chunks are used.",
                "source_passage_ids": ["passage-method"],
                "evidence_quote": "heading-aware chunks",
                "confidence": 0.8,
                "reasoning_summary": "Same source supports the method.",
            },
        ],
        passages=[
            SourcePassageInput(
                id="passage-method",
                page_number=4,
                text="The parser chunks by headings and keeps source IDs.",
                section="Methods",
                heading_path=["Methods"],
            ),
        ],
        max_cards=20,
    )

    combined = f"{prompt.system_prompt}\n{prompt.user_prompt}"
    assert prompt.schema_name == "card_merge_dedup"
    assert prompt.schema["title"] == "CardMergeDedup"
    assert "passage-method" in prompt.user_prompt
    assert '"page_number":4' in prompt.user_prompt
    assert '"candidate_index":0' in prompt.user_prompt
    assert '"candidate_index":1' in prompt.user_prompt
    assert "max_cards" in prompt.user_prompt
    assert_prompt_is_grounded_and_sanitized(combined)
