"""Tier 2 normalization (guide §2: "Normalizes sample rates, aspect ratios, and pixel formats via
FFmpeg probes"): probe every asset a plan references, report mismatches, and fix the fixable ones
into <workspace>/cache/normalized/. The render then reads the cached copies; scene_plan.json and
assets/ are never modified.

Checked per role (a file used both as picture and as sound is checked for both):
  audio  sample rate != 48 kHz                         -> fix: resample to 48 kHz 24-bit wav
  video  variable frame rate (r_frame_rate != avg)     -> fix: constant rate at the plan's fps
         pixel format other than 8-bit 4:2:0          -> fix: yuv420p
         non-square pixels (SAR != 1:1)               -> fix: rescale to square pixels
  still  pixel format above 8 bits / unusual          -> fix: 8-bit RGB png
         non-square pixels                            -> fix: rescale to square pixels
  both   aspect ratio differs from meta.resolution     -> report only: the clip's `fit` decides
                                                         crop (cover) or pad (contain) at render
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .plan import Clip, Plan
from .probe import ProbeError, tool

SAMPLE_RATE = 48000
VIDEO_PIX_FMTS = {"yuv420p", "yuvj420p"}
STILL_PIX_FMTS = {"rgb24", "rgba", "bgr24", "bgra", "pal8", "gray", "ya8", "monob", "monow",
                  "yuv420p", "yuv422p", "yuv444p", "yuvj420p", "yuvj422p", "yuvj444p"}
ASPECT_TOLERANCE = 0.01
CACHE_DIR = Path("cache") / "normalized"
VERSION = "1"  # bump when a fix recipe changes, so stale cache entries are not reused


class NormalizeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Issue:
    kind: str     # sample_rate | vfr | pix_fmt | sar | aspect
    detail: str
    fixable: bool


@dataclass(frozen=True)
class AssetReport:
    src: Path
    role: str                  # video | still | audio
    summary: str               # what the probe saw
    issues: tuple[Issue, ...]
    normalized: Path | None    # cached copy the render reads; None = the original is used

    @property
    def needs_fix(self) -> bool:
        return any(i.fixable for i in self.issues)


def probe_streams(path: Path) -> list[dict]:
    result = subprocess.run(
        [tool("ffprobe"), "-v", "error",
         "-show_entries", "stream=codec_type,codec_name,width,height,pix_fmt,sample_aspect_ratio,"
                          "r_frame_rate,avg_frame_rate,sample_rate,channels:stream_disposition=attached_pic",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise ProbeError(result.stderr.strip() or f"ffprobe exited {result.returncode}")
    return json.loads(result.stdout or "{}").get("streams", [])


def _ratio(text: str | None) -> Fraction | None:
    try:
        num, _, den = (text or "").replace(":", "/").partition("/")
        f = Fraction(int(num), int(den or 1))
        return f if f > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def _role(clip: Clip, track_type: str) -> str:
    if track_type == "audio":
        return "audio"
    return "still" if clip.is_still else "video"


def _first(streams: list[dict], kind: str) -> dict | None:
    return next((s for s in streams if s.get("codec_type") == kind
                 and not (s.get("disposition") or {}).get("attached_pic")), None)


def _aspect_issue(w: int, h: int, sar: Fraction, plan_w: int, plan_h: int) -> Issue | None:
    src = Fraction(w, h) * sar
    target = Fraction(plan_w, plan_h)
    if abs(float(src / target) - 1) <= ASPECT_TOLERANCE:
        return None
    if src > target:
        how = f"cover crops {1 - float(target / src):.0%} of the width; contain pads top and bottom"
    else:
        how = f"cover crops {1 - float(src / target):.0%} of the height; contain pads left and right"
    return Issue("aspect", f"source aspect {float(src):.3f} vs plan {float(target):.3f} "
                           f"({plan_w}x{plan_h}): {how}", False)


def inspect(path: Path, role: str, plan: Plan, streams: list[dict]) -> tuple[str, list[Issue]]:
    """Pure: probe result -> (summary, issues). No ffmpeg calls, so it is unit-testable."""
    issues: list[Issue] = []
    if role == "audio":
        a = _first(streams, "audio")
        if a is None:
            raise NormalizeError(f"{path.name}: no audio stream")
        rate = int(a.get("sample_rate") or 0)
        if rate != SAMPLE_RATE:
            issues.append(Issue("sample_rate", f"{rate} Hz, mix runs at {SAMPLE_RATE}", True))
        return f"{a.get('codec_name')} {rate} Hz {a.get('channels')}ch", issues

    v = _first(streams, "video")
    if v is None:
        raise NormalizeError(f"{path.name}: no video stream")
    w, h, pix = int(v.get("width") or 0), int(v.get("height") or 0), v.get("pix_fmt") or "unknown"
    sar = _ratio(v.get("sample_aspect_ratio")) or Fraction(1)
    summary = f"{v.get('codec_name')} {w}x{h} {pix}"
    if sar != 1:
        issues.append(Issue("sar", f"non-square pixels (SAR {sar.numerator}:{sar.denominator})", True))
    if role == "still":
        if pix not in STILL_PIX_FMTS:
            issues.append(Issue("pix_fmt", f"{pix} is not 8-bit; converted to 8-bit RGB", True))
    else:
        r, avg = _ratio(v.get("r_frame_rate")), _ratio(v.get("avg_frame_rate"))
        summary += f" {float(avg or r or 0):.3g} fps"
        if r and avg and r != avg:
            issues.append(Issue("vfr", f"variable frame rate (nominal {float(r):g}, average {float(avg):.3f} fps); "
                                       f"made constant at {plan.meta.fps} fps", True))
        if pix not in VIDEO_PIX_FMTS:
            issues.append(Issue("pix_fmt", f"{pix}; converted to yuv420p", True))
    if w and h:
        aspect = _aspect_issue(w, h, sar, plan.meta.width, plan.meta.height)
        if aspect:
            issues.append(aspect)
    return summary, issues


def _fix_command(src: Path, role: str, issues: list[Issue], fps: int, out: Path) -> list[str]:
    kinds = {i.kind for i in issues if i.fixable}
    base = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src)]
    if role == "audio":
        return base + ["-map", "0:a:0", "-vn", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s24le", str(out)]
    vf = []
    if "vfr" in kinds:
        vf.append(f"fps={fps}")
    if "sar" in kinds:
        vf.append("scale=trunc(iw*sar/2)*2:trunc(ih/2)*2")
    vf.append("setsar=1")
    if role == "still":
        return base + ["-map", "0:v:0", "-frames:v", "1", "-vf", ",".join(vf + ["format=rgb24"]), str(out)]
    vf.append("format=yuv420p")
    return base + ["-map", "0:v:0", "-an", "-sn", "-vf", ",".join(vf),
                   "-c:v", "libx264", "-preset", "fast", "-crf", "12", "-pix_fmt", "yuv420p", str(out)]


_EXT = {"audio": ".wav", "video": ".mp4", "still": ".png"}


def _cache_path(plan: Plan, src: Path, role: str, issues: list[Issue]) -> Path:
    st = src.stat()
    key = "|".join([VERSION, str(src), str(st.st_size), str(st.st_mtime_ns), role, str(plan.meta.fps),
                    ",".join(sorted(i.kind for i in issues if i.fixable))])
    digest = hashlib.sha1(key.encode()).hexdigest()[:10]
    return plan.workspace / CACHE_DIR / f"{src.stem}-{role}-{digest}{_EXT[role]}"


def normalize_plan(plan: Plan, fix: bool = False, probe=probe_streams, run=subprocess.run) -> list[AssetReport]:
    """Probe every (asset, role) the plan uses. With fix=True, write cached copies for the fixable ones."""
    seen: dict[tuple[Path, str], AssetReport] = {}
    probed: dict[Path, list[dict]] = {}
    for track_type, track in [("video", plan.video)] + [("audio", t) for t in plan.audio]:
        for clip in track.clips:
            role = _role(clip, track_type)
            if (clip.src, role) in seen:
                continue
            if clip.src not in probed:
                try:
                    probed[clip.src] = probe(clip.src)
                except ProbeError as exc:
                    raise NormalizeError(f"{clip.src.name}: {exc}") from exc
            summary, issues = inspect(clip.src, role, plan, probed[clip.src])
            normalized = None
            if fix and any(i.fixable for i in issues):
                normalized = _cache_path(plan, clip.src, role, issues)
                if not normalized.is_file():
                    normalized.parent.mkdir(parents=True, exist_ok=True)
                    tmp = normalized.with_name(f"{normalized.stem}.tmp{normalized.suffix}")
                    r = run(_fix_command(clip.src, role, issues, plan.meta.fps, tmp),
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
                    if r.returncode != 0:
                        tmp.unlink(missing_ok=True)
                        tail = "\n".join((r.stderr or "").strip().splitlines()[-10:])
                        raise NormalizeError(f"normalizing {clip.src.name} ({role}) failed:\n{tail}")
                    tmp.replace(normalized)
            seen[(clip.src, role)] = AssetReport(clip.src, role, summary, tuple(issues), normalized)
    return list(seen.values())


def apply(plan: Plan, reports: list[AssetReport]) -> Plan:
    """A copy of the plan whose clips read the normalized files. The plan on disk is untouched."""
    swap = {(r.src, r.role): r.normalized for r in reports if r.normalized}
    if not swap:
        return plan

    def track(t, track_type):
        clips = tuple(dataclasses.replace(c, src=swap[(c.src, _role(c, track_type))])
                      if (c.src, _role(c, track_type)) in swap else c for c in t.clips)
        return dataclasses.replace(t, clips=clips)

    return dataclasses.replace(plan, video=track(plan.video, "video"),
                               audio=tuple(track(t, "audio") for t in plan.audio))
