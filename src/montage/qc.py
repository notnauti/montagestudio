"""Post-render QC: exact frame count, format, and EBU R128 loudness."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .plan import Plan
from .probe import tool
from .render import TARGET_LUFS

LUFS_TOLERANCE = 1.0  # ±1 LU, the usual R128 delivery tolerance
MAX_TRUE_PEAK = -1.0  # dBTP; loudnorm aims for -1.5, AAC encoding can add a little


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _streams(path: Path) -> list[dict]:
    result = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-count_packets",
         "-show_entries", "stream=codec_type,width,height,avg_frame_rate,nb_read_packets",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    return json.loads(result.stdout).get("streams", [])


def _loudness(path: Path) -> tuple[float | None, float | None]:
    result = subprocess.run(
        [tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path),
         "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    summary = result.stderr.rsplit("Summary:", 1)[-1]
    i = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    as_float = lambda m: float(m.group(1)) if m and m.group(1) != "-inf" else None  # noqa: E731
    return as_float(i), as_float(peak)


def run_qc(path: str | Path, plan: Plan) -> list[Check]:
    path = Path(path)
    meta = plan.meta
    streams = _streams(path)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    checks: list[Check] = []

    if video is None:
        return [Check("video stream", False, "no video stream in output")]
    frames = int(video.get("nb_read_packets", -1))
    checks.append(Check("frame count", frames == meta.duration_frames,
                        f"{frames} frames, plan says {meta.duration_frames}"))
    size = (video.get("width"), video.get("height"))
    checks.append(Check("resolution", size == (meta.width, meta.height),
                        f"{size[0]}x{size[1]}, plan says {meta.width}x{meta.height}"))
    fps = Fraction(video.get("avg_frame_rate", "0/1"))
    checks.append(Check("fps", fps == meta.fps, f"{float(fps):g}, plan says {meta.fps}"))

    if audio is None:
        checks.append(Check("audio stream", False, "no audio stream in output"))
        return checks
    integrated, true_peak = _loudness(path)
    checks.append(Check(
        "loudness", integrated is not None and abs(integrated - TARGET_LUFS) <= LUFS_TOLERANCE,
        f"{integrated} LUFS integrated, target {TARGET_LUFS} ±{LUFS_TOLERANCE}"))
    checks.append(Check(
        "true peak", true_peak is not None and true_peak <= MAX_TRUE_PEAK,
        f"{true_peak} dBTP, max {MAX_TRUE_PEAK}"))
    return checks
