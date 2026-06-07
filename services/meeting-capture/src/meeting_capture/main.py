"""FastAPI app for Telemost meeting capture."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timezone

from core.db import create_all_tables, get_session
from core.seed import ensure_default_team
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from meeting_capture.config import get_settings
from meeting_capture.dispatcher import MeetingDispatcher
from meeting_capture.repository import MeetingRepository, meeting_to_dto, transcript_to_dto
from meeting_capture.schemas import (
    CreateMeetingRequest,
    CreateMeetingResponse,
    MeetingDTO,
    StopMeetingResponse,
    TranscriptDTO,
)
from meeting_capture.storage import build_object_store
from meeting_capture.transcription import SpeechKitTranscriber
from meeting_capture.url import TelemostUrlError, normalize_telemost_url

DEFAULT_TEAM_ID = "00000000-0000-0000-0000-000000000001"


def _default_team_id() -> uuid.UUID:
    return uuid.UUID(os.getenv("DEFAULT_TEAM_ID") or DEFAULT_TEAM_ID)


def _initial_status(starts_at) -> str:
    if starts_at is None:
        return "joining"
    value = starts_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    from meeting_capture.repository import utcnow

    return "scheduled" if value > utcnow() else "joining"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    await create_all_tables()
    async with get_session() as session:
        await ensure_default_team(session, str(_default_team_id()))

    repository = MeetingRepository(_default_team_id())
    app.state.repository = repository
    app.state.dispatcher = MeetingDispatcher(
        repository=repository,
        settings=settings,
        object_store=build_object_store(settings),
        transcriber=SpeechKitTranscriber(settings),
    )
    yield


app = FastAPI(title="Meeting Capture API", lifespan=lifespan)


def _repo() -> MeetingRepository:
    return app.state.repository


def _dispatcher() -> MeetingDispatcher:
    return app.state.dispatcher


@app.post("/meetings", response_model=CreateMeetingResponse)
async def create_meeting(request: CreateMeetingRequest) -> CreateMeetingResponse:
    try:
        telemost_url = normalize_telemost_url(request.telemost_url)
    except TelemostUrlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    status = _initial_status(request.starts_at)
    meeting = await _repo().create_meeting(
        telemost_url=telemost_url,
        scheduled_at=request.starts_at,
        title=request.title,
        language=request.language,
        consent_ack=request.consent_ack,
        initial_status=status,
    )
    _dispatcher().schedule(meeting.id, request.starts_at)
    return CreateMeetingResponse(meeting_id=str(meeting.id), status=status)  # type: ignore[arg-type]


@app.post("/meetings/{meeting_id}/stop", response_model=StopMeetingResponse)
async def stop_meeting(meeting_id: str) -> StopMeetingResponse:
    parsed_id = _parse_uuid(meeting_id)
    await _dispatcher().stop(parsed_id)
    meeting = await _repo().get(parsed_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    return StopMeetingResponse(meeting_id=str(meeting.id), status=meeting.status)  # type: ignore[arg-type]


@app.get("/meetings/{meeting_id}", response_model=MeetingDTO)
async def get_meeting(meeting_id: str) -> MeetingDTO:
    parsed_id = _parse_uuid(meeting_id)
    meeting = await _repo().get(parsed_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    return meeting_to_dto(meeting)


@app.get("/meetings/{meeting_id}/transcript", response_model=TranscriptDTO)
async def get_transcript(meeting_id: str) -> TranscriptDTO:
    parsed_id = _parse_uuid(meeting_id)
    meeting = await _repo().get(parsed_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    if meeting.transcript is None:
        raise HTTPException(status_code=404, detail="transcript not ready")
    return transcript_to_dto(meeting.transcript)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid meeting id") from exc


__all__ = ["app"]
