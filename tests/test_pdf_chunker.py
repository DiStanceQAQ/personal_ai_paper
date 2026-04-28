"""Behavior tests for structure-aware PDF parse chunking."""

import pytest

import paper_engine.pdf.chunking as pdf_chunker
from paper_engine.pdf.chunking import chunk_parse_document
from paper_engine.pdf.models import ParseDocument, ParseElement, ParseTable, PdfQualityReport


def _element(
    element_id: str,
    element_index: int,
    element_type: str,
    text: str,
    *,
    page_number: int = 1,
    heading_path: list[str] | None = None,
    metadata: dict | None = None,
) -> ParseElement:
    return ParseElement(
        id=element_id,
        element_index=element_index,
        element_type=element_type,
        text=text,
        page_number=page_number,
        heading_path=heading_path or [],
        extraction_method="native_text",
        metadata=metadata or {},
    )


def _document(
    elements: list[ParseElement],
    *,
    tables: list[ParseTable] | None = None,
    paper_id: str = "paper-chunker",
    space_id: str = "space-chunker",
) -> ParseDocument:
    return ParseDocument(
        paper_id=paper_id,
        space_id=space_id,
        backend="structured-parser",
        extraction_method="native_text",
        quality=PdfQualityReport(page_count=4, native_text_pages=4),
        elements=elements,
        tables=tables or [],
        metadata={"parse_run_id": "parse-run-1"},
    )


def _passage_containing(passages, text: str):
    matches = [passage for passage in passages if text in passage.original_text]
    assert len(matches) == 1
    return matches[0]


def _payload_after_header(passage_text: str, header_line: str) -> str:
    return passage_text.split(header_line, 1)[1].lstrip()


def test_preserves_section_boundaries_and_heading_metadata() -> None:
    """Passages should not merge body text from different heading paths."""
    doc = _document(
        [
            _element("intro-heading", 0, "heading", "1 Introduction", heading_path=[]),
            _element(
                "intro-p1",
                1,
                "paragraph",
                "Introduction establishes the retrieval problem and scope.",
                heading_path=["Introduction"],
            ),
            _element(
                "intro-p2",
                2,
                "paragraph",
                "The motivating example remains within the introduction.",
                heading_path=["Introduction"],
            ),
            _element("methods-heading", 3, "heading", "2 Methods", heading_path=[]),
            _element(
                "methods-p1",
                4,
                "paragraph",
                "Methods describe the segmentation pipeline and evidence model.",
                heading_path=["Methods"],
            ),
        ]
    )

    passages = chunk_parse_document(doc, max_tokens=80, soft_tokens=60, overlap_tokens=0)

    intro = _passage_containing(passages, "retrieval problem")
    methods = _passage_containing(passages, "segmentation pipeline")
    assert "segmentation pipeline" not in intro.original_text
    assert "retrieval problem" not in methods.original_text
    assert intro.heading_path == ["Introduction"]
    assert intro.section == "Introduction"
    assert intro.element_ids == ["intro-p1", "intro-p2"]
    assert methods.heading_path == ["Methods"]
    assert methods.section == "Methods"
    assert methods.element_ids == ["methods-p1"]
    assert intro.paper_id == doc.paper_id
    assert intro.space_id == doc.space_id
    assert intro.parser_backend == doc.backend
    assert intro.extraction_method == doc.extraction_method


def test_respects_max_token_budget_for_paragraph_chunks() -> None:
    """Ordinary paragraph passages should never exceed the hard token budget."""
    elements = [
        _element(
            f"method-p{i}",
            i,
            "paragraph",
            (
                f"Method paragraph {i} describes calibrated extraction signals, "
                "layout cues, and evidence ranking for repeatable retrieval."
            ),
            heading_path=["Methods"],
        )
        for i in range(12)
    ]
    doc = _document(elements)

    passages = chunk_parse_document(doc, max_tokens=35, soft_tokens=25, overlap_tokens=0)

    assert len(passages) > 1
    assert all(passage.token_count <= 35 for passage in passages)
    assert all(passage.heading_path == ["Methods"] for passage in passages)
    passage_element_ids = [
        element_id for passage in passages for element_id in passage.element_ids
    ]
    assert set(passage_element_ids) == {element.id for element in elements}
    assert len(passage_element_ids) == len(set(passage_element_ids))


