"""Tests for idea space management API routes."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from paper_engine.storage.database import DATABASE_PATH, get_connection, init_db
from main import app


@pytest.fixture
def db_path() -> Generator[str, None, None]:
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "test.db")
        init_db(database_path=Path(db_file))
        yield db_file


@pytest.fixture
def client(db_path: str) -> Generator[AsyncClient, None, None]:
    """Create a test client with the temporary database."""
    # Override the database path for the app
    import paper_engine.storage.database as db_module

    original_path = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = Path(db_path)

    transport = ASGITransport(app=app)
    test_client = AsyncClient(transport=transport, base_url="http://test")

    yield test_client

    db_module.DATABASE_PATH = original_path


@pytest.mark.asyncio
async def test_create_and_get_space(client: AsyncClient) -> None:
    """Test creating a space and retrieving it."""
    # Create
    resp = await client.post("/api/spaces", json={"name": "Test Space", "description": "A test"})
    assert resp.status_code == 200
    data = resp.json()
    space_id = data["id"]
    assert data["name"] == "Test Space"
    assert data["description"] == "A test"
    assert data["status"] == "active"

    # Get
    resp = await client.get(f"/api/spaces/{space_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Space"


@pytest.mark.asyncio
async def test_list_spaces(client: AsyncClient) -> None:
    """Test listing spaces."""
    await client.post("/api/spaces", json={"name": "Space A"})
    await client.post("/api/spaces", json={"name": "Space B"})

    resp = await client.get("/api/spaces")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {s["name"] for s in data}
    assert names == {"Space A", "Space B"}


@pytest.mark.asyncio
async def test_rename_space(client: AsyncClient) -> None:
    """Test renaming a space."""
    resp = await client.post("/api/spaces", json={"name": "Original"})
    space_id = resp.json()["id"]

    resp = await client.patch(
        f"/api/spaces/{space_id}", json={"name": "Renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_update_space_description(client: AsyncClient) -> None:
    """Test updating a space's description."""
    resp = await client.post(
        "/api/spaces", json={"name": "Desc Test", "description": "Original desc"}
    )
    space_id = resp.json()["id"]

    resp = await client.patch(
        f"/api/spaces/{space_id}", json={"description": "Updated desc"}
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "Updated desc"
    assert resp.json()["name"] == "Desc Test"  # name unchanged


@pytest.mark.asyncio
async def test_archive_space(client: AsyncClient) -> None:
    """Test archiving a space."""
    resp = await client.post("/api/spaces", json={"name": "To Archive"})
    space_id = resp.json()["id"]

    resp = await client.patch(f"/api/spaces/{space_id}/archive")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"

    # Archived space should still be accessible via get
    resp = await client.get(f"/api/spaces/{space_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"

    # Archived spaces appear in list with archived status
    resp = await client.get("/api/spaces")
    spaces = resp.json()
    archived = [s for s in spaces if s["id"] == space_id]
    assert len(archived) == 1
    assert archived[0]["status"] == "archived"


@pytest.mark.asyncio
async def test_archiving_active_space_clears_active_space(client: AsyncClient) -> None:
    """Archiving the current active space should leave no active space selected."""
    resp = await client.post("/api/spaces", json={"name": "Active Archive"})
    space_id = resp.json()["id"]
    await client.put(f"/api/spaces/active/{space_id}")

    resp = await client.patch(f"/api/spaces/{space_id}/archive")

    assert resp.status_code == 200
    active_resp = await client.get("/api/spaces/active")
    assert active_resp.status_code == 404

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = 'active_space'"
        ).fetchone()
        assert row is None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_delete_space(client: AsyncClient) -> None:
    """Test deleting a space."""
    resp = await client.post("/api/spaces", json={"name": "To Delete"})
    space_id = resp.json()["id"]

    resp = await client.delete(f"/api/spaces/{space_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    # Deleted space should return 404
    resp = await client.get(f"/api/spaces/{space_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deleting_active_space_clears_active_space(client: AsyncClient) -> None:
    """Deleting the current active space should leave no active space selected."""
    resp = await client.post("/api/spaces", json={"name": "Active Delete"})
    space_id = resp.json()["id"]
    await client.put(f"/api/spaces/active/{space_id}")

    resp = await client.delete(f"/api/spaces/{space_id}")

    assert resp.status_code == 200
    active_resp = await client.get("/api/spaces/active")
    assert active_resp.status_code == 404

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = 'active_space'"
        ).fetchone()
        assert row is None
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_set_and_get_active_space(client: AsyncClient) -> None:
    """Test setting and reading the active space."""
    resp = await client.post("/api/spaces", json={"name": "Active One"})
    space_id = resp.json()["id"]

    # Set active
    resp = await client.put(f"/api/spaces/active/{space_id}")
    assert resp.status_code == 200
    assert resp.json()["active_space_id"] == space_id

    # Get active
    resp = await client.get("/api/spaces/active")
    assert resp.status_code == 200
    assert resp.json()["id"] == space_id


@pytest.mark.asyncio
async def test_delete_does_not_affect_other_spaces(client: AsyncClient) -> None:
    """Test that deleting one space does not affect others."""
    resp_a = await client.post("/api/spaces", json={"name": "Space A"})
    resp_b = await client.post("/api/spaces", json={"name": "Space B"})
    id_a = resp_a.json()["id"]
    id_b = resp_b.json()["id"]

    await client.delete(f"/api/spaces/{id_a}")

    # Space B should still be accessible
    resp = await client.get(f"/api/spaces/{id_b}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Space B"


@pytest.mark.asyncio
async def test_archive_does_not_affect_other_spaces(client: AsyncClient) -> None:
    """Test that archiving one space does not affect others."""
    resp_a = await client.post("/api/spaces", json={"name": "Space A"})
    resp_b = await client.post("/api/spaces", json={"name": "Space B"})
    id_a = resp_a.json()["id"]
    id_b = resp_b.json()["id"]

    await client.patch(f"/api/spaces/{id_a}/archive")

    # Space B should still be accessible and active
    resp = await client.get(f"/api/spaces/{id_b}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Space B"
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_get_nonexistent_space_returns_404(client: AsyncClient) -> None:
    """Test that requesting a nonexistent space returns 404."""
    resp = await client.get("/api/spaces/nonexistent-id")
    assert resp.status_code == 404
