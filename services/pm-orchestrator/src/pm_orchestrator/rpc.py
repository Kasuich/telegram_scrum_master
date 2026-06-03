"""
JSON-RPC 2.0 server for the orchestrator.

All requests go to  POST /rpc

Methods
-------
list_agents()
    → [{"name": "pm_agent", "description": "..."}]

invoke(agent, message, session_id)
    → AgentResult

resume(confirm_id, approved)
    → AgentResult

get_actions(session_id?, limit?)
    → [action, ...]
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pm_orchestrator.orchestrator import OrchestratorService

# ---------------------------------------------------------------------------
# Shared service instance
# ---------------------------------------------------------------------------

_svc = OrchestratorService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _svc.discover_agents()
    yield


app = FastAPI(title="PM Orchestrator JSON-RPC", lifespan=lifespan)


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 types
# ---------------------------------------------------------------------------


class RpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    id: int | str | None = None


def _ok(result: Any, rpc_id: Any) -> dict:
    return {"jsonrpc": "2.0", "result": result, "id": rpc_id}


def _err(code: int, message: str, rpc_id: Any) -> dict:
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": rpc_id}


# ---------------------------------------------------------------------------
# Method registry
# ---------------------------------------------------------------------------


async def _list_agents(params: dict) -> list:
    return _svc.list_agents()


async def _invoke(params: dict) -> dict:
    agent = params.get("agent", "pm_agent")
    message = params["message"]
    session_id = params["session_id"]
    result = await _svc.invoke(agent, message, session_id)
    return result.model_dump()


async def _resume(params: dict) -> dict:
    confirm_id = params["confirm_id"]
    approved = params["approved"]
    result = await _svc.resume(confirm_id, approved)
    return result.model_dump()


async def _get_actions(params: dict) -> list:
    session_id = params.get("session_id")
    limit = int(params.get("limit", 50))
    actions = _svc.actions
    if session_id:
        actions = [a for a in actions if a.get("session_id") == session_id]
    return actions[-limit:]


_METHODS = {
    "list_agents": _list_agents,
    "invoke": _invoke,
    "resume": _resume,
    "get_actions": _get_actions,
}

# ---------------------------------------------------------------------------
# RPC endpoint
# ---------------------------------------------------------------------------


@app.post("/rpc")
async def rpc(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_err(-32700, "Parse error", None))

    try:
        req = RpcRequest(**body)
    except Exception:
        return JSONResponse(_err(-32600, "Invalid request", body.get("id")))

    handler = _METHODS.get(req.method)
    if handler is None:
        return JSONResponse(_err(-32601, f"Method not found: {req.method}", req.id))

    try:
        result = await handler(req.params)
        return JSONResponse(_ok(result, req.id))
    except KeyError as exc:
        return JSONResponse(_err(-32001, str(exc), req.id), status_code=404)
    except Exception as exc:
        return JSONResponse(_err(-32000, str(exc), req.id), status_code=500)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "agents": [a["name"] for a in _svc.list_agents()]}
