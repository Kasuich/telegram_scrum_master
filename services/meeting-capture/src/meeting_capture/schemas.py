"""Public DTOs for meeting-capture HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MeetingStatus = Literal[
    "scheduled",
    "joining",
    "waiting_room",
    "recording",
    "transcribing",
    "ready",
    "failed",
    "skipped",
]


class CreateMeetingRequest(BaseModel):
    telemost_url: str = Field(min_length=1)
    starts_at: datetime | None = None
    title: str | None = Field(default=None, max_length=255)
    consent_ack: bool = True
    language: str = Field(default="ru-RU", min_length=2, max_length=16)
    # External Telegram chat id to deliver the summary back to (optional).
    target_chat_id: str | None = Field(default=None, max_length=64)

    @field_validator("consent_ack")
    @classmethod
    def require_recording_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("consent_ack must be true before scheduling a recording bot")
        return value


class CreateMeetingResponse(BaseModel):
    meeting_id: str
    status: MeetingStatus


class ArtifactDTO(BaseModel):
    id: str
    kind: str
    object_key: str
    content_type: str
    size_bytes: int
    expires_at: str | None
    created_at: str


class SpeakerDiagnosticsDTO(BaseModel):
    participants_observed_count: int = 0
    speechkit_unique_labels: int = 0
    top_label_share: float = 0.0
    diarization_quality: str = "unknown"
    diarization_collapsed: bool = False
    timeline_windows: int = 0
    timeline_coverage: float = 0.0
    segments_by_source: dict[str, int] = Field(default_factory=dict)


class MeetingDTO(BaseModel):
    id: str
    telemost_url: str
    title: str | None
    status: MeetingStatus
    language: str
    consent_ack: bool
    error: str | None
    metadata_json: dict[str, Any]
    speaker_diagnostics: SpeakerDiagnosticsDTO | None = None
    scheduled_at: str | None
    joined_at: str | None
    recording_started_at: str | None
    ended_at: str | None
    created_at: str
    updated_at: str
    artifacts: list[ArtifactDTO] = Field(default_factory=list)
    transcription_status: Literal["missing", "ready"]


class StopMeetingResponse(BaseModel):
    meeting_id: str
    status: MeetingStatus


class RetranscribeResponse(BaseModel):
    meeting_id: str
    status: MeetingStatus
    segments_count: int
    source: str


class TranscriptSegmentDTO(BaseModel):
    start_ms: int
    end_ms: int
    speaker_label: str
    speaker_name: str | None = None
    speaker_confidence: float | None = None
    speaker_source: str | None = None
    text: str


class TranscriptDTO(BaseModel):
    meeting_id: str
    source: str
    segments: list[TranscriptSegmentDTO]
    participants_observed: list[dict[str, Any]]


__all__ = [
    "ArtifactDTO",
    "CreateMeetingRequest",
    "CreateMeetingResponse",
    "MeetingDTO",
    "MeetingStatus",
    "RetranscribeResponse",
    "SpeakerDiagnosticsDTO",
    "StopMeetingResponse",
    "TranscriptDTO",
    "TranscriptSegmentDTO",
]
