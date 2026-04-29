from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from paper_engine.pdf.jobs import queue_parse_run
from paper_engine.pdf.models import (
    ParseDocument,
    ParseElement,
    PassageRecord,
    PdfQualityReport,
)
from paper_engine.pdf.worker import ParseWorker, ParserFactory
from paper_engine.storage.database import init_db


class FakeBackend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[Path] = []

    def is_available(self) -> bool:
        return True

    def parse(
        self,
        file_path: Path,
        paper_id: str,
        space_id: str,
        quality_report: PdfQualityReport,
    ) -> ParseDocument:
        self.calls.append(file_path)
        return ParseDocument(
            paper_id=paper_id,
            space_id=space_id,
            backend=self.name,
            extraction_method="layout_model",
            quality=quality_report,
            elements=[
                ParseElement(
                    id="element-1",
                    element_index=0,
                    element_type="paragraph",
                    text="parsed text",
                    page_number=1,
                    extraction_method="layout_model",
                )
            ],
        )


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(database_path=tmp_path / "test.db")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, file_path, file_hash)
        VALUES ('paper-1', 'space-1', ?, 'hash')
        """,
        (str(pdf),),
    )
    conn.commit()
    return conn


def test_worker_executes_selected_parser_and_persists(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="mineru",
        parser_config={"parser_backend": "mineru"},
    )
    backend = FakeBackend("mineru")

    persisted: dict[str, Any] = {}

    def fake_persist(
        conn_arg: sqlite3.Connection,
        paper_id: str,
        space_id: str,
        document: ParseDocument,
        passages: list[PassageRecord],
        *,
        parse_run_id: str | None = None,
    ) -> str:
        persisted["document"] = document
        persisted["passages"] = passages
        return parse_run_id or "parse-run-storage-id"

    worker = ParseWorker(
        conn_factory=lambda: conn,
        worker_id="worker-1",
        parser_factory=ParserFactory(
            mineru=lambda config: backend,
            docling=lambda config: FakeBackend("docling"),
        ),
        persist_parse_result=fake_persist,
        embed_passages_for_parse_run=lambda conn_arg, parse_run_id: [],
        inspect_pdf=lambda file_path: PdfQualityReport(page_count=1),
        chunk_parse_document=lambda document: [
            PassageRecord(
                id="passage-1",
                paper_id=document.paper_id,
                space_id=document.space_id,
                original_text="parsed text",
                element_ids=["element-1"],
                parser_backend=document.backend,
                extraction_method="layout_model",
            )
        ],
        close_connection=False,
    )

    assert worker.run_once() is True

    row = conn.execute("SELECT status FROM parse_runs").fetchone()
    paper = conn.execute(
        "SELECT parse_status FROM papers WHERE id = 'paper-1'"
    ).fetchone()
    assert row["status"] == "completed"
    assert paper["parse_status"] == "parsed"
    assert persisted["document"].backend == "mineru"
    assert backend.calls


def test_worker_fails_missing_file(tmp_path: Path) -> None:
    conn = init_db(database_path=tmp_path / "test.db")
    conn.execute("INSERT INTO spaces (id, name) VALUES ('space-1', 'Space')")
    conn.execute(
        """
        INSERT INTO papers (id, space_id, file_path, file_hash)
        VALUES ('paper-1', 'space-1', ?, 'hash')
        """,
        (str(tmp_path / "missing.pdf"),),
    )
    queue_parse_run(
        conn,
        paper_id="paper-1",
        space_id="space-1",
        parser_backend="docling",
        parser_config={"parser_backend": "docling"},
    )

    worker = ParseWorker(
        conn_factory=lambda: conn,
        worker_id="worker-1",
        close_connection=False,
    )

    assert worker.run_once() is True
    row = conn.execute("SELECT status, last_error FROM parse_runs").fetchone()
    assert row["status"] == "failed"
    assert "PDF file not found" in row["last_error"]
