"""`montage plan`: brief.json -> script.json -> narration wavs -> scene_plan.json + captions.json.

script.json is kept in the workspace and reused on the next run, so the script can be
edited by hand and re-planned without calling a model again (`--rewrite` forces a new one).
The brief's `recipe` names the pipeline (pipelines/<name>.json) whose prompt and timing apply.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import captions as captions_mod
from .align import ALIGNED
from .brief import Brief, load_brief
from .captions import write_captions
from .compose import compose, write_plan
from .narrate import narrate
from .pipeline import Pipeline, load_pipeline
from .plan import Plan, parse_plan
from .script import Script, parse_script, write_script


@dataclass(frozen=True)
class PlanResult:
    brief: Brief
    script: Script
    plan: Plan
    plan_path: Path
    script_reused: bool
    pipeline: Pipeline


def get_script(brief: Brief, writer: str, rewrite: bool, log=print) -> tuple[Script, bool]:
    path = brief.workspace / "script.json"
    if path.is_file() and not rewrite:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return parse_script(raw, brief, writer=raw.get("writer", "script.json")), True
    script = write_script(brief, writer, log=log)
    path.write_text(json.dumps(script.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return script, False


def run_plan(brief_path: str | Path, writer: str = "auto", rewrite: bool = False, log=print) -> PlanResult:
    brief = load_brief(brief_path)
    pipeline = load_pipeline(brief.recipe)
    script, reused = get_script(brief, writer, rewrite, log=log)
    log(f"script: {len(script.beats)} beats by {script.writer}" + (" (reused script.json)" if reused else ""))
    fewest, most = pipeline.beat_range(brief.target_seconds)
    if not fewest <= len(script.beats) <= most:
        log(f"  note: the {pipeline.name} pipeline asks for {fewest}-{most} beats at {brief.target_seconds}s; "
            f"this script has {len(script.beats)}")
    narration = narrate(script, brief.voice, brief.workspace)
    raw_plan = compose(brief, script, narration, pipeline.recipe)
    plan_path = write_plan(raw_plan, brief.workspace)
    voice_clips = next(t["clips"] for t in raw_plan["tracks"] if t["id"] == "narration")
    _, doc = captions_mod.build(voice_clips, narration, raw_plan["meta"]["fps"],
                                raw_plan["meta"]["durationFrames"], pipeline.recipe.caption_style)
    write_captions(doc, brief.workspace)
    estimated = [b["beat"] for b in doc["beats"] if b["words"] and b["method"] != ALIGNED]
    if estimated:
        log(f"  note: word timings for beat(s) {estimated} are estimated (spread by word length), "
            f"not aligned; captions may drift")
    plan = parse_plan(raw_plan, brief.workspace)  # the contract gates the planner's own output too
    return PlanResult(brief, script, plan, plan_path, reused, pipeline)
