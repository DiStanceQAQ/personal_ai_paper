"""API routes for internal Agent and LLM configuration."""

from typing import Any, Literal
from fastapi import APIRouter, Body
from pydantic import BaseModel, Field
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
DEFAULT_LLM_TIMEOUT_SECONDS = 180


class LLMConfig(BaseModel):
    llm_provider: str = "openai"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    llm_timeout_seconds: int = Field(
        default=DEFAULT_LLM_TIMEOUT_SECONDS,
        ge=5,
        le=600,
    )
    llm_api_key: str | None = None
    llamaparse_base_url: str = DEFAULT_LLAMAPARSE_BASE_URL
    llamaparse_api_key: str | None = None
    pdf_parser_backend: Literal["mineru", "docling"] | None = None
    mineru_base_url: str | None = None
    mineru_api_key: str | None = None


def _llm_timeout_from_config(config: dict[str, str]) -> int:
    try:
        timeout = int(config.get("llm_timeout_seconds", DEFAULT_LLM_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        timeout = DEFAULT_LLM_TIMEOUT_SECONDS
    return min(600, max(5, timeout))


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
            "llm_timeout_seconds": _llm_timeout_from_config(config),
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
