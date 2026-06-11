"""SpeechKit transcription and speaker-label parsing."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from meeting_capture.bot import is_noise_participant_name, sanitize_participant_name
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

        async with httpx.AsyncClient(timeout=60) as client:
            operation_id = await self._start_operation(
                client,
                audio_uri=audio_uri,
                language=language,
            )
            payload = await self._poll_result(client, operation_id)

        return TranscriptionResult(
            source="speechkit",
            segments=deduplicate_mirror_segments(parse_speechkit_segments(payload)),
            participants_observed=participants_observed,
        )

    async def _start_operation(
        self,
        client: httpx.AsyncClient,
        *,
        audio_uri: str,
        language: str,
    ) -> str:
        # SpeechKit v2 async API: POST to transcribe.api.cloud.yandex.net
        url = "https://transcribe.api.cloud.yandex.net/speech/stt/v2/longRunningRecognize"
        body = {
            "config": {
                "specification": {
                    "languageCode": language,
                    "audioEncoding": "OGG_OPUS",
                    "model": "general",
                    "enableSpeakerDiarization": True,
                    "speakerCount": 10,
                }
            },
            "audio": {"uri": audio_uri},
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
            raise RuntimeError(f"SpeechKit did not return an operation id: {payload}")
        return str(operation_id)

    async def _poll_result(self, client: httpx.AsyncClient, operation_id: str) -> Any:
        # Operations API: GET operation.api.cloud.yandex.net/operations/{id}
        deadline = time.monotonic() + self.settings.speechkit_timeout_sec
        url = f"https://operation.api.cloud.yandex.net/operations/{operation_id}"
        while time.monotonic() < deadline:
            response = await client.get(
                url,
                headers={"Authorization": f"Api-Key {self.settings.effective_speechkit_api_key}"},
            )
            if response.status_code in (202, 404, 409):
                await asyncio.sleep(self.settings.speechkit_poll_interval_sec)
                continue
            response.raise_for_status()
            payload = response.json()
            if not payload.get("done"):
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
    """Normalize SpeechKit v2 async response into transcript segments.

    The Operations API wraps the result as:
      {"done": true, "response": {"chunks": [{"alternatives": [...], "speakerTag": "1"}]}}
    Each chunk has one winning alternative. Words carry per-word speakerTag for
    diarization when enableSpeakerDiarization=true.
    Also handles bare list/dict payloads (tests, v3 streaming fallback).
    """
    # Unwrap Operations API envelope.
    if isinstance(payload, dict) and "response" in payload:
        payload = payload["response"]

    segments: list[dict[str, Any]] = []

    chunks = None
    if isinstance(payload, dict):
        chunks = payload.get("chunks")

    if chunks is None:
        # Fallback: iterate all dicts looking for "final" (v3 streaming shape).
        for event in _iter_dicts(payload):
            final = event.get("final")
            if not isinstance(final, dict):
                continue
            channel_or_speaker = _first_value(
                final, "speaker_tag", "speakerTag", "channel_tag", "channelTag"
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
                    _first_value(
                        alternative, "speaker_tag", "speakerTag", "channel_tag", "channelTag"
                    )
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

    # v2 chunks path.
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_speaker = (
            _first_value(chunk, "speakerTag", "speaker_tag", "channelTag", "channel_tag") or "0"
        )
        alternatives = chunk.get("alternatives") or []
        if not isinstance(alternatives, list):
            continue
        # v2 returns only the best alternative per chunk.
        alternative = alternatives[0] if alternatives else None
        if not isinstance(alternative, dict):
            continue
        # v2 uses "transcript"; v3 uses "text".
        text = str(alternative.get("transcript") or alternative.get("text") or "").strip()
        if not text:
            continue
        words = alternative.get("words") or []
        # Per-word speakerTag is authoritative when diarization is on.
        speaker = _speaker_from_words(words) or chunk_speaker
        # v2 uses "startTime"/"endTime" as "X.XXXs" strings.
        start_ms = _time_to_ms(alternative.get("startTime")) or _int_value(
            alternative, "startTimeMs", "start_time_ms"
        )
        end_ms = _time_to_ms(alternative.get("endTime")) or _int_value(
            alternative, "endTimeMs", "end_time_ms"
        )
        if start_ms is None and words:
            start_ms = _time_to_ms(words[0].get("startTime")) or _int_value(
                words[0], "startTimeMs", "start_time_ms"
            )
        if end_ms is None and words:
            end_ms = _time_to_ms(words[-1].get("endTime")) or _int_value(
                words[-1], "endTimeMs", "end_time_ms"
            )
        speaker_label = f"SPEAKER_{speaker.zfill(2)}" if speaker.isdigit() else str(speaker)
        segments.append(
            {
                "start_ms": max(start_ms or 0, 0),
                "end_ms": max(end_ms or start_ms or 0, 0),
                "speaker_label": speaker_label,
                "text": text,
            }
        )

    return sorted(segments, key=lambda item: (item["start_ms"], item["end_ms"]))


def _normalize_segment_text(text: str) -> str:
    return " ".join(text.split())


# Mono Telemost mixes often get the same phrase on SPEAKER_01 and SPEAKER_02 with
# slightly different SpeechKit time bounds — collapse those mirrors.
_MIRROR_START_TOLERANCE_MS = 2_000


def deduplicate_mirror_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop diarization mirror duplicates on mono recordings.

    SpeechKit frequently emits the same utterance twice under different speaker
    labels. Mirrors share the same words but may differ in ``end_ms`` or by a
    few hundred ms at ``start_ms``. Keep one segment per near-duplicate cluster.
    """
    prepared: list[dict[str, Any]] = []
    for seg in segments:
        text = _normalize_segment_text(str(seg.get("text") or ""))
        if not text:
            continue
        prepared.append({**seg, "text": text})

    prepared.sort(
        key=lambda item: (
            int(item.get("start_ms") or 0),
            str(item.get("speaker_label") or ""),
        )
    )

    kept: list[dict[str, Any]] = []
    for seg in prepared:
        start = int(seg.get("start_ms") or 0)
        text = seg["text"]
        replace_idx: int | None = None
        for idx, existing in enumerate(kept):
            if existing["text"] != text:
                continue
            estart = int(existing.get("start_ms") or 0)
            if abs(start - estart) > _MIRROR_START_TOLERANCE_MS:
                continue
            # Same phrase near the same time — mirror duplicate.
            if not existing.get("speaker_name") and seg.get("speaker_name"):
                replace_idx = idx
            else:
                replace_idx = -1  # drop seg
            break
        if replace_idx is None:
            kept.append(seg)
        elif replace_idx >= 0:
            kept[replace_idx] = seg
    return kept


