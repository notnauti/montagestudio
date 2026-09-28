"""`montage plan <brief>`, `montage validate <plan>` and `montage render <plan>`.

Exit codes: 0 ok, 1 invalid input (brief, script, plan), 2 a tool or model failed, 3 QC failed.
"""

from __future__ import annotations

import argparse
import sys

from .brief import BriefError
from .compose import ComposeError
from .narrate import NarrationError
from .plan import PlanError, load_plan
from .probe import ToolMissing
from .qc import run_qc
from .render import RenderError, render
from .script import ScriptError


def _print_errors(label: str, errors: list[str]) -> None:
    print(f"invalid {label} ({len(errors)} error{'s' if len(errors) != 1 else ''}):", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)


def plan_command(args) -> int:
    from .planner import run_plan
    try:
        result = run_plan(args.brief, writer=args.writer, rewrite=args.rewrite)
    except BriefError as exc:
        _print_errors("brief", exc.errors)
        return 1
    except PlanError as exc:
        _print_errors("generated plan", exc.errors)
        return 1
    except ComposeError as exc:
        print(exc, file=sys.stderr)
        return 1
    except (ScriptError, NarrationError, ToolMissing) as exc:
        print(exc, file=sys.stderr)
        return 2
    meta, target = result.plan.meta, result.brief.target_seconds
    seconds = meta.duration_frames / meta.fps
    print(f"scene plan: {result.plan_path}")
    print(f"  '{meta.title}': {len(result.script.beats)} beats, {seconds:.1f}s (target {target}s)")
    if abs(seconds - target) > 0.25 * target:
        print(f"  note: {seconds:.0f}s is more than 25% off the {target}s target — edit script.json and re-run")
    print(f"next: montage render \"{result.plan_path}\"")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="montage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="brief.json -> script.json -> narration -> scene_plan.json")
    p.add_argument("brief")
    p.add_argument("--writer", choices=["auto", "claude", "ollama"], default="auto",
                   help="auto: Claude when credentials resolve, else local Ollama")
    p.add_argument("--rewrite", action="store_true", help="write a new script even if script.json exists")
    v = sub.add_parser("validate", help="check a scene_plan.json against the slice 1 contract")
    v.add_argument("plan")
    r = sub.add_parser("render", help="validate, render to mp4, then QC")
    r.add_argument("plan")
    r.add_argument("-o", "--output", help="default: <workspace>/renders/<title>.mp4")
    r.add_argument("--skip-qc", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "plan":
        return plan_command(args)

    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        _print_errors("plan", exc.errors)
        return 1
    except ToolMissing as exc:
        print(exc, file=sys.stderr)
        return 2

    meta = plan.meta
    clips = len(plan.video.clips) + sum(len(t.clips) for t in plan.audio)
    print(f"plan ok: '{meta.title}', {meta.width}x{meta.height} @ {meta.fps} fps, "
          f"{meta.duration_frames} frames ({meta.duration_frames / meta.fps:.2f}s), {clips} clips")
    if args.cmd == "validate":
        return 0

    try:
        out = render(plan, args.output)
    except (RenderError, ToolMissing) as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"rendered: {out}")
    if args.skip_qc:
        return 0

    checks = run_qc(out, plan)
    for c in checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name}: {c.detail}")
    return 0 if all(c.ok for c in checks) else 3
