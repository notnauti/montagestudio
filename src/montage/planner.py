"""`montage plan`: brief.json -> script.json -> narration wavs -> scene_plan.json.

script.json is kept in the workspace and reused on the next run, so the script can be
edited by hand and re-planned without calling a model again (`--rewrite` forces a new one).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .brief import Brief, load_brief
from .compose import compose, write_plan
from .narrate import narrate
from .plan import Plan, parse_plan
from .script import Script, parse_script, write_script


@dataclass(frozen=True)
class PlanResult:
    brief: Brief
    script: Script
    plan: Plan
    plan_path: Path
    script_reused: bool


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
    script, reused = get_script(brief, writer, rewrite, log=log)
    log(f"script: {len(script.beats)} beats by {script.writer}" + (" (reused script.json)" if reused else ""))
    narration = narrate(script, brief.voice, brief.workspace)
    raw_plan = compose(brief, script, narration)
    plan_path = write_plan(raw_plan, brief.workspace)
    plan = parse_plan(raw_plan, brief.workspace)  # the contract gates the planner's own output too
    return PlanResult(brief, script, plan, plan_path, reused)
