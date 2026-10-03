"""Pipeline recipes: declarative JSON files under <project>/pipelines/ (guide §3 `pipelines/`).

A recipe sets what the script writer is told (format, beat count, tone) and how the planner
times the cut (crossfade, lead-in, tail, end hold, zoom). Same strictness as the brief and
the scene plan: unknown fields are errors, not ignored.

{
  "name": "documentary",                      # must equal the file stem
  "description": "one line, shown to the script writer",
  "defaultTargetSeconds": 90,                 # used when --target-duration is not given
  "beatSeconds": [7, 12],                     # narration seconds per beat -> beat count range
  "tone": "tone and pacing instructions for the script writer",
  "briefTemplate": "A short documentary about {topic} ...",   # brief text for `plan --topic`
  "timing": {"crossfade": 18, "lead": 18, "tail": 15, "endHold": 60, "zoom": 1.04, "easing": "easeInOutCubic"},
  "captionStyle": "highlight"                 # optional: highlight | pop | none (default highlight)
}
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from .plan import CAPTION_STYLES, EASINGS, MAX_SCALE

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINES_DIR = Path(os.environ.get("MONTAGE_PIPELINES_DIR", PROJECT_ROOT / "pipelines"))
DEFAULT_PIPELINE = "explainer"

FIELDS = {"name", "description", "defaultTargetSeconds", "beatSeconds", "tone", "briefTemplate", "timing"}
OPTIONAL_FIELDS = {"captionStyle"}
TIMING_FIELDS = {"crossfade", "lead", "tail", "endHold", "zoom", "easing"}
RECIPE_CAPTION_STYLES = CAPTION_STYLES | {"none"}


class PipelineError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class Recipe:
    """Timeline constants the planner composes with (frames at `fps`)."""
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crossfade: int = 12   # 0.4 s
    lead: int = 12        # silence before each beat's narration; holds the crossfade
    tail: int = 9         # breath after each beat's narration
    end_hold: int = 36    # last frame held after the final line
    zoom: float = 1.06
    easing: str = "easeInOutCubic"
    caption_style: str = "highlight"  # scene_plan caption track style; "none" = no caption track


@dataclass(frozen=True)
class Pipeline:
    name: str
    description: str
    default_target_seconds: int
    beat_seconds: tuple[float, float]
    tone: str
    brief_template: str
    recipe: Recipe

    def beat_range(self, target_seconds: int) -> tuple[int, int]:
        """How many beats a script of `target_seconds` should have under this recipe."""
        lo_s, hi_s = self.beat_seconds
        fewest = max(1, math.ceil(target_seconds / hi_s))
        most = max(fewest, math.floor(target_seconds / lo_s))
        return fewest, most

    def brief_text(self, topic: str) -> str:
        return self.brief_template.replace("{topic}", topic)


def available(directory: Path | None = None) -> list[str]:
    d = Path(directory or PIPELINES_DIR)
    return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_pipeline(raw: object, name: str) -> Pipeline:
    where = f"pipeline '{name}'"
    if not isinstance(raw, dict):
        raise PipelineError([f"{where}: must be a JSON object"])
    errs: list[str] = []
    for k in raw:
        if k not in FIELDS | OPTIONAL_FIELDS:
            errs.append(f"{where}: unsupported field '{k}'")
    caption_style = raw.get("captionStyle", "highlight")
    if caption_style not in RECIPE_CAPTION_STYLES:
        errs.append(f"{where}.captionStyle: must be one of {sorted(RECIPE_CAPTION_STYLES)}, got {caption_style!r}")
    for k in sorted(FIELDS - raw.keys()):
        errs.append(f"{where}: missing '{k}'")
    if "name" in raw and raw["name"] != name:
        errs.append(f"{where}.name: must match the file name, got {raw['name']!r}")
    for k in ("description", "tone", "briefTemplate"):
        if k in raw and (not isinstance(raw[k], str) or not raw[k].strip()):
            errs.append(f"{where}.{k}: must be a non-empty string")
    if "briefTemplate" in raw and isinstance(raw["briefTemplate"], str) and "{topic}" not in raw["briefTemplate"]:
        errs.append(f"{where}.briefTemplate: must contain {{topic}}")
    target = raw.get("defaultTargetSeconds")
    if "defaultTargetSeconds" in raw and not (_int(target) and 10 <= target <= 600):
        errs.append(f"{where}.defaultTargetSeconds: must be an integer 10-600, got {target!r}")
    beats = raw.get("beatSeconds")
    if "beatSeconds" in raw and not (isinstance(beats, list) and len(beats) == 2 and all(_num(b) for b in beats)
                                     and 0 < beats[0] <= beats[1]):
        errs.append(f"{where}.beatSeconds: must be [min, max] seconds with 0 < min <= max, got {beats!r}")

    recipe = None
    timing = raw.get("timing")
    if "timing" in raw:
        tw = f"{where}.timing"
        if not isinstance(timing, dict):
            errs.append(f"{tw}: must be an object")
        else:
            before = len(errs)
            for k in timing:
                if k not in TIMING_FIELDS:
                    errs.append(f"{tw}: unsupported field '{k}'")
            for k in sorted(TIMING_FIELDS - timing.keys()):
                errs.append(f"{tw}: missing '{k}'")
            for k in ("crossfade", "lead", "tail", "endHold"):
                if k in timing and not (_int(timing[k]) and timing[k] >= (1 if k == "crossfade" else 0)):
                    errs.append(f"{tw}.{k}: must be a frame count (integer), got {timing[k]!r}")
            if "zoom" in timing and not (_num(timing["zoom"]) and 1.0 <= timing["zoom"] <= MAX_SCALE):
                errs.append(f"{tw}.zoom: must be a number 1.0-{MAX_SCALE}, got {timing['zoom']!r}")
            if "easing" in timing and timing["easing"] not in EASINGS:
                errs.append(f"{tw}.easing: must be one of {sorted(EASINGS)}, got {timing['easing']!r}")
            if len(errs) == before:
                if timing["crossfade"] > timing["lead"]:
                    # compose() puts each crossfade inside the next beat's lead-in, before speech
                    errs.append(f"{tw}: crossfade ({timing['crossfade']}) must not exceed lead "
                                f"({timing['lead']}), or transitions would cover narration")
                else:
                    recipe = Recipe(crossfade=timing["crossfade"], lead=timing["lead"], tail=timing["tail"],
                                    end_hold=timing["endHold"], zoom=float(timing["zoom"]), easing=timing["easing"],
                                    caption_style=caption_style)
    if errs:
        raise PipelineError(errs)
    return Pipeline(name, raw["description"].strip(), target, (float(beats[0]), float(beats[1])),
                    raw["tone"].strip(), raw["briefTemplate"].strip(), recipe)


def load_pipeline(name: str, directory: Path | None = None) -> Pipeline:
    d = Path(directory or PIPELINES_DIR)
    path = d / f"{name}.json"
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or not path.is_file():
        raise PipelineError([f"unknown pipeline {name!r}; available in {d}: {available(d)}"])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError([f"{path}: {exc}"]) from exc
    return parse_pipeline(raw, name)
