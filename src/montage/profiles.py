"""Render profiles (`montage render --profile <name>`) and H.264 hardware-encoder selection.

A profile overrides the output frame size, frame rate and codec at render time. It never
touches scene_plan.json: the plan's `meta.resolution` / `meta.fps` stay the authoring grid that
every frame number is written in, and the renderer maps that grid onto the profile's.

Size: the profile fixes the short edge (720 / 1080 / 2160); the long edge follows the plan's
aspect ratio, so a vertical 1080x1920 plan at 720p30-h264 renders 720x1280, not a squashed 1280x720.

Hardware H.264 (h264_nvenc / h264_qsv / h264_amf) is used only when ffmpeg lists the encoder
AND a one-frame test encode at the output size succeeds; otherwise libx264, with the reason.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from .plan import Plan
from .probe import tool

SAMPLE_RATE = 48000
HW_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")
ENCODER_MODES = ("software", "auto", *HW_ENCODERS)

# Option names checked against `ffmpeg -h encoder=<name>` on ffmpeg 8.1 (extracted); the quality
# numbers are chosen to sit near libx264 crf 18 (inferred, not measured).
_H264_ARGS = {
    "libx264": ("-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"),
    "h264_nvenc": ("-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                   "-pix_fmt", "yuv420p"),
    "h264_qsv": ("-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "20", "-pix_fmt", "nv12"),
    "h264_amf": ("-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "20",
                 "-pix_fmt", "yuv420p"),
}
_AAC = ("-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE))
_PRORES = ("-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le")  # 3 = HQ
_PCM = ("-c:a", "pcm_s24le", "-ar", str(SAMPLE_RATE))


@dataclass(frozen=True)
class Profile:
    name: str
    short_edge: int
    fps: int
    codec: str  # "h264" | "prores"
    ext: str
    description: str


PROFILES = {p.name: p for p in (
    Profile("1080p30-h264", 1080, 30, "h264", ".mp4", "1080p 30 fps H.264 + AAC, MP4 (the slice 1 output)"),
    Profile("720p30-h264", 720, 30, "h264", ".mp4", "720p 30 fps H.264 + AAC, MP4 (previews, upload drafts)"),
    Profile("4k60-prores", 2160, 60, "prores", ".mov", "2160p 60 fps ProRes 422 HQ + 24-bit PCM, MOV (mastering)"),
)}
DEFAULT_PROFILE = "1080p30-h264"


@dataclass(frozen=True)
class OutputSpec:
    """Everything the renderer and QC need to know about the file being produced."""
    profile: str          # profile name, or "native" (plan's own size / fps)
    width: int
    height: int
    fps: int
    codec: str            # ffprobe codec_name of the video stream
    audio_codec: str      # ffprobe codec_name of the audio stream
    pix_fmt: str          # filter-graph pixel format; also what QC expects to read back
    encoder: str          # the ffmpeg encoder actually used
    ext: str
    video_args: tuple[str, ...]
    audio_args: tuple[str, ...]
    notes: tuple[str, ...] = field(default=())
    supersample: int = 0  # zoompan supersampling factor; 0 = the renderer's default for this size
    still_once: bool = False  # decode and fit each still once and loop it (preview), not re-read per frame


def _even(x: float) -> int:
    return max(2, 2 * round(x / 2))


def output_size(plan_w: int, plan_h: int, short_edge: int) -> tuple[int, int]:
    k = short_edge / min(plan_w, plan_h)
    return _even(plan_w * k), _even(plan_h * k)


# ── hardware encoder detection ────────────────────────────────────────────────

_listed: set[str] | None = None
_probe_cache: dict[tuple[str, int, int], str | None] = {}


def listed_encoders(run=subprocess.run) -> set[str]:
    global _listed
    if _listed is None:
        r = run([tool("ffmpeg"), "-hide_banner", "-encoders"], capture_output=True, text=True,
                encoding="utf-8", errors="replace")
        names = set()
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
                names.add(parts[1])
        _listed = names
    return _listed


def probe_encode(encoder: str, width: int, height: int, fps: int, run=subprocess.run) -> str | None:
    """One-frame test encode with the real options. None = works; otherwise ffmpeg's last error line."""
    key = (encoder, width, height)
    if key not in _probe_cache:
        cmd = [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", f"color=black:s={width}x{height}:r={fps}", "-frames:v", "1",
               *_H264_ARGS[encoder], "-f", "null", "-"]
        r = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        lines = [ln for ln in (r.stderr or "").strip().splitlines() if ln.strip()]
        _probe_cache[key] = None if r.returncode == 0 else (lines[0] if lines else f"exit {r.returncode}")
    return _probe_cache[key]


