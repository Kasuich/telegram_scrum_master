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


def test_from_env_builds_webhook_url(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setenv("MAIN_BRIDGE_URL", "https://main.example/internal/telegram/v1")
    monkeypatch.setenv("TELEGRAM_BRIDGE_HMAC_KEY_ID", "key1")
    monkeypatch.setenv("TELEGRAM_BRIDGE_HMAC_KEYS", "key1:secret-1")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_BASE_URL", "https://misisdarkhorse.ru/")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_PATH", "/telegram/webhook")

    settings = GatewaySettings.from_env()

    assert settings.webhook_base_url == "https://misisdarkhorse.ru/"
    assert settings.webhook_url == "https://misisdarkhorse.ru/telegram/webhook"


def test_from_env_default_bridge_timeout_is_120(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setenv("MAIN_BRIDGE_URL", "https://main.example/internal/telegram/v1")
    monkeypatch.setenv("TELEGRAM_BRIDGE_HMAC_KEY_ID", "key1")
    monkeypatch.setenv("TELEGRAM_BRIDGE_HMAC_KEYS", "key1:secret-1")
    monkeypatch.delenv("GATEWAY_BRIDGE_TIMEOUT", raising=False)

    settings = GatewaySettings.from_env()

    assert settings.bridge_timeout_seconds == 120.0


def test_webhook_url_empty_without_base() -> None:
    settings = GatewaySettings(
        bot_token="bot-token",
        webhook_secret="hook-secret",
        main_bridge_url="https://main.example",
        bridge_key_id="key1",
        bridge_key_secret="secret-1",
    )

    assert settings.webhook_url == ""
