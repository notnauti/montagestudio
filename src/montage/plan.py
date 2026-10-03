"""Slice 1 scene_plan.json validator.

The contract lives in `data/(C) scene-plan-contract.md` ("Slice 1 fields").
Every rule there is enforced here; anything not listed is rejected, not ignored.
All errors are collected and reported together.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import probe as probe_mod

STILL_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
FITS = {"cover", "contain"}
EASINGS = {"linear", "easeInOutCubic"}
MAX_SCALE = 10.0  # zoompan's ceiling

CLIP_FIELDS = {"id", "src", "startFrame", "durationFrames", "sourceInFrame"}
AUDIO_FIELDS = CLIP_FIELDS | {"volume"}
VIDEO_FIELDS = CLIP_FIELDS | {"fit", "transition", "motion"}
CAPTION_FIELDS = {"id", "startFrame", "durationFrames", "words"}
WORD_FIELDS = {"text", "startFrame", "endFrame"}
CAPTION_STYLES = {"highlight", "pop"}
TRACK_TYPES = ("video", "audio", "caption")


class PlanError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class Meta:
    title: str
    width: int
    height: int
    fps: int
    duration_frames: int


@dataclass(frozen=True)
class Motion:
    scale_from: float
    scale_to: float
    easing: str


@dataclass(frozen=True)
class Clip:
    id: str
    src: Path  # absolute
    start_frame: int
    duration_frames: int
    source_in_frame: int = 0
    volume: float = 1.0
    fit: str = "cover"
    crossfade_frames: int = 0  # crossfade into the next clip; 0 = hard cut
    motion: Motion | None = None
    is_still: bool = False

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.duration_frames


@dataclass(frozen=True)
class Track:
    id: str
    type: str
    clips: tuple[Clip, ...]


@dataclass(frozen=True)
class CaptionWord:
    text: str
    start_frame: int  # timeline frames, like every startFrame in the plan
    end_frame: int    # exclusive


@dataclass(frozen=True)
class CaptionClip:
    id: str
    start_frame: int
    duration_frames: int
    words: tuple[CaptionWord, ...]

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.duration_frames


@dataclass(frozen=True)
class CaptionTrack:
    id: str
    style: str
    clips: tuple[CaptionClip, ...]


@dataclass(frozen=True)
class Plan:
    meta: Meta
    video: Track
    audio: tuple[Track, ...]
    workspace: Path
    captions: CaptionTrack | None = None


Prober = Callable[[Path], probe_mod.MediaInfo]


def load_plan(path: str | Path, probe: Prober = probe_mod.probe) -> Plan:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError([f"{path}: {exc}"]) from exc
    return parse_plan(raw, path.parent, probe=probe)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fields(obj: dict, allowed: set[str], required: set[str], where: str, errs: list[str]) -> None:
    for key in obj:
        if key not in allowed:
            errs.append(f"{where}: unsupported field '{key}'")
    for key in sorted(required - obj.keys()):
        errs.append(f"{where}: missing '{key}'")


def _int_field(obj: dict, key: str, where: str, errs: list[str], *, minimum: int, default: int | None = None) -> int | None:
    if key not in obj:
        return default
    v = obj[key]
    if not _is_int(v) or v < minimum:
        errs.append(f"{where}.{key}: must be an integer >= {minimum}, got {v!r}")
        return None
    return v


def _parse_meta(raw: Any, errs: list[str]) -> Meta | None:
    where = "meta"
    if not isinstance(raw, dict):
        errs.append(f"{where}: must be an object")
        return None
    before = len(errs)
    _fields(raw, {"title", "resolution", "fps", "durationFrames"},
            {"title", "resolution", "fps", "durationFrames"}, where, errs)

    title = raw.get("title")
    if "title" in raw and (not isinstance(title, str) or not title.strip()):
        errs.append(f"{where}.title: must be a non-empty string")

    res = raw.get("resolution")
    if "resolution" in raw:
        if not (isinstance(res, list) and len(res) == 2 and all(_is_int(x) and x > 0 for x in res)):
            errs.append(f"{where}.resolution: must be [width, height] positive integers, got {res!r}")
        elif res[0] % 2 or res[1] % 2:
            errs.append(f"{where}.resolution: both dimensions must be even (yuv420p), got {res!r}")

    fps = raw.get("fps")
    if "fps" in raw and not (_is_int(fps) and 0 < fps <= 120):
        errs.append(f"{where}.fps: must be an integer 1-120 (no fractional fps in slice 1), got {fps!r}")

    _int_field(raw, "durationFrames", where, errs, minimum=1)

    if len(errs) > before:
        return None
    return Meta(title.strip(), res[0], res[1], fps, raw["durationFrames"])


def _parse_clip(raw: Any, track_type: str, where: str, meta: Meta | None,
                workspace: Path, errs: list[str]) -> Clip | None:
    if not isinstance(raw, dict):
        errs.append(f"{where}: must be an object")
        return None
    before = len(errs)
    allowed = AUDIO_FIELDS if track_type == "audio" else VIDEO_FIELDS
    _fields(raw, allowed, {"id", "src", "startFrame", "durationFrames"}, where, errs)

    cid = raw.get("id")
    if "id" in raw and (not isinstance(cid, str) or not cid):
        errs.append(f"{where}.id: must be a non-empty string")

    src_path = None
    is_still = False
    src = raw.get("src")
    if "src" in raw:
        if not isinstance(src, str) or not src:
            errs.append(f"{where}.src: must be a non-empty string")
        else:
            src_path = (workspace / src).resolve()
            assets = (workspace / "assets").resolve()
            is_still = src_path.suffix.lower() in STILL_EXTS
            if not src_path.is_relative_to(assets):
                errs.append(f"{where}.src: must be inside assets/, got {src!r}")
            elif not src_path.is_file():
                errs.append(f"{where}.src: file not found: {src!r}")
            if is_still and track_type == "audio":
                errs.append(f"{where}.src: audio clip cannot use a still image ({src!r})")

    start = _int_field(raw, "startFrame", where, errs, minimum=0)
    dur = _int_field(raw, "durationFrames", where, errs, minimum=1)
    src_in = _int_field(raw, "sourceInFrame", where, errs, minimum=0, default=0)
    if start is not None and dur is not None and meta and start + dur > meta.duration_frames:
        errs.append(f"{where}: ends at frame {start + dur}, past meta.durationFrames {meta.duration_frames}")

    volume = raw.get("volume", 1.0)
    if not (_is_num(volume) and 0 <= volume <= 1):
        errs.append(f"{where}.volume: must be a number 0-1, got {volume!r}")

    fit = raw.get("fit", "cover")
    if fit not in FITS:
        errs.append(f"{where}.fit: must be one of {sorted(FITS)}, got {fit!r}")

    crossfade = 0
    if "transition" in raw:
        t = raw["transition"]
        tw = f"{where}.transition"
        if not isinstance(t, dict):
            errs.append(f"{tw}: must be an object")
        else:
            _fields(t, {"type", "durationFrames"}, {"type", "durationFrames"}, tw, errs)
            if "type" in t and t["type"] != "crossfade":
                errs.append(f"{tw}.type: only 'crossfade' in slice 1, got {t['type']!r}")
            crossfade = _int_field(t, "durationFrames", tw, errs, minimum=1) or 0

    motion = None
    if "motion" in raw:
        m = raw["motion"]
        mw = f"{where}.motion"
        if not isinstance(m, dict):
            errs.append(f"{mw}: must be an object")
        else:
            _fields(m, {"scale", "easing"}, {"scale", "easing"}, mw, errs)
            scale = m.get("scale")
            if "scale" in m and not (isinstance(scale, list) and len(scale) == 2
                                     and all(_is_num(s) and 1.0 <= s <= MAX_SCALE for s in scale)):
                errs.append(f"{mw}.scale: must be [from, to] with both between 1.0 and {MAX_SCALE}, got {scale!r}")
            if "easing" in m and m["easing"] not in EASINGS:
                errs.append(f"{mw}.easing: must be one of {sorted(EASINGS)}, got {m['easing']!r}")
            if len(errs) == before:
                motion = Motion(float(scale[0]), float(scale[1]), m["easing"])

    if len(errs) > before:
        return None
    return Clip(cid, src_path, start, dur, src_in, float(volume), fit, crossfade, motion, is_still)


def _parse_caption_clip(raw: Any, where: str, meta: Meta | None, errs: list[str]) -> CaptionClip | None:
    if not isinstance(raw, dict):
        errs.append(f"{where}: must be an object")
        return None
    before = len(errs)
    _fields(raw, CAPTION_FIELDS, CAPTION_FIELDS, where, errs)
    cid = raw.get("id")
    if "id" in raw and (not isinstance(cid, str) or not cid):
        errs.append(f"{where}.id: must be a non-empty string")
    start = _int_field(raw, "startFrame", where, errs, minimum=0)
    dur = _int_field(raw, "durationFrames", where, errs, minimum=1)
    end = start + dur if start is not None and dur is not None else None
    if end is not None and meta and end > meta.duration_frames:
        errs.append(f"{where}: ends at frame {end}, past meta.durationFrames {meta.duration_frames}")

    words: list[CaptionWord] = []
    wraw = raw.get("words")
    if "words" in raw and not (isinstance(wraw, list) and wraw):
        errs.append(f"{where}.words: must be a non-empty array")
        wraw = []
    prev_end = None
    for wi, w in enumerate(wraw or []):
        ww = f"{where}.words[{wi}]"
        if not isinstance(w, dict):
            errs.append(f"{ww}: must be an object")
            continue
        wb = len(errs)
        _fields(w, WORD_FIELDS, WORD_FIELDS, ww, errs)
        text = w.get("text")
        if "text" in w and (not isinstance(text, str) or not text.strip()
                            or any(ord(ch) < 32 for ch in text)):
            errs.append(f"{ww}.text: must be a non-empty single-line string, got {text!r}")
        ws = _int_field(w, "startFrame", ww, errs, minimum=0)
        we = _int_field(w, "endFrame", ww, errs, minimum=1)
        if len(errs) > wb:
            continue
        if ws >= we:
            errs.append(f"{ww}: endFrame {we} must be after startFrame {ws}")
        if start is not None and end is not None and not (start <= ws and we <= end):
            errs.append(f"{ww}: frames {ws}-{we} fall outside the clip ({start}-{end})")
        if prev_end is not None and ws < prev_end:
            errs.append(f"{ww}: starts at {ws}, before the previous word ends ({prev_end}); "
                        f"words must be in order and must not overlap")
        prev_end = we
        words.append(CaptionWord(text.strip(), ws, we))
    if len(errs) > before:
        return None
    return CaptionClip(cid, start, dur, tuple(words))


def _parse_caption_track(traw: dict, tw: str, meta: Meta | None, clip_ids: dict[str, str],
                         errs: list[str]) -> CaptionTrack | None:
    style = traw.get("style", "highlight")
    before = len(errs)
    if style not in CAPTION_STYLES:
        errs.append(f"{tw}.style: must be one of {sorted(CAPTION_STYLES)}, got {style!r}")
    clips: list[CaptionClip] = []
    for ci, c in enumerate(traw["clips"]):
        cw = f"{tw}.clips[{ci}]"
        clip = _parse_caption_clip(c, cw, meta, errs)
        if clip is None:
            continue
        if clip.id in clip_ids:
            errs.append(f"{cw}.id: duplicate clip id '{clip.id}' (also {clip_ids[clip.id]})")
        else:
            clip_ids[clip.id] = cw
        clips.append(clip)
    for prev, cur in zip(clips, clips[1:]):
        if cur.start_frame < prev.end_frame:
            errs.append(f"{tw}: '{cur.id}' starts at {cur.start_frame}, before '{prev.id}' ends "
                        f"({prev.end_frame}); caption clips must be sorted and must not overlap")
    if len(errs) > before:
        return None
    return CaptionTrack(traw["id"], style, tuple(clips))


def _check_track_timing(track: Track, where: str, meta: Meta | None, errs: list[str]) -> None:
    clips = track.clips
    for i in range(1, len(clips)):
        if clips[i].start_frame < clips[i - 1].start_frame:
            errs.append(f"{where}: clips must be sorted by startFrame ('{clips[i].id}' comes after '{clips[i - 1].id}')")
            return

    if track.type == "audio":
        for prev, cur in zip(clips, clips[1:]):
            if cur.start_frame < prev.end_frame:
                errs.append(f"{where}: '{cur.id}' starts at {cur.start_frame}, overlapping '{prev.id}' "
                            f"(ends {prev.end_frame}); put overlapping audio on another track")
        return

    if clips[0].start_frame != 0:
        errs.append(f"{where}: first clip '{clips[0].id}' must start at frame 0, got {clips[0].start_frame}")
    for prev, cur in zip(clips, clips[1:]):
        expected = prev.end_frame - prev.crossfade_frames
        if cur.start_frame != expected:
            how = f"minus crossfade {prev.crossfade_frames}" if prev.crossfade_frames else "no crossfade"
            errs.append(f"{where}: '{cur.id}' starts at {cur.start_frame}; expected {expected} "
                        f"('{prev.id}' ends at {prev.end_frame}, {how})")
        if prev.crossfade_frames and not (prev.crossfade_frames < prev.duration_frames
                                          and prev.crossfade_frames < cur.duration_frames):
            errs.append(f"{where}: crossfade from '{prev.id}' ({prev.crossfade_frames} frames) must be shorter "
                        f"than both clips it joins")
    last = clips[-1]
    if last.crossfade_frames:
        errs.append(f"{where}: last clip '{last.id}' cannot have a transition (nothing follows it)")
    if meta and last.end_frame != meta.duration_frames:
        errs.append(f"{where}: video track ends at frame {last.end_frame}; must end at "
                    f"meta.durationFrames {meta.duration_frames}")


def _check_media(clip: Clip, track_type: str, where: str, fps: int, probe: Prober,
                 cache: dict[Path, Any], errs: list[str]) -> None:
    if clip.src not in cache:
        try:
            cache[clip.src] = probe(clip.src)
        except probe_mod.ProbeError as exc:
            cache[clip.src] = exc
    info = cache[clip.src]
    name = clip.src.name
    if isinstance(info, Exception):
        errs.append(f"{where}.src: unreadable media '{name}': {info}")
        return
    if track_type == "video" and not info.has_video:
        errs.append(f"{where}.src: '{name}' has no video stream")
        return
    if track_type == "audio" and not info.has_audio:
        errs.append(f"{where}.src: '{name}' has no audio stream")
        return
    if clip.is_still:
        return
    needed = (clip.source_in_frame + clip.duration_frames) / fps
    if info.duration is None or info.duration < needed - 0.5 / fps:
        have = "unknown length" if info.duration is None else f"{info.duration:.3f}s"
        errs.append(f"{where}: source '{name}' is {have}; clip needs {needed:.3f}s "
                    f"(sourceInFrame {clip.source_in_frame} + durationFrames {clip.duration_frames} at {fps} fps)")


def parse_plan(raw: Any, workspace: str | Path, probe: Prober = probe_mod.probe) -> Plan:
    workspace = Path(workspace).resolve()
    errs: list[str] = []
    if not isinstance(raw, dict):
        raise PlanError(["plan: must be a JSON object"])
    _fields(raw, {"$schema", "meta", "tracks"}, {"meta", "tracks"}, "plan", errs)
    meta = _parse_meta(raw.get("meta"), errs) if "meta" in raw else None

    tracks_raw = raw.get("tracks")
    tracks: list[tuple[str, Track]] = []
    clip_ids: dict[str, str] = {}
    if "tracks" in raw and not (isinstance(tracks_raw, list) and tracks_raw):
        errs.append("tracks: must be a non-empty array")
        tracks_raw = []
    track_ids: set[str] = set()
    captions: CaptionTrack | None = None
    for ti, traw in enumerate(tracks_raw or []):
        tw = f"tracks[{ti}]"
        if not isinstance(traw, dict):
            errs.append(f"{tw}: must be an object")
            continue
        before = len(errs)
        ttype = traw.get("type")
        allowed = {"id", "type", "clips", "style"} if ttype == "caption" else {"id", "type", "clips"}
        _fields(traw, allowed, {"id", "type", "clips"}, tw, errs)
        tid, craw = traw.get("id"), traw.get("clips")
        if "id" in traw:
            if not isinstance(tid, str) or not tid:
                errs.append(f"{tw}.id: must be a non-empty string")
            elif tid in track_ids:
                errs.append(f"{tw}.id: duplicate track id '{tid}'")
            else:
                track_ids.add(tid)
        if "type" in traw and ttype not in TRACK_TYPES:
            errs.append(f"{tw}.type: must be one of {list(TRACK_TYPES)}, got {ttype!r}")
            continue
        if "clips" in traw and not (isinstance(craw, list) and craw):
            errs.append(f"{tw}.clips: must be a non-empty array")
            continue
        if len(errs) > before:
            continue
        if ttype == "caption":
            if any(isinstance(t, dict) and t.get("type") == "caption" for t in tracks_raw[:ti]):
                errs.append(f"{tw}: at most one caption track")
                continue
            captions = _parse_caption_track(traw, tw, meta, clip_ids, errs)
            continue

        clips: list[Clip] = []
        for ci, c in enumerate(craw):
            cw = f"{tw}.clips[{ci}]"
            clip = _parse_clip(c, ttype, cw, meta, workspace, errs)
            if clip is None:
                continue
            if clip.id in clip_ids:
                errs.append(f"{cw}.id: duplicate clip id '{clip.id}' (also {clip_ids[clip.id]})")
            else:
                clip_ids[clip.id] = cw
            clips.append(clip)
        if len(clips) == len(craw):
            track = Track(tid, ttype, tuple(clips))
            _check_track_timing(track, tw, meta, errs)
            tracks.append((tw, track))

    videos = [t for t in (tracks_raw or []) if isinstance(t, dict) and t.get("type") == "video"]
    audios = [t for t in (tracks_raw or []) if isinstance(t, dict) and t.get("type") == "audio"]
    if isinstance(tracks_raw, list) and tracks_raw:
        if len(videos) != 1:
            errs.append(f"tracks: slice 1 needs exactly one video track, got {len(videos)}")
        if not audios:
            errs.append("tracks: slice 1 needs at least one audio track")

    # Media checks only once the structure is sound: probing a malformed plan just adds noise.
    if not errs and meta:
        cache: dict[Path, Any] = {}
        for tw, track in tracks:
            for ci, clip in enumerate(track.clips):
                _check_media(clip, track.type, f"{tw}.clips[{ci}]", meta.fps, probe, cache, errs)

    if errs:
        raise PlanError(errs)
    video = next(t for _, t in tracks if t.type == "video")
    audio = tuple(t for _, t in tracks if t.type == "audio")
    return Plan(meta, video, audio, workspace, captions)