def reset_encoder_cache() -> None:
    global _listed
    _listed = None
    _probe_cache.clear()


def select_h264_encoder(mode: str, width: int, height: int, fps: int, run=subprocess.run) -> tuple[str, list[str]]:
    """-> (encoder, notes). `software` never probes; `auto` tries NVENC, QSV, AMF in that order."""
    if mode not in ENCODER_MODES:
        raise ValueError(f"encoder must be one of {ENCODER_MODES}, got {mode!r}")
    if mode == "software":
        return "libx264", []
    candidates = HW_ENCODERS if mode == "auto" else (mode,)
    listed = listed_encoders(run)
    reasons = []
    for enc in candidates:
        if enc not in listed:
            reasons.append(f"{enc} not in `ffmpeg -encoders`")
            continue
        err = probe_encode(enc, width, height, fps, run)
        if err is None:
            return enc, [f"hardware encoder {enc} passed a 1-frame test encode at {width}x{height}"]
        reasons.append(f"{enc} listed but its 1-frame test encode failed ({err})")
    return "libx264", [f"falling back to libx264: {'; '.join(reasons)}"]


# ── spec ──────────────────────────────────────────────────────────────────────

PREVIEW_PROFILE = "preview-480p"
PREVIEW_SHORT_EDGE = 480
# Speed over quality: ultrafast + crf 30 (inferred: plenty to judge picture, pacing and captions).
_PREVIEW_H264 = ("-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p")
_PREVIEW_AAC = ("-c:a", "aac", "-b:a", "128k", "-ar", str(SAMPLE_RATE))


def preview_spec(plan: Plan) -> OutputSpec:
    """The animatic preview (`montage preview`): 480p short edge at the plan's own fps, libx264
    ultrafast, no zoom supersampling, each still decoded once (image2 -loop re-reads and re-decodes
    the file for every frame: 14 s vs 0.4 s for 300 frames of a 2220x1665 JPEG, measured). Not a
    --profile: a preview is for review, never delivery."""
    w, h = output_size(plan.meta.width, plan.meta.height, PREVIEW_SHORT_EDGE)
    return OutputSpec(PREVIEW_PROFILE, w, h, plan.meta.fps, "h264", "aac", "yuv420p", "libx264", ".mp4",
                      _PREVIEW_H264, _PREVIEW_AAC, supersample=1, still_once=True)


def resolve(plan: Plan, profile: str | None = None, encoder: str = "software", run=subprocess.run) -> OutputSpec:
    """Profile name (None = the plan's own size and fps, H.264) -> OutputSpec for this plan."""
    meta = plan.meta
    if profile is None:
        name, (w, h), fps, codec, ext = "native", (meta.width, meta.height), meta.fps, "h264", ".mp4"
    else:
        if profile not in PROFILES:
            raise ValueError(f"unknown profile {profile!r}; available: {sorted(PROFILES)}")
        p = PROFILES[profile]
        name, (w, h), fps, codec, ext = p.name, output_size(meta.width, meta.height, p.short_edge), p.fps, p.codec, p.ext

    if codec == "prores":
        notes = (f"hardware encoders are H.264 only; {name} uses prores_ks",) if encoder != "software" else ()
        return OutputSpec(name, w, h, fps, "prores", "pcm_s24le", "yuv422p10le", "prores_ks", ext,
                          _PRORES, _PCM, notes)
    enc, notes = select_h264_encoder(encoder, w, h, fps, run)
    return OutputSpec(name, w, h, fps, "h264", "aac", "yuv420p", enc, ext, _H264_ARGS[enc], _AAC, tuple(notes))
