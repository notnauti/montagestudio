"""Timeline edits on scene_plan.json: the MCP server's write path (guide §5).

There is no canvas yet, so the scene plan (the contract) is what gets edited. Every edit is a
pure function over the raw plan dict; `PlanFile.apply` then re-validates the whole result with
the same validator `montage validate` uses, and only a valid plan is written -- atomically
(temp file + os.replace), after copying the previous version to
<workspace>/cache/plan-history/. A rejected edit leaves scene_plan.json byte-for-byte unchanged.

Edit semantics (all in timeline frames, like the contract):
  split_clip      one clip -> two at a frame; timing of everything else unchanged. The second
                  part's sourceInFrame advances so the picture/sound is continuous; a zoom keeps
                  its values at both cut points (exact for linear easing).
  trim_boundary   video track (gapless, single layer): a *roll* edit -- the cut moves and the
                  neighbour absorbs it, so nothing after it shifts. Audio / caption clips: the
                  edge moves in place (content stays where it was), checked against neighbours.
  apply_easing    `motion.scale` only (the contract's one motion property): two keyframes at the
                  clip's first and last frame, curve linear | easeInOutCubic.
  replace_broll   a video clip's src; timing, fit, motion and transition are kept.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import url2pathname

from . import probe as probe_mod
from .plan import EASINGS, STILL_EXTS, Plan, PlanError, parse_plan

HISTORY_DIR = ("cache", "plan-history")
# cubic-bezier control points that are exactly one of the contract's easings (inferred: the
# usual CSS / easings.net definitions)
BEZIER_ALIASES = {(0.0, 0.0, 1.0, 1.0): "linear", (0.65, 0.0, 0.35, 1.0): "easeInOutCubic"}
UNSUPPORTED_PROPERTIES = {"position", "opacity"}


class EditError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass
class EditResult:
    plan: dict
    changes: list[str]
    notes: list[str] = field(default_factory=list)
    created: list[Path] = field(default_factory=list)  # files the edit made; removed if it is rejected


@dataclass(frozen=True)
class Located:
    track: dict
    index: int
    clip: dict

    @property
    def kind(self) -> str:
        return self.track.get("type")


# ── helpers ───────────────────────────────────────────────────────────────────

def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _need_int(v: Any, name: str) -> int:
    if not _is_int(v):
        raise EditError([f"{name}: must be an integer, got {v!r}"])
    return v


def find_clip(raw: dict, clip_id: str) -> Located:
    for track in raw.get("tracks") or []:
        for i, clip in enumerate(track.get("clips") or []):
            if isinstance(clip, dict) and clip.get("id") == clip_id:
                return Located(track, i, clip)
    ids = [c.get("id") for t in raw.get("tracks") or [] for c in t.get("clips") or [] if isinstance(c, dict)]
    raise EditError([f"no clip '{clip_id}' in the plan; clip ids: {', '.join(map(str, ids))}"])


def find_track(raw: dict, track_id: str) -> dict:
    for track in raw.get("tracks") or []:
        if track.get("id") == track_id:
            return track
    ids = [t.get("id") for t in raw.get("tracks") or []]
    raise EditError([f"no track '{track_id}' in the plan; tracks: {', '.join(map(str, ids))}"])


def _all_ids(raw: dict) -> set[str]:
    return {c.get("id") for t in raw.get("tracks") or [] for c in t.get("clips") or [] if isinstance(c, dict)}


def _new_id(raw: dict, base: str) -> str:
    ids = _all_ids(raw)
    for n in range(2, 1000):
        cand = f"{base}_b" if n == 2 else f"{base}_b{n}"
        if cand not in ids:
            return cand
    raise EditError([f"could not find a free id for the second part of '{base}'"])


def _is_still(clip: dict) -> bool:
    return isinstance(clip.get("src"), str) and Path(clip["src"]).suffix.lower() in STILL_EXTS


def _end(clip: dict) -> int:
    return clip["startFrame"] + clip["durationFrames"]


def _xfade(clip: dict) -> int:
    t = clip.get("transition")
    return t.get("durationFrames", 0) if isinstance(t, dict) else 0


def _set_source_in(clip: dict, value: int) -> None:
    if value or "sourceInFrame" in clip:
        clip["sourceInFrame"] = value


def ease(t: float, easing: str) -> float:
    """The renderer's easing curves (render._ease), in Python."""
    t = min(max(t, 0.0), 1.0)
    if easing == "easeInOutCubic":
        return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2
    return t


