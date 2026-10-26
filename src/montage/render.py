"""Renderer: validated Plan -> video file, straight through FFmpeg.

Two passes. Pass 1 renders only the audio mix through `loudnorm` to measure it;
pass 2 renders picture + mix with linear loudnorm to the target (-14 LUFS).
Every clip is conformed to the output size, fps, SAR and pixel format and cut
to an exact frame count, so xfade offsets land on exact frames.

Output size / fps / codec come from an OutputSpec (see profiles.py). Without a profile
the spec is the plan's own grid in H.264, i.e. the slice 1 output. With a profile whose
fps differs from meta.fps, every clip boundary is mapped onto the output grid
(round(frame * out_fps / plan_fps)) and crossfades are re-derived from the mapped
boundaries, so `next.start == prev.end - crossfade` still holds exactly.

Before rendering, assets are normalized (normalize.py) into the workspace cache unless
normalize=False; the render then reads the cached copies.

A caption track is written as an ASS file (subtitles.py) under <workspace>/cache/captions/ and
burned in with the `ass` filter as the last video step, unless captions=False (--no-captions).

preview() is the same graph at preview_spec() (480p, ultrafast, no zoom supersampling) with one
single-pass loudnorm instead of the measure + linear pass; it runs on unapproved plans (that is
its job) and records the plan fingerprint it was made from (review.py) for `montage approve`.
"""

from __future__ import annotations

import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path

from . import normalize as normalize_mod
from . import review
from . import subtitles as subtitles_mod
from .plan import Clip, Plan
from .probe import tool
from .profiles import OutputSpec, preview_spec, resolve

TARGET_LUFS = -14.0
TARGET_TP = -1.5
TARGET_LRA = 11.0
SAMPLE_RATE = 48000
SUPERSAMPLE = 4  # zoompan rounds x/y to whole pixels; zooming a 4x frame hides the jitter
MAX_ZOOM_EDGE = 8192  # cap on the supersampled frame's long edge (4K output gets 2x, not 4x)


class RenderError(RuntimeError):
    pass


def _secs(frames: int, fps: int) -> str:
    return f"{frames / fps:.6f}"


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "render"


def default_output(plan: Plan, spec: OutputSpec | None = None) -> Path:
    if spec is None or spec.profile == "native":
        return plan.workspace / "renders" / f"{_slug(plan.meta.title)}.mp4"
    return plan.workspace / "renders" / f"{_slug(plan.meta.title)}-{spec.profile}{spec.ext}"


def out_frames(frames: int, plan: Plan, spec: OutputSpec) -> int:
    """A plan frame number on the output grid."""
    return round(Fraction(frames * spec.fps, plan.meta.fps))


