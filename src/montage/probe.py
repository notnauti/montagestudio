"""Thin wrappers over the ffmpeg / ffprobe binaries."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ToolMissing(RuntimeError):
    """ffmpeg or ffprobe is not on PATH."""


class ProbeError(RuntimeError):
    """ffprobe could not read a file."""


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolMissing(f"{name} not found on PATH")
    return path


@dataclass(frozen=True)
class MediaInfo:
    has_video: bool
    has_audio: bool
    duration: float | None  # seconds; None when the container reports none (still images)


def probe(path: Path) -> MediaInfo:
    result = subprocess.run(
        [tool("ffprobe"), "-v", "error",
         "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ProbeError(result.stderr.strip() or f"ffprobe exited {result.returncode}")
    data = json.loads(result.stdout or "{}")
    kinds = {s.get("codec_type") for s in data.get("streams", [])}
    raw = data.get("format", {}).get("duration")
    try:
        duration = float(raw) if raw not in (None, "N/A") else None
    except ValueError:
        duration = None
    return MediaInfo("video" in kinds, "audio" in kinds, duration)
