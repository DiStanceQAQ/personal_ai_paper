from collections.abc import Generator
from pathlib import Path
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from paper_engine.api.app import app
from paper_engine.storage.database import init_db


@pytest.fixture
def client() -> Generator[AsyncClient, None, None]:
    import paper_engine.storage.database as db_module

    with tempfile.TemporaryDirectory() as tmpdir:
        original_db_path = db_module.DATABASE_PATH
        db_module.DATABASE_PATH = Path(tmpdir) / "test.db"
        init_db(database_path=db_module.DATABASE_PATH)
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        db_module.DATABASE_PATH = original_db_path


@pytest.mark.asyncio
async def test_agent_config_includes_pdf_parser_defaults(client: AsyncClient) -> None:
    response = await client.get("/api/agent/config")

    assert response.status_code == 200
    data = response.json()
    assert data["pdf_parser_backend"] == "docling"
    assert data["mineru_base_url"] == ""
    assert data["has_mineru_api_key"] is False
    assert data["parsers"]["docling"]["install_hint"] in {
        "",
        'pip install -e ".[pdf-advanced]"',
    }


@pytest.mark.asyncio
async def test_update_agent_config_saves_pdf_parser_settings(
    client: AsyncClient,
) -> None:
    response = await client.put(
        "/api/agent/config",
        json={
            "llm_provider": "openai",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_model": "gpt-4o",
            "pdf_parser_backend": "mineru",
            "mineru_base_url": "http://mineru.test",
            "mineru_api_key": "secret",
        },
    )
    assert response.status_code == 200

    config = (await client.get("/api/agent/config")).json()
    assert config["pdf_parser_backend"] == "mineru"
    assert config["mineru_base_url"] == "http://mineru.test"
    assert config["has_mineru_api_key"] is True


@pytest.mark.asyncio
async def test_mineru_test_endpoint_reports_missing_credentials(
    client: AsyncClient,
) -> None:
    response = await client.post("/api/agent/config/mineru/test")

    assert response.status_code == 200
    assert response.json()["status"] == "missing_credentials"
