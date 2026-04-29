"""HTTP routes for agent and LLM configuration."""

from typing import Any

from fastapi import APIRouter, Body

from paper_engine.agent import service
from paper_engine.agent.service import LLMConfig

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/config")
async def get_agent_config() -> dict[str, Any]:
    return await service.get_agent_config()


@router.put("/config")
async def update_agent_config(config: LLMConfig) -> dict[str, str]:
    return await service.update_agent_config(config)


@router.post("/config/mineru/test")
async def test_mineru_config() -> dict[str, str]:
    return await service.test_mineru_config()


@router.get("/status")
async def get_agent_status() -> dict[str, Any]:
    return await service.get_agent_status()


@router.put("/status")
async def set_agent_status(enabled: bool = Body(..., embed=True)) -> dict[str, bool]:
    return await service.set_agent_status(enabled)


@router.put("/enable")
async def enable_agent() -> dict[str, str]:
    return await service.enable_agent()


@router.put("/disable")
async def disable_agent() -> dict[str, str]:
    return await service.disable_agent()
