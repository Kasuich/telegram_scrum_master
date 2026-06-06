from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from telegram_gateway.spool import SpoolItem


@dataclass(slots=True)
class BridgeRequestError(Exception):
    status_code: int | None
    detail: str
    retry_after_seconds: int | None = None


class MainBridgeClient:
    def __init__(
        self,
        *,
        base_url: str,
        key_id: str,
        key_secret: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.key_id = key_id
        self.key_secret = key_secret
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _body_sha256(body: bytes) -> str:
        return hashlib.sha256(body).hexdigest()

    def _sign(self, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
        parts = [method.upper(), path, timestamp, nonce, self._body_sha256(body)]
        payload = "\n".join(parts).encode("utf-8")
        return hmac.new(self.key_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    def _headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = str(uuid.uuid4())
        return {
            "X-Telegram-Bridge-Key-Id": self.key_id,
            "X-Telegram-Bridge-Timestamp": timestamp,
            "X-Telegram-Bridge-Nonce": nonce,
            "X-Telegram-Bridge-Signature": self._sign(method, path, timestamp, nonce, body),
        }

    async def _request(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        response = await self._client.request(
            method,
            f"{self.base_url}{path}",
            content=body,
            headers={
                "Content-Type": "application/json",
                **self._headers(method, path, body),
            },
        )
        if 200 <= response.status_code < 300:
            if response.content:
                return response.json()
            return {}
        retry_after = response.headers.get("Retry-After")
        retry_after_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
        raise BridgeRequestError(
            status_code=response.status_code,
            detail=response.text,
            retry_after_seconds=retry_after_seconds,
        )

    async def ingest_update(
        self,
        item: SpoolItem,
        *,
        gateway_id: str,
        version: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/internal/telegram/v1/events:ingest",
            {
                "team_id": item.payload.get("team_id"),
                "installation_id": item.payload.get("installation_id"),
                "update_id": item.update_id,
                "payload": item.payload,
                "received_at": item.received_at.isoformat(),
                "gateway_id": gateway_id,
                "version": version,
            },
        )

    async def heartbeat(
        self,
        *,
        gateway_id: str,
        version: str,
        queue_depth: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/internal/telegram/v1/heartbeat",
            {
                "gateway_id": gateway_id,
                "version": version,
                "queue_depth": queue_depth,
                "metadata": metadata or {},
            },
        )


def bridge_error_is_permanent(exc: BridgeRequestError) -> bool:
    if exc.status_code is None:
        return False
    return 400 <= exc.status_code < 500 and exc.status_code != 429
