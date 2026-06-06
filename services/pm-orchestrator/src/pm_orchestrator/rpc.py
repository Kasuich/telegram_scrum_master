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

import asyncio
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
    from core.config import get_config
    from core.scheduler import SchedulerDaemon

    from pm_orchestrator.tools.call_agent import register_call_agent_tool
    from pm_orchestrator.tools.schedule_task import register_schedule_task_tool

    _svc.discover_agents()
    _svc.configure_persistence()
    await _svc.ensure_schema_and_seed()
    register_call_agent_tool(_svc)
    register_schedule_task_tool(_svc)

    scheduler_task = None
    if get_config().app.scheduler_enabled:
        daemon = SchedulerDaemon(_svc)
        scheduler_task = asyncio.create_task(daemon.run(), name="scheduler")

    yield

    if scheduler_task is not None:
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)


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
    context = params.get("context")
    result = await _svc.invoke(agent, message, session_id, context=context)
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
        # JSON-RPC spec: always HTTP 200, error in body
        return JSONResponse(_err(-32001, str(exc), req.id))
    except Exception as exc:
        import logging
        import traceback

        logging.getLogger(__name__).error(
            "RPC method %s failed: %s\n%s", req.method, exc, traceback.format_exc()
        )
        return JSONResponse(_err(-32000, str(exc), req.id))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "agents": [a["name"] for a in _svc.list_agents()]}


@app.get("/metrics")
async def metrics():
    """Prometheus metrics."""
    from fastapi.responses import PlainTextResponse
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return PlainTextResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
