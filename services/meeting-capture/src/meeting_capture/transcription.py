"""SpeechKit transcription and speaker-label parsing."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from meeting_capture.config import CaptureSettings


@dataclass(frozen=True)
class TranscriptionResult:
    source: str
    segments: list[dict[str, Any]]
    participants_observed: list[dict[str, Any]] = field(default_factory=list)


class Transcriber:
    async def transcribe(
        self,
        audio_path: Path,
        *,
        audio_uri: str | None,
        language: str,
        participants_observed: list[dict[str, Any]],
    ) -> TranscriptionResult:
        raise NotImplementedError


class SpeechKitTranscriber(Transcriber):
    """Yandex SpeechKit v3 async file transcription."""

    def __init__(self, settings: CaptureSettings) -> None:
        self.settings = settings

    async def transcribe(
        self,
        audio_path: Path,
        *,
        audio_uri: str | None,
        language: str,
        participants_observed: list[dict[str, Any]],
    ) -> TranscriptionResult:
        del audio_path
        if not self.settings.effective_speechkit_api_key:
            return TranscriptionResult(
                source="speechkit_unconfigured",
                segments=[],
                participants_observed=participants_observed,
            )
        if not audio_uri:
            return TranscriptionResult(
                source="speechkit_missing_audio_uri",
                segments=[],
                participants_observed=participants_observed,
            )

        async with httpx.AsyncClient(timeout=30) as client:
            operation_id = await self._start_operation(
                client,
                audio_uri=audio_uri,
                language=language,
            )
            payload = await self._poll_result(client, operation_id)

        return TranscriptionResult(
            source="speechkit",
            segments=parse_speechkit_segments(payload),
            participants_observed=participants_observed,
        )

    async def _start_operation(
        self,
        client: httpx.AsyncClient,
        *,
        audio_uri: str,
        language: str,
    ) -> str:
        url = f"{self.settings.speechkit_base_url.rstrip('/')}/stt/v3/recognizeFileAsync"
        body = {
            "uri": audio_uri,
            "recognitionModel": {
                "model": "general",
                "audioFormat": {"containerAudio": {"containerAudioType": "OGG_OPUS"}},
                "languageRestriction": {
                    "restrictionType": "WHITELIST",
                    "languageCode": [language],
                },
            },
            "speechAnalysis": {"enableSpeakerAnalysis": True},
            "speakerLabeling": {"speakerLabeling": "SPEAKER_LABELING_ENABLED"},
        }
        response = await client.post(
            url,
            json=body,
            headers={"Authorization": f"Api-Key {self.settings.effective_speechkit_api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
        operation_id = (
            payload.get("id") or payload.get("operation_id") or payload.get("operationId")
        )
        if not operation_id:
            raise RuntimeError("SpeechKit did not return an operation id")
        return str(operation_id)

    async def _poll_result(self, client: httpx.AsyncClient, operation_id: str) -> Any:
        deadline = time.monotonic() + self.settings.speechkit_timeout_sec
        url = f"{self.settings.speechkit_base_url.rstrip('/')}/stt/v3/getRecognition"
        while time.monotonic() < deadline:
            response = await client.get(
                url,
                params={"operationId": operation_id},
                headers={"Authorization": f"Api-Key {self.settings.effective_speechkit_api_key}"},
            )
            if response.status_code in (202, 404, 409):
                await asyncio.sleep(self.settings.speechkit_poll_interval_sec)
                continue
            response.raise_for_status()
            payload = response.json()
            if payload.get("done") is False:
                await asyncio.sleep(self.settings.speechkit_poll_interval_sec)
                continue
            return payload
        raise TimeoutError("SpeechKit transcription timed out")


def empty_transcription_user_message(source: str) -> str:
    """Human-readable Telegram notice when a meeting has no transcript segments."""
    if source == "speechkit_unconfigured":
        return (
            "Встреча записана, но транскрибация не настроена. "
            "Задайте SPEECHKIT_API_KEY или YC_API_KEY с ролью SpeechKit."
        )
    if source == "speechkit_s3_not_configured":
        return (
            "Встреча записана локально, но S3 не подключён в meeting-capture. "
            "Добавьте S3_* в .env и пересоздайте контейнер "
            "(docker compose ... --env-file .env.test up -d --force-recreate meeting-capture)."
        )
    if source == "speechkit_missing_audio_file":
        return (
            "Встреча завершена, но аудиофайл не записался (ffmpeg/PulseAudio). "
            "Проверьте логи meeting-capture."
        )
    if source == "speechkit_missing_audio_uri":
        return (
            "Встреча записана, но аудио не попало в Object Storage. "
            "Проверьте S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY и права на бакет."
        )
    if source == "speechkit":
        return (
            "Встреча записана, но речь не распознана "
            "(тишина, слишком короткая запись или ошибка SpeechKit)."
        )
    return f"Встреча записана, но транскрипт пуст (источник: {source})."


def parse_speechkit_segments(payload: Any) -> list[dict[str, Any]]:
    """Normalize SpeechKit response events into transcript segments."""

    segments: list[dict[str, Any]] = []
    for event in _iter_dicts(payload):
        final = event.get("final")
        if not isinstance(final, dict):
            continue
        channel_or_speaker = _first_value(
            final,
            "speaker_tag",
            "speakerTag",
            "channel_tag",
            "channelTag",
        )
        alternatives = final.get("alternatives") or []
        if not isinstance(alternatives, list):
            continue
        for alternative in alternatives:
            if not isinstance(alternative, dict):
                continue
            text = str(alternative.get("text") or "").strip()
            if not text:
                continue
            words = alternative.get("words")
            speaker = (
                _first_value(alternative, "speaker_tag", "speakerTag", "channel_tag", "channelTag")
                or _speaker_from_words(words)
                or channel_or_speaker
                or "SPEAKER_00"
            )
            start_ms = _int_value(alternative, "start_time_ms", "startTimeMs")
            end_ms = _int_value(alternative, "end_time_ms", "endTimeMs")
            if start_ms is None and isinstance(words, list) and words:
                start_ms = _int_value(words[0], "start_time_ms", "startTimeMs")
            if end_ms is None and isinstance(words, list) and words:
                end_ms = _int_value(words[-1], "end_time_ms", "endTimeMs")
            segments.append(
                {
                    "start_ms": max(start_ms or 0, 0),
                    "end_ms": max(end_ms or start_ms or 0, 0),
                    "speaker_label": str(speaker),
                    "text": text,
                }
            )
    return sorted(segments, key=lambda item: (item["start_ms"], item["end_ms"]))


def map_speakers_to_names(
    segments: list[dict[str, Any]],
    speaker_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach a human ``speaker_name`` to each diarized segment.

    SpeechKit gives anonymous diarization labels (SPEAKER_00, ...). The bot
    records an active-speaker timeline from the Telemost DOM as a list of
    ``{"start_ms", "end_ms", "display_name"}`` windows (same time base as the
    audio — both relative to recording start).

    For each transcript segment we accumulate, per timeline name, how much its
    window overlaps the segment; the name with the largest total overlap across
    ALL segments of a given ``speaker_label`` wins (majority by duration). This
    is robust to small misalignments and to the occasional missing window.

    Returns NEW segment dicts with ``speaker_name`` added (``None`` when the
    timeline is empty or no overlap was found — callers fall back to the label).
    """
    if not segments:
        return segments
    if not speaker_timeline:
        return [{**seg, "speaker_name": None} for seg in segments]

    # Tally overlap(label -> name -> ms) across all segments.
    tally: dict[str, dict[str, int]] = {}
    for seg in segments:
        label = str(seg.get("speaker_label") or "")
        s_start = int(seg.get("start_ms") or 0)
        s_end = int(seg.get("end_ms") or s_start)
        for window in speaker_timeline:
            name = window.get("display_name")
            if not name:
                continue
            w_start = int(window.get("start_ms") or 0)
            w_end = int(window.get("end_ms") or w_start)
            overlap = min(s_end, w_end) - max(s_start, w_start)
            if overlap <= 0:
                continue
            tally.setdefault(label, {})[name] = tally.setdefault(label, {}).get(name, 0) + overlap

    # Pick the dominant name per label.
    label_to_name: dict[str, str] = {}
    for label, names in tally.items():
        if names:
            label_to_name[label] = max(names.items(), key=lambda kv: kv[1])[0]

    return [
        {**seg, "speaker_name": label_to_name.get(str(seg.get("speaker_label") or ""))}
        for seg in segments
    ]


def _iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _int_value(mapping: dict[str, Any], *keys: str) -> int | None:
    value = _first_value(mapping, *keys)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _speaker_from_words(words: Any) -> Any:
    if not isinstance(words, list):
        return None
    for word in words:
        if isinstance(word, dict):
            value = _first_value(word, "speaker_tag", "speakerTag", "channel_tag", "channelTag")
            if value:
                return value
    return None


__all__ = [
    "SpeechKitTranscriber",
    "Transcriber",
    "TranscriptionResult",
    "empty_transcription_user_message",
    "map_speakers_to_names",
    "parse_speechkit_segments",
]
