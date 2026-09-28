"""Slice 1 renderer: validated Plan -> mp4, straight through FFmpeg.

Two passes. Pass 1 renders only the audio mix through `loudnorm` to measure it;
pass 2 renders picture + mix with linear loudnorm to the target (-14 LUFS).
Every clip is normalised to the plan's size, fps, SAR and pixel format and cut
to an exact frame count, so xfade offsets land on exact frames.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .plan import Clip, Plan
from .probe import tool

TARGET_LUFS = -14.0
TARGET_TP = -1.5
TARGET_LRA = 11.0
SAMPLE_RATE = 48000
SUPERSAMPLE = 4  # zoompan rounds x/y to whole pixels; zooming a 4x frame hides the jitter


class RenderError(RuntimeError):
    pass


def _secs(frames: int, fps: int) -> str:
    return f"{frames / fps:.6f}"


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "render"


def default_output(plan: Plan) -> Path:
    return plan.workspace / "renders" / f"{_slug(plan.meta.title)}.mp4"


# ── inputs ────────────────────────────────────────────────────────────────────

def _video_input(clip: Clip, fps: int) -> list[str]:
    # One spare second on every input; the filter chain pads and trims to the exact frame count.
    if clip.is_still:
        return ["-loop", "1", "-framerate", str(fps), "-t", _secs(clip.duration_frames + fps, fps),
                "-i", str(clip.src)]
    return ["-ss", _secs(clip.source_in_frame, fps), "-t", _secs(clip.duration_frames + fps, fps),
            "-i", str(clip.src)]


def _audio_input(clip: Clip, fps: int) -> list[str]:
    return ["-ss", _secs(clip.source_in_frame, fps), "-t", _secs(clip.duration_frames + fps, fps),
            "-i", str(clip.src)]


# ── video graph ───────────────────────────────────────────────────────────────

def _ease(clip: Clip) -> str:
    t = f"min(on/{max(clip.duration_frames - 1, 1)},1)"
    if clip.motion.easing == "easeInOutCubic":
        return f"if(lt({t},0.5),4*pow({t},3),1-pow(-2*{t}+2,3)/2)"
    return t


def _video_chain(idx: int, clip: Clip, plan: Plan, label: str) -> str:
    w, h, fps = plan.meta.width, plan.meta.height, plan.meta.fps
    f = [f"[{idx}:v]setpts=PTS-STARTPTS", f"fps={fps}"]
    if clip.fit == "cover":
        f += [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}"]
    else:
        f += [f"scale={w}:{h}:force_original_aspect_ratio=decrease",
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"]
    if clip.motion:
        m = clip.motion
        z = f"{m.scale_from}+({m.scale_to - m.scale_from})*({_ease(clip)})"
        f += [f"scale={w * SUPERSAMPLE}:{h * SUPERSAMPLE}:flags=lanczos",
              f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps}"]
    f += ["setsar=1", "format=yuv420p",
          "tpad=stop_mode=clone:stop_duration=1",
          f"trim=end_frame={clip.duration_frames}", "setpts=PTS-STARTPTS"]
    return ",".join(f) + f"[{label}]"


def _video_graph(plan: Plan) -> list[str]:
    fps = plan.meta.fps
    clips = plan.video.clips
    parts = [_video_chain(i, c, plan, f"v{i}") for i, c in enumerate(clips)]
    acc = "v0"
    for i in range(1, len(clips)):
        prev, cur = clips[i - 1], clips[i]
        out = f"x{i}"
        if prev.crossfade_frames:
            # offset is on the accumulated timeline, which starts at frame 0 = plan frame 0
            parts.append(f"[{acc}][v{i}]xfade=transition=fade:duration={_secs(prev.crossfade_frames, fps)}"
                         f":offset={_secs(cur.start_frame, fps)}[{out}]")
        else:
            parts.append(f"[{acc}][v{i}]concat=n=2:v=1:a=0[{out}]")
        acc = out
    parts.append(f"[{acc}]null[vout]")
    return parts


# ── audio graph ───────────────────────────────────────────────────────────────

def _audio_clips(plan: Plan) -> list[Clip]:
    return [c for t in plan.audio for c in t.clips]


def _samples(frames: int, fps: int) -> int:
    return round(frames * SAMPLE_RATE / fps)


def _audio_graph(plan: Plan, first_input: int) -> list[str]:
    """Mix every audio clip onto one stereo bus exactly meta.durationFrames long -> [amixed]."""
    fps = plan.meta.fps
    clips = _audio_clips(plan)
    parts = []
    for j, clip in enumerate(clips):
        parts.append(
            f"[{first_input + j}:a]asetpts=PTS-STARTPTS,aresample={SAMPLE_RATE},"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"apad,atrim=end_sample={_samples(clip.duration_frames, fps)},"
            f"volume={clip.volume},adelay=delays={_samples(clip.start_frame, fps)}S:all=1[a{j}]"
        )
    labels = "".join(f"[a{j}]" for j in range(len(clips)))
    if len(clips) == 1:
        parts.append(f"{labels}anull[mix]")
    else:
        parts.append(f"{labels}amix=inputs={len(clips)}:normalize=0:duration=longest[mix]")
    total = _samples(plan.meta.duration_frames, fps)
    parts.append(f"[mix]apad=whole_len={total},atrim=end_sample={total}[amixed]")
    return parts


def _loudnorm(measured: dict | None = None) -> str:
    base = f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}"
    if measured is None:
        return base + ":print_format=json"
    return (base + f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}:linear=true")


# ── commands ──────────────────────────────────────────────────────────────────

def measure_command(plan: Plan) -> list[str]:
    fps = plan.meta.fps
    inputs = [a for c in _audio_clips(plan) for a in _audio_input(c, fps)]
    graph = _audio_graph(plan, 0) + [f"[amixed]{_loudnorm()}[aout]"]
    return [tool("ffmpeg"), "-hide_banner", "-nostats", *inputs,
            "-filter_complex", ";".join(graph), "-map", "[aout]", "-f", "null", "-"]


def render_command(plan: Plan, out: Path, measured: dict) -> list[str]:
    fps = plan.meta.fps
    inputs = [a for c in plan.video.clips for a in _video_input(c, fps)]
    inputs += [a for c in _audio_clips(plan) for a in _audio_input(c, fps)]
    graph = _video_graph(plan) + _audio_graph(plan, len(plan.video.clips))
    graph.append(f"[amixed]{_loudnorm(measured)},aresample={SAMPLE_RATE}[aout]")
    return [tool("ffmpeg"), "-hide_banner", "-nostats", "-y", *inputs,
            "-filter_complex", ";".join(graph),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
            "-movflags", "+faststart", str(out)]


def _run(cmd: list[str], what: str) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-25:])
        raise RenderError(f"{what} failed (ffmpeg exit {result.returncode}):\n{tail}")
    return result.stderr


def measure_loudness(plan: Plan) -> dict:
    stderr = _run(measure_command(plan), "loudness measurement")
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start < 0 or end < start:
        raise RenderError("loudness measurement: no loudnorm JSON in ffmpeg output")
    measured = json.loads(stderr[start:end + 1])
    if measured.get("input_i") in ("-inf", "inf") or measured.get("input_thresh") in ("-inf", "inf"):
        raise RenderError("audio mix is silent; nothing to normalise to -14 LUFS")
    return measured


def render(plan: Plan, out: str | Path | None = None) -> Path:
    out = Path(out) if out else default_output(plan)
    out.parent.mkdir(parents=True, exist_ok=True)
    measured = measure_loudness(plan)
    _run(render_command(plan, out, measured), "render")
    return out
