from __future__ import annotations

from telegram_gateway.settings import GatewaySettings


def test_from_env_uses_matching_secret_from_hmac_keys(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setenv("MAIN_BRIDGE_URL", "https://main.example/internal/telegram/v1")
    monkeypatch.setenv("TELEGRAM_BRIDGE_HMAC_KEY_ID", "key2")
    monkeypatch.delenv("TELEGRAM_BRIDGE_HMAC_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BRIDGE_HMAC_KEYS", "key1:secret-1,key2:secret-2")

    settings = GatewaySettings.from_env()

    assert settings.bridge_key_id == "key2"
    assert settings.bridge_key_secret == "secret-2"


def test_from_env_reads_polling_transport_mode(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setenv("MAIN_BRIDGE_URL", "https://main.example/internal/telegram/v1")
    monkeypatch.setenv("TELEGRAM_BRIDGE_HMAC_KEY_ID", "key1")
    monkeypatch.setenv("TELEGRAM_BRIDGE_HMAC_KEYS", "key1:secret-1")
    monkeypatch.setenv("TELEGRAM_TRANSPORT_MODE", "polling")

    settings = GatewaySettings.from_env()

    assert settings.transport_mode == "polling"