def test_overlaps_adjacent_split_chunks_with_clear_provenance() -> None:
    """Split chunks should carry enough overlap provenance for retrieval continuity."""
    elements = [
        _element(
            f"results-p{i}",
            i,
            "paragraph",
            (
                f"Results paragraph {i} reports ablation evidence, retrieval "
                "continuity, and parser quality signals across benchmark papers."
            ),
            heading_path=["Results"],
        )
        for i in range(10)
    ]
    doc = _document(elements)

    passages = chunk_parse_document(doc, max_tokens=42, soft_tokens=30, overlap_tokens=12)

    assert len(passages) > 1
    valid_element_ids = {element.id for element in elements}
    for left, right in zip(passages, passages[1:]):
        shared_element_ids = set(left.element_ids) & set(right.element_ids)
        metadata_overlap_ids = set(right.metadata.get("overlap_element_ids", []))
        assert shared_element_ids or metadata_overlap_ids
        assert set(left.element_ids) <= valid_element_ids
        assert set(right.element_ids) <= valid_element_ids
        if shared_element_ids:
            assert shared_element_ids <= valid_element_ids
            assert shared_element_ids <= set(left.element_ids)
            assert shared_element_ids <= set(right.element_ids)
        if metadata_overlap_ids:
            assert metadata_overlap_ids <= valid_element_ids
            assert metadata_overlap_ids <= set(left.element_ids)
        assert left.heading_path == right.heading_path == ["Results"]
        assert left.page_number <= right.page_number


def test_filters_references_and_page_chrome_from_searchable_body_passages() -> None:
    """References, References sections, page headers, and footers should be excluded."""
    doc = _document(
        [
            _element(
                "header-1",
                0,
                "page_header",
                "Journal of Local Paper Knowledge",
                metadata={"role": "running_header"},
            ),
            _element(
                "body-1",
                1,
                "paragraph",
                "The body passage is useful searchable evidence.",
                heading_path=["Discussion"],
            ),
            _element(
                "footer-1",
                2,
                "page_footer",
                "Page 7",
                metadata={"role": "page_number"},
            ),
            _element("refs-heading", 3, "heading", "References", heading_path=[]),
            _element(
                "ref-1",
                4,
                "reference",
                "Smith, A. 2024. Chunking papers for retrieval.",
                heading_path=["References"],
            ),
            _element(
                "ref-note",
                5,
                "paragraph",
                "Additional bibliography text under the references heading.",
                heading_path=["References"],
            ),
        ]
    )

    passages = chunk_parse_document(doc, max_tokens=100, soft_tokens=80, overlap_tokens=0)

    assert len(passages) == 1
    assert passages[0].original_text == "The body passage is useful searchable evidence."
    assert passages[0].element_ids == ["body-1"]
    assert passages[0].heading_path == ["Discussion"]
    combined_text = "".join(passage.original_text for passage in passages)
    assert "Journal of Local Paper Knowledge" not in combined_text
    assert "Page 7" not in combined_text
    assert "Smith, A." not in combined_text
    assert "bibliography text" not in combined_text


def test_isolates_tables_with_table_metadata_and_provenance() -> None:
    """A table element should become its own passage with table provenance."""
    table = ParseTable(
        id="table-1",
        element_id="table-element",
        table_index=0,
        page_number=2,
        caption="Table 1: Retrieval metrics",
        cells=[
            ["System", "Recall", "Precision"],
            ["Baseline", "0.61", "0.72"],
            ["Chunker", "0.84", "0.81"],
        ],
        metadata={"header_rows": 1},
    )
    doc = _document(
        [
            _element(
                "before-table",
                0,
                "paragraph",
                "The paragraph before the table explains the evaluation setup.",
                page_number=2,
                heading_path=["Evaluation"],
            ),
            _element(
                "table-element",
                1,
                "table",
                "Table 1: Retrieval metrics\nSystem Recall Precision\nBaseline 0.61 0.72\nChunker 0.84 0.81",
                page_number=2,
                heading_path=["Evaluation"],
            ),
            _element(
                "after-table",
                2,
                "paragraph",
                "The paragraph after the table interprets the metric changes.",
                page_number=2,
                heading_path=["Evaluation"],
            ),
        ],
        tables=[table],
    )

    passages = chunk_parse_document(doc, max_tokens=120, soft_tokens=90, overlap_tokens=0)

    table_passages = [
        passage for passage in passages if passage.element_ids == ["table-element"]
    ]
    assert len(table_passages) == 1
    table_passage = table_passages[0]
    assert "Retrieval metrics" in table_passage.original_text
    assert "Baseline" in table_passage.original_text
    assert table_passage.heading_path == ["Evaluation"]
    assert table_passage.page_number == 2
    assert table_passage.metadata["table_id"] == "table-1"
    assert table_passage.metadata["table_index"] == 0
    assert table_passage.metadata["caption"] == "Table 1: Retrieval metrics"
    assert table_passage.metadata["header_rows"] == 1
    assert _passage_containing(passages, "before the table").element_ids == [
        "before-table"
    ]
    assert _passage_containing(passages, "after the table").element_ids == [
        "after-table"
    ]