def scale_at(motion: dict, on: int, frames: int) -> float:
    """Zoom on the clip's `on`-th frame, exactly as render.py's zoompan expression computes it."""
    s0, s1 = motion["scale"]
    return s0 + (s1 - s0) * ease(on / max(frames - 1, 1), motion.get("easing", "linear"))


# ── edits ─────────────────────────────────────────────────────────────────────

def split_clip(raw: dict, track_id: str, clip_id: str, split_frame: int, new_id: str | None = None) -> EditResult:
    split_frame = _need_int(split_frame, "splitFrame")
    loc = find_clip(raw, clip_id)
    if loc.track.get("id") != track_id:
        raise EditError([f"clip '{clip_id}' is on track '{loc.track.get('id')}', not '{track_id}'"])
    c = loc.clip
    start, end = c["startFrame"], _end(c)
    if not start < split_frame < end:
        raise EditError([f"splitFrame {split_frame} must be strictly inside '{clip_id}' "
                         f"(frames {start + 1}..{end - 1}; the clip covers {start}-{end})"])
    if new_id is None:
        new_id = _new_id(raw, clip_id)
    elif new_id in _all_ids(raw):
        raise EditError([f"newClipId '{new_id}' is already used in the plan"])
    k = split_frame - start
    first, second = copy.deepcopy(c), copy.deepcopy(c)
    first["durationFrames"] = k
    first.pop("transition", None)  # the outgoing crossfade belongs to the second part
    second.update(id=new_id, startFrame=split_frame, durationFrames=end - split_frame)
    notes = []

    if loc.kind == "caption":
        before = [w for w in c["words"] if w["startFrame"] < split_frame]
        after = [w for w in c["words"] if w["startFrame"] >= split_frame]
        if not before or not after:
            raise EditError([f"splitting '{clip_id}' at {split_frame} leaves one part with no words; "
                             f"its words start at {', '.join(str(w['startFrame']) for w in c['words'])}"])
        if before[-1]["endFrame"] > split_frame:
            before = before[:-1] + [dict(before[-1], endFrame=split_frame)]
            notes.append(f"word '{before[-1]['text']}' ended after the split; it now ends at {split_frame}")
        first["words"], second["words"] = before, after
    elif "src" in c and not _is_still(c):
        _set_source_in(second, c.get("sourceInFrame", 0) + k)

    if "motion" in c:
        m, dur = c["motion"], c["durationFrames"]
        first["motion"]["scale"] = [m["scale"][0], round(scale_at(m, k - 1, dur), 6)]
        second["motion"]["scale"] = [round(scale_at(m, k, dur), 6), m["scale"][1]]
        if m.get("easing") != "linear":
            notes.append(f"zoom values at both cut points are kept; each part now eases on its own "
                         f"({m.get('easing')}), so the curve inside each part differs from the original")
    clips = loc.track["clips"]
    clips[loc.index:loc.index + 1] = [first, second]
    changes = [f"split '{clip_id}' ({start}-{end}) at {split_frame}: '{clip_id}' {start}-{split_frame}, "
               f"'{new_id}' {split_frame}-{end}"]
    if "sourceInFrame" in second and loc.kind != "caption":
        changes.append(f"'{new_id}' sourceInFrame {second['sourceInFrame']} (continues the source)")
    return EditResult(raw, changes, notes)


