"""Client for calling LLM APIs (OpenAI compatible)."""

import json
from typing import Any
import httpx
from db import get_connection

async def get_llm_config() -> dict[str, str]:
    """Retrieve LLM configuration from database."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM app_state WHERE key LIKE 'llm_%'").fetchall()
        config = {row["key"]: row["value"] for row in rows}
        return {
            "api_key": config.get("llm_api_key", ""),
            "base_url": config.get("llm_base_url", "https://api.openai.com/v1"),
            "model": config.get("llm_model", "gpt-4o"),
        }
    finally:
        conn.close()

async def call_llm(system_prompt: str, user_prompt: str, json_mode: bool = True) -> dict[str, Any]:
    """Call an OpenAI-compatible LLM API."""
    config = await get_llm_config()
    if not config["api_key"] and "localhost" not in config["base_url"] and "127.0.0.1" not in config["base_url"]:
        raise ValueError("LLM API Key is missing. Please configure it in settings.")

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
    }
    
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{config['base_url'].rstrip('/')}/chat/completions",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        data = response.json()
        
        content = data["choices"][0]["message"]["content"]
        if json_mode:
            return json.loads(content)
        return content