def test_splits_large_tables_by_rows_while_preserving_headers() -> None:
    """Large table passages should split by data rows and repeat header rows."""
    table = ParseTable(
        id="table-large",
        element_id="large-table-element",
        table_index=1,
        page_number=3,
        caption="Table 2: Detailed retrieval runs",
        cells=[
            ["System", "Recall", "Precision"],
            ["Run A", "signal calibration baseline", "ranking trace alpha"],
            ["Run B", "layout evidence expansion", "ranking trace beta"],
            ["Run C", "heading continuity filter", "ranking trace gamma"],
        ],
        metadata={"header_rows": 1},
    )
    doc = _document(
        [
            _element(
                "large-table-element",
                0,
                "table",
                "",
                page_number=3,
                heading_path=["Evaluation"],
            )
        ],
        tables=[table],
    )

    passages = chunk_parse_document(doc, max_tokens=24, soft_tokens=18, overlap_tokens=0)

    assert len(passages) > 1
    assert all("System | Recall | Precision" in passage.original_text for passage in passages)
    assert all(passage.metadata["table_id"] == "table-large" for passage in passages)
    assert all(passage.metadata["header_rows"] == 1 for passage in passages)
    combined_text = "".join(passage.original_text for passage in passages)
    assert "Run A" in combined_text
    assert "Run B" in combined_text
    assert "Run C" in combined_text


def test_splits_oversized_table_rows_without_dropping_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Oversized table row groups should stay within budget and preserve row text."""
    monkeypatch.setattr(
        pdf_chunker.importlib,
        "import_module",
        lambda _: (_ for _ in ()).throw(ImportError("force fallback estimator")),
    )
    table = ParseTable(
        id="table-oversized-row",
        element_id="oversized-table-element",
        table_index=2,
        page_number=3,
        caption="Table 3: Extremely detailed retrieval trace",
        cells=[
            ["System", "Recall", "Precision"],
            ["Run A", "x" * 80, "alpha"],
            ["Run B", "y" * 80, "beta"],
        ],
        metadata={"header_rows": 1},
    )
    doc = _document(
        [
            _element(
                "oversized-table-element",
                0,
                "table",
                "",
                page_number=3,
                heading_path=["Evaluation"],
            )
        ],
        tables=[table],
    )

    passages = chunk_parse_document(doc, max_tokens=12, soft_tokens=10, overlap_tokens=0)

    assert passages
    assert all(passage.token_count <= 12 for passage in passages)
    assert all(passage.metadata["table_id"] == "table-oversized-row" for passage in passages)
    assert any("row_start" in passage.metadata for passage in passages)
    row_a_text = "".join(
        _payload_after_header(passage.original_text, "System | Recall | Precision")
        for passage in passages
        if passage.metadata.get("row_start") == 0
    )
    row_b_text = "".join(
        _payload_after_header(passage.original_text, "System | Recall | Precision")
        for passage in passages
        if passage.metadata.get("row_start") == 1
    )
    assert "Run A" in row_a_text
    assert "Run B" in row_b_text
    assert "x" * 80 in row_a_text.replace(" ", "")
    assert "y" * 80 in row_b_text.replace(" ", "")


def test_oversized_single_table_row_fragments_preserve_header_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each split fragment of one large data row should repeat the table header."""
    monkeypatch.setattr(
        pdf_chunker.importlib,
        "import_module",
        lambda _: (_ for _ in ()).throw(ImportError("force fallback estimator")),
    )
    long_value = "abcdefghij" * 12
    table = ParseTable(
        id="table-split-row-header",
        element_id="split-row-table-element",
        table_index=4,
        page_number=3,
        caption="Table 5: split row",
        cells=[
            ["ID", "Value"],
            ["RunA", long_value],
        ],
        metadata={"header_rows": 1},
    )
    doc = _document(
        [
            _element(
                "split-row-table-element",
                0,
                "table",
                "",
                page_number=3,
                heading_path=["Evaluation"],
            )
        ],
        tables=[table],
    )

    passages = chunk_parse_document(doc, max_tokens=14, soft_tokens=10, overlap_tokens=0)

    row_passages = [
        passage
        for passage in passages
        if passage.metadata.get("table_id") == "table-split-row-header"
        and passage.metadata.get("row_start") == 0
    ]
    assert len(row_passages) > 1
    assert all("ID | Value" in passage.original_text for passage in row_passages)
    assert all(passage.token_count <= 14 for passage in row_passages)
    assert {passage.metadata["row_end"] for passage in row_passages} == {1}
    assert [passage.metadata["split_index"] for passage in row_passages] == list(
        range(len(row_passages))
    )
    assert all(
        passage.metadata["split_count"] == len(row_passages)
        for passage in row_passages
    )
    row_fragments = [
        _payload_after_header(passage.original_text, "ID | Value")
        for passage in row_passages
    ]
    assert "RunA" in "".join(row_fragments)
    assert long_value in "".join(row_fragments).replace(" ", "")