def trim_boundary(raw: dict, clip_id: str, edge: str, delta: int) -> EditResult:
    delta = _need_int(delta, "deltaFrames")
    if edge not in ("start", "end"):
        raise EditError([f"edge: must be 'start' or 'end', got {edge!r}"])
    if delta == 0:
        raise EditError(["deltaFrames: 0 changes nothing"])
    loc = find_clip(raw, clip_id)
    c, clips, i = loc.clip, loc.track["clips"], loc.index
    errs: list[str] = []
    notes: list[str] = []
    old = {id(x): (x["startFrame"], _end(x)) for x in clips}

    if loc.kind == "video":
        if edge == "end":
            if i == len(clips) - 1:
                raise EditError([f"'{clip_id}' is the last video clip; it must end at meta.durationFrames. "
                                 f"Trim the start of '{clip_id}' instead (moves its cut with the previous clip)"])
            a, b = c, clips[i + 1]
        else:
            if i == 0:
                raise EditError([f"'{clip_id}' is the first video clip; it must start at frame 0. "
                                 f"Trim the end of '{clip_id}' instead (moves its cut with the next clip)"])
            a, b = clips[i - 1], c
        # roll edit: the cut between a and b moves by delta; b's content stays where it was
        a["durationFrames"] += delta
        b["startFrame"] += delta
        b["durationFrames"] -= delta
        if not _is_still(b):
            new_in = b.get("sourceInFrame", 0) + delta
            if new_in < 0:
                errs.append(f"'{b['id']}' would need sourceInFrame {new_in}: its source has no frames before "
                            f"its current in-point; the cut can move at most {b.get('sourceInFrame', 0)} frames earlier")
            _set_source_in(b, new_in)
        xf = _xfade(a)
        for x in (a, b):
            if x["durationFrames"] <= xf:
                errs.append(f"collision: '{x['id']}' would be {x['durationFrames']} frames, not longer than "
                            f"the {xf}-frame crossfade between '{a['id']}' and '{b['id']}'")
        for x in (a, b):
            if x.get("motion"):
                notes.append(f"'{x['id']}' keeps its zoom {x['motion']['scale']} across its new length")
        changes = [f"moved the cut between '{a['id']}' and '{b['id']}' by {delta:+d} "
                   f"(roll edit; nothing else on the video track moves)"]
        changes += [f"'{x['id']}' {old[id(x)][0]}-{old[id(x)][1]} -> {x['startFrame']}-{_end(x)}" for x in (a, b)]
        if not _is_still(b):
            changes.append(f"'{b['id']}' sourceInFrame -> {b.get('sourceInFrame', 0)} (its picture stays in place)")
    else:
        if edge == "start":
            c["startFrame"] += delta
            c["durationFrames"] -= delta
            if loc.kind == "audio":
                new_in = c.get("sourceInFrame", 0) + delta
                if new_in < 0:
                    errs.append(f"'{clip_id}' would need sourceInFrame {new_in}; the source has no audio before "
                                f"its current in-point (at most {c.get('sourceInFrame', 0)} frames earlier)")
                _set_source_in(c, new_in)
            if c["startFrame"] < 0:
                errs.append(f"'{clip_id}' would start at frame {c['startFrame']}, before 0")
            if i > 0 and c["startFrame"] < _end(clips[i - 1]):
                errs.append(f"collision: '{clip_id}' would start at {c['startFrame']}, inside "
                            f"'{clips[i - 1]['id']}' (ends {_end(clips[i - 1])})")
        else:
            c["durationFrames"] += delta
            if i + 1 < len(clips) and _end(c) > clips[i + 1]["startFrame"]:
                errs.append(f"collision: '{clip_id}' would end at {_end(c)}, inside "
                            f"'{clips[i + 1]['id']}' (starts {clips[i + 1]['startFrame']})")
        if c["durationFrames"] <= 0:
            errs.append(f"'{clip_id}' would be {c['durationFrames']} frames long")
        if loc.kind == "caption":
            outside = [w["text"] for w in c["words"]
                       if w["startFrame"] < c["startFrame"] or w["endFrame"] > _end(c)]
            if outside:
                errs.append(f"caption words would fall outside '{clip_id}': {', '.join(outside[:6])}")
        changes = [f"'{clip_id}' {edge} {delta:+d}: {old[id(c)][0]}-{old[id(c)][1]} -> {c['startFrame']}-{_end(c)}"]
    if errs:
        raise EditError(errs)
    return EditResult(raw, changes, notes)


