"""API routes for internal Agent and LLM configuration."""

from typing import Any, Literal
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel
from paper_engine.storage.database import get_connection
from paper_engine.pdf.settings import (
    ParserSettingsUpdate,
    get_parser_settings,
    save_parser_settings,
    test_mineru_connection,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])

ACTIVE_SPACE_KEY = "active_space"
AGENT_ACCESS_KEY = "agent_access"
LEGACY_AGENT_ACCESS_KEY = "agent_enabled"
MCP_SERVER_NAME = "paper-knowledge-engine"
MCP_TRANSPORT = "stdio"
DEFAULT_LLAMAPARSE_BASE_URL = "https://api.cloud.llamaindex.ai"
NO_PASSAGES_FOUND_MESSAGE = "No passages found. Please parse PDF first."


class LLMConfig(BaseModel):
    llm_provider: str = "openai"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    llm_api_key: str | None = None
    llamaparse_base_url: str = DEFAULT_LLAMAPARSE_BASE_URL
    llamaparse_api_key: str | None = None
    pdf_parser_backend: Literal["mineru", "docling"] | None = None
    mineru_base_url: str | None = None
    mineru_api_key: str | None = None


def _get_agent_access_value() -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (AGENT_ACCESS_KEY,),
        ).fetchone()
        if row:
            return str(row["value"])

        legacy_row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (LEGACY_AGENT_ACCESS_KEY,),
        ).fetchone()
        return str(legacy_row["value"]) if legacy_row else "disabled"
    finally:
        conn.close()


def _set_agent_access_value(value: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (AGENT_ACCESS_KEY, value),
        )
        conn.execute("DELETE FROM app_state WHERE key = ?", (LEGACY_AGENT_ACCESS_KEY,))
        conn.commit()
    finally:
        conn.close()


def _get_active_space() -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT s.id, s.name, s.description, s.status, s.created_at, s.updated_at
               FROM spaces s
               JOIN app_state a ON a.value = s.id
               WHERE a.key = ? AND s.status != 'deleted'""",
            (ACTIVE_SPACE_KEY,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_active_space_id() -> str:
    active_space = _get_active_space()
    if active_space is None or active_space["status"] != "active":
        raise HTTPException(status_code=400, detail="No active space selected.")
    return str(active_space["id"])


async def analyze_paper_with_llm(paper_id: str, space_id: str) -> dict[str, Any]:
    """Run LLM analysis without importing the heavy executor during API startup."""
    from paper_engine.agent.executor import analyze_paper_with_llm as run_analysis

    return await run_analysis(paper_id, space_id)


@router.get("/config")
async def get_agent_config() -> dict[str, Any]:
    """Get the current LLM configuration (excluding full API key)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM app_state WHERE key LIKE 'llm_%' OR key LIKE 'llamaparse_%'"
        ).fetchall()
        config = {row["key"]: row["value"] for row in rows}
        parser_settings = get_parser_settings(conn)
        return {
            "llm_provider": config.get("llm_provider", "openai"),
            "llm_base_url": config.get("llm_base_url", "https://api.openai.com/v1"),
            "llm_model": config.get("llm_model", "gpt-4o"),
            "has_api_key": bool(config.get("llm_api_key")),
            "llamaparse_base_url": config.get(
                "llamaparse_base_url",
                DEFAULT_LLAMAPARSE_BASE_URL,
            ),
            "has_llamaparse_api_key": bool(config.get("llamaparse_api_key")),
            **parser_settings.model_dump(),
        }
    finally:
        conn.close()

@router.put("/config")
async def update_agent_config(config: LLMConfig) -> dict[str, str]:
    """Update the LLM configuration."""
    conn = get_connection()
    try:
        data = config.model_dump(exclude_unset=True)
        parser_update = ParserSettingsUpdate(
            pdf_parser_backend=data.pop("pdf_parser_backend", None),
            mineru_base_url=data.pop("mineru_base_url", None),
            mineru_api_key=data.pop("mineru_api_key", None),
        )
        save_parser_settings(conn, parser_update)
        
        # Logic: If api_key is empty string or None, don't overwrite the existing one in DB
        if not data.get("llm_api_key"):
            data.pop("llm_api_key", None)
        if not data.get("llamaparse_api_key"):
            data.pop("llamaparse_api_key", None)

        for key, value in data.items():
            if value is not None:
                conn.execute(
                    "INSERT INTO app_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value))
                )
        conn.commit()
        return {"status": "success"}
    finally:
        conn.close()


async def test_mineru_config() -> dict[str, str]:
    """Test configured MinerU connectivity without returning credentials."""
    conn = get_connection()
    try:
        result = test_mineru_connection(conn)
        return {"status": result["status"], "detail": result["detail"]}
    finally:
        conn.close()

@router.post("/analyze/{paper_id}")
async def run_deep_analysis(paper_id: str) -> dict[str, Any]:
    """Trigger the internal Agent to analyze a paper using the configured LLM."""
    active_space_id = _get_active_space_id()
    conn = get_connection()
    try:
        paper = conn.execute(
            "SELECT space_id FROM papers WHERE id = ? AND space_id = ?",
            (paper_id, active_space_id),
        ).fetchone()
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found")
        
        space_id = str(paper["space_id"])
    finally:
        conn.close()

    result = await analyze_paper_with_llm(paper_id, space_id)
    if result["status"] == "error":
        detail = str(result["message"])
        if detail == NO_PASSAGES_FOUND_MESSAGE:
            raise HTTPException(
                status_code=409,
                detail="PDF parsing has not completed yet. Please wait for parsing to finish.",
            )
        raise HTTPException(status_code=500, detail=detail)
    
    return result

@router.get("/status")
async def get_agent_status() -> dict[str, Any]:
    """Get the current agent status."""
    return {
        "enabled": _get_agent_access_value() == "enabled",
        "server_name": MCP_SERVER_NAME,
        "transport": MCP_TRANSPORT,
        "active_space": _get_active_space(),
    }

@router.put("/status")
async def set_agent_status(enabled: bool = Body(..., embed=True)) -> dict[str, bool]:
    """Enable or disable the agent."""
    _set_agent_access_value("enabled" if enabled else "disabled")
    return {"enabled": enabled}


@router.put("/enable")
async def enable_agent() -> dict[str, str]:
    """Enable agent access for MCP tools."""
    _set_agent_access_value("enabled")
    return {"status": "enabled"}


@router.put("/disable")
async def disable_agent() -> dict[str, str]:
    """Disable agent access for MCP tools."""
    _set_agent_access_value("disabled")
    return {"status": "disabled"}