def test_splits_header_only_oversized_table_without_dropping_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-empty table with no data rows should still produce budgeted passages."""
    monkeypatch.setattr(
        pdf_chunker.importlib,
        "import_module",
        lambda _: (_ for _ in ()).throw(ImportError("force fallback estimator")),
    )
    table = ParseTable(
        id="table-header-only",
        element_id="header-only-table-element",
        table_index=3,
        page_number=3,
        caption="Table 4: " + ("longcaption" * 10),
        cells=[["VeryLongHeader" * 8, "Metric"]],
        metadata={"header_rows": 1},
    )
    doc = _document(
        [
            _element(
                "header-only-table-element",
                0,
                "table",
                "",
                page_number=3,
                heading_path=["Evaluation"],
            )
        ],
        tables=[table],
    )

    passages = chunk_parse_document(doc, max_tokens=10, soft_tokens=8, overlap_tokens=0)

    assert passages
    assert all(passage.token_count <= 10 for passage in passages)
    combined_text = "".join(passage.original_text for passage in passages)
    assert "longcaption" in combined_text
    assert "VeryLongHeader" in combined_text


def test_splits_long_unsegmented_text_with_fallback_estimator(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single long token should be split by deterministic fallback budgeting."""
    monkeypatch.setattr(
        pdf_chunker.importlib,
        "import_module",
        lambda _: (_ for _ in ()).throw(ImportError("force fallback estimator")),
    )
    long_token = "https://example.test/" + ("abcdef0123456789" * 20)
    doc = _document(
        [
            _element(
                "long-token",
                0,
                "paragraph",
                long_token,
                heading_path=["Methods"],
            )
        ]
    )

    passages = chunk_parse_document(doc, max_tokens=12, soft_tokens=10, overlap_tokens=0)

    assert len(passages) > 1
    assert all(passage.token_count <= 12 for passage in passages)
    assert "".join(passage.original_text for passage in passages) == long_token