def _keyframe_values(keyframes: Any, duration: int) -> tuple[float, float]:
    if not (isinstance(keyframes, list) and len(keyframes) == 2):
        raise EditError([
            "keyframes: the contract's motion is one segment across the whole clip, so exactly two keyframes: "
            "[from, to] or [{frame: 0, value}, {frame: <last frame>, value}]. For a zoom over part of a clip, "
            "split it first (timeline_split_clip) and ease the part you want"])
    values, last = [], duration - 1
    for n, kf in enumerate(keyframes):
        if isinstance(kf, dict):
            extra = set(kf) - {"frame", "value"}
            if extra or "value" not in kf:
                raise EditError([f"keyframes[{n}]: needs 'value' (and optional 'frame'), got {sorted(kf)}"])
            want = (0,) if n == 0 else (last, duration)
            if "frame" in kf and kf["frame"] not in want:
                raise EditError([f"keyframes[{n}].frame: {kf['frame']!r} -- keyframes sit on the clip's first "
                                 f"frame (0) and last frame ({last}, clip-relative); intermediate keyframes are not "
                                 f"in the contract. Split the clip first to zoom over part of it"])
            kf = kf["value"]
        if not isinstance(kf, (int, float)) or isinstance(kf, bool):
            raise EditError([f"keyframes[{n}]: value must be a number, got {kf!r}"])
        values.append(float(kf))
    return values[0], values[1]


def _curve(curve: Any) -> str:
    if isinstance(curve, str) and curve in EASINGS:
        return curve
    if isinstance(curve, list) and len(curve) == 4 and all(isinstance(x, (int, float)) for x in curve):
        name = BEZIER_ALIASES.get(tuple(round(float(x), 4) for x in curve))
        if name:
            return name
    raise EditError([f"curve: the contract supports {sorted(EASINGS)} (or their cubic-bezier points "
                     f"{[list(k) for k in BEZIER_ALIASES]}), got {curve!r}. Arbitrary bezier curves are not "
                     f"in the scene_plan contract"])


def apply_easing(raw: dict, clip_id: str, prop: str, keyframes: Any, curve: Any) -> EditResult:
    if prop in UNSUPPORTED_PROPERTIES:
        raise EditError([f"property '{prop}' is not in the scene_plan contract: motion supports 'scale' only "
                         f"(position / opacity are explicitly out of slice 1; see data/(C) scene-plan-contract.md). "
                         f"Not widened silently"])
    if prop != "scale":
        raise EditError([f"property: must be 'scale', got {prop!r}"])
    loc = find_clip(raw, clip_id)
    if loc.kind != "video":
        raise EditError([f"'{clip_id}' is on a {loc.kind} track; motion applies to video clips only"])
    v0, v1 = _keyframe_values(keyframes, loc.clip["durationFrames"])
    name = _curve(curve)
    before = loc.clip.get("motion")
    loc.clip["motion"] = {"scale": [v0, v1], "easing": name}
    return EditResult(raw, [f"'{clip_id}' motion {before or 'none'} -> {loc.clip['motion']}"])


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_source(uri: str, workspace: Path) -> Path:
    """sourceUri -> a local file. file:// URIs, absolute paths, or paths relative to the workspace."""
    if not isinstance(uri, str) or not uri.strip():
        raise EditError(["sourceUri: must be a non-empty string"])
    parsed = urlparse(uri)
    if parsed.scheme in ("http", "https"):
        raise EditError([f"sourceUri: remote URLs are not fetched here ({uri}); a download would bypass "
                         f"`montage scrape`'s license gate. Put the file in assets/ (or run montage scrape) "
                         f"and pass its path"])
    if parsed.scheme == "file":
        # file:///C:/x.mp4 (Windows) or file:///home/x.mp4; file://host/share/x.mp4 is a UNC path
        local = parsed.path if parsed.netloc in ("", "localhost") else f"//{parsed.netloc}{parsed.path}"
        path = Path(url2pathname(local))
    elif parsed.scheme and len(parsed.scheme) > 1:
        raise EditError([f"sourceUri: unsupported scheme '{parsed.scheme}:' (use a file path or file:// URI)"])
    else:
        path = Path(uri)
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()
    if not path.is_file():
        raise EditError([f"sourceUri: file not found: {path}"])
    return path


