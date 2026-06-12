"""JSON-RPC client for pm-orchestrator eval agent calls."""

from __future__ import annotations

from typing import Any

import httpx

from core.eval.constants import MAX_RESUME_PER_AGENT_CALL
from core.react import AgentResult


class OrchestratorRpcClient:
    def __init__(self, base_url: str, *, timeout: float = 300.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": "eval"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{self._base_url}/rpc", json=payload)
            response.raise_for_status()
            body = response.json()
            if "error" in body:
                raise RuntimeError(body["error"])
            return body.get("result")

    async def invoke_agent(
        self,
        *,
        message: str,
        session_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        raw = await self._call(
            "invoke",
            {
                "agent": "pm_agent",
                "message": message,
                "session_id": session_id,
                "context": context,
            },
        )
        result = AgentResult.model_validate(raw)
        resumes = 0
        while result.pending_confirm and resumes < MAX_RESUME_PER_AGENT_CALL:
            raw = await self._call(
                "resume",
                {"confirm_id": result.pending_confirm.confirm_id, "approved": True},
            )
            result = AgentResult.model_validate(raw)
            resumes += 1
        return result
