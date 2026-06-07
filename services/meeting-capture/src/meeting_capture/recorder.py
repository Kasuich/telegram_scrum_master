"""FFmpeg-based screen and audio recorder."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from meeting_capture.config import CaptureSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecordingFiles:
    recording_path: Path
    audio_path: Path


class Recorder:
    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> RecordingFiles:
        raise NotImplementedError


class FfmpegRecorder(Recorder):
    """Capture X11 display video and PulseAudio output into meeting artifacts."""

    def __init__(self, settings: CaptureSettings, work_dir: Path) -> None:
        self.settings = settings
        self.work_dir = work_dir
        self.recording_path = work_dir / "recording.webm"
        self.audio_path = work_dir / "audio.ogg"
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        command = [
            self.settings.ffmpeg_bin,
            "-y",
            "-video_size",
            self.settings.video_size,
            "-framerate",
            str(self.settings.framerate),
            "-f",
            "x11grab",
            "-i",
            self.settings.effective_display,
            "-f",
            "pulse",
            "-i",
            self.settings.pulse_source,
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libvpx-vp9",
            "-deadline",
            "realtime",
            "-cpu-used",
            "4",
            "-c:a",
            "libopus",
            str(self.recording_path),
            "-map",
            "1:a",
            "-vn",
            "-c:a",
            "libopus",
            str(self.audio_path),
        ]
        logger.info("Starting ffmpeg recorder: %s", " ".join(command))
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def stop(self) -> RecordingFiles:
        if self._process is None:
            return RecordingFiles(self.recording_path, self.audio_path)

        process = self._process
        self._process = None
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("FFmpeg did not stop gracefully, killing it")
                process.kill()
                await process.wait()
        stderr = b""
        if process.stderr is not None:
            try:
                stderr = await asyncio.wait_for(process.stderr.read(), timeout=1)
            except asyncio.TimeoutError:
                stderr = b""
        if process.returncode not in (0, 255, -15, None):
            logger.warning("FFmpeg exited with %s: %s", process.returncode, stderr[-1000:])
        return RecordingFiles(self.recording_path, self.audio_path)


__all__ = ["FfmpegRecorder", "Recorder", "RecordingFiles"]
