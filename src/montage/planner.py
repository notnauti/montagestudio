"""`montage plan`: brief.json -> script.json -> [script gate] -> narration wavs -> scene_plan.json + captions.json.

script.json is kept in the workspace and reused on the next run, so the script can be
edited by hand and re-planned without calling a model again (`--rewrite` forces a new one).
The brief's `recipe` names the pipeline (pipelines/<name>.json) whose prompt and timing apply.

A script a model wrote stops at the script gate (script_gate.py) before any narration: the
run prints the beat table and returns a ScriptHeld until `--approve-script` (or
`montage approve-script`) records that exact script. A model draft that fails the hard checks
is rejected outright (ScriptRejected).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import captions as captions_mod
from . import script_gate as gate
from .align import ALIGNED
from .brief import Brief, load_brief
from .captions import write_captions
from .compose import compose, write_plan
from .narrate import narrate, prune_stale
from .pipeline import Pipeline, load_pipeline
from .plan import Plan, parse_plan
from .script import Script, ScriptError, parse_script, write_script


class ScriptRejected(ValueError):
    """The script gate refused the script (or the request): exit 1, nothing narrated."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class PlanResult:
    brief: Brief
    script: Script
    plan: Plan
    plan_path: Path
    script_reused: bool
    pipeline: Pipeline


@dataclass(frozen=True)
class ScriptHeld:
    """Planning stopped at the script gate: script.json waits for approval."""
    brief: Brief
    script: Script
    pipeline: Pipeline
    report: gate.GateReport
    script_path: Path
    changed: bool  # an approval exists, but for a different version of the script


def _read_script(path: Path, brief: Brief) -> Script:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScriptRejected([f"{path}: {exc}"]) from exc
    try:
        return parse_script(raw, brief, writer=raw.get("writer", "script.json") if isinstance(raw, dict) else "")
    except ScriptError as exc:
        raise ScriptRejected([str(exc)]) from exc


def get_script(brief: Brief, writer: str, rewrite: bool, log=print) -> tuple[Script, bool]:
    path = brief.workspace / "script.json"
    if path.is_file() and not rewrite:
        return _read_script(path, brief), True
    script = write_script(brief, writer, log=log)
    path.write_text(json.dumps(script.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    gate.record_draft(brief.workspace, script)  # a fresh draft: any earlier approval no longer applies
    return script, False


def _rejection(report: gate.GateReport) -> ScriptRejected:
    return ScriptRejected([f"script gate: the model's draft in script.json fails: {p}" for p in report.problems]
                          + ["edit script.json (an edited script gets these as warnings) or re-run with --rewrite"])


def run_plan(brief_path: str | Path, writer: str = "auto", rewrite: bool = False, log=print,
             approve_script: bool = False, script_gate: bool = True) -> PlanResult | ScriptHeld:
    brief = load_brief(brief_path)
    pipeline = load_pipeline(brief.recipe)
    if approve_script and rewrite:
        raise ScriptRejected(["--approve-script approves the script.json you reviewed; with --rewrite there "
                              "would be a new, unreviewed draft. Run --rewrite first, then --approve-script"])
    if approve_script and not (brief.workspace / "script.json").is_file():
        # without this, the model would write a draft and it would be approved unseen in the same run
        raise ScriptRejected(["--approve-script approves the script.json you reviewed, and there is none yet; "
                              "run montage plan without it first, read the draft, then --approve-script"])
    script, reused = get_script(brief, writer, rewrite, log=log)
    log(f"script: {len(script.beats)} beats by {script.writer}" + (" (reused script.json)" if reused else ""))
    status = gate.status(brief.workspace, script)
    report = gate.build_report(brief, pipeline, script, model_draft=status.model_draft)
    if status.approved and not approve_script:
        log(f"script gate: approved ({gate.RECORD})")
    else:
        for line in gate.format_report(report):
            log(line)
    if report.rejected:
        raise _rejection(report)
    if approve_script:
        gate.record_approval(brief.workspace, script)
        log(f"script gate: approved this script ({gate.RECORD})")
    elif status.tracked and not status.approved:
        if script_gate:
            changed = bool((gate.load_record(brief.workspace) or {}).get("approvedSha256"))
            return ScriptHeld(brief, script, pipeline, report, brief.workspace / "script.json", changed)
        log("script gate: skipped (--no-script-gate); this script is not approved")
    narration = narrate(script, brief.voice, brief.workspace)
    raw_plan = compose(brief, script, narration, pipeline.recipe)
    plan_path = write_plan(raw_plan, brief.workspace)
    voice_clips = next(t["clips"] for t in raw_plan["tracks"] if t["id"] == "narration")
    _, doc = captions_mod.build(voice_clips, narration, raw_plan["meta"]["fps"],
                                raw_plan["meta"]["durationFrames"], pipeline.recipe.caption_style)
    write_captions(doc, brief.workspace)
    prune_stale(brief.workspace, [n.path for n in narration], log=log)  # only once the new plan is written
    estimated = [b["beat"] for b in doc["beats"] if b["words"] and b["method"] != ALIGNED]
    if estimated:
        log(f"  note: word timings for beat(s) {estimated} are estimated (spread by word length), "
            f"not aligned; captions may drift")
    plan = parse_plan(raw_plan, brief.workspace)  # the contract gates the planner's own output too
    return PlanResult(brief, script, plan, plan_path, reused, pipeline)


def approve_script(workspace: str | Path, note: str | None = None, log=print) -> Path:
    """`montage approve-script <ws>`: record the current script.json as approved, without planning."""
    ws = Path(workspace)
    brief = load_brief(ws / "brief.json")
    pipeline = load_pipeline(brief.recipe)
    path = brief.workspace / "script.json"
    if not path.is_file():
        raise ScriptRejected([f"no script.json in {brief.workspace}; run montage plan first"])
    script = _read_script(path, brief)
    status = gate.status(brief.workspace, script)
    report = gate.build_report(brief, pipeline, script, model_draft=status.model_draft)
    for line in gate.format_report(report):
        log(line)
    if report.rejected:
        raise _rejection(report)
    return gate.record_approval(brief.workspace, script, note)
