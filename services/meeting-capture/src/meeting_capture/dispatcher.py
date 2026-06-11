"""Meeting capture lifecycle dispatcher."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from meeting_capture.bot import PlaywrightTelemostBot, TelemostBot
from meeting_capture.config import CaptureSettings
from meeting_capture.recorder import FfmpegRecorder, Recorder, RecordingFiles
from meeting_capture.repository import MeetingRepository, utcnow
from meeting_capture.storage import ObjectStore, UploadedObject, artifact_key
from meeting_capture.transcription import Transcriber, TranscriptionResult

logger = logging.getLogger(__name__)


def _fmt_ts(ms: int) -> str:
    """Format milliseconds as mm:ss for human-readable transcript lines."""
    total_sec = max(int(ms), 0) // 1000
    return f"{total_sec // 60:02d}:{total_sec % 60:02d}"


BotFactory = Callable[[], TelemostBot]
RecorderFactory = Callable[[Path], Recorder]


class MeetingDispatcher:
    """Owns in-process capture tasks and stop signals."""

    def __init__(
        self,
        *,
        repository: MeetingRepository,
        settings: CaptureSettings,
        object_store: ObjectStore,
        transcriber: Transcriber,
        bot_factory: BotFactory | None = None,
        recorder_factory: RecorderFactory | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.object_store = object_store
        self.transcriber = transcriber
        self.bot_factory = bot_factory or (lambda: PlaywrightTelemostBot(settings))
        self.recorder_factory = recorder_factory or (
            lambda work_dir: FfmpegRecorder(settings, work_dir)
        )
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._stop_events: dict[uuid.UUID, asyncio.Event] = {}

    def schedule(self, meeting_id: uuid.UUID, starts_at: datetime | None) -> None:
        if meeting_id in self._tasks and not self._tasks[meeting_id].done():
            return
        task = asyncio.create_task(
            self._run_after_delay(meeting_id, starts_at),
            name=f"meeting-capture:{meeting_id}",
        )
        self._tasks[meeting_id] = task
        task.add_done_callback(lambda done: self._tasks.pop(meeting_id, None))

    _ACTIVE_CAPTURE_STATUSES = frozenset(
        {"scheduled", "joining", "waiting_room", "recording", "transcribing"}
    )

    async def stop(self, meeting_id: uuid.UUID) -> None:
        event = self._stop_events.get(meeting_id)
        if event is not None:
            event.set()
            return
        meeting = await self.repository.get(meeting_id)
        if meeting and meeting.status in {"scheduled", "joining", "waiting_room", "recording"}:
            await self.repository.set_status(
                meeting_id,
                "skipped",
                error="stop requested before active recorder was found",
                ended_at=utcnow(),
            )

    async def _run_after_delay(self, meeting_id: uuid.UUID, starts_at: datetime | None) -> None:
        delay = self._delay_seconds(starts_at)
        if delay > 0:
            await asyncio.sleep(delay)
        await self.run_now(meeting_id)

    async def run_now(self, meeting_id: uuid.UUID) -> None:
        meeting = await self.repository.get(meeting_id)
        if meeting is None:
            logger.warning("Meeting %s not found before capture", meeting_id)
            return

        stop_event = asyncio.Event()
        self._stop_events[meeting_id] = stop_event
        work_dir = self.settings.work_dir / str(meeting_id)
        bot = self.bot_factory()
        recorder = self.recorder_factory(work_dir)
        recorder_started = False

        try:
            await self.repository.set_status(meeting_id, "joining")
            join_result = await bot.join(
                meeting.telemost_url,
                display_name=self.settings.bot_display_name,
                timeout_sec=self.settings.join_timeout_sec,
            )
            if not join_result.admitted:
                if join_result.waiting_room:
                    await self.repository.set_status(
                        meeting_id,
                        "waiting_room",
                        metadata_update={
                            "participants_observed": join_result.participants_observed
                        },
                    )
                await self.repository.set_status(
                    meeting_id,
                    "skipped",
                    error=join_result.skipped_reason or "bot was not admitted",
                    metadata_update={"participants_observed": join_result.participants_observed},
                    ended_at=utcnow(),
                )
                return

            await self.repository.set_status(
                meeting_id,
                "recording",
                joined_at=utcnow(),
                recording_started_at=utcnow(),
                metadata_update={"participants_observed": join_result.participants_observed},
            )
            # Anchor the speaker timeline to audio capture start, not post-await drift.
            record_start_monotonic = asyncio.get_running_loop().time()
            await recorder.start()
            recorder_started = True
            # Sample the active speaker from the DOM in parallel with the call so
            # SpeechKit's anonymous labels can later be mapped to real names.
            speaker_stop = asyncio.Event()
            speaker_task = asyncio.create_task(
                bot.poll_active_speakers(
                    stop_event=speaker_stop,
                    record_start_monotonic=record_start_monotonic,
                )
            )
            finish_reason = await bot.wait_until_finished(
                stop_event=stop_event,
                max_duration_sec=self.settings.max_duration_sec,
            )
            speaker_stop.set()
            speaker_timeline = await self._collect_speaker_timeline(speaker_task)
            files = await recorder.stop()
            recorder_started = False

            logger.info("Meeting %s finished: %s", meeting_id, finish_reason)
            await self.repository.set_status(
                meeting_id,
                "transcribing",
                ended_at=utcnow(),
                metadata_update={
                    "finish_reason": finish_reason,
                    "speaker_timeline": speaker_timeline,
                    "participants_observed": join_result.participants_observed,
                },
            )

            uploaded = await self._upload_recording_files(meeting_id, files)
            audio_obj = uploaded.get("audio")
            audio_uri = audio_obj.uri if audio_obj else None
            if audio_uri:
                # Hard timeout so a hung SpeechKit poll cannot wedge the meeting in
                # "transcribing" forever — fall through to the failed branch instead.
                transcription = await asyncio.wait_for(
                    self.transcriber.transcribe(
                        files.audio_path,
                        audio_uri=audio_uri,
                        language=meeting.language,
                        participants_observed=join_result.participants_observed,
                    ),
                    timeout=self.settings.transcribe_timeout_sec,
                )
            else:
                transcription = self._empty_audio_transcription_result(
                    files,
                    uploaded,
                    participants_observed=join_result.participants_observed,
                )
            from meeting_capture.transcription import map_speakers_to_names

            named_segments = map_speakers_to_names(
                transcription.segments,
                speaker_timeline,
                participants_observed=join_result.participants_observed,
                bot_display_name=self.settings.bot_display_name,
            )
            transcription = TranscriptionResult(
                source=transcription.source,
                segments=named_segments,
                participants_observed=transcription.participants_observed,
            )
            target_chat_id = (getattr(meeting, "metadata_json", None) or {}).get("target_chat_id")
            await self._store_transcription_result(
                meeting_id,
                transcription,
                target_chat_id=target_chat_id,
                summarize=True,
            )
        except Exception as exc:
            logger.exception("Meeting capture failed for %s", meeting_id)
            if recorder_started:
                try:
                    await recorder.stop()
                except Exception:
                    logger.exception("Failed to stop recorder after capture error")
            await self.repository.set_status(
                meeting_id,
                "failed",
                error=str(exc),
                ended_at=utcnow(),
            )
        finally:
            self._stop_events.pop(meeting_id, None)
            await bot.close()

    async def retranscribe(
        self, meeting_id: uuid.UUID, *, summarize: bool = True
    ) -> TranscriptionResult:
        """Re-run SpeechKit on an existing audio artifact (e.g. after a failed STT)."""
        meeting = await self.repository.get(meeting_id)
        if meeting is None:
            raise ValueError("meeting not found")
        if meeting.status in self._ACTIVE_CAPTURE_STATUSES:
            raise ValueError(f"meeting is busy: {meeting.status}")

        audio_key = self._audio_object_key(meeting)
        if audio_key is None:
            raise ValueError("meeting has no audio artifact")

        audio_uri = self.object_store.object_uri(audio_key)
        if not audio_uri:
            raise ValueError("object storage does not expose a public URI for SpeechKit")

        metadata = meeting.metadata_json or {}
        participants_observed = metadata.get("participants_observed") or []

        await self.repository.set_status(meeting_id, "transcribing")
        try:
            transcription = await asyncio.wait_for(
                self.transcriber.transcribe(
                    Path("audio.ogg"),
                    audio_uri=audio_uri,
                    language=meeting.language,
                    participants_observed=participants_observed,
                ),
                timeout=self.settings.transcribe_timeout_sec,
            )
        except Exception as exc:
            await self.repository.set_status(
                meeting_id,
                "failed",
                error=f"retranscribe failed: {exc}",
            )
            raise

        from meeting_capture.transcription import map_speakers_to_names

        speaker_timeline = metadata.get("speaker_timeline") or []
        named_segments = map_speakers_to_names(
            transcription.segments,
            speaker_timeline,
            participants_observed=participants_observed,
            bot_display_name=self.settings.bot_display_name,
        )
        transcription = TranscriptionResult(
            source=transcription.source,
            segments=named_segments,
            participants_observed=transcription.participants_observed,
        )
        target_chat_id = metadata.get("target_chat_id")
        await self._store_transcription_result(
            meeting_id,
            transcription,
            target_chat_id=target_chat_id,
            summarize=summarize,
        )
        return transcription

    @staticmethod
    def _audio_object_key(meeting: Any) -> str | None:
        artifacts = getattr(meeting, "artifacts", None) or []
        audio = [artifact for artifact in artifacts if artifact.kind == "audio"]
        if not audio:
            return None
        audio.sort(key=lambda item: item.created_at or item.id, reverse=True)
        return str(audio[0].object_key)

    async def _store_transcription_result(
        self,
        meeting_id: uuid.UUID,
        transcription: TranscriptionResult,
        *,
        target_chat_id: str | None,
        summarize: bool,
    ) -> None:
        await self.repository.save_transcript(
            meeting_id=meeting_id,
            source=transcription.source,
            segments=transcription.segments,
            participants_observed=transcription.participants_observed,
        )
        await self._persist_transcript_artifacts(meeting_id, transcription)
        await self.repository.set_status(meeting_id, "ready")
        if transcription.segments:
            if summarize:
                await self._summarize_and_fanout(
                    meeting_id,
                    transcription,
                    target_chat_id=target_chat_id,
                )
        else:
            await self._deliver_empty_transcription_notice(
                meeting_id,
                transcription.source,
                target_chat_id=target_chat_id,
            )

    async def _collect_speaker_timeline(
        self, speaker_task: "asyncio.Task[list[dict[str, Any]]]"
    ) -> list[dict[str, Any]]:
        """Await the active-speaker task, isolating any failure to an empty
        timeline (diarization labels remain as the fallback)."""
        try:
            return await speaker_task
        except Exception:
            logger.exception("Active-speaker polling failed; using labels only")
            return []

    def _empty_audio_transcription_result(
        self,
        files: RecordingFiles,
        uploaded: dict[str, UploadedObject],
        *,
        participants_observed: list[dict[str, Any]],
    ) -> TranscriptionResult:
        if not self.settings.s3_enabled:
            source = "speechkit_s3_not_configured"
        elif not files.audio_path.exists() or "audio" not in uploaded:
            source = "speechkit_missing_audio_file"
        else:
            source = "speechkit_missing_audio_uri"
        logger.warning(
            "Meeting has no audio URI for SpeechKit (source=%s, s3_enabled=%s)",
            source,
            self.settings.s3_enabled,
        )
        return TranscriptionResult(
            source=source,
            segments=[],
            participants_observed=participants_observed,
        )

    async def _upload_recording_files(
        self,
        meeting_id: uuid.UUID,
        files: RecordingFiles,
    ) -> dict[str, UploadedObject]:
        expires_at = self._artifact_expiration()
        uploaded: dict[str, UploadedObject] = {}
        candidates = [
            ("recording", files.recording_path, "video/webm"),
            ("audio", files.audio_path, "audio/ogg"),
        ]
        for kind, path, content_type in candidates:
            if not path.exists():
                logger.warning("Expected %s artifact does not exist: %s", kind, path)
                continue
            obj = await self.object_store.upload_file(
                path,
                key=artifact_key(str(meeting_id), path.name),
                content_type=content_type,
            )
            await self.repository.add_artifact(
                meeting_id=meeting_id,
                kind=kind,
                object_key=obj.key,
                content_type=obj.content_type,
                size_bytes=obj.size_bytes,
                expires_at=expires_at,
            )
            uploaded[kind] = obj
        return uploaded

    async def _persist_transcript_artifacts(
        self,
        meeting_id: uuid.UUID,
        transcription: TranscriptionResult,
    ) -> None:
        """Upload human-readable and structured transcript files to object storage."""
        try:
            transcript_text = self._format_transcript(transcription)
            payload = {
                "meeting_id": str(meeting_id),
                "source": transcription.source,
                "participants_observed": transcription.participants_observed,
                "segments": transcription.segments,
            }
            await self._upload_bytes_artifact(
                meeting_id,
                kind="transcript",
                filename="transcript.txt",
                data=transcript_text.encode("utf-8"),
                content_type="text/plain; charset=utf-8",
            )
            await self._upload_bytes_artifact(
                meeting_id,
                kind="transcript_json",
                filename="transcript.json",
                data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                content_type="application/json; charset=utf-8",
            )
        except Exception:
            logger.exception("Failed to persist transcript artifacts for meeting %s", meeting_id)

    async def _persist_summary_artifact(self, meeting_id: uuid.UUID, summary: str) -> None:
        """Upload the meeting summary markdown to object storage."""
        if not summary.strip():
            return
        try:
            await self._upload_bytes_artifact(
                meeting_id,
                kind="summary",
                filename="summary.md",
                data=summary.encode("utf-8"),
                content_type="text/markdown; charset=utf-8",
            )
        except Exception:
            logger.exception("Failed to persist summary artifact for meeting %s", meeting_id)

    async def _upload_bytes_artifact(
        self,
        meeting_id: uuid.UUID,
        *,
        kind: str,
        filename: str,
        data: bytes,
        content_type: str,
    ) -> UploadedObject:
        obj = await self.object_store.upload_bytes(
            data,
            key=artifact_key(str(meeting_id), filename),
            content_type=content_type,
        )
        await self.repository.add_artifact(
            meeting_id=meeting_id,
            kind=kind,
            object_key=obj.key,
            content_type=obj.content_type,
            size_bytes=obj.size_bytes,
            expires_at=self._artifact_expiration(),
        )
        return obj

    async def _summarize_and_fanout(
        self,
        meeting_id: uuid.UUID,
        transcription: TranscriptionResult,
        *,
        target_chat_id: str | None,
    ) -> None:
        """Summarize the transcript, then deliver the summary to Telegram and
        hand it to pm_agent for board/task creation.

        Each leg is best-effort and isolated: a Telegram failure must not block
        pm_agent, and a summarizer failure must not break the (already ``ready``)
        meeting. Failures are logged, never raised.
        """
        if not self.settings.orchestrator_url or not transcription.segments:
            return
        base = self.settings.orchestrator_url.rstrip("/")
        transcript_text = self._format_transcript(transcription)

        summary: str | None = None
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                agents_response = await client.post(
                    f"{base}/rpc",
                    json={"jsonrpc": "2.0", "method": "list_agents", "params": {}, "id": 1},
                )
                agents_response.raise_for_status()
                agents = agents_response.json().get("result", [])
                if not any(agent.get("name") == "meeting_summarizer" for agent in agents):
                    return
                invoke_response = await client.post(
                    f"{base}/rpc",
                    json={
                        "jsonrpc": "2.0",
                        "method": "invoke",
                        "params": {
                            "agent": "meeting_summarizer",
                            "message": (
                                f"Обработай транскрипт встречи {meeting_id}:\n{transcript_text}"
                            ),
                            "session_id": str(meeting_id),
                        },
                        "id": 2,
                    },
                )
                invoke_response.raise_for_status()
                summary = (invoke_response.json().get("result") or {}).get("reply")
        except Exception:
            logger.exception("Failed to summarize meeting %s", meeting_id)
            return

        if not summary or not summary.strip():
            logger.warning("Empty summary for meeting %s; nothing to fan out", meeting_id)
            return

        # Persist the summary on the meeting for later retrieval.
        try:
            await self.repository.set_status(
                meeting_id,
                "ready",
                metadata_update={"summary": summary},
            )
        except Exception:
            logger.exception("Failed to store summary for meeting %s", meeting_id)

        await self._persist_summary_artifact(meeting_id, summary)
        await self._deliver_summary_to_telegram(meeting_id, summary, target_chat_id)
        if self.settings.summary_fanout_pm_agent:
            await self._send_summary_to_pm_agent(meeting_id, summary, target_chat_id, base)

    def _format_transcript(self, transcription: TranscriptionResult) -> str:
        """One line per segment: ``[mm:ss] Speaker: text`` (name if resolved)."""
        lines: list[str] = []
        for s in transcription.segments:
            who = s.get("speaker_name") or s.get("speaker_label") or "SPEAKER"
            lines.append(f"[{_fmt_ts(s.get('start_ms', 0))}] {who}: {s.get('text', '')}")
        return "\n".join(lines)

    async def _enqueue_telegram_text(
        self,
        meeting_id: uuid.UUID,
        *,
        target_chat_id: str | None,
        text: str,
        dedupe_key: str,
        skip_reason: str,
    ) -> None:
        chat_id = target_chat_id or self.settings.telegram_fallback_chat_id
        if not chat_id:
            logger.info("Meeting %s has no target_chat_id; skipping Telegram delivery", meeting_id)
            return
        from meeting_capture.telegram_outbox import enqueue_telegram_message

        team_id = getattr(self.repository, "team_id", None)
        if team_id is None:
            logger.warning("Repository has no team_id; cannot enqueue Telegram %s", skip_reason)
            return
        try:
            await enqueue_telegram_message(
                team_id=team_id,
                target_chat_id=str(chat_id),
                text=text,
                dedupe_key=dedupe_key,
            )
        except Exception:
            logger.exception(
                "Failed to enqueue Telegram %s for meeting %s",
                skip_reason,
                meeting_id,
            )

    async def _deliver_empty_transcription_notice(
        self,
        meeting_id: uuid.UUID,
        source: str,
        *,
        target_chat_id: str | None,
    ) -> None:
        from meeting_capture.transcription import empty_transcription_user_message

        message = empty_transcription_user_message(source)
        logger.info("Meeting %s has empty transcript (%s); notifying chat", meeting_id, source)
        await self._enqueue_telegram_text(
            meeting_id,
            target_chat_id=target_chat_id,
            text=f"⚠️ {message}",
            dedupe_key=f"meeting:transcript-empty:{meeting_id}",
            skip_reason="empty-transcript notice",
        )

    async def _deliver_summary_to_telegram(
        self,
        meeting_id: uuid.UUID,
        summary: str,
        target_chat_id: str | None,
    ) -> None:
        await self._enqueue_telegram_text(
            meeting_id,
            target_chat_id=target_chat_id,
            text=f"📝 Итоги встречи:\n\n{summary}",
            dedupe_key=f"meeting:summary:{meeting_id}",
            skip_reason="summary",
        )

    async def _send_summary_to_pm_agent(
        self,
        meeting_id: uuid.UUID,
        summary: str,
        target_chat_id: str | None,
        base: str,
    ) -> None:
        context: dict[str, Any] = {}
        if target_chat_id:
            context["chat_id"] = str(target_chat_id)
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                await client.post(
                    f"{base}/rpc",
                    json={
                        "jsonrpc": "2.0",
                        "method": "invoke",
                        "params": {
                            "agent": "pm_agent",
                            "message": f"Синхронизируй доску по итогам встречи:\n{summary}",
                            "session_id": f"meeting-board:{meeting_id}",
                            "context": context,
                        },
                        "id": 3,
                    },
                )
        except Exception:
            logger.exception("Failed to hand summary to pm_agent for meeting %s", meeting_id)

    def _artifact_expiration(self) -> datetime | None:
        if self.settings.audio_ttl_days <= 0:
            return None
        return utcnow() + timedelta(days=self.settings.audio_ttl_days)

    @staticmethod
    def _delay_seconds(starts_at: datetime | None) -> float:
        if starts_at is None:
            return 0
        value = starts_at
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return max((value - utcnow()).total_seconds(), 0)


__all__ = ["MeetingDispatcher"]
