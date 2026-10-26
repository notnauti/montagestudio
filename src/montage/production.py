"""Per-video production measurement: what a video cost to make, in time and in hand edits.

Three records, all derived or appended, none of them edited by hand:

- `<ws>/production_log.jsonl`: one line per `montage` command run on the workspace (cmd, argv,
  start, seconds, exit). Appended by the CLI; a write failure never fails the command.
- `<ws>/publish/production_summary.json`: written by `publish-pack` and `stats`. Wall time per
  command from the log, hand-edit counts from the records the pipeline already keeps, the
  self-reported hands-on minutes, and YouTube stats entered with `montage stats`.
- the cross-video ledger (`logs/production-ledger.jsonl` at the project root): one line per video,
  the summary as it is, upserted by video name. Workspaces are gitignored; the ledger is not.

Hand edits come from existing records only:
- scriptEdited: script_approval.json approved a script whose hash differs from the model draft
  (None when no model drafted it: a hand-written script has no draft to compare)
- picks: manifest entries with `pickedRank` (a candidate chosen over scrape's first)
- planEdits: snapshots in cache/plan-history/ (one per accepted MCP edit)
- handManifestEntries: manifest entries scrape did not write (no `ranking` field)
- forcedRenders / scriptRewrites / storyboardApprovals: from production_log.jsonl
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .edit import HISTORY_DIR

LOG = "production_log.jsonl"
SUMMARY = ("publish", "production_summary.json")
LEDGER = ("logs", "production-ledger.jsonl")
UNTIMED = ("mcp", "stats")   # logged and listed, but not in wallSecondsTotal


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


# ── the command log ───────────────────────────────────────────────────────────

def log_command(ws: Path, cmd: str, argv: list[str], started: datetime, seconds: float,
                exit_code: int | str) -> bool:
    """Append one run to <ws>/production_log.jsonl. Never raises; False when it could not write, or
    when `ws` is no workspace (no brief.json or scene_plan.json): a bare `montage validate x.json`
    resolves to the current directory, which must not collect a log."""
    try:
        if not ((ws / "brief.json").is_file() or (ws / "scene_plan.json").is_file()):
            return False
        line = json.dumps({"cmd": cmd, "argv": argv, "start": started.isoformat(timespec="seconds"),
                           "seconds": round(seconds, 2), "exit": exit_code}, ensure_ascii=False)
        with (ws / LOG).open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception:  # noqa: BLE001 -- measurement must never fail the command it measures
        return False


def read_log(ws: Path) -> list[dict]:
    """The log's entries; an unreadable line is skipped, not fatal."""
    try:
        lines = (ws / LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("cmd"), str):
            out.append(entry)
    return out


# ── the summary ───────────────────────────────────────────────────────────────

def hand_edits(ws: Path, log: list[dict]) -> dict:
    rec = _read_json(ws / "script_approval.json") or {}
    draft, approved = rec.get("draftSha256"), rec.get("approvedSha256")
    script_edited = (draft != approved) if draft and approved else None
    manifest = _read_json(ws / "manifest.json") or {}
    entries = [e for e in (manifest.get("assets") or {}).values() if isinstance(e, dict)] \
        if isinstance(manifest.get("assets"), dict) else []
    hist = ws.joinpath(*HISTORY_DIR)
    ok = [e for e in log if e.get("exit") == 0]
    return {
        "scriptWriter": rec.get("writer") or (_read_json(ws / "script.json") or {}).get("writer"),
        "scriptEdited": script_edited,
        "picks": sum("pickedRank" in e for e in entries),
        "planEdits": sum(1 for p in hist.iterdir() if p.is_file()) if hist.is_dir() else 0,
        "handManifestEntries": sum("ranking" not in e for e in entries),
        "forcedRenders": sum(e["cmd"] == "render" and "--force" in e.get("argv", []) for e in ok),
        "scriptRewrites": sum(e["cmd"] == "plan" and "--rewrite" in e.get("argv", []) for e in ok),
        "storyboardApprovals": sum(e["cmd"] == "approve" for e in ok),
    }