def _clean_participant_roster(
    participants_observed: list[dict[str, Any]] | None,
    *,
    bot_display_name: str = "",
) -> list[str]:
    roster: list[str] = []
    seen: set[str] = set()
    for item in participants_observed or []:
        name = sanitize_participant_name(str(item.get("display_name") or ""))
        key = name.casefold()
        if not name or key in seen:
            continue
        if is_noise_participant_name(name, bot_display_name=bot_display_name):
            continue
        seen.add(key)
        roster.append(name)
    return roster


def _map_speakers_from_roster(
    segments: list[dict[str, Any]],
    roster: list[str],
) -> list[dict[str, Any]]:
    """Fallback when DOM timeline is empty: map labels to roster by speech order."""
    labels_order: list[str] = []
    for seg in sorted(segments, key=lambda item: int(item.get("start_ms") or 0)):
        label = str(seg.get("speaker_label") or "")
        if label and label not in labels_order:
            labels_order.append(label)
    if not labels_order or len(labels_order) != len(roster):
        return [{**seg, "speaker_name": None} for seg in segments]
    mapping = dict(zip(labels_order, roster, strict=True))
    return [
        {**seg, "speaker_name": mapping.get(str(seg.get("speaker_label") or ""))}
        for seg in segments
    ]


def map_speakers_to_names(
    segments: list[dict[str, Any]],
    speaker_timeline: list[dict[str, Any]],
    *,
    participants_observed: list[dict[str, Any]] | None = None,
    bot_display_name: str = "",
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
        roster = _clean_participant_roster(
            participants_observed,
            bot_display_name=bot_display_name,
        )
        if roster:
            return _map_speakers_from_roster(segments, roster)
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


def _time_to_ms(value: Any) -> int | None:
    """Convert SpeechKit v2 time string '9.480s' to milliseconds."""
    if value is None:
        return None
    s = str(value).strip()
    if s.endswith("s"):
        s = s[:-1]
    try:
        return int(float(s) * 1000)
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
    "deduplicate_mirror_segments",
    "empty_transcription_user_message",
    "map_speakers_to_names",
    "parse_speechkit_segments",
]
