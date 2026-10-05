"""What a review artifact (contact sheet, animatic preview) was made from, by sha256.

Every contact sheet and preview gets a sidecar `<artifact name>.source.json` next to it:

  {"version": 1, "kind": "contact-sheet" | "preview", "madeAt": "...",
   "artifact": {"path": "contact_sheet.png", "sha256": "..."},
   "plan": {"path": "scene_plan.json", "sha256": "..."},
   "assets": {"assets/a.jpg": "...", ...}}

`plan` and `assets` are the plan fingerprint taken *before* the artifact was drawn, so a plan
edited while a preview renders does not get credited. Approval compares the record with the
plan and assets as they are now, and the artifact's own hash with the file on disk. That
replaces the old mtime comparison, which a copy, a checkout or a touched file could fool both
ways (an older sheet could look newer than an edited plan; an unchanged plan could look newer).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .plan import Plan

VERSION = 1
SUFFIX = ".source.json"
PREVIEW_NAME = "preview.mp4"


def preview_path(workspace: Path) -> Path:
    """Where `montage preview` writes by default, and the only preview approval looks at."""
    return Path(workspace) / "cache" / PREVIEW_NAME


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path, workspace: Path) -> str:
    return path.relative_to(workspace).as_posix() if path.is_relative_to(workspace) else path.as_posix()


def asset_paths(plan: Plan) -> list[Path]:
    seen: dict[Path, None] = {}
    for clip in plan.video.clips + tuple(c for t in plan.audio for c in t.clips):
        seen.setdefault(clip.src, None)
    return list(seen)


def fingerprint(plan: Plan, plan_path: Path) -> dict:
    ws = plan.workspace
    return {"plan": {"path": Path(plan_path).name, "sha256": sha256(Path(plan_path))},
            "assets": {rel(p, ws): sha256(p) for p in asset_paths(plan)}}


def sidecar(artifact: Path) -> Path:
    artifact = Path(artifact)
    return artifact.with_name(artifact.name + SUFFIX)


def record(artifact: Path, kind: str, fp: dict, workspace: Path) -> Path:
    """Write the sidecar for a freshly made artifact; `fp` is the fingerprint taken before making it."""
    artifact = Path(artifact)
    doc = {"version": VERSION, "kind": kind,
           "madeAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "artifact": {"path": rel(artifact.resolve(), Path(workspace).resolve()), "sha256": sha256(artifact)},
           **fp}
    path = sidecar(artifact)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def stale_reasons(artifact: Path, now: dict) -> list[str]:
    """[] when `artifact` was made from exactly the plan + assets in `now`; else why not."""
    artifact = Path(artifact)
    name = artifact.name
    if not artifact.is_file():
        return [f"no {name}"]
    side = sidecar(artifact)
    if not side.is_file():
        return [f"{name} has no record of the plan it was made from ({side.name} is missing; "
                f"it predates the record or was made by hand)"]
    try:
        doc = json.loads(side.read_text(encoding="utf-8"))
        made, plan, assets = doc["artifact"], doc["plan"], doc["assets"]
        if not all(isinstance(x, dict) for x in (made, plan, assets)):
            raise TypeError("'artifact', 'plan' and 'assets' must be objects")
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return [f"{side.name} is unreadable ({exc})"]
    reasons = []
    if made.get("sha256") != sha256(artifact):
        reasons.append(f"{name} is not the file its record describes (replaced or edited since)")
    if plan.get("path") != now["plan"]["path"] or plan.get("sha256") != now["plan"]["sha256"]:
        reasons.append(f"{now['plan']['path']} changed since {name} was made")
    changed = sorted(set(assets) ^ set(now["assets"])
                     | {k for k in set(assets) & set(now["assets"]) if assets[k] != now["assets"][k]})
    if changed:
        more = f" (+{len(changed) - 3} more)" if len(changed) > 3 else ""
        reasons.append(f"assets changed since {name} was made: {', '.join(changed[:3])}{more}")
    return reasons
