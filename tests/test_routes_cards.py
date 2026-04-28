"""Tests for knowledge cards API."""

import json
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from paper_engine.storage.database import DATABASE_PATH, get_connection, init_db
from main import app


PRIOR_CARD_FIELDS = {
    "id",
    "space_id",
    "paper_id",
    "source_passage_id",
    "card_type",
    "summary",
    "confidence",
    "user_edited",
    "created_at",
    "updated_at",
}

MANUAL_PROVENANCE = {
    "created_by": "user",
    "extractor_version": "",
    "analysis_run_id": None,
    "evidence_json": "{}",
    "quality_flags_json": "[]",
}

HEURISTIC_PROVENANCE = {
    "created_by": "heuristic",
    "extractor_version": "heuristic-v1",
    "analysis_run_id": None,
    "evidence_json": "{}",
}

HEURISTIC_REVIEW_FLAGS = [
    "heuristic_low_confidence",
    "needs_manual_review",
]


def assert_prior_card_fields(card: dict[str, object]) -> None:
    """Card API responses keep the original public fields."""
    assert PRIOR_CARD_FIELDS <= card.keys()


def assert_manual_provenance(card: dict[str, object]) -> None:
    """Manual card writes are marked as user-owned for AI replacement safety."""
    for key, expected_value in MANUAL_PROVENANCE.items():
        assert card[key] == expected_value


def assert_heuristic_provenance(card: dict[str, object]) -> None:
    """Heuristic extraction keeps its non-AI generated provenance."""
    for key, expected_value in HEURISTIC_PROVENANCE.items():
        assert card[key] == expected_value
    assert json.loads(str(card["quality_flags_json"])) == HEURISTIC_REVIEW_FLAGS


@pytest.fixture
def db_path() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "test.db")
        init_db(database_path=Path(db_file))
        yield db_file


@pytest.fixture
def client(db_path: str) -> Generator[AsyncClient, None, None]:
    import paper_engine.storage.database as db_module
    import paper_engine.core.config as config_module
    with tempfile.TemporaryDirectory() as spaces_tmpdir:
        odb = db_module.DATABASE_PATH; osp = config_module.SPACES_DIR
        db_module.DATABASE_PATH = Path(db_path)
        config_module.SPACES_DIR = Path(spaces_tmpdir)
        transport = ASGITransport(app=app)
        tc = AsyncClient(transport=transport, base_url="http://test")
        yield tc
        db_module.DATABASE_PATH = odb; config_module.SPACES_DIR = osp