def _supersample(spec: OutputSpec) -> int:
    if spec.supersample:
        return spec.supersample
    return max(1, min(SUPERSAMPLE, MAX_ZOOM_EDGE // max(spec.width, spec.height)))


# ── inputs ────────────────────────────────────────────────────────────────────

def _video_input(clip: Clip, fps: int, still_once: bool = False) -> list[str]:
    # One spare second on every input; the filter chain pads and trims to the exact frame count.
    if clip.is_still and still_once:
        return ["-i", str(clip.src)]  # one decoded frame; the chain fits it once, then loops it
    if clip.is_still:
        return ["-loop", "1", "-framerate", str(fps), "-t", _secs(clip.duration_frames + fps, fps),
                "-i", str(clip.src)]
    return ["-ss", _secs(clip.source_in_frame, fps), "-t", _secs(clip.duration_frames + fps, fps),
            "-i", str(clip.src)]


def _audio_input(clip: Clip, fps: int) -> list[str]:
    return ["-ss", _secs(clip.source_in_frame, fps), "-t", _secs(clip.duration_frames + fps, fps),
            "-i", str(clip.src)]


# ── video graph ───────────────────────────────────────────────────────────────

def _ease(frames: int, easing: str) -> str:
    t = f"min(on/{max(frames - 1, 1)},1)"
    if easing == "easeInOutCubic":
        return f"if(lt({t},0.5),4*pow({t},3),1-pow(-2*{t}+2,3)/2)"
    return t


def _video_chain(idx: int, clip: Clip, frames: int, spec: OutputSpec, label: str) -> str:
    w, h, fps = spec.width, spec.height, spec.fps
    still_once = clip.is_still and spec.still_once
    f = [f"[{idx}:v]setpts=PTS-STARTPTS"] + ([] if still_once else [f"fps={fps}"])
    if clip.fit == "cover":
        f += [f"scale={w}:{h}:force_original_aspect_ratio=increase", f"crop={w}:{h}"]
    else:
        f += [f"scale={w}:{h}:force_original_aspect_ratio=decrease",
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"]
    if still_once:  # decode + fit the still once, then repeat that frame on the output grid
        f += [f"loop=loop={max(frames - 1, 0)}:size=1", f"setpts=N/{fps}/TB"]
    if clip.motion:
        m = clip.motion
        ss = _supersample(spec)
        z = f"{m.scale_from}+({m.scale_to - m.scale_from})*({_ease(frames, m.easing)})"
        f += [f"scale={w * ss}:{h * ss}:flags=lanczos",
              f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps}"]
    # out_range=tv: a full-range source (any JPEG, e.g. a sourced Commons still) otherwise keeps its
    # range flag through format=, and the output is tagged yuvj420p instead of yuv420p
    f += ["setsar=1", "scale=out_range=tv", f"format={spec.pix_fmt}",
          "tpad=stop_mode=clone:stop_duration=1",
          f"trim=end_frame={frames}", "setpts=PTS-STARTPTS"]
    return ",".join(f) + f"[{label}]"


def _video_graph(plan: Plan, spec: OutputSpec, subtitles: Path | None = None) -> list[str]:
    fps = spec.fps
    clips = plan.video.clips
    starts = [out_frames(c.start_frame, plan, spec) for c in clips]
    ends = [out_frames(c.end_frame, plan, spec) for c in clips]
    parts = [_video_chain(i, c, ends[i] - starts[i], spec, f"v{i}") for i, c in enumerate(clips)]
    acc = "v0"
    for i in range(1, len(clips)):
        out = f"x{i}"
        overlap = ends[i - 1] - starts[i]  # = the crossfade on the output grid; 0 for a hard cut
        if clips[i - 1].crossfade_frames and overlap > 0:
            # offset is on the accumulated timeline, which starts at frame 0 = plan frame 0
            parts.append(f"[{acc}][v{i}]xfade=transition=fade:duration={_secs(overlap, fps)}"
                         f":offset={_secs(starts[i], fps)}[{out}]")
        else:
            parts.append(f"[{acc}][v{i}]concat=n=2:v=1:a=0[{out}]")
        acc = out
    if subtitles is not None:
        parts.append(f"[{acc}]ass=filename={subtitles_mod.filter_path(subtitles)}[vout]")
    else:
        parts.append(f"[{acc}]null[vout]")
    return parts


def write_subtitles(plan: Plan, spec: OutputSpec) -> Path | None:
    """The plan's caption track as an ASS file on the output grid, or None when it has none."""
    if plan.captions is None:
        return None
    path = plan.workspace / "cache" / "captions" / f"{_slug(plan.meta.title)}-{spec.profile}.ass"
    return subtitles_mod.write_ass(plan.captions, path, spec.width, spec.height, spec.fps,
                                   lambda f: out_frames(f, plan, spec))


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


def _loudnorm(measured: dict | None = None, *, one_pass: bool = False) -> str:
    base = f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}"
    if one_pass:  # dynamic mode, no measurement pass: close to the target, not held to QC tolerance
        return base
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


def render_command(plan: Plan, out: Path, measured: dict | None, spec: OutputSpec | None = None,
                   subtitles: Path | None = None) -> list[str]:
    """measured=None: single-pass loudnorm (the preview); else the linear second pass."""
    spec = spec or resolve(plan)
    fps = plan.meta.fps
    inputs = [a for c in plan.video.clips for a in _video_input(c, fps, spec.still_once)]
    inputs += [a for c in _audio_clips(plan) for a in _audio_input(c, fps)]
    graph = _video_graph(plan, spec, subtitles) + _audio_graph(plan, len(plan.video.clips))
    graph.append(f"[amixed]{_loudnorm(measured, one_pass=measured is None)},aresample={SAMPLE_RATE}[aout]")
    return [tool("ffmpeg"), "-hide_banner", "-nostats", "-y", *inputs,
            "-filter_complex", ";".join(graph),
            "-map", "[vout]", "-map", "[aout]",
            *spec.video_args, "-r", str(spec.fps),
            *spec.audio_args,
            "-movflags", "+faststart", str(out)]


def _run(cmd: list[str], what: str, timeout: float | None = None) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout)
    except subprocess.TimeoutExpired as exc:  # run() has killed it
        raise RenderError(f"{what} timed out after {timeout:g} s") from exc
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


def _normalized(plan: Plan) -> Plan:
    try:
        return normalize_mod.apply(plan, normalize_mod.normalize_plan(plan, fix=True))
    except normalize_mod.NormalizeError as exc:
        raise RenderError(str(exc)) from exc


def render(plan: Plan, out: str | Path | None = None, spec: OutputSpec | None = None,
           *, normalize: bool = True, captions: bool = True) -> Path:
    spec = spec or resolve(plan)
    out = Path(out) if out else default_output(plan, spec)
    if out.suffix.lower() != spec.ext:
        raise RenderError(f"{spec.profile} writes {spec.ext} files; output is {out.name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    if normalize:
        plan = _normalized(plan)
    measured = measure_loudness(plan)
    subtitles = write_subtitles(plan, spec) if captions else None
    _run(render_command(plan, out, measured, spec, subtitles), "render")
    return out


def preview_timeout(plan: Plan) -> float:
    """Seconds before a preview ffmpeg counts as hung: a 480p animatic renders about real time, so 10x."""
    return max(120, 10 * plan.meta.duration_frames / plan.meta.fps)


def default_preview(plan: Plan) -> Path:
    return review.preview_path(plan.workspace)


def preview(plan: Plan, plan_path: str | Path, out: str | Path | None = None,
            *, normalize: bool = True, captions: bool = True) -> Path:
    """Fast animatic of an (approved or not) plan -> <workspace>/cache/preview.mp4 + its source record.

    One ffmpeg pass: no loudness measurement, single-pass loudnorm. The fingerprint is taken
    before rendering, so a plan edited mid-render leaves the preview marked stale."""
    spec = preview_spec(plan)
    out = Path(out) if out else default_preview(plan)
    if out.suffix.lower() != spec.ext:
        raise RenderError(f"the preview is {spec.ext}; output is {out.name}")
    fp = review.fingerprint(plan, Path(plan_path))
    out.parent.mkdir(parents=True, exist_ok=True)
    if normalize:
        plan = _normalized(plan)
    subtitles = write_subtitles(plan, spec) if captions else None
    _run(render_command(plan, out, None, spec, subtitles), "preview", preview_timeout(plan))
    review.record(out, "preview", fp, plan.workspace)
    return out
