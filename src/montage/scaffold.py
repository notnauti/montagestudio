"""Workspaces: `montage init <dir>` and the brief that `montage plan --topic` synthesizes.

A topic-only plan needs media already in <workspace>/assets/ (dropped in by hand, or fetched by
`montage scrape` for the visuals a brief lists). Their descriptions come from the file names; the
brief is written to brief.json so they can be improved by hand and re-planned with `montage plan <brief>`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .pipeline import Pipeline
from . import probe as probe_mod
from .probe import ProbeError

BRIEF_NAME = "brief.json"
MUSIC_VOLUME = 0.12


class WorkspaceError(ValueError):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "untitled"


def _write_new(path: Path, data: dict) -> None:
    if path.exists():
        raise WorkspaceError(f"{path} already exists; edit it and run `montage plan \"{path}\"`, "
                             f"or pick another --workspace")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def init_workspace(directory: str | Path, pipeline: Pipeline) -> Path:
    """Create <dir>/assets/ and a template brief.json. Never overwrites an existing brief."""
    ws = Path(directory)
    (ws / "assets").mkdir(parents=True, exist_ok=True)
    brief = {
        "title": "Untitled",
        "brief": pipeline.brief_text("<your topic>"),
        "targetSeconds": pipeline.default_target_seconds,
        "recipe": pipeline.name,
        "assets": [{"src": "./assets/replace-me.mp4",
                    "description": "what is on screen; the script writer picks visuals by this text"}],
    }
    path = ws / BRIEF_NAME
    _write_new(path, brief)
    return path


def _describe(path: Path) -> str:
    return re.sub(r"[_\-.]+", " ", path.stem).strip() or path.name


def synthesize_brief(workspace: str | Path, pipeline: Pipeline, topic: str, target_seconds: int | None,
                     probe=None) -> tuple[Path, list[str]]:
    """Build brief.json for `plan --topic` from the pipeline and whatever media sits in assets/.

    Visual assets are every file with a video stream (stills included); a single audio-only
    file becomes the music bed. Returns (brief path, notes for the user).
    """
    ws = Path(workspace)
    probe = probe or probe_mod.probe
    if not topic.strip():
        raise WorkspaceError("--topic must not be empty")
    if target_seconds is not None and not 10 <= target_seconds <= 600:
        raise WorkspaceError(f"--target-duration must be 10-600 seconds, got {target_seconds}")
    if (ws / BRIEF_NAME).exists():
        raise WorkspaceError(f"{ws / BRIEF_NAME} already exists; run `montage plan \"{ws / BRIEF_NAME}\"` "
                             f"to plan from it, or pick another --workspace")
    assets_dir = ws / "assets"
    files = sorted(p for p in assets_dir.iterdir() if p.is_file()) if assets_dir.is_dir() else []
    visuals, audio_only, notes = [], [], []
    for p in files:
        try:
            info = probe(p)
        except ProbeError:
            notes.append(f"skipped {p.name}: not media ffprobe can read")
            continue
        if info.has_video:
            visuals.append({"src": f"./assets/{p.name}", "description": _describe(p)})
        elif info.has_audio:
            audio_only.append(p)
    if not visuals:
        raise WorkspaceError(
            f"no video or image files in {assets_dir}. Either drop footage and stills there and re-run, or "
            f"source them (guide Stage 2): `montage init \"{ws}\" --pipeline {pipeline.name}`, list the visuals "
            f"you want in brief.json (src + description), `montage scrape \"{ws}\"`, then `montage plan`")
    brief = {
        "title": topic.strip(),
        "brief": pipeline.brief_text(topic.strip()),
        "targetSeconds": target_seconds or pipeline.default_target_seconds,
        "recipe": pipeline.name,
        "assets": visuals,
    }
    if len(audio_only) == 1:
        brief["music"] = {"src": f"./assets/{audio_only[0].name}", "volume": MUSIC_VOLUME}
    elif audio_only:
        notes.append(f"{len(audio_only)} audio-only files in assets/; no music bed chosen "
                     f"(add \"music\" to brief.json by hand)")
    notes.append("asset descriptions come from file names; edit them in brief.json for better visual picks")
    path = ws / BRIEF_NAME
    _write_new(path, brief)
    return path, notes
