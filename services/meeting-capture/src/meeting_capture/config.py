"""Configuration for the meeting-capture service."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CaptureSettings(BaseSettings):
    """Environment-driven capture settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_display_name: str = Field(
        default="PM Assistant (recording)",
        alias="CAPTURE_BOT_DISPLAY_NAME",
    )
    join_timeout_sec: int = Field(default=900, ge=30, alias="CAPTURE_JOIN_TIMEOUT_SEC")
    max_duration_sec: int = Field(default=14_400, ge=60, alias="CAPTURE_MAX_DURATION_SEC")
    audio_ttl_days: int = Field(default=7, ge=0, alias="CAPTURE_AUDIO_TTL_DAYS")
    work_dir: Path = Field(default=Path("/tmp/meeting-capture"), alias="CAPTURE_WORK_DIR")
    object_storage_dir: Path = Field(
        default=Path("/tmp/meeting-capture-objects"),
        alias="CAPTURE_OBJECT_STORAGE_DIR",
    )

    ffmpeg_bin: str = Field(default="ffmpeg", alias="CAPTURE_FFMPEG_BIN")
    display: str = Field(default=":99.0", alias="CAPTURE_DISPLAY")
    video_size: str = Field(default="1280x720", alias="CAPTURE_VIDEO_SIZE")
    framerate: int = Field(default=15, ge=1, le=60, alias="CAPTURE_FRAMERATE")
    pulse_source: str = Field(default="default", alias="CAPTURE_PULSE_SOURCE")

    s3_endpoint: str = Field(default="", alias="S3_ENDPOINT")
    s3_bucket: str = Field(default="", alias="S3_BUCKET")
    s3_access_key: str = Field(default="", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="", alias="S3_SECRET_KEY")
    s3_region: str = Field(default="ru-central1", alias="S3_REGION")

    speechkit_api_key: str = Field(default="", alias="SPEECHKIT_API_KEY")
    yc_api_key: str = Field(default="", alias="YC_API_KEY")
    speechkit_base_url: str = Field(
        default="https://stt.api.cloud.yandex.net",
        alias="SPEECHKIT_BASE_URL",
    )
    speechkit_poll_interval_sec: float = Field(
        default=5.0,
        ge=0.1,
        alias="SPEECHKIT_POLL_INTERVAL_SEC",
    )
    speechkit_timeout_sec: int = Field(default=3600, ge=30, alias="SPEECHKIT_TIMEOUT_SEC")
    # Hard ceiling on the whole transcribe step so a hung SpeechKit poll cannot
    # leave a meeting stuck in "transcribing". Slightly above speechkit_timeout.
    transcribe_timeout_sec: int = Field(default=3900, ge=30, alias="CAPTURE_TRANSCRIBE_TIMEOUT_SEC")

    meeting_capture_url: str = Field(
        default="http://meeting-capture:8003",
        alias="MEETING_CAPTURE_URL",
    )
    orchestrator_url: str = Field(default="", alias="ORCHESTRATOR_URL")

    # Fan-out of the meeting summary after transcription.
    # Fallback Telegram chat when a meeting has no target_chat_id (e.g. it was
    # scheduled outside Telegram). Empty -> no Telegram delivery in that case.
    telegram_fallback_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    # When true, also send the summary to pm_agent for auto board/task creation.
    summary_fanout_pm_agent: bool = Field(default=True, alias="CAPTURE_SUMMARY_TO_PM_AGENT")

    @property
    def s3_enabled(self) -> bool:
        return bool(
            self.s3_bucket and self.s3_access_key and self.s3_secret_key
        )

    @property
    def effective_speechkit_api_key(self) -> str:
        return (self.speechkit_api_key or self.yc_api_key).strip()

    @property
    def effective_display(self) -> str:
        return os.getenv("DISPLAY", self.display)


@lru_cache
def get_settings() -> CaptureSettings:
    return CaptureSettings()


def reset_settings() -> None:
    get_settings.cache_clear()


__all__ = ["CaptureSettings", "get_settings", "reset_settings"]
