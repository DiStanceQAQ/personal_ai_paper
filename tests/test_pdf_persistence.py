"""Tests for transactional structured parse persistence."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from paper_engine.storage.database import init_db
from paper_engine.pdf.models import (
    ParseAsset,
    ParseDocument,
    ParseElement,
    ParseTable,
    PassageRecord,
    PdfQualityReport,
)
from paper_engine.pdf.persistence import persist_parse_result


def _test_conn() -> sqlite3.Connection:
    db_path = Path(tempfile.mkdtemp()) / "test.db"
    return init_db(database_path=db_path)


def _seed_space_and_paper(
    conn: sqlite3.Connection,
    *,
    space_id: str = "space-1",
    paper_id: str = "paper-1",
) -> None:
    conn.execute(
        "INSERT INTO spaces (id, name, description) VALUES (?, ?, ?)",
        (space_id, "Space", ""),
    )
    conn.execute(
        "INSERT INTO papers (id, space_id, title) VALUES (?, ?, ?)",
        (paper_id, space_id, "Paper"),
    )
    conn.commit()


def _document(
    *,
    paper_id: str = "paper-1",
    space_id: str = "space-1",
    suffix: str = "a",
) -> ParseDocument:
    return ParseDocument(
        paper_id=paper_id,
        space_id=space_id,
        backend="unit-backend",
        extraction_method="layout_model",
        quality=PdfQualityReport(
            quality_score=0.91,
            warnings=["low contrast"],
            metadata={"pages": 2},
        ),
        elements=[
            ParseElement(
                id=f"element-{suffix}-1",
                element_index=0,
                element_type="heading",
                text="Introduction",
                page_number=1,
                bbox=[1.0, 2.0, 3.0, 4.0],
                heading_path=["Introduction"],
                extraction_method="layout_model",
                metadata={"role": "heading"},
            ),
            ParseElement(
                id=f"element-{suffix}-2",
                element_index=1,
                element_type="table",
                text="Table text",
                page_number=1,
                extraction_method="layout_model",
            ),
        ],
        tables=[
            ParseTable(
                id=f"table-{suffix}-1",
                element_id=f"element-{suffix}-2",
                table_index=0,
                page_number=1,
                caption="A table",
                cells=[["A", "B"], ["1", "2"]],
                bbox=[0.0, 0.0, 10.0, 10.0],
                metadata={"source": "unit"},
            )
        ],
        assets=[
            ParseAsset(
                id=f"asset-{suffix}-1",
                element_id=f"element-{suffix}-2",
                asset_type="figure",
                page_number=2,
                uri="file://figure.png",
                bbox=[2.0, 2.0, 8.0, 8.0],
                metadata={"mime": "image/png"},
            )
        ],
        metadata={"parser": "test"},
    )


def _passage(
    passage_id: str,
    content_hash: str | None,
    *,
    paper_id: str = "paper-1",
    space_id: str = "space-1",
    element_id: str = "element-a-1",
    text: str | None = None,
) -> PassageRecord:
    return PassageRecord(
        id=passage_id,
        paper_id=paper_id,
        space_id=space_id,
        section="Introduction",
        page_number=1,
        paragraph_index=0,
        original_text=text or f"Text for {passage_id}",
        parse_confidence=0.88,
        passage_type="introduction",
        parse_run_id="input-provenance",
        element_ids=[element_id],
        heading_path=["Introduction"],
        bbox=[1.0, 2.0, 3.0, 4.0],
        token_count=5,
        char_count=20,
        content_hash=content_hash,
        parser_backend="input-backend",
        extraction_method="native_text",
        quality_flags=["ok"],
    )


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _stored_id(parse_run_id: str, source_id: str) -> str:
    return f"{parse_run_id}:{source_id}"


def test_persist_parse_result_inserts_structured_rows_and_fts() -> None:
    conn = _test_conn()
    _seed_space_and_paper(conn)

    parse_run_id = persist_parse_result(
        conn,
        "paper-1",
        "space-1",
        _document(),
        [_passage("passage-1", "hash-1")],
    )

    assert parse_run_id.startswith("parse-run-")
    parse_run = conn.execute("SELECT * FROM parse_runs WHERE id = ?", (parse_run_id,)).fetchone()
    assert parse_run["paper_id"] == "paper-1"
    assert parse_run["space_id"] == "space-1"
    assert parse_run["backend"] == "unit-backend"
    assert parse_run["extraction_method"] == "layout_model"
    assert parse_run["status"] == "completed"
    assert parse_run["quality_score"] == 0.91
    assert json.loads(parse_run["warnings_json"]) == ["low contrast"]
    assert json.loads(parse_run["metadata_json"]) == {"parser": "test"}

    element = conn.execute(
        "SELECT * FROM document_elements WHERE id = ?",
        (_stored_id(parse_run_id, "element-a-1"),),
    ).fetchone()
    assert element["parse_run_id"] == parse_run_id
    assert json.loads(element["bbox_json"]) == [1.0, 2.0, 3.0, 4.0]
    assert json.loads(element["heading_path_json"]) == ["Introduction"]
    assert json.loads(element["metadata_json"]) == {
        "role": "heading",
        "source_element_id": "element-a-1",
    }

    table = conn.execute(
        "SELECT * FROM document_tables WHERE id = ?",
        (_stored_id(parse_run_id, "table-a-1"),),
    ).fetchone()
    assert table["element_id"] == _stored_id(parse_run_id, "element-a-2")
    assert json.loads(table["cells_json"]) == [["A", "B"], ["1", "2"]]
    assert json.loads(table["metadata_json"]) == {
        "source": "unit",
        "source_table_id": "table-a-1",
    }

    asset = conn.execute(
        "SELECT * FROM document_assets WHERE id = ?",
        (_stored_id(parse_run_id, "asset-a-1"),),
    ).fetchone()
    assert asset["element_id"] == _stored_id(parse_run_id, "element-a-2")
    assert asset["uri"] == "file://figure.png"
    assert json.loads(asset["metadata_json"]) == {
        "mime": "image/png",
        "source_asset_id": "asset-a-1",
    }

    passage = conn.execute(
        "SELECT * FROM passages WHERE id = ?",
        (_stored_id(parse_run_id, "passage-1"),),
    ).fetchone()
    assert passage["parse_run_id"] == parse_run_id
    assert json.loads(passage["element_ids_json"]) == [
        _stored_id(parse_run_id, "element-a-1")
    ]
    assert json.loads(passage["heading_path_json"]) == ["Introduction"]
    assert json.loads(passage["bbox_json"]) == [1.0, 2.0, 3.0, 4.0]
    assert passage["content_hash"] == "hash-1"
    assert passage["parser_backend"] == "input-backend"
    assert passage["extraction_method"] == "native_text"
    assert json.loads(passage["quality_flags_json"]) == ["ok"]

    fts = conn.execute(
        "SELECT passage_id, paper_id, space_id, section, original_text FROM passages_fts"
    ).fetchall()
    assert [dict(row) for row in fts] == [
        {
            "passage_id": _stored_id(parse_run_id, "passage-1"),
            "paper_id": "paper-1",
            "space_id": "space-1",
            "section": "Introduction",
            "original_text": "Text for passage-1",
        }
    ]


def test_persist_parse_result_can_use_existing_queued_parse_run() -> None:
    conn = _test_conn()
    _seed_space_and_paper(conn)
    conn.execute(
        """
        INSERT INTO parse_runs (
            id, paper_id, space_id, backend, status, config_json, metadata_json
        )
        VALUES (
            'queued-run-1',
            'paper-1',
            'space-1',
            'docling',
            'running',
            '{"parser_backend":"docling"}',
            '{"progress":{"stage":"chunking","label":"切分论文片段","progress":64,"details":{"element_count":2}}}'
        )
        """
    )
    conn.commit()

    document = _document()
    passages = [_passage("passage-1", "hash-1")]

    parse_run_id = persist_parse_result(
        conn,
        "paper-1",
        "space-1",
        document,
        passages,
        parse_run_id="queued-run-1",
    )

    assert parse_run_id == "queued-run-1"
    run = conn.execute("SELECT * FROM parse_runs WHERE id = 'queued-run-1'").fetchone()
    assert run["backend"] == document.backend
    assert run["status"] == "running"
    assert json.loads(run["config_json"]) == {"parser_backend": "docling"}
    metadata = json.loads(run["metadata_json"])
    assert metadata["parser"] == "test"
    assert metadata["progress"]["stage"] == "chunking"
    assert metadata["progress"]["details"]["element_count"] == 2
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM document_elements WHERE parse_run_id = 'queued-run-1'"
        ).fetchone()[0]
        == len(document.elements)
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM passages WHERE parse_run_id = 'queued-run-1'"
        ).fetchone()[0]
        == len(passages)
    )


def test_persist_parse_result_namespaces_local_ids_across_papers() -> None:
    conn = _test_conn()
    _seed_space_and_paper(conn, paper_id="paper-1")
    conn.execute(
        "INSERT INTO papers (id, space_id, title) VALUES (?, ?, ?)",
        ("paper-2", "space-1", "Second Paper"),
    )
    conn.commit()

    first_run_id = persist_parse_result(
        conn,
        "paper-1",
        "space-1",
        _document(paper_id="paper-1", suffix="local"),
        [
            _passage(
                "passage-local-1",
                "same-hash",
                paper_id="paper-1",
                element_id="element-local-1",
            )
        ],
    )
    second_run_id = persist_parse_result(
        conn,
        "paper-2",
        "space-1",
        _document(paper_id="paper-2", suffix="local"),
        [
            _passage(
                "passage-local-1",
                "same-hash",
                paper_id="paper-2",
                element_id="element-local-1",
            )
        ],
    )

    element_rows = conn.execute(
        """
        SELECT id, parse_run_id, metadata_json
        FROM document_elements
        WHERE element_index = 0
        ORDER BY paper_id
        """
    ).fetchall()
    element_ids = [row["id"] for row in element_rows]
    assert len(set(element_ids)) == 2
    assert element_ids == [
        f"{first_run_id}:element-local-1",
        f"{second_run_id}:element-local-1",
    ]
    assert [
        json.loads(row["metadata_json"])["source_element_id"] for row in element_rows
    ] == ["element-local-1", "element-local-1"]

    table_rows = conn.execute(
        "SELECT id, element_id, metadata_json FROM document_tables ORDER BY paper_id"
    ).fetchall()
    assert [row["element_id"] for row in table_rows] == [
        f"{first_run_id}:element-local-2",
        f"{second_run_id}:element-local-2",
    ]
    assert [json.loads(row["metadata_json"])["source_table_id"] for row in table_rows] == [
        "table-local-1",
        "table-local-1",
    ]

    asset_rows = conn.execute(
        "SELECT id, element_id, metadata_json FROM document_assets ORDER BY paper_id"
    ).fetchall()
    assert [row["element_id"] for row in asset_rows] == [
        f"{first_run_id}:element-local-2",
        f"{second_run_id}:element-local-2",
    ]
    assert [json.loads(row["metadata_json"])["source_asset_id"] for row in asset_rows] == [
        "asset-local-1",
        "asset-local-1",
    ]

    passage_rows = conn.execute(
        """
        SELECT id, paper_id, element_ids_json, content_hash
        FROM passages
        ORDER BY paper_id
        """
    ).fetchall()
    assert [row["id"] for row in passage_rows] == [
        f"{first_run_id}:passage-local-1",
        f"{second_run_id}:passage-local-1",
    ]
    assert [json.loads(row["element_ids_json"]) for row in passage_rows] == [
        [f"{first_run_id}:element-local-1"],
        [f"{second_run_id}:element-local-1"],
    ]
    assert [row["content_hash"] for row in passage_rows] == ["same-hash", "same-hash"]
    assert conn.execute("SELECT COUNT(*) FROM passages_fts").fetchone()[0] == 2


def test_reparse_replaces_generated_rows_and_remaps_card_sources_by_hash() -> None:
    conn = _test_conn()
    _seed_space_and_paper(conn)
    first_run_id = persist_parse_result(
        conn,
        "paper-1",
        "space-1",
        _document(suffix="old"),
        [
            _passage("old-passage-1", "h1", element_id="element-old-1"),
            _passage("old-passage-2", "h2", element_id="element-old-1"),
        ],
    )
    conn.execute(
        """
        INSERT INTO knowledge_cards (
            id, space_id, paper_id, source_passage_id, card_type, summary,
            evidence_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "card-h1",
            "space-1",
            "paper-1",
            _stored_id(first_run_id, "old-passage-1"),
            "Evidence",
            "kept",
            json.dumps(
                {
                    "source_passage_ids": [
                        _stored_id(first_run_id, "old-passage-1"),
                        _stored_id(first_run_id, "old-passage-2"),
                        "manual-passage",
                    ],
                    "reasoning_summary": "old ids should be remapped",
                }
            ),
            "2000-01-01 00:00:00",
            "card-h2",
            "space-1",
            "paper-1",
            _stored_id(first_run_id, "old-passage-2"),
            "Evidence",
            "nulled",
            "{}",
            "2000-01-01 00:00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO knowledge_card_sources (
            id, card_id, passage_id, paper_id, space_id, analysis_run_id,
            evidence_quote, confidence, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source-h1",
            "card-h1",
            _stored_id(first_run_id, "old-passage-1"),
            "paper-1",
            "space-1",
            None,
            "quote h1",
            0.9,
            '{"source":"old-h1"}',
            "source-h2",
            "card-h1",
            _stored_id(first_run_id, "old-passage-2"),
            "paper-1",
            "space-1",
            None,
            "quote h2",
            0.8,
            '{"source":"old-h2"}',
        ),
    )
    conn.commit()

    second_run_id = persist_parse_result(
        conn,
        "paper-1",
        "space-1",
        _document(suffix="new"),
        [
            _passage("new-passage-1", "h1", element_id="element-new-1"),
            _passage("new-passage-3", "h3", element_id="element-new-1"),
        ],
    )

    assert second_run_id != first_run_id
    assert conn.execute(
        "SELECT COUNT(*) FROM parse_runs WHERE id = ?", (first_run_id,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM document_elements WHERE parse_run_id = ?",
        (first_run_id,),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM document_elements WHERE parse_run_id = ?",
        (second_run_id,),
    ).fetchone()[0] == len(_document(suffix="new").elements)
    assert {row["id"] for row in conn.execute("SELECT id FROM passages")} == {
        _stored_id(second_run_id, "new-passage-1"),
        _stored_id(second_run_id, "new-passage-3"),
    }
    assert {row["passage_id"] for row in conn.execute("SELECT passage_id FROM passages_fts")} == {
        _stored_id(second_run_id, "new-passage-1"),
        _stored_id(second_run_id, "new-passage-3"),
    }

    cards = {
        row["id"]: (
            row["source_passage_id"],
            row["updated_at"],
            json.loads(row["evidence_json"]),
        )
        for row in conn.execute(
            "SELECT id, source_passage_id, updated_at, evidence_json FROM knowledge_cards"
        )
    }
    assert cards["card-h1"][0] == _stored_id(second_run_id, "new-passage-1")
    assert cards["card-h2"][0] is None
    assert cards["card-h1"][1] != "2000-01-01 00:00:00"
    assert cards["card-h2"][1] != "2000-01-01 00:00:00"
    assert cards["card-h1"][2]["source_passage_ids"] == [
        _stored_id(second_run_id, "new-passage-1"),
        "manual-passage",
    ]

    source_rows = conn.execute(
        """
        SELECT card_id, passage_id, evidence_quote, confidence, metadata_json
        FROM knowledge_card_sources
        ORDER BY passage_id
        """
    ).fetchall()
    assert [dict(row) for row in source_rows] == [
        {
            "card_id": "card-h1",
            "passage_id": _stored_id(second_run_id, "new-passage-1"),
            "evidence_quote": "quote h1",
            "confidence": 0.9,
            "metadata_json": '{"source":"old-h1"}',
        }
    ]


def test_persist_parse_result_rolls_back_all_changes_on_insert_failure() -> None:
    conn = _test_conn()
    _seed_space_and_paper(conn)
    old_run_id = persist_parse_result(
        conn,
        "paper-1",
        "space-1",
        _document(suffix="old"),
        [_passage("old-passage-1", "h1", element_id="element-old-1")],
    )
    conn.execute(
        """
        INSERT INTO knowledge_cards (
            id, space_id, paper_id, source_passage_id, card_type, summary
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "card-1",
            "space-1",
            "paper-1",
            _stored_id(old_run_id, "old-passage-1"),
            "Evidence",
            "kept",
        ),
    )
    conn.execute(
        """
        CREATE TRIGGER fail_new_passage
        BEFORE INSERT ON passages
        WHEN instr(NEW.id, ':new-passage-fail') > 0
        BEGIN
            SELECT RAISE(ABORT, 'forced passage insert failure');
        END
        """
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced passage insert failure"):
        persist_parse_result(
            conn,
            "paper-1",
            "space-1",
            _document(suffix="new"),
            [_passage("new-passage-fail", "h1", element_id="element-new-1")],
        )

    assert [row["id"] for row in conn.execute("SELECT id FROM parse_runs")] == [old_run_id]
    assert [row["id"] for row in conn.execute("SELECT id FROM passages")] == [
        _stored_id(old_run_id, "old-passage-1")
    ]
    assert [row["passage_id"] for row in conn.execute("SELECT passage_id FROM passages_fts")] == [
        _stored_id(old_run_id, "old-passage-1")
    ]
    card = conn.execute(
        "SELECT source_passage_id FROM knowledge_cards WHERE id = ?", ("card-1",)
    ).fetchone()
    assert card["source_passage_id"] == _stored_id(old_run_id, "old-passage-1")


def test_duplicate_content_hash_validation_happens_before_cleanup() -> None:
    conn = _test_conn()
    _seed_space_and_paper(conn)
    old_run_id = persist_parse_result(
        conn,
        "paper-1",
        "space-1",
        _document(suffix="old"),
        [_passage("old-passage-1", "h1", element_id="element-old-1")],
    )

    with pytest.raises(ValueError, match="duplicate content_hash"):
        persist_parse_result(
            conn,
            "paper-1",
            "space-1",
            _document(suffix="new"),
            [
                _passage("new-passage-1", "dup", element_id="element-new-1"),
                _passage("new-passage-2", "dup", element_id="element-new-1"),
            ],
        )

    assert _count(conn, "parse_runs") == 1
    assert [row["id"] for row in conn.execute("SELECT id FROM passages")] == [
        _stored_id(old_run_id, "old-passage-1")
    ]
