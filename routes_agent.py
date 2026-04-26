"""API routes for internal Agent and LLM configuration."""

from typing import Any
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel
from db import get_connection
from agent_executor import analyze_paper_with_llm

router = APIRouter(prefix="/api/agent", tags=["agent"])

class LLMConfig(BaseModel):
    llm_provider: str = "openai"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    llm_api_key: str | None = None

@router.get("/config")
async def get_agent_config() -> dict[str, Any]:
    """Get the current LLM configuration (excluding full API key)."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM app_state WHERE key LIKE 'llm_%'").fetchall()
        config = {row["key"]: row["value"] for row in rows}
        return {
            "llm_provider": config.get("llm_provider", "openai"),
            "llm_base_url": config.get("llm_base_url", "https://api.openai.com/v1"),
            "llm_model": config.get("llm_model", "gpt-4o"),
            "has_api_key": bool(config.get("llm_api_key")),
        }
    finally:
        conn.close()

@router.put("/config")
async def update_agent_config(config: LLMConfig) -> dict[str, str]:
    """Update the LLM configuration."""
    conn = get_connection()
    try:
        # data = config.dict(exclude_unset=True) # Compatibility for older pydantic
        data = config.model_dump()
        
        # Logic: If api_key is empty string or None, don't overwrite the existing one in DB
        if not data.get("llm_api_key"):
            data.pop("llm_api_key", None)

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

@router.post("/analyze/{paper_id}")
async def run_deep_analysis(paper_id: str) -> dict[str, Any]:
    """Trigger the internal Agent to analyze a paper using the configured LLM."""
    conn = get_connection()
    try:
        paper = conn.execute("SELECT space_id FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found")
        
        space_id = paper["space_id"]
    finally:
        conn.close()

    result = await analyze_paper_with_llm(paper_id, space_id)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    
    return result

@router.get("/status")
async def get_agent_status() -> dict[str, bool]:
    """Get the current agent status."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM app_state WHERE key = 'agent_enabled'").fetchone()
        return {"enabled": row["value"] == "enabled" if row else False}
    finally:
        conn.close()

@router.put("/status")
async def set_agent_status(enabled: bool = Body(..., embed=True)) -> dict[str, bool]:
    """Enable or disable the agent."""
    conn = get_connection()
    try:
        value = "enabled" if enabled else "disabled"
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES ('agent_enabled', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (value,)
        )
        conn.commit()
        return {"enabled": enabled}
    finally:
        conn.close()
