"""The storyboard gate: `montage approve --workspace <dir>` and the check `montage render` runs.

approval.json pins what the director looked at: the sha256 of scene_plan.json and of every
asset file the plan uses (video and audio, music included), plus the contact sheet's and/or the
animatic preview's. Render recomputes them; any difference -- an edited plan, a re-planned script,
a swapped asset -- means the approval no longer covers the render, and it is refused (exit 1)
unless --force.

Approving needs a review artifact made from exactly this plan and these assets: contact_sheet.png
or the preview (cache/preview.mp4). Each carries a sidecar record (review.py) with the sha256 of
the plan and assets it was made from, so freshness is decided by content, not file mtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .contact_sheet import SHEET_NAME
from .plan import Plan
from .review import asset_paths, fingerprint, preview_path, sha256, stale_reasons  # noqa: F401  (re-exported)
from .review import rel as _rel

APPROVAL_NAME = "approval.json"
VERSION = 1


class ApprovalError(ValueError):
    pass


def approve(plan: Plan, plan_path: str | Path, note: str | None = None) -> Path:
    plan_path = Path(plan_path).resolve()
    ws = plan.workspace
    now = fingerprint(plan, plan_path)
    reviewed = {"contactSheet": ws / SHEET_NAME, "preview": preview_path(ws)}
    fresh, why = {}, []
    for key, artifact in reviewed.items():
        reasons = stale_reasons(artifact, now)
        if reasons:
            why += reasons
        else:
            fresh[key] = {"path": _rel(artifact, ws), "sha256": sha256(artifact)}
    if not fresh:
        raise ApprovalError(
            "nothing reviewed matches this plan and its assets:\n"
            + "".join(f"  - {r}\n" for r in why)
            + f"make one and look at it: montage contact-sheet \"{ws}\" or montage preview \"{ws}\", "
              f"then approve")
    doc = {"version": VERSION,
           "approvedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           **now, **fresh}
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