def timings(log: list[dict]) -> dict:
    wall: dict[str, float] = defaultdict(float)
    for e in log:
        if isinstance(e.get("seconds"), (int, float)):
            wall[e["cmd"]] += e["seconds"]
    starts = sorted(e["start"] for e in log if isinstance(e.get("start"), str))
    return {
        "commandRuns": dict(Counter(e["cmd"] for e in log)),
        "wallSeconds": {k: round(v, 1) for k, v in sorted(wall.items())},
        # mcp is a server left open for a whole editing session, stats runs after publishing: neither is work
        "wallSecondsTotal": round(sum(v for k, v in wall.items() if k not in UNTIMED), 1),
        "firstCommandAt": starts[0] if starts else None,
        "lastCommandAt": starts[-1] if starts else None,
    }


def summary_path(ws: Path) -> Path:
    return ws.joinpath(*SUMMARY)


def build_summary(ws: Path, *, render: Path | None = None, hands_on_min: float | None = None) -> dict:
    """The summary as of now. Hands-on minutes, YouTube stats and the URL carry over from the
    previous summary unless given again; everything else is re-derived."""
    prev = _read_json(summary_path(ws)) or {}
    log = read_log(ws)
    brief = _read_json(ws / "brief.json") or {}
    title = brief.get("title") or (_read_json(ws / "script.json") or {}).get("title")
    if render is None and not prev.get("render"):
        from .publish import latest_render
        render = latest_render(ws)
    return {
        "version": 1,
        "video": ws.name,
        "title": title,
        "writtenAt": _now(),
        "render": render.relative_to(ws).as_posix() if render and render.is_relative_to(ws)
        else prev.get("render"),
        "handsOnMinutes": hands_on_min if hands_on_min is not None else prev.get("handsOnMinutes"),
        "logged": bool(log),   # False: made before production_log.jsonl existed (timings unknown)
        **timings(log),
        "handEdits": hand_edits(ws, log),
        "url": prev.get("url"),
        "youtube": prev.get("youtube") or {},
    }


def write_summary(ws: Path, summary: dict) -> Path:
    path = summary_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def record_youtube(summary: dict, day: int, views: int, avg_view_seconds: float | None) -> dict:
    stats = {"views": views, "recordedAt": _now()}
    if avg_view_seconds is not None:
        stats["avgViewSeconds"] = avg_view_seconds
    summary["youtube"] = {**summary.get("youtube", {}), f"day{day}": stats}
    return summary


# ── the ledger ────────────────────────────────────────────────────────────────

def upsert_ledger(ledger: Path, summary: dict) -> Path:
    """Replace this video's line in the ledger (keyed by `video`) or append it."""
    rows = []
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("video") != summary["video"]:
                rows.append(row)
    rows.append(summary)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, ledger)
    return ledger


def describe(summary: dict) -> list[str]:
    """Human lines for the CLI."""
    h, yt = summary["handEdits"], summary.get("youtube", {})
    mins = summary.get("handsOnMinutes")
    lines = [
        f"hands-on: {f'{mins:g} min' if mins is not None else 'not recorded'}; "
        + (f"montage wall time {summary['wallSecondsTotal'] / 60:.1f} min over "
           f"{sum(summary['commandRuns'].values())} command runs" if summary["logged"]
           else "no production log (made before logging)"),
        f"hand edits: script {'edited' if h['scriptEdited'] else 'as drafted' if h['scriptEdited'] is False else 'n/a'}"
        f" (writer {h['scriptWriter'] or '?'}); picks {h['picks']}; plan edits {h['planEdits']}; "
        f"hand manifest entries {h['handManifestEntries']}; forced renders {h['forcedRenders']}; "
        f"rewrites {h['scriptRewrites']}; storyboard approvals {h['storyboardApprovals']}",
    ]
    if summary.get("url"):
        lines.append(f"url: {summary['url']}")
    for day in sorted(yt, key=lambda d: int(d.removeprefix("day")) if d.removeprefix("day").isdigit() else 0):
        s = yt[day]
        avd = f", avg view {s['avgViewSeconds']:g}s" if "avgViewSeconds" in s else ""
        lines.append(f"youtube {day}: {s['views']} views{avd} (recorded {s['recordedAt'][:10]})")
    return lines