def test_repeated_identical_splits_have_unique_deterministic_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identical split text from the same source should not duplicate passage IDs."""
    monkeypatch.setattr(
        pdf_chunker.importlib,
        "import_module",
        lambda _: (_ for _ in ()).throw(ImportError("force fallback estimator")),
    )
    repeated_token = "z" * 80
    doc = _document(
        [
            _element(
                "repeated-split-source",
                0,
                "paragraph",
                repeated_token * 2,
                heading_path=["Methods"],
            )
        ]
    )

    passages = chunk_parse_document(doc, max_tokens=10, soft_tokens=8, overlap_tokens=0)
    second_passages = chunk_parse_document(doc, max_tokens=10, soft_tokens=8, overlap_tokens=0)

    assert len(passages) > 1
    assert len({passage.id for passage in passages}) == len(passages)
    assert [passage.id for passage in passages] == [passage.id for passage in second_passages]


def test_skips_empty_unmatched_table_placeholders_without_crashing() -> None:
    """Empty table placeholders without ParseTable detail should be ignored."""
    doc = _document(
        [
            _element(
                "before-empty-table",
                0,
                "paragraph",
                "Body text before an empty table placeholder.",
                heading_path=["Results"],
            ),
            _element(
                "empty-table-placeholder",
                1,
                "table",
                "",
                heading_path=["Results"],
            ),
            _element(
                "after-empty-table",
                2,
                "paragraph",
                "Body text after an empty table placeholder.",
                heading_path=["Results"],
            ),
        ]
    )

    passages = chunk_parse_document(doc, max_tokens=100, soft_tokens=80, overlap_tokens=0)

    assert len(passages) == 2
    assert [passage.element_ids for passage in passages] == [
        ["before-empty-table"],
        ["after-empty-table"],
    ]


def test_content_hashes_are_deterministic_normalized_and_content_sensitive() -> None:
    """Content hashes should cover normalized content and public provenance fields."""
    base_doc = _document(
        [
            _element(
                "abstract-p1",
                0,
                "paragraph",
                "Stable hashes use normalized text for repeated ingestion.",
                page_number=1,
                heading_path=["Abstract"],
            )
        ]
    )
    whitespace_doc = _document(
        [
            _element(
                "abstract-p1",
                0,
                "paragraph",
                "Stable   hashes use\nnormalized text for repeated ingestion.",
                page_number=1,
                heading_path=["Abstract"],
            )
        ]
    )
    changed_doc = _document(
        [
            _element(
                "abstract-p1",
                0,
                "paragraph",
                "Stable hashes use normalized text for changed ingestion.",
                page_number=1,
                heading_path=["Abstract"],
            )
        ]
    )
    different_heading_doc = _document(
        [
            _element(
                "abstract-p1",
                0,
                "paragraph",
                "Stable hashes use normalized text for repeated ingestion.",
                page_number=1,
                heading_path=["Summary"],
            )
        ]
    )
    different_page_doc = _document(
        [
            _element(
                "abstract-p1",
                0,
                "paragraph",
                "Stable hashes use normalized text for repeated ingestion.",
                page_number=2,
                heading_path=["Abstract"],
            )
        ]
    )
    different_element_doc = _document(
        [
            _element(
                "abstract-p2",
                0,
                "paragraph",
                "Stable hashes use normalized text for repeated ingestion.",
                page_number=1,
                heading_path=["Abstract"],
            )
        ]
    )

    first = chunk_parse_document(base_doc, max_tokens=80, soft_tokens=60, overlap_tokens=0)
    second = chunk_parse_document(base_doc, max_tokens=80, soft_tokens=60, overlap_tokens=0)
    whitespace = chunk_parse_document(
        whitespace_doc,
        max_tokens=80,
        soft_tokens=60,
        overlap_tokens=0,
    )
    changed = chunk_parse_document(
        changed_doc,
        max_tokens=80,
        soft_tokens=60,
        overlap_tokens=0,
    )
    different_heading = chunk_parse_document(
        different_heading_doc,
        max_tokens=80,
        soft_tokens=60,
        overlap_tokens=0,
    )
    different_page = chunk_parse_document(
        different_page_doc,
        max_tokens=80,
        soft_tokens=60,
        overlap_tokens=0,
    )
    different_element = chunk_parse_document(
        different_element_doc,
        max_tokens=80,
        soft_tokens=60,
        overlap_tokens=0,
    )

    assert [passage.content_hash for passage in first] == [
        passage.content_hash for passage in second
    ]
    assert all(passage.content_hash for passage in first)
    assert first[0].content_hash == whitespace[0].content_hash
    assert first[0].content_hash != changed[0].content_hash
    assert first[0].heading_path != different_heading[0].heading_path
    assert first[0].content_hash != different_heading[0].content_hash
    assert first[0].page_number != different_page[0].page_number
    assert first[0].content_hash != different_page[0].content_hash
    assert first[0].element_ids != different_element[0].element_ids
    assert first[0].content_hash != different_element[0].content_hash
