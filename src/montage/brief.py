"""brief.json: what the user hands the planner.

{
  "title": "How GPS corrects for relativity",
  "brief": "One paragraph: what the video says and for whom.",
  "targetSeconds": 60,
  "recipe": "explainer",          # optional; a pipeline name from <project>/pipelines/*.json
  "assets": [
    {"src": "./assets/orbit.mp4", "description": "satellite orbiting Earth, slow pan"},
    {"src": "./assets/clock.png", "description": "diagram of two clocks drifting apart",
     "query": "atomic clock"}     # optional; search words for `montage scrape` (default: description)
  ],

An asset whose file does not exist yet is "wanted": `montage scrape <workspace>` fetches it
(scrape.py). Planning needs every file present.
  "music": {"src": "./assets/bed.wav", "volume": 0.12},   # optional
  "voice": "en_US-lessac-medium"                           # optional
}

Rejected, not ignored, like the scene plan: unknown fields are errors.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import pipeline as pipeline_mod
from .pipeline import DEFAULT_PIPELINE
from .plan import STILL_EXTS
from .probe import MediaInfo, ProbeError, probe as default_probe

DEFAULT_VOICE = "en_US-lessac-medium"


class BriefError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class Asset:
    src: str  # as written in the brief; becomes the scene_plan src
    path: Path
    description: str
    is_still: bool
    duration: float | None  # seconds; None for stills


@dataclass(frozen=True)
class Brief:
    title: str
    text: str
    target_seconds: int
    recipe: str
    assets: tuple[Asset, ...]
    music_src: str | None
    music_path: Path | None
    music_volume: float
    music_duration: float | None
    voice: str
    workspace: Path


def load_brief(path: str | Path, probe=default_probe) -> Brief:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BriefError([f"{path}: {exc}"]) from exc
    return parse_brief(raw, path.parent, probe=probe)


def _media(src: object, where: str, workspace: Path, errs: list[str], probe) -> tuple[Path, MediaInfo] | None:
    if not isinstance(src, str) or not src:
        errs.append(f"{where}.src: must be a non-empty string")
        return None
    p = (workspace / src).resolve()
    if not p.is_relative_to((workspace / "assets").resolve()):
        errs.append(f"{where}.src: must be inside assets/, got {src!r}")
        return None
    if not p.is_file():
        hint = (f" (run `montage scrape \"{workspace}\"` to source it from Commons / NASA / Archive.org, "
                f"or put the file there)") if where.startswith("brief.assets") else ""
        errs.append(f"{where}.src: file not found: {src!r}{hint}")
        return None
    try:
        return p, probe(p)
    except ProbeError as exc:
        errs.append(f"{where}.src: unreadable media {src!r}: {exc}")
        return None


def parse_brief(raw: object, workspace: str | Path, probe=default_probe) -> Brief:
    workspace = Path(workspace).resolve()
    errs: list[str] = []
    if not isinstance(raw, dict):
        raise BriefError(["brief: must be a JSON object"])
    allowed = {"title", "brief", "targetSeconds", "recipe", "assets", "music", "voice"}
    for k in raw:
        if k not in allowed:
            errs.append(f"brief: unsupported field '{k}'")
    for k in ("title", "brief", "targetSeconds", "assets"):
        if k not in raw:
            errs.append(f"brief: missing '{k}'")

    title, text = raw.get("title"), raw.get("brief")
    if "title" in raw and (not isinstance(title, str) or not title.strip()):
        errs.append("brief.title: must be a non-empty string")
    if "brief" in raw and (not isinstance(text, str) or not text.strip()):
        errs.append("brief.brief: must be a non-empty string")
    target = raw.get("targetSeconds")
    if "targetSeconds" in raw and not (isinstance(target, int) and not isinstance(target, bool) and 10 <= target <= 600):
        errs.append(f"brief.targetSeconds: must be an integer 10-600, got {target!r}")
    recipe = raw.get("recipe", DEFAULT_PIPELINE)
    known = pipeline_mod.available()
    if recipe not in known:
        errs.append(f"brief.recipe: unknown pipeline {recipe!r}; available: {known}")
    voice = raw.get("voice", DEFAULT_VOICE)
    if not isinstance(voice, str) or not voice:
        errs.append("brief.voice: must be a Piper voice name")

    assets: list[Asset] = []
    seen: set[str] = set()
    raw_assets = raw.get("assets")
    if "assets" in raw and not (isinstance(raw_assets, list) and raw_assets):
        errs.append("brief.assets: must be a non-empty array")
        raw_assets = []
    for i, a in enumerate(raw_assets or []):
        where = f"brief.assets[{i}]"
        if not isinstance(a, dict):
            errs.append(f"{where}: must be an object")
            continue
        for k in a:
            if k not in ("src", "description", "query"):
                errs.append(f"{where}: unsupported field '{k}'")
        if "query" in a and (not isinstance(a["query"], str) or not a["query"].strip()):
            errs.append(f"{where}.query: must be a non-empty string (search words for `montage scrape`)")
        desc = a.get("description")
        if not isinstance(desc, str) or not desc.strip():
            errs.append(f"{where}.description: must be a non-empty string (the script writer picks visuals by it)")
        src = a.get("src")
        if isinstance(src, str) and src in seen:
            errs.append(f"{where}.src: listed twice: {src!r}")
        media = _media(src, where, workspace, errs, probe)
        if media is None or not isinstance(desc, str):
            continue
        p, info = media
        if not info.has_video:
            errs.append(f"{where}.src: {src!r} has no video stream")
            continue
        seen.add(src)
        still = p.suffix.lower() in STILL_EXTS
        assets.append(Asset(src, p, desc.strip(), still, None if still else info.duration))

    music_src = music_path = music_duration = None
    music_volume = 0.0
    if "music" in raw:
        m = raw["music"]
        if not isinstance(m, dict):
            errs.append("brief.music: must be an object")
        else:
            for k in m:
                if k not in ("src", "volume"):
                    errs.append(f"brief.music: unsupported field '{k}'")
            vol = m.get("volume", 0.12)
            if not (isinstance(vol, (int, float)) and not isinstance(vol, bool) and 0 < vol <= 1):
                errs.append(f"brief.music.volume: must be a number in (0, 1], got {vol!r}")
            media = _media(m.get("src"), "brief.music", workspace, errs, probe)
            if media:
                music_path, info = media
                if not info.has_audio:
                    errs.append("brief.music.src: has no audio stream")
                music_src, music_volume, music_duration = m["src"], float(vol), info.duration

    if errs:
        raise BriefError(errs)
    return Brief(title.strip(), text.strip(), target, recipe, tuple(assets),
                 music_src, music_path, music_volume, music_duration, voice, workspace)
