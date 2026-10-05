"""The script gate: a model-written script.json stops before narration until it is approved.

`montage plan` prints a beat table (beat, estimated seconds, asset, narration excerpt, which
brief points the beat says), the estimated runtime against the target, and the brief points
no beat covers. Estimates are words / WORDS_PER_SECOND plus the recipe's pauses (lead per
beat, tail between beats, end hold), the same arithmetic compose() uses with real wavs.

Hard checks (`problems`): estimated runtime more than 15% over the target, or more beats
than the recipe's maximum for the target. They reject a model draft as written (exit 1);
a script someone has edited gets them as warnings, because a person chose it.

The record lives in <workspace>/script_approval.json:
  {"version": 1,
   "draftSha256": "...", "writer": "claude-...", "draftedAt": "...",    # set when a model writes
   "approvedSha256": "...", "approvedAt": "...", "note": "..."}          # set by approval
Hashes are sha256 of the script's content (title + beats, canonical JSON), so reformatting
script.json needs no new approval but any changed word does. A script.json with no record
was written by hand from scratch (or predates the gate): its table is printed, and planning
goes on without stopping.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .brief import Brief
from .coverage import Coverage, cover, key_points
from .pipeline import Pipeline
from .script import RUNTIME_TOLERANCE, WORDS_PER_SECOND, Script

RECORD = "script_approval.json"
EXCERPT = 60


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?", text))


def speech_seconds(text: str) -> float:
    return word_count(text) / WORDS_PER_SECOND


def pause_seconds(pipeline: Pipeline, beats: int) -> float:
    """Silence compose() puts around the narration: lead per beat, tail between, end hold."""
    r = pipeline.recipe
    if beats <= 0:
        return 0.0
    return (beats * r.lead + (beats - 1) * r.tail + r.end_hold) / r.fps


def script_sha(script: Script) -> str:
    doc = {"title": script.title,
           "beats": [{"narration": b.narration, "asset": b.asset, "motion": b.motion} for b in script.beats]}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Row:
    beat: int            # 1-based
    seconds: float
    words: int
    asset: str
    excerpt: str
    covers: tuple[int, ...]  # 1-based brief points


@dataclass
class GateReport:
    rows: list[Row]
    speech: float
    runtime: float
    target: int
    beat_range: tuple[int, int]
    coverage: Coverage
    model_draft: bool
    problems: list[str] = field(default_factory=list)   # hard checks
    warnings: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return self.model_draft and bool(self.problems)


def build_report(brief: Brief, pipeline: Pipeline, script: Script, model_draft: bool) -> GateReport:
    fewest, most = pipeline.beat_range(brief.target_seconds)
    points = key_points(brief.text, brief.title)
    cov = cover(points, [b.narration for b in script.beats])
    per_beat = cov.by_beat(len(script.beats))
    assets = {a.src: a for a in brief.assets}
    rows, speech = [], 0.0
    warnings: list[str] = []
    for i, b in enumerate(script.beats):
        s = speech_seconds(b.narration)
        speech += s
        text = b.narration if len(b.narration) <= EXCERPT else b.narration[:EXCERPT - 3].rstrip() + "..."
        rows.append(Row(i + 1, s, word_count(b.narration), b.asset, text, tuple(p + 1 for p in per_beat[i])))
        a = assets.get(b.asset)
        if a is not None and not a.is_still and a.duration and s > a.duration:
            warnings.append(f"beat {i + 1}: about {s:.1f}s of narration over '{b.asset}', a {a.duration:.1f}s video "
                            f"(planning will refuse it; at most {int(a.duration * WORDS_PER_SECOND)} words)")
    runtime = speech + pause_seconds(pipeline, len(script.beats))
    target = brief.target_seconds
    problems: list[str] = []
    if runtime > target * (1 + RUNTIME_TOLERANCE):
        problems.append(f"estimated runtime {runtime:.0f}s is {100 * (runtime / target - 1):.0f}% over the "
                        f"{target}s target (limit +{RUNTIME_TOLERANCE:.0%}); cut about "
                        f"{int((runtime - target) * WORDS_PER_SECOND)} words")
    if len(script.beats) > most:
        problems.append(f"{len(script.beats)} beats; the {pipeline.name} pipeline allows at most {most} "
                        f"at {target}s")
    if len(script.beats) < fewest:
        warnings.append(f"the {pipeline.name} pipeline asks for {fewest}-{most} beats at {target}s; "
                        f"this script has {len(script.beats)}")
    if runtime < target * (1 - RUNTIME_TOLERANCE):
        warnings.append(f"estimated runtime {runtime:.0f}s is {100 * (1 - runtime / target):.0f}% under the "
                        f"{target}s target")
    for p in cov.uncovered:
        warnings.append(f"brief point {p + 1} is not covered: \"{points[p].text}\"")
    late = cov.out_of_order()
    if late:
        warnings.append("brief point(s) " + ", ".join(str(p + 1) for p in late)
                        + " come earlier in the script than the point before them in the brief")
    return GateReport(rows, speech, runtime, target, (fewest, most), cov, model_draft, problems, warnings)


def format_report(report: GateReport) -> list[str]:
    asset_w = max([len("asset")] + [len(r.asset) for r in report.rows])
    lines = [f"  {'#':>2}  {'est s':>5}  {'words':>5}  {'asset':<{asset_w}}  {'covers':<7}  narration",
             f"  {'-' * 2}  {'-' * 5}  {'-' * 5}  {'-' * asset_w}  {'-' * 7}  {'-' * 9}"]
    for r in report.rows:
        covers = ",".join(map(str, r.covers)) or "-"
        lines.append(f"  {r.beat:>2}  {r.seconds:>5.1f}  {r.words:>5}  {r.asset:<{asset_w}}  {covers:<7}  {r.excerpt}")
    pauses = report.runtime - report.speech
    fewest, most = report.beat_range
    lines.append(f"  total: {len(report.rows)} beats (recipe {fewest}-{most}), {sum(r.words for r in report.rows)} "
                 f"words, about {report.speech:.0f}s speech + {pauses:.0f}s pauses = {report.runtime:.0f}s "
                 f"(target {report.target}s, at {WORDS_PER_SECOND:g} words/s)")
    cov = report.coverage
    if cov.points:
        lines.append(f"  brief points ({len(cov.points) - len(cov.uncovered)}/{len(cov.points)} covered):")
        for i, (p, beats, said) in enumerate(zip(cov.points, cov.beats, cov.matched), 1):
            where = ("beat " + "+".join(str(b + 1) for b in beats) + f" ({', '.join(said)})") if beats \
                else "NOT COVERED"
            lines.append(f"    {i}. {p.text} -> {where}")
    severity = "rejected" if report.model_draft else "warning"
    lines += [f"  {severity}: {p}" for p in report.problems]
    lines += [f"  note: {w}" for w in report.warnings]
    return lines


# ── the approval record ───────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_record(workspace: Path) -> dict | None:
    path = Path(workspace) / RECORD
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}  # unreadable: treat as tracked but unapproved, so it gets re-approved
    return raw if isinstance(raw, dict) else {}


def _write(workspace: Path, record: dict) -> Path:
    path = Path(workspace) / RECORD
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def record_draft(workspace: Path, script: Script) -> Path:
    """A model just wrote script.json: start a fresh record (any older approval is void)."""
    return _write(workspace, {"version": 1, "draftSha256": script_sha(script), "writer": script.writer,
                              "draftedAt": _now()})


def record_approval(workspace: Path, script: Script, note: str | None = None) -> Path:
    record = load_record(workspace) or {"version": 1}
    record.update({"version": 1, "approvedSha256": script_sha(script), "approvedAt": _now()})
    if note:
        record["note"] = note
    else:
        record.pop("note", None)
    return _write(workspace, record)


@dataclass(frozen=True)
class Status:
    tracked: bool       # a record exists: the gate applies
    approved: bool      # the record approves this exact script
    model_draft: bool   # the script is the model's draft, unedited


def status(workspace: Path, script: Script) -> Status:
    record = load_record(workspace)
    if record is None:
        return Status(False, False, False)
    sha = script_sha(script)
    return Status(True, record.get("approvedSha256") == sha, record.get("draftSha256") == sha)
