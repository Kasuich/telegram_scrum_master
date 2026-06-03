"""
JSON-RPC 2.0 client for pm-orchestrator.

In Docker: calls http://pm-orchestrator:8001/rpc
In dev (in-process): calls the orchestrator directly without HTTP.
"""

from __future__ import annotations

import os
from typing import Any

from core.react import AgentResult


def _orchestrator_url() -> str:
    return os.getenv("ORCHESTRATOR_URL", "http://pm-orchestrator:8001")


# ---------------------------------------------------------------------------
# In-process mode (default for local dev / tests)
# The orchestrator runs in the same process — no HTTP overhead.
# ---------------------------------------------------------------------------

_in_process_svc: Any = None  # OrchestratorService | None


def _get_in_process():
    global _in_process_svc
    if _in_process_svc is None:
        from pm_orchestrator.orchestrator import OrchestratorService

        _in_process_svc = OrchestratorService()
        _in_process_svc.discover_agents()
    return _in_process_svc


def _use_http() -> bool:
    """Use HTTP only when ORCHESTRATOR_URL is explicitly set."""
    return "ORCHESTRATOR_URL" in os.environ


# ---------------------------------------------------------------------------
# HTTP mode
# ---------------------------------------------------------------------------


async def _rpc_http(method: str, params: dict) -> Any:
    import httpx

    url = f"{_orchestrator_url()}/rpc"
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    if "error" in data:
        raise RuntimeError(data["error"]["message"])
    return data["result"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_agents() -> list[dict[str, str]]:
    if _use_http():
        return await _rpc_http("list_agents", {})
    return _get_in_process().list_agents()


async def invoke(agent: str, message: str, session_id: str) -> AgentResult:
    if _use_http():
        data = await _rpc_http(
            "invoke", {"agent": agent, "message": message, "session_id": session_id}
        )
        return AgentResult(**data)
    result = await _get_in_process().invoke(agent, message, session_id)
    return result


async def resume(confirm_id: str, approved: bool) -> AgentResult:
    if _use_http():
        data = await _rpc_http("resume", {"confirm_id": confirm_id, "approved": approved})
        return AgentResult(**data)
    result = await _get_in_process().resume(confirm_id, approved)
    return result


async def get_actions(session_id: str | None = None, limit: int = 50) -> list[dict]:
    if _use_http():
        return await _rpc_http("get_actions", {"session_id": session_id, "limit": limit})
    svc = _get_in_process()
    actions = svc.actions
    if session_id:
        actions = [a for a in actions if a.get("session_id") == session_id]
    return actions[-limit:]
