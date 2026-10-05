"""Post-render QC: exact frame count, size, fps, codec, pixel format, and EBU R128 loudness.

Expectations come from the OutputSpec the file was rendered with (profile size / fps / codec);
without one, from the plan itself (the slice 1 H.264 output).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .plan import Plan
from .probe import tool
from .profiles import OutputSpec, resolve
from .render import TARGET_LUFS, out_frames

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
         "-show_entries", "stream=codec_type,codec_name,pix_fmt,width,height,avg_frame_rate,nb_read_packets",
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


def run_qc(path: str | Path, plan: Plan, spec: OutputSpec | None = None, *, loudness: bool = True) -> list[Check]:
    """loudness=False is the preview's light QC: stream shape only, no EBU R128 pass."""
    path = Path(path)
    spec = spec or resolve(plan)
    expect_frames = out_frames(plan.meta.duration_frames, plan, spec)
    want = f"{spec.profile} expects" if spec.profile != "native" else "plan says"
    streams = _streams(path)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    checks: list[Check] = []

    if video is None:
        return [Check("video stream", False, "no video stream in output")]
    frames = int(video.get("nb_read_packets", -1))
    checks.append(Check("frame count", frames == expect_frames, f"{frames} frames, {want} {expect_frames}"))
    size = (video.get("width"), video.get("height"))
    checks.append(Check("resolution", size == (spec.width, spec.height),
                        f"{size[0]}x{size[1]}, {want} {spec.width}x{spec.height}"))
    fps = Fraction(video.get("avg_frame_rate", "0/1"))
    checks.append(Check("fps", fps == spec.fps, f"{float(fps):g}, {want} {spec.fps}"))
    vcodec, pix = video.get("codec_name"), video.get("pix_fmt")
    checks.append(Check("video codec", vcodec == spec.codec and pix == spec.pix_fmt,
                        f"{vcodec} {pix} (encoder {spec.encoder}), {want} {spec.codec} {spec.pix_fmt}"))

    if audio is None:
        checks.append(Check("audio stream", False, "no audio stream in output"))
        return checks
    acodec = audio.get("codec_name")
    checks.append(Check("audio codec", acodec == spec.audio_codec, f"{acodec}, {want} {spec.audio_codec}"))
    if not loudness:
        return checks
    integrated, true_peak = _loudness(path)
    checks.append(Check(
        "loudness", integrated is not None and abs(integrated - TARGET_LUFS) <= LUFS_TOLERANCE,
        f"{integrated} LUFS integrated, target {TARGET_LUFS} ±{LUFS_TOLERANCE}"))
    checks.append(Check(
        "true peak", true_peak is not None and true_peak <= MAX_TRUE_PEAK,
        f"{true_peak} dBTP, max {MAX_TRUE_PEAK}"))
    return checks
