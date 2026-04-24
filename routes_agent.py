"""API routes for agent access controls (US-016)."""

from typing import Any

from fastapi import APIRouter, Body

from db import get_connection

router = APIRouter(prefix="/api/agent", tags=["agent"])

MCP_SERVER_NAME = "paper-knowledge-engine"
MCP_TRANSPORT = "stdio"
AGENT_ACCESS_KEY = "agent_access"


def _get_agent_access(conn: Any) -> bool:
    row = conn.execute(
        "SELECT value FROM app_state WHERE key = ?", (AGENT_ACCESS_KEY,)
    ).fetchone()
    return row is not None and row["value"] == "enabled"


def _set_agent_access(conn: Any, enabled: bool) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
        (AGENT_ACCESS_KEY, "enabled" if enabled else "disabled"),
    )


@router.get("/status")
def get_agent_status() -> dict[str, Any]:
    """Get agent access status and MCP connection information."""
    conn = get_connection()
    try:
        enabled = _get_agent_access(conn)

        active_row = conn.execute(
            "SELECT s.* FROM app_state a "
            "JOIN spaces s ON s.id = a.value "
            "WHERE a.key = 'active_space'"
        ).fetchone()

        active_space = dict(active_row) if active_row else None

        return {
            "enabled": enabled,
            "server_name": MCP_SERVER_NAME,
            "transport": MCP_TRANSPORT,
            "active_space": active_space,
        }
    finally:
        conn.close()


@router.put("/status")
def set_agent_status(enabled: bool = Body(..., embed=True)) -> dict[str, Any]:
    """Enable or disable local agent access."""
    conn = get_connection()
    try:
        _set_agent_access(conn, enabled)
        conn.commit()
    finally:
        conn.close()
    return {"enabled": enabled}


@router.put("/enable")
def enable_agent() -> dict[str, str]:
    """Enable agent access."""
    conn = get_connection()
    try:
        _set_agent_access(conn, True)
        conn.commit()
    finally:
        conn.close()
    return {"status": "enabled"}


@router.put("/disable")
def disable_agent() -> dict[str, str]:
    """Disable agent access."""
    conn = get_connection()
    try:
        _set_agent_access(conn, False)
        conn.commit()
    finally:
        conn.close()
    return {"status": "disabled"}