@pytest.fixture
async def setup_space_and_paper(client: AsyncClient) -> tuple[str, str]:
    resp = await client.post("/api/spaces", json={"name": "Card Test"})
    space_id = resp.json()["id"]
    await client.put(f"/api/spaces/active/{space_id}")
    import pymupdf
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        doc = pymupdf.open(); doc.new_page(); doc.save(f.name); doc.close()
        pdf_bytes = Path(f.name).read_bytes(); Path(f.name).unlink()
    resp = await client.post("/api/papers/upload",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")})
    paper_id = resp.json()["id"]
    await client.post(f"/api/papers/{paper_id}/parse")
    return space_id, paper_id


@pytest.mark.asyncio
async def test_create_and_get_card(client: AsyncClient, setup_space_and_paper: tuple[str, str]) -> None:
    space_id, paper_id = setup_space_and_paper
    resp = await client.post("/api/cards", json={
        "paper_id": paper_id, "card_type": "Method",
        "summary": "Uses transformer architecture", "confidence": 0.9
    })
    assert resp.status_code == 200
    card = resp.json()
    assert_prior_card_fields(card)
    assert_manual_provenance(card)
    assert card["card_type"] == "Method"
    assert card["paper_id"] == paper_id

    resp = await client.get(f"/api/cards/{card['id']}")
    assert resp.status_code == 200
    fetched_card = resp.json()
    assert_prior_card_fields(fetched_card)
    assert_manual_provenance(fetched_card)


@pytest.mark.asyncio
async def test_list_cards(client: AsyncClient, setup_space_and_paper: tuple[str, str]) -> None:
    space_id, paper_id = setup_space_and_paper
    await client.post("/api/cards", json={"paper_id": paper_id, "card_type": "Method", "summary": "M1"})
    await client.post("/api/cards", json={"paper_id": paper_id, "card_type": "Result", "summary": "R1"})

    resp = await client.get("/api/cards")
    assert resp.status_code == 200
    cards = resp.json()
    assert len(cards) == 2
    for card in cards:
        assert_prior_card_fields(card)
        assert_manual_provenance(card)


@pytest.mark.asyncio
async def test_update_card(client: AsyncClient, setup_space_and_paper: tuple[str, str]) -> None:
    space_id, paper_id = setup_space_and_paper
    resp = await client.post("/api/cards", json={"paper_id": paper_id, "card_type": "Method", "summary": "Old"})
    card_id = resp.json()["id"]

    resp = await client.patch(f"/api/cards/{card_id}", json={"summary": "New"})
    assert resp.status_code == 200
    card = resp.json()
    assert_prior_card_fields(card)
    assert_manual_provenance(card)
    assert card["summary"] == "New"
    assert card["user_edited"] == 1


@pytest.mark.asyncio
async def test_delete_card(client: AsyncClient, setup_space_and_paper: tuple[str, str]) -> None:
    space_id, paper_id = setup_space_and_paper
    resp = await client.post("/api/cards", json={"paper_id": paper_id, "card_type": "Claim", "summary": "C1"})
    card_id = resp.json()["id"]

    resp = await client.delete(f"/api/cards/{card_id}")
    assert resp.status_code == 200

    resp = await client.get(f"/api/cards/{card_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_card_rejects_source_passage_from_other_paper(
    client: AsyncClient,
    setup_space_and_paper: tuple[str, str],
) -> None:
    """A card source passage must belong to the same paper and active space."""
    space_id, paper_id = setup_space_and_paper

    conn = get_connection()
    try:
        conn.execute("INSERT INTO spaces (id, name) VALUES ('other-space', 'Other')")
        conn.execute(
            """INSERT INTO papers (id, space_id, title, parse_status)
               VALUES ('other-paper', 'other-space', 'Other Paper', 'parsed')"""
        )
        conn.execute(
            """INSERT INTO passages (id, paper_id, space_id, section, original_text)
               VALUES ('foreign-passage', 'other-paper', 'other-space', 'method', 'foreign text')"""
        )
        conn.commit()
    finally:
        conn.close()

    resp = await client.post(
        "/api/cards",
        json={
            "paper_id": paper_id,
            "card_type": "Method",
            "summary": "Uses a method",
            "source_passage_id": "foreign-passage",
            "confidence": 0.9,
        },
    )

    assert resp.status_code == 422
    assert "source_passage_id" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_card_rejects_deleted_active_space(
    client: AsyncClient,
    setup_space_and_paper: tuple[str, str],
) -> None:
    """A stale deleted active space should not accept card writes."""
    space_id, paper_id = setup_space_and_paper
    delete_resp = await client.delete(f"/api/spaces/{space_id}")
    assert delete_resp.status_code == 200

    resp = await client.post(
        "/api/cards",
        json={
            "paper_id": paper_id,
            "card_type": "Method",
            "summary": "Should not be written",
        },
    )

    assert resp.status_code == 400
    assert "active space" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_extract_cards_returns_heuristic_metadata(
    client: AsyncClient,
    setup_space_and_paper: tuple[str, str],
) -> None:
    space_id, paper_id = setup_space_and_paper

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO passages
               (id, paper_id, space_id, section, original_text)
               VALUES ('heuristic-passage', ?, ?, 'method', ?)""",
            (
                paper_id,
                space_id,
                "The protocol measures sample stability after synthesis.",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    resp = await client.post(f"/api/cards/extract/{paper_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "extracted"
    assert body["mode"] == "heuristic"
    assert body["review_required"] is True
    assert "人工复核" in body["message"]

    resp = await client.get(f"/api/cards?paper_id={paper_id}")
    assert resp.status_code == 200
    cards = resp.json()
    assert len(cards) == body["card_count"]
    assert cards
    for card in cards:
        assert_prior_card_fields(card)
        assert_heuristic_provenance(card)
        assert card["confidence"] <= 0.55


@pytest.mark.asyncio
async def test_extract_cards_does_not_replace_user_or_ai_cards_on_id_conflict(
    client: AsyncClient,
    setup_space_and_paper: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    space_id, paper_id = setup_space_and_paper

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO passages
               (id, paper_id, space_id, section, original_text)
               VALUES ('conflict-passage', ?, ?, 'method', ?)""",
            (paper_id, space_id, "We propose a method."),
        )
        conn.execute(
            """INSERT INTO knowledge_cards
               (id, space_id, paper_id, source_passage_id, card_type, summary,
                confidence, user_edited, created_by, extractor_version)
               VALUES
               ('manual-card', ?, ?, 'conflict-passage', 'Method',
                'Manual summary stays', 0.99, 1, 'user', ''),
               ('ai-card', ?, ?, 'conflict-passage', 'Result',
                'AI summary stays', 0.88, 0, 'ai', 'analysis-v2')""",
            (space_id, paper_id, space_id, paper_id),
        )
        conn.commit()
    finally:
        conn.close()

    def fake_extract_cards(
        passages: list[dict[str, object]],
        extracted_paper_id: str,
        extracted_space_id: str,
    ) -> list[dict[str, object]]:
        base = {
            "space_id": extracted_space_id,
            "paper_id": extracted_paper_id,
            "source_passage_id": "conflict-passage",
            "card_type": "Method",
            "confidence": 0.3,
            "user_edited": 0,
            "created_by": "heuristic",
            "extractor_version": "heuristic-v1",
            "analysis_run_id": None,
            "evidence_json": "{}",
            "quality_flags_json": json.dumps(HEURISTIC_REVIEW_FLAGS),
        }
        return [
            {**base, "id": "manual-card", "summary": "Should not replace manual"},
            {**base, "id": "ai-card", "summary": "Should not replace AI"},
            {**base, "id": "new-heuristic-card", "summary": "New heuristic"},
        ]

    monkeypatch.setattr(
        "paper_engine.cards.service.extract_cards_from_passages",
        fake_extract_cards,
    )

    resp = await client.post(f"/api/cards/extract/{paper_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["card_count"] == 1
    assert body["skipped_card_count"] == 2
    assert body["review_required"] is True

    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, summary, created_by, confidence, extractor_version,
                      quality_flags_json
               FROM knowledge_cards
               WHERE id IN ('manual-card', 'ai-card', 'new-heuristic-card')
               ORDER BY id"""
        ).fetchall()
    finally:
        conn.close()

    cards = {row["id"]: dict(row) for row in rows}
    assert cards["manual-card"]["summary"] == "Manual summary stays"
    assert cards["manual-card"]["created_by"] == "user"
    assert cards["manual-card"]["confidence"] == 0.99
    assert cards["ai-card"]["summary"] == "AI summary stays"
    assert cards["ai-card"]["created_by"] == "ai"
    assert cards["ai-card"]["extractor_version"] == "analysis-v2"
    assert cards["new-heuristic-card"]["summary"] == "New heuristic"
    assert cards["new-heuristic-card"]["created_by"] == "heuristic"
    assert json.loads(cards["new-heuristic-card"]["quality_flags_json"]) == (
        HEURISTIC_REVIEW_FLAGS
    )
