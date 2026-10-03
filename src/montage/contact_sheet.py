"""contact_sheet.png: the storyboard the director approves before anything renders (guide Stage 2).

One tile per visual clip: a representative frame (the middle of the stretch the clip uses; a
still is the still), fitted the way the render will fit it (cover / contain), with a label:
index, clip id, timeline position and length, then the start of the narration under it.

Narration comes from the plan's own caption track (words that start inside the clip, before its
outgoing crossfade); without one, from script.json when it has one beat per clip.

FFmpeg only: one call per tile (seek, fit, pad, drawtext), then `tile` + a header. Label text
goes through drawtext `textfile=` with `expansion=none`, so no narration character needs
escaping; the paths are escaped for the filtergraph like the caption burn-in.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from .plan import Clip, Plan
from .probe import tool
from .subtitles import escape_filter_value, filter_path

SHEET_NAME = "contact_sheet.png"
TILE_LONG_EDGE = 480
LABEL_HEIGHT = 74
HEADER_HEIGHT = 44
GAP = 8
BG = "0x111111"
LABEL_BG = "0x222222"
FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


class ContactSheetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tile:
    index: int
    clip: Clip
    frame_seconds: float | None  # source time of the representative frame; None for a still
    narration: str


def _font_option() -> str:
    """drawtext font: an explicit TTF when one exists (Windows has no default fontconfig config
    outside the ffmpeg build's own), else ask fontconfig for a sans face."""
    override = os.environ.get("MONTAGE_FONT")
    for f in ((override,) if override else ()) + FONT_CANDIDATES:
        if f and Path(f).is_file():
            return f"fontfile={escape_filter_value(Path(f).as_posix())}"
    return "font=Sans"


def _narration_by_clip(plan: Plan) -> list[str]:
    clips = plan.video.clips
    if plan.captions:
        words = [w for c in plan.captions.clips for w in c.words]
        out = []
        for c in clips:
            stop = c.end_frame - c.crossfade_frames
            out.append(" ".join(w.text for w in words if c.start_frame <= w.start_frame < stop))
        return out
    script = plan.workspace / "script.json"
    try:
        beats = json.loads(script.read_text(encoding="utf-8")).get("beats") or []
    except (OSError, json.JSONDecodeError, AttributeError):
        beats = []
    if len(beats) == len(clips):
        return [str(b.get("narration", "")) if isinstance(b, dict) else "" for b in beats]
    return [""] * len(clips)


def tiles(plan: Plan) -> list[Tile]:
    fps = plan.meta.fps
    out = []
    for i, (clip, text) in enumerate(zip(plan.video.clips, _narration_by_clip(plan))):
        t = None if clip.is_still else (clip.source_in_frame + clip.duration_frames / 2) / fps
        out.append(Tile(i + 1, clip, t, text))
    return out


def tile_size(plan: Plan) -> tuple[int, int]:
    w, h = plan.meta.width, plan.meta.height
    if w >= h:
        return TILE_LONG_EDGE, max(2, round(TILE_LONG_EDGE * h / w / 2) * 2)
    return max(2, round(TILE_LONG_EDGE * w / h / 2) * 2), TILE_LONG_EDGE


def label_text(tile: Tile, fps: int, width: int) -> str:
    c = tile.clip
    head = (f"#{tile.index}  {c.id}  @{c.start_frame / fps:.1f}s  {c.duration_frames / fps:.1f}s"
            + ("  still" if c.is_still else f"  in {tile.frame_seconds:.1f}s")
            + (f"  zoom {c.motion.scale_from:g}-{c.motion.scale_to:g}" if c.motion else ""))
    chars = max(20, int(width / 8.6))
    body = textwrap.wrap(tile.narration, chars) if tile.narration else ["(no narration)"]
    if len(body) > 2:
        body = [body[0], body[1][: chars - 3].rstrip() + "..."]
    return "\n".join([head, *body])


def _write_text(path: Path, text: str) -> None:
    # LF only: drawtext draws a CR as one more line break, which doubles every line on Windows
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _run(cmd: list[str], what: str, cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        raise ContactSheetError(f"{what} failed (ffmpeg exit {r.returncode}): {r.stderr.strip()[-600:]}")


def _tile_command(tile: Tile, label: Path, out: Path, w: int, h: int, font: str) -> list[str]:
    c = tile.clip
    fit = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}" if c.fit == "cover" else
           f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    vf = (f"{fit},setsar=1,pad={w}:{h + LABEL_HEIGHT}:0:0:color={LABEL_BG},"
          f"drawtext={font}:textfile={filter_path(label)}:expansion=none:x=8:y={h + 8}:"
          f"fontsize=15:fontcolor=white:line_spacing=7,"
          # one pixel format for every tile: a format change mid-sequence (a grayscale JPEG, say)
          # re-initialises the grid's `tile` filter and silently drops the tiles before it
          f"format=rgb24")
    seek = [] if tile.frame_seconds is None else ["-ss", f"{tile.frame_seconds:.3f}"]
    return [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *seek, "-i", str(c.src),
            "-frames:v", "1", "-update", "1", "-vf", vf, str(out)]


def make_contact_sheet(plan: Plan, out: str | Path | None = None) -> Path:
    """Render <workspace>/contact_sheet.png (or `out`) for a validated plan."""
    out = Path(out) if out else plan.workspace / SHEET_NAME
    work = plan.workspace / "cache" / "contact_sheet"
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("*"):
        old.unlink()
    w, h = tile_size(plan)
    font = _font_option()
    items = tiles(plan)
    for t in items:
        label = work / f"label_{t.index:03d}.txt"
        _write_text(label, label_text(t, plan.meta.fps, w))
        _run(_tile_command(t, label, work / f"tile_{t.index:03d}.png", w, h, font),
             f"contact sheet frame for {t.clip.id}")

    n = len(items)
    cols = min(n, 4 if n <= 12 else 5)
    rows = math.ceil(n / cols)
    header = work / "header.txt"
    _write_text(header, f"{plan.meta.title}  |  {n} visual clips, {plan.meta.duration_frames / plan.meta.fps:.1f}s"
                        f"  |  review, then: montage approve --workspace <dir>")
    vf = (f"tile={cols}x{rows}:padding={GAP}:margin={GAP}:color={BG},"
          f"pad=iw:ih+{HEADER_HEIGHT}:0:{HEADER_HEIGHT}:color={BG},"
          f"drawtext={font}:textfile={filter_path(header)}:expansion=none:x={GAP + 4}:y=14:"
          f"fontsize=18:fontcolor=white")
    out.parent.mkdir(parents=True, exist_ok=True)
    # the image2 sequence pattern is relative to `work` (cwd): a '%' anywhere in the workspace path
    # would otherwise be read as part of the pattern and the grid input would not open
    _run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-framerate", "1",
          "-i", "tile_%03d.png", "-vf", vf, "-frames:v", "1", "-update", "1", str(out.resolve())],
         "contact sheet grid", cwd=work)
    return out
