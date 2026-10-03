"""The storyboard gate: `montage approve --workspace <dir>` and the check `montage render` runs.

approval.json pins what the director looked at: the sha256 of scene_plan.json and of every
asset file the plan uses (video and audio, music included), plus the contact sheet's. Render
recomputes them; any difference -- an edited plan, a re-planned script, a swapped asset --
means the approval no longer covers the render, and it is refused (exit 1) unless --force.

Approving needs a contact sheet at least as new as the plan and its assets, so an approval is
never given for a storyboard that was not regenerated after a change.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .contact_sheet import SHEET_NAME
from .plan import Plan

APPROVAL_NAME = "approval.json"
VERSION = 1


class ApprovalError(ValueError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: Path, workspace: Path) -> str:
    return path.relative_to(workspace).as_posix() if path.is_relative_to(workspace) else path.as_posix()


def asset_paths(plan: Plan) -> list[Path]:
    seen: dict[Path, None] = {}
    for clip in plan.video.clips + tuple(c for t in plan.audio for c in t.clips):
        seen.setdefault(clip.src, None)
    return list(seen)


def fingerprint(plan: Plan, plan_path: Path) -> dict:
    ws = plan.workspace
    return {"plan": {"path": plan_path.name, "sha256": sha256(plan_path)},
            "assets": {_rel(p, ws): sha256(p) for p in asset_paths(plan)}}


def approve(plan: Plan, plan_path: str | Path, note: str | None = None) -> Path:
    plan_path = Path(plan_path).resolve()
    ws = plan.workspace
    sheet = ws / SHEET_NAME
    if not sheet.is_file():
        raise ApprovalError(f"no {SHEET_NAME} in {ws}; run `montage contact-sheet \"{ws}\"`, look at it, "
                            f"then approve")
    newest = max([plan_path.stat().st_mtime] + [p.stat().st_mtime for p in asset_paths(plan)])
    if sheet.stat().st_mtime < newest:
        raise ApprovalError(f"{SHEET_NAME} is older than the plan or one of its assets; run "
                            f"`montage contact-sheet \"{ws}\"` again and review it before approving")
    doc = {"version": VERSION,
           "approvedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           **fingerprint(plan, plan_path),
           "contactSheet": {"path": SHEET_NAME, "sha256": sha256(sheet)}}
    if note:
        doc["note"] = note
    path = ws / APPROVAL_NAME
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class GateStatus:
    approved: bool
    problems: tuple[str, ...]
    approved_at: str | None = None


def check(plan: Plan, plan_path: str | Path) -> GateStatus:
    """Is this plan, with these exact asset bytes, the one that was approved?"""
    plan_path = Path(plan_path).resolve()
    path = plan.workspace / APPROVAL_NAME
    if not path.is_file():
        return GateStatus(False, ("not approved: no approval.json",))
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        approved_plan, approved_assets = doc["plan"], doc["assets"]
        if not isinstance(approved_plan, dict) or not isinstance(approved_assets, dict):
            raise TypeError("'plan' and 'assets' must be objects")
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return GateStatus(False, (f"approval.json is unreadable ({exc}); approve again",))
    now = fingerprint(plan, plan_path)
    problems = []
    if approved_plan.get("path") != now["plan"]["path"]:
        problems.append(f"approval is for {approved_plan.get('path')}, not {now['plan']['path']}")
    elif approved_plan.get("sha256") != now["plan"]["sha256"]:
        problems.append(f"{now['plan']['path']} changed since it was approved")
    for rel, digest in now["assets"].items():
        if rel not in approved_assets:
            problems.append(f"{rel} is new since approval")
        elif approved_assets[rel] != digest:
            problems.append(f"{rel} changed since approval")
    for rel in approved_assets:
        if rel not in now["assets"]:
            problems.append(f"{rel} was approved but the plan no longer uses it")
    return GateStatus(not problems, tuple(problems), doc.get("approvedAt"))
