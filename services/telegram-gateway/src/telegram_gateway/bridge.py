from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from telegram_gateway.spool import SpoolItem


@dataclass(slots=True)
class BridgeRequestError(Exception):
    status_code: int | None
    detail: str
    retry_after_seconds: int | None = None


@dataclass(slots=True)
class LeaseItem:
    delivery_id: str
    team_id: str
    installation_id: str | None
    category: str
    target_chat_id: str | None
    target_user_id: str | None
    payload: dict[str, Any]
    business_connection_id: str | None
    lease_expires_at: datetime | None


@dataclass(slots=True)
class LeaseResponse:
    items: list[LeaseItem]


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

    def _request_url(self, path: str) -> tuple[str, str]:
        base_path = httpx.URL(self.base_url).path.rstrip("/")
        request_path = path
        if base_path and (path == base_path or path.startswith(f"{base_path}/")):
            request_path = path[len(base_path) :] or "/"
        signed_path = f"{base_path}{request_path}" if base_path else request_path
        return f"{self.base_url}{request_path}", signed_path

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
        url, signed_path = self._request_url(path)
        response = await self._client.request(
            method,
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                **self._headers(method, signed_path, body),
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
        team_id: str,
        installation_id: str,
        gateway_id: str,
        version: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/internal/telegram/v1/events:ingest",
            {
                "team_id": team_id,
                "installation_id": installation_id,
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

    async def lease_outbox(
        self,
        worker_id: str,
        limit: int,
        lease_seconds: int,
    ) -> LeaseResponse:
        response = await self._request(
            "POST",
            "/internal/telegram/v1/outbox:lease",
            {
                "worker_id": worker_id,
                "limit": limit,
                "lease_seconds": lease_seconds,
            },
        )
        items = [
            LeaseItem(
                delivery_id=item["delivery_id"],
                team_id=item["team_id"],
                installation_id=item.get("installation_id"),
                category=item["category"],
                target_chat_id=item.get("target_chat_id"),
                target_user_id=item.get("target_user_id"),
                payload=item["payload"],
                business_connection_id=item.get("business_connection_id"),
                lease_expires_at=(
                    datetime.fromisoformat(item["lease_expires_at"])
                    if item.get("lease_expires_at")
                    else None
                ),
            )
            for item in response.get("items", [])
        ]
        return LeaseResponse(items=items)

    async def ack_outbox(
        self,
        delivery_id: str,
        status: str,
        provider_message_id: str | None = None,
        last_error: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> dict[str, Any]:
        payload = {"status": status}
        if provider_message_id is not None:
            payload["provider_message_id"] = provider_message_id
        if last_error is not None:
            payload["last_error"] = last_error
        if retry_after_seconds is not None:
            payload["retry_after_seconds"] = retry_after_seconds
        return await self._request(
            "POST",
            f"/internal/telegram/v1/outbox/{delivery_id}:ack",
            payload,
        )

    async def resolve_installation(
        self,
        token: str,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/internal/telegram/v1/installations/by-token/{token}",
            {},
        )

    async def resolve_bot_installation(
        self,
        external_bot_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/internal/telegram/v1/installations/by-bot/{external_bot_id}",
            {},
        )


def bridge_error_is_permanent(exc: BridgeRequestError) -> bool:
    if exc.status_code is None:
        return False
    return 400 <= exc.status_code < 500 and exc.status_code != 429
