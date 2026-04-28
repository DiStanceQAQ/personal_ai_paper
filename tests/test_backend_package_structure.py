"""Backend package structure regression tests."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ROOT_BACKEND_MODULES = {
    "agent_executor.py",
    "analysis_models.py",
    "analysis_pipeline.py",
    "analysis_prompts.py",
    "analysis_verifier.py",
    "api_sidecar.py",
    "card_extractor.py",
    "config.py",
    "db.py",
    "db_migrations.py",
    "embeddings.py",
    "hybrid_search.py",
    "llm_client.py",
    "main.py",
    "mcp_server.py",
    "parser.py",
    "pdf_backend_base.py",
    "pdf_backend_docling.py",
    "pdf_backend_grobid.py",
    "pdf_backend_legacy.py",
    "pdf_backend_llamaparse.py",
    "pdf_backend_pymupdf4llm.py",
    "pdf_chunker.py",
    "pdf_models.py",
    "pdf_persistence.py",
    "pdf_profile.py",
    "pdf_router.py",
    "routes_agent.py",
    "routes_cards.py",
    "routes_papers.py",
    "routes_search.py",
    "routes_spaces.py",
    "search.py",
}


def test_backend_modules_live_under_package() -> None:
    remaining = sorted(path for path in ROOT_BACKEND_MODULES if (ROOT / path).exists())
    assert remaining == []
