"""Database repository for meeting capture."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from core.db import get_session
from core.models import Meeting, MeetingArtifact, Transcript
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from meeting_capture.schemas import ArtifactDTO, MeetingDTO, TranscriptDTO


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class MeetingRepository:
    """Thin async repository over the shared core models."""

    def __init__(self, team_id: uuid.UUID) -> None:
        self.team_id = team_id

    async def create_meeting(
        self,
        *,
        telemost_url: str,
        scheduled_at: datetime | None,
        title: str | None,
        language: str,
        consent_ack: bool,
        initial_status: str,
        target_chat_id: str | None = None,
    ) -> Meeting:
        metadata: dict[str, Any] = {}
        if target_chat_id:
            metadata["target_chat_id"] = target_chat_id
        async with get_session() as session:
            meeting = Meeting(
                id=uuid.uuid4(),
                team_id=self.team_id,
                telemost_url=telemost_url,
                scheduled_at=scheduled_at,
                title=title,
                language=language,
                consent_ack=consent_ack,
                status=initial_status,
                metadata_json=metadata,
            )
            session.add(meeting)
            await session.flush()
            return meeting

    async def get(self, meeting_id: uuid.UUID) -> Meeting | None:
        async with get_session() as session:
            return (
                await session.execute(
                    select(Meeting)
                    .options(
                        selectinload(Meeting.artifacts),
                        selectinload(Meeting.transcript),
                    )
                    .where(Meeting.id == meeting_id, Meeting.team_id == self.team_id)
                )
            ).scalar_one_or_none()

    async def set_status(
        self,
        meeting_id: uuid.UUID,
        status: str,
        *,
        error: str | None = None,
        metadata_update: dict[str, Any] | None = None,
        joined_at: datetime | None = None,
        recording_started_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> Meeting | None:
        async with get_session() as session:
            meeting = (
                await session.execute(
                    select(Meeting).where(Meeting.id == meeting_id, Meeting.team_id == self.team_id)
                )
            ).scalar_one_or_none()
            if meeting is None:
                return None
            meeting.status = status
            meeting.error = error
            meeting.updated_at = utcnow()
            if metadata_update:
                meeting.metadata_json = {**(meeting.metadata_json or {}), **metadata_update}
            if joined_at is not None:
                meeting.joined_at = joined_at
            if recording_started_at is not None:
                meeting.recording_started_at = recording_started_at
            if ended_at is not None:
                meeting.ended_at = ended_at
            await session.flush()
            return meeting

    async def add_artifact(
        self,
        *,
        meeting_id: uuid.UUID,
        kind: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
        expires_at: datetime | None,
    ) -> MeetingArtifact:
        async with get_session() as session:
            artifact = MeetingArtifact(
                id=uuid.uuid4(),
                meeting_id=meeting_id,
                kind=kind,
                object_key=object_key,
                content_type=content_type,
                size_bytes=size_bytes,
                expires_at=expires_at,
            )
            session.add(artifact)
            await session.flush()
            return artifact

    async def save_transcript(
        self,
        *,
        meeting_id: uuid.UUID,
        source: str,
        segments: list[dict[str, Any]],
        participants_observed: list[dict[str, Any]],
    ) -> Transcript:
        async with get_session() as session:
            existing = (
                await session.execute(select(Transcript).where(Transcript.meeting_id == meeting_id))
            ).scalar_one_or_none()
            if existing is None:
                existing = Transcript(
                    id=uuid.uuid4(),
                    meeting_id=meeting_id,
                    source=source,
                    segments=segments,
                    participants_observed=participants_observed,
                )
                session.add(existing)
            else:
                existing.source = source
                existing.segments = segments
                existing.participants_observed = participants_observed
            await session.flush()
            return existing


def artifact_to_dto(artifact: MeetingArtifact) -> ArtifactDTO:
    return ArtifactDTO(
        id=str(artifact.id),
        kind=artifact.kind,
        object_key=artifact.object_key,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        expires_at=_iso(artifact.expires_at),
        created_at=_iso(artifact.created_at) or "",
    )


def meeting_to_dto(meeting: Meeting) -> MeetingDTO:
    from meeting_capture.schemas import SpeakerDiagnosticsDTO

    metadata = meeting.metadata_json or {}
    raw_diag = metadata.get("speaker_diagnostics")
    speaker_diagnostics = (
        SpeakerDiagnosticsDTO.model_validate(raw_diag) if isinstance(raw_diag, dict) else None
    )
    return MeetingDTO(
        id=str(meeting.id),
        telemost_url=meeting.telemost_url,
        title=meeting.title,
        status=meeting.status,  # type: ignore[arg-type]
        language=meeting.language,
        consent_ack=meeting.consent_ack,
        error=meeting.error,
        metadata_json=metadata,
        speaker_diagnostics=speaker_diagnostics,
        scheduled_at=_iso(meeting.scheduled_at),
        joined_at=_iso(meeting.joined_at),
        recording_started_at=_iso(meeting.recording_started_at),
        ended_at=_iso(meeting.ended_at),
        created_at=_iso(meeting.created_at) or "",
        updated_at=_iso(meeting.updated_at) or "",
        artifacts=[artifact_to_dto(artifact) for artifact in meeting.artifacts],
        transcription_status="ready" if meeting.transcript else "missing",
    )


def transcript_to_dto(transcript: Transcript) -> TranscriptDTO:
    return TranscriptDTO(
        meeting_id=str(transcript.meeting_id),
        source=transcript.source,
        segments=transcript.segments,
        participants_observed=transcript.participants_observed,
    )


__all__ = [
    "MeetingRepository",
    "artifact_to_dto",
    "meeting_to_dto",
    "transcript_to_dto",
    "utcnow",
]
