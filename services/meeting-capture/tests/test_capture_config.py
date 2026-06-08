"""Tests for meeting-capture configuration."""

from __future__ import annotations

from meeting_capture.config import CaptureSettings, reset_settings


def test_effective_speechkit_api_key_prefers_explicit() -> None:
    reset_settings()
    settings = CaptureSettings(SPEECHKIT_API_KEY="speech-key", YC_API_KEY="yc-key")
    assert settings.effective_speechkit_api_key == "speech-key"


def test_effective_speechkit_api_key_falls_back_to_yc() -> None:
    reset_settings()
    settings = CaptureSettings(SPEECHKIT_API_KEY="", YC_API_KEY="yc-key")
    assert settings.effective_speechkit_api_key == "yc-key"
