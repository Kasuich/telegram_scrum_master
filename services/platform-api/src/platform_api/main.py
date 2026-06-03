"""
PM Agent Platform API.

Endpoints:
  POST /chat              — send a message, get reply or pending_confirm
  POST /confirm/{id}      — approve or reject a pending tool call
  GET  /actions           — list recent agent actions (in-memory)
  GET  /health            — liveness probe
  GET  /metrics           — Prometheus metrics
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from core.config import RuntimeConfig
from core.react import AgentResult, ReActRunner
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .agent import PMAgent

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------


class _AppState:
    runner: ReActRunner
    actions: list[dict[str, Any]]


_state = _AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared ReActRunner on startup."""
    rc = RuntimeConfig(
        auto_risk=["low"],
        confirm_risk=["medium", "high"],
    )
    _state.runner = ReActRunner(agent=PMAgent(), runtime_config=rc)
    _state.actions = []
    yield


app = FastAPI(title="PM Agent Platform API", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request / Response models
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


def _to_response(result: AgentResult) -> ChatResponse:
    pc = None
    if result.pending_confirm:
        pc = PendingConfirmDTO(
            confirm_id=result.pending_confirm.confirm_id,
            tool_name=result.pending_confirm.tool_name,
            tool_args=result.pending_confirm.tool_args,
            risk=result.pending_confirm.risk,
            prompt=result.pending_confirm.prompt,
        )
    return ChatResponse(
        reply=result.reply,
        pending_confirm=pc,
        session_id=result.session_id,
        steps=result.steps,
    )


def _log_action(result: AgentResult) -> None:
    """Append tool-related steps to the in-memory action log."""
    loggable = {"tool_call", "tool_result", "confirm_wait", "confirm_rejected", "tool_error"}
    for step in result.steps:
        if step.get("kind") in loggable:
            _state.actions.append({"session_id": result.session_id, **step})
    # Cap at 200 entries
    if len(_state.actions) > 200:
        _state.actions[:] = _state.actions[-200:]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Send a message to the PM agent.

    Returns either a text `reply` or a `pending_confirm` requiring
    a follow-up call to `POST /confirm/{confirm_id}`.
    """
    try:
        result = await _state.runner.invoke(request.message, request.session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    _log_action(result)
    return _to_response(result)


@app.post("/confirm/{confirm_id}", response_model=ChatResponse)
async def confirm(confirm_id: str, request: ConfirmRequest) -> ChatResponse:
    """
    Approve or reject a pending tool call.

    - `approved: true`  → execute the tool and continue the agent loop
    - `approved: false` → skip the tool, let the agent respond instead
    """
    try:
        result = await _state.runner.resume(confirm_id, request.approved)
    except Exception as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 500
        raise HTTPException(status_code=status, detail=detail) from exc

    _log_action(result)
    return _to_response(result)


@app.get("/actions")
async def list_actions(
    session_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recent agent actions, optionally filtered by session_id."""
    actions = _state.actions
    if session_id:
        actions = [a for a in actions if a.get("session_id") == session_id]
    return actions[-limit:]


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    """Expose Prometheus metrics in text format."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return PlainTextResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
