from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bridge_key_secret_from_env() -> str:
    direct_secret = os.getenv("TELEGRAM_BRIDGE_HMAC_KEY")
    if direct_secret:
        return direct_secret

    key_id = os.environ["TELEGRAM_BRIDGE_HMAC_KEY_ID"]
    raw_keys = os.environ["TELEGRAM_BRIDGE_HMAC_KEYS"]
    for pair in raw_keys.split(","):
        candidate = pair.strip()
        if not candidate:
            continue
        current_key_id, sep, secret = candidate.partition(":")
        if sep and current_key_id == key_id and secret:
            return secret

    raise KeyError("TELEGRAM_BRIDGE_HMAC_KEY")


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    bot_token: str
    webhook_secret: str
    main_bridge_url: str
    bridge_key_id: str
    bridge_key_secret: str
    transport_mode: str = "webhook"
    spool_path: Path = Path("/var/lib/telegram-gateway/spool.db")
    gateway_id: str = "telegram-gateway"
    version: str = "0.1.0"
    webhook_path: str = "/webhook"
    worker_poll_interval: float = 0.5
    heartbeat_interval_seconds: float = 30.0
    lease_seconds: int = 60
    max_attempts: int = 8
    bridge_timeout_seconds: float = 10.0
    webhook_secret_header: str = "X-Telegram-Bot-Api-Secret-Token"
    # Cosmetic streaming for pm_agent replies (status line + mocked typing).
    stream_enabled: bool = True
    stream_cps: float = 6.0
    stream_interval: float = 0.8
    stream_max_steps: int = 10
    stream_max_duration: float = 6.0
    stream_status_interval: float = 0.5
    stream_min_chars: int = 16

    @classmethod
    def from_env(cls) -> "GatewaySettings":
        transport_mode = os.getenv("TELEGRAM_TRANSPORT_MODE", "webhook").strip().lower()
        if transport_mode not in {"webhook", "polling"}:
            raise ValueError(f"Unsupported TELEGRAM_TRANSPORT_MODE: {transport_mode}")
        return cls(
            bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            webhook_secret=os.environ["TELEGRAM_WEBHOOK_SECRET"],
            main_bridge_url=os.environ["MAIN_BRIDGE_URL"],
            bridge_key_id=os.environ["TELEGRAM_BRIDGE_HMAC_KEY_ID"],
            bridge_key_secret=_bridge_key_secret_from_env(),
            transport_mode=transport_mode,
            spool_path=Path(
                os.getenv("GATEWAY_SPOOL_PATH", "/var/lib/telegram-gateway/spool.db")
            ),
            gateway_id=os.getenv("GATEWAY_ID", "telegram-gateway"),
            version=os.getenv("GATEWAY_VERSION", "0.1.0"),
            webhook_path=os.getenv("TELEGRAM_WEBHOOK_PATH", "/webhook"),
            worker_poll_interval=float(os.getenv("GATEWAY_WORKER_POLL_INTERVAL", "0.5")),
            heartbeat_interval_seconds=float(os.getenv("GATEWAY_HEARTBEAT_INTERVAL", "30")),
            lease_seconds=int(os.getenv("TELEGRAM_OUTBOX_LEASE_SECONDS", "60")),
            max_attempts=int(os.getenv("GATEWAY_MAX_ATTEMPTS", "8")),
            bridge_timeout_seconds=float(os.getenv("GATEWAY_BRIDGE_TIMEOUT", "10")),
            stream_enabled=os.getenv("TELEGRAM_STREAM_ENABLED", "true").strip().lower()
            not in {"0", "false", "no", "off"},
            stream_cps=float(os.getenv("TELEGRAM_STREAM_CPS", "6")),
            stream_interval=float(os.getenv("TELEGRAM_STREAM_INTERVAL", "0.8")),
            stream_max_steps=int(os.getenv("TELEGRAM_STREAM_MAX_STEPS", "10")),
            stream_max_duration=float(os.getenv("TELEGRAM_STREAM_MAX_DURATION", "6")),
            stream_status_interval=float(os.getenv("TELEGRAM_STREAM_STATUS_INTERVAL", "0.5")),
            stream_min_chars=int(os.getenv("TELEGRAM_STREAM_MIN_CHARS", "16")),
        )
