"""inspect_frame_context: one timeline frame as a PNG, plus what is on screen and on air at it.

The guide asks for "an uncompressed frame buffer" with "OCR/visual descriptions". There is no
OCR or vision model here: the description is read from the plan itself (which clips, source
times, zoom, crossfade progress, audio, the caption page and current word), and the picture is
rendered by the same filter chains the renderer uses, so it is what `montage render` would draw:

  only the video clips active at the frame (one, or two inside a crossfade) are decoded, through
  render._video_chain at the plan's own size and fps; two are joined with the same xfade; the
  stream's timestamps are shifted to the timeline so the plan's caption track (written as ASS)
  burns in at the right moment; then the one frame is cut out with trim.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import normalize as normalize_mod
from . import render as render_mod
from . import subtitles as subtitles_mod
from .edit import ease
from .plan import Clip, Plan
from .probe import tool
from .profiles import resolve


GRAB_TIMEOUT = 120  # seconds; one frame takes a few, so this only trips on a hung ffmpeg


class FrameError(RuntimeError):
    pass


@dataclass(frozen=True)
class Active:
    video: tuple[Clip, ...]
    audio: tuple[tuple[str, Clip], ...]


def active(plan: Plan, frame: int) -> Active:
    video = tuple(c for c in plan.video.clips if c.start_frame <= frame < c.end_frame)
    audio = tuple((t.id, c) for t in plan.audio for c in t.clips if c.start_frame <= frame < c.end_frame)
    return Active(video, audio)


def _scale(clip: Clip, frame: int) -> float | None:
    if not clip.motion:
        return None
    m = clip.motion
    on = frame - clip.start_frame
    return m.scale_from + (m.scale_to - m.scale_from) * ease(on / max(clip.duration_frames - 1, 1), m.easing)


def _rel(path: Path, ws: Path) -> str:
    return "./" + path.relative_to(ws).as_posix() if path.is_relative_to(ws) else str(path)


def describe(plan: Plan, frame: int) -> str:
    fps = plan.meta.fps
    act = active(plan, frame)
    lines = [f"frame {frame} of {plan.meta.duration_frames} ({frame / fps:.3f}s at {fps} fps), "
             f"{plan.meta.width}x{plan.meta.height}, plan '{plan.meta.title}'"]
    if len(act.video) == 2:
        a, b = act.video
        k = frame - b.start_frame
        lines.append(f"video: crossfade '{a.id}' -> '{b.id}', {k / a.crossfade_frames:.0%} of the way to "
                     f"'{b.id}' (crossfade frame {k} of {a.crossfade_frames})")
    else:
        lines.append("video:")
    for c in act.video:
        on = frame - c.start_frame
        src_t = "still" if c.is_still else f"source {(c.source_in_frame + on) / fps:.3f}s"
        zoom = _scale(c, frame)
        lines.append(f"  - '{c.id}' {_rel(c.src, plan.workspace)} [{c.start_frame}-{c.end_frame}), "
                     f"clip frame {on} of {c.duration_frames}, {src_t}, fit {c.fit}"
                     + (f", zoom {zoom:.4f} ({c.motion.easing} {c.motion.scale_from}->{c.motion.scale_to})"
                        if zoom is not None else ""))
    lines.append("audio:" if act.audio else "audio: none at this frame")
    for tid, c in act.audio:
        on = frame - c.start_frame
        lines.append(f"  - '{c.id}' on '{tid}' {_rel(c.src, plan.workspace)}, source "
                     f"{(c.source_in_frame + on) / fps:.3f}s, volume {c.volume:g}")
    lines.append(_caption_line(plan, frame))
    lines.append("(no OCR or vision model: this text is read from scene_plan.json; look at the image for content)")
    return "\n".join(lines)


def _caption_line(plan: Plan, frame: int) -> str:
    if plan.captions is None:
        return "captions: the plan has no caption track"
    for clip in plan.captions.clips:
        if not clip.start_frame <= frame < clip.end_frame:
            continue
        groups = subtitles_mod.pages(clip)
        words = clip.words
        current = max((i for i, w in enumerate(words) if w.start_frame <= frame), default=None)
        if current is None:
            return f"captions: '{clip.id}' is open but its first word starts at {words[0].start_frame}; nothing shown"
        page = next(g for g in groups if current in g)
        text = " ".join(words[i].text for i in page)
        w = words[current]
        spoken = "being spoken" if frame < w.end_frame else f"spoken (ended {w.end_frame}); pause"
        return (f"captions ({plan.captions.style}): '{clip.id}' page \"{text}\"; current word "
                f"'{w.text}' [{w.start_frame}-{w.end_frame}) {spoken}")
    return "captions: none on screen at this frame"


def grab_command(plan: Plan, frame: int, out: Path, subtitles: Path | None,
                 max_width: int | None = None) -> list[str]:
    spec = resolve(plan)  # the plan's own grid; software encoder, so nothing is probed
    fps = plan.meta.fps
    clips = active(plan, frame).video
    inputs = [a for c in clips for a in render_mod._video_input(c, fps)]
    parts = [render_mod._video_chain(j, c, c.duration_frames, spec, f"v{j}") for j, c in enumerate(clips)]
    base = clips[0].start_frame
    acc = "v0"
    if len(clips) == 2:
        a, b = clips
        parts.append(f"[v0][v1]xfade=transition=fade:duration={render_mod._secs(a.crossfade_frames, fps)}"
                     f":offset={render_mod._secs(b.start_frame - base, fps)}[x]")
        acc = "x"
    chain = f"[{acc}]setpts=PTS-STARTPTS+{render_mod._secs(base, fps)}/TB"
    if subtitles is not None:
        chain += f",ass=filename={subtitles_mod.filter_path(subtitles)}"
    k = frame - base
    chain += f",trim=start_frame={k}:end_frame={k + 1}"
    if max_width and spec.width > max_width:  # after the burn-in, so captions shrink with the picture
        chain += f",scale={max_width}:{2 * round(spec.height * max_width / spec.width / 2)}:flags=lanczos"
    parts.append(chain + ",format=rgb24[vout]")
    return [tool("ffmpeg"), "-hide_banner", "-nostats", "-loglevel", "error", "-y", *inputs,
            "-filter_complex", ";".join(parts), "-map", "[vout]", "-frames:v", "1", "-update", "1", str(out)]


def grab(plan: Plan, frame: int, out: Path | None = None, *, normalize: bool = True,
         captions: bool = True, max_width: int | None = None) -> Path:
    """Frame `frame` as a PNG; max_width downscales it (keeping the aspect) for an agent's context."""
    if not 0 <= frame < plan.meta.duration_frames:
        raise FrameError(f"frameNumber {frame} is outside the plan (0..{plan.meta.duration_frames - 1})")
    ws = plan.workspace
    out = out or ws / "cache" / "inspect" / f"frame_{frame:06d}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if normalize:
        try:
            plan = normalize_mod.apply(plan, normalize_mod.normalize_plan(plan, fix=True))
        except normalize_mod.NormalizeError as exc:
            raise FrameError(str(exc)) from exc
    subtitles = None
    if captions and plan.captions is not None:
        subtitles = subtitles_mod.write_ass(plan.captions, ws / "cache" / "inspect" / "captions.ass",
                                            plan.meta.width, plan.meta.height, plan.meta.fps, lambda f: f)
    # ffmpeg writes as it goes: render beside `out` and swap it in whole, so a reader (or a cache check)
    # never sees a half-written PNG and a failed grab leaves nothing behind
    part = out.with_name(out.stem + ".part.png")
    part.unlink(missing_ok=True)
    try:
        r = subprocess.run(grab_command(plan, frame, part, subtitles, max_width), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=GRAB_TIMEOUT)
    except subprocess.TimeoutExpired as exc:  # run() has killed it; view.frame holds a lock around this
        part.unlink(missing_ok=True)
        raise FrameError(f"frame grab timed out after {GRAB_TIMEOUT} s") from exc
    if r.returncode != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        tail = "\n".join(r.stderr.strip().splitlines()[-15:])
        raise FrameError(f"frame grab failed (ffmpeg exit {r.returncode}):\n{tail}")
    os.replace(part, out)
    return out