def _into_assets(path: Path, workspace: Path) -> tuple[str, Path | None]:
    """-> (workspace-relative src, file created by the copy or None)."""
    assets = (workspace / "assets").resolve()
    if path.is_relative_to(assets):
        return "./" + path.relative_to(workspace.resolve()).as_posix(), None
    dest = assets / "broll" / path.name
    if dest.exists():
        if dest.is_file() and _sha(dest) == _sha(path):
            return "./" + dest.relative_to(workspace.resolve()).as_posix(), None
        dest = dest.with_name(f"{path.stem}-{_sha(path)[:8]}{path.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    created = None if dest.exists() else dest
    if created:
        shutil.copy2(path, dest)
    return "./" + dest.relative_to(workspace.resolve()).as_posix(), created


def replace_broll(raw: dict, workspace: Path, clip_id: str, source_uri: str, source_in: int | None = None,
                  probe: Callable = probe_mod.probe) -> EditResult:
    loc = find_clip(raw, clip_id)
    if loc.kind != "video":
        raise EditError([f"'{clip_id}' is on a {loc.kind} track; asset_replace_broll swaps video clips only"])
    if source_in is not None:
        source_in = _need_int(source_in, "sourceInFrame")
        if source_in < 0:
            raise EditError([f"sourceInFrame: must be >= 0, got {source_in}"])
    path = resolve_source(source_uri, workspace)
    c = loc.clip
    old_src = c.get("src")
    src, created = _into_assets(path, workspace)
    notes = [f"copied into the workspace as {src} (the contract needs files inside assets/)"] if created else []
    c["src"] = src
    if _is_still(c):
        if c.pop("sourceInFrame", None):
            notes.append("the new source is a still; sourceInFrame dropped")
    else:
        want = source_in if source_in is not None else c.get("sourceInFrame", 0)
        fps = (raw.get("meta") or {}).get("fps")
        if source_in is None and want and _is_int(fps) and fps > 0:
            try:
                info = probe(path)
            except probe_mod.ProbeError:
                info = None
            have = math.floor((info.duration or 0) * fps) if info else None
            if have is not None and want + c["durationFrames"] > have >= c["durationFrames"]:
                notes.append(f"the new source is {have} frames; in-point {want} would run past its end, "
                             f"so it plays from frame 0")
                want = 0
        if want:
            c["sourceInFrame"] = want
        else:
            c.pop("sourceInFrame", None)  # 0 is the default
    kept = [k for k in ("fit", "motion", "transition") if k in c]
    changes = [f"'{clip_id}' src {old_src} -> {src} (kept timing {c['startFrame']}-{_end(c)}"
               + (f", {', '.join(kept)}" if kept else "") + ")"]
    return EditResult(raw, changes, notes, [created] if created else [])


# ── the file ──────────────────────────────────────────────────────────────────

class PlanFile:
    """scene_plan.json on disk: read, validate, and write only valid plans, atomically."""

    def __init__(self, path: str | Path, probe: Callable = probe_mod.probe):
        self.path = Path(path).resolve()
        self.workspace = self.path.parent
        self.probe = probe

    def read(self) -> tuple[bytes, dict]:
        try:
            data = self.path.read_bytes()
            return data, json.loads(data.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EditError([f"{self.path.name}: {exc}"]) from exc

    def load(self) -> Plan:
        _, raw = self.read()
        return parse_plan(raw, self.workspace, probe=self.probe)

    def apply(self, edit: Callable[[dict], EditResult], dry_run: bool = False) -> tuple[EditResult, Plan]:
        data, raw = self.read()
        result: EditResult | None = None
        keep = False
        try:
            try:
                result = edit(copy.deepcopy(raw))
            except (KeyError, TypeError, AttributeError, IndexError) as exc:
                # the edits assume the structure the validator guarantees
                raise EditError([f"scene_plan.json is malformed as it stands ({type(exc).__name__}: {exc}); "
                                 f"fix it first -- plan_summary / montage validate list the errors"]) from exc
            try:
                plan = parse_plan(result.plan, self.workspace, probe=self.probe)
            except PlanError as exc:
                raise EditError(["edit rejected by the scene_plan validator; scene_plan.json unchanged:"]
                                + [f"  {e}" for e in exc.errors]) from exc
            if not dry_run:
                if self.path.read_bytes() != data:
                    raise EditError(["scene_plan.json changed on disk while this edit was being checked; "
                                     "nothing written -- re-read it and retry"])
                self._write(data, result.plan)
                keep = True
        finally:
            if not keep:  # rejected or dry run: leave no copied asset behind
                for f in (result.created if result else []):
                    f.unlink(missing_ok=True)
        return result, plan

    def _write(self, previous: bytes, plan: dict) -> None:
        hist = self.workspace.joinpath(*HISTORY_DIR)
        hist.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (hist / f"scene_plan-{stamp}.json").write_bytes(previous)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
