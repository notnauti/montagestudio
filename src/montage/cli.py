"""`montage validate <plan>` and `montage render <plan>`.

Exit codes: 0 ok, 1 invalid plan, 2 render failed / tool missing, 3 QC failed.
"""

from __future__ import annotations

import argparse
import sys

from .plan import PlanError, load_plan
from .probe import ToolMissing
from .qc import run_qc
from .render import RenderError, render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="montage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="check a scene_plan.json against the slice 1 contract")
    v.add_argument("plan")
    r = sub.add_parser("render", help="validate, render to mp4, then QC")
    r.add_argument("plan")
    r.add_argument("-o", "--output", help="default: <workspace>/renders/<title>.mp4")
    r.add_argument("--skip-qc", action="store_true")
    args = parser.parse_args(argv)

    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"invalid plan ({len(exc.errors)} error{'s' if len(exc.errors) != 1 else ''}):", file=sys.stderr)
        for err in exc.errors:
            print(f"  - {err}", file=sys.stderr)
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
