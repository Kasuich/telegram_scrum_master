"""
PM Agent Platform — HTTP API (thin transport layer).

All agent logic lives in pm-orchestrator. This service:
  - Accepts HTTP requests
  - Translates them to JSON-RPC calls (or in-process calls in dev mode)
  - Returns responses

Routes are auto-generated per agent at startup.

Endpoints:
  GET  /agents                      — list registered agents
  POST /agents/{agent}/chat         — send message to specific agent
  POST /agents/{agent}/confirm/{id} — confirm/reject pending tool call
  POST /chat                        — shortcut → default agent (pm_agent)
  POST /confirm/{id}                — shortcut → resume any confirm
  GET  /actions                     — recent tool call log
  GET  /metrics                     — Prometheus metrics
  GET  /health                      — liveness probe
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from platform_api import rpc_client

DEFAULT_AGENT = "pm_agent"


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up the orchestrator (triggers agent discovery in in-process mode)
    await rpc_client.list_agents()
    yield


app = FastAPI(title="PM Agent Platform API", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class PendingConfirmDTO(BaseModel):
    confirm_id: str
    tool_name: str
    tool_args: dict[str, Any]
    risk: str
    prompt: str


class ChatResponse(BaseModel):
    reply: str | None = None
    pending_confirm: PendingConfirmDTO | None = None
    session_id: str
    steps: list[dict[str, Any]] = Field(default_factory=list)


class ConfirmRequest(BaseModel):
    approved: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(result: Any) -> ChatResponse:
    pc = None
    if result.pending_confirm:
        p = result.pending_confirm
        pc = PendingConfirmDTO(
            confirm_id=p.confirm_id,
            tool_name=p.tool_name,
            tool_args=p.tool_args,
            risk=p.risk,
            prompt=p.prompt,
        )
    return ChatResponse(
        reply=result.reply,
        pending_confirm=pc,
        session_id=result.session_id,
        steps=result.steps,
    )


async def _invoke(agent: str, request: ChatRequest) -> ChatResponse:
    try:
        result = await rpc_client.invoke(agent, request.message, request.session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _to_response(result)


async def _resume(confirm_id: str, approved: bool) -> ChatResponse:
    try:
        result = await rpc_client.resume(confirm_id, approved)
    except Exception as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 500
        raise HTTPException(status_code=status, detail=detail) from exc
    return _to_response(result)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/agents")
async def list_agents() -> list[dict[str, str]]:
    """List all registered agents."""
    return await rpc_client.list_agents()


@app.post("/agents/{agent_name}/chat", response_model=ChatResponse)
async def agent_chat(agent_name: str, request: ChatRequest) -> ChatResponse:
    """Send a message to a specific agent."""
    return await _invoke(agent_name, request)


@app.post("/agents/{agent_name}/confirm/{confirm_id}", response_model=ChatResponse)
async def agent_confirm(agent_name: str, confirm_id: str, request: ConfirmRequest) -> ChatResponse:
    """Approve or reject a pending tool call for a specific agent."""
    return await _resume(confirm_id, request.approved)


# Shortcuts (backward compat + convenience)


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Send a message to the default agent (pm_agent)."""
    return await _invoke(DEFAULT_AGENT, request)


@app.post("/confirm/{confirm_id}", response_model=ChatResponse)
async def confirm(confirm_id: str, request: ConfirmRequest) -> ChatResponse:
    """Resume any pending confirm (agent is looked up from confirm_id)."""
    return await _resume(confirm_id, request.approved)


@app.get("/actions")
async def list_actions(
    session_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Recent agent tool call log."""
    return await rpc_client.get_actions(session_id=session_id, limit=limit)


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    """Prometheus metrics."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return PlainTextResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
