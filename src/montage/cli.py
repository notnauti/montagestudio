"""`montage init|scrape|plan|contact-sheet|approve|validate|normalize|render|mcp`.

Exit codes: 0 ok, 1 invalid input (brief, script, plan, pipeline, arguments, an unapproved
plan, an asset no source could supply), 2 a tool, model or network failed, 3 QC failed.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from . import approval as approval_mod
from .brief import BriefError
from .compose import ComposeError
from .contact_sheet import ContactSheetError, make_contact_sheet
from .narrate import NarrationError
from .pipeline import DEFAULT_PIPELINE, PipelineError, available, load_pipeline
from .plan import PlanError, load_plan
from .probe import ToolMissing
from .profiles import DEFAULT_PROFILE, ENCODER_MODES, PROFILES, resolve
from .qc import run_qc
from .render import RenderError, default_output, render
from .scaffold import WorkspaceError, init_workspace, slug, synthesize_brief
from .script import ScriptError


def _print_errors(label: str, errors: list[str]) -> None:
    print(f"invalid {label} ({len(errors)} error{'s' if len(errors) != 1 else ''}):", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)


def _fail(msg: str, code: int = 1) -> int:
    print(msg, file=sys.stderr)
    return code


def init_command(args) -> int:
    try:
        pipeline = load_pipeline(args.pipeline)
        path = init_workspace(args.dir, pipeline)
    except PipelineError as exc:
        _print_errors("pipeline", exc.errors)
        return 1
    except WorkspaceError as exc:
        return _fail(str(exc))
    print(f"workspace: {path.parent}")
    print(f"  {path.name}: template for the '{pipeline.name}' pipeline; assets/: empty")
    print(f"next: list the visuals in {path.name} (src + description); put files in assets/ or fetch the "
          f"missing ones with montage scrape \"{path.parent}\"; then montage plan \"{path}\"")
    return 0


def _topic_brief(args) -> Path | int:
    """`plan --topic`: write <workspace>/brief.json from the pipeline. Returns its path or an exit code."""
    if args.brief:
        return _fail("give either a brief.json or --topic, not both")
    pipeline_name = args.pipeline or DEFAULT_PIPELINE
    try:
        pipeline = load_pipeline(pipeline_name)
    except PipelineError as exc:
        _print_errors("pipeline", exc.errors)
        return 1
    ws = Path(args.workspace) if args.workspace else Path("workspace") / slug(args.topic)
    try:
        path, notes = synthesize_brief(ws, pipeline, args.topic, args.target_duration)
    except WorkspaceError as exc:
        return _fail(str(exc))
    except ToolMissing as exc:
        return _fail(str(exc), 2)
    print(f"brief: wrote {path} ({pipeline.name} pipeline)")
    for note in notes:
        print(f"  note: {note}")
    return path


def plan_command(args) -> int:
    from .planner import run_plan
    if args.topic is not None:
        brief_path = _topic_brief(args)
        if isinstance(brief_path, int):
            return brief_path
    elif not args.brief:
        return _fail("montage plan needs a brief.json, or --topic (with optional --pipeline, --target-duration)")
    elif args.pipeline or args.target_duration or args.workspace:
        return _fail("--pipeline / --target-duration / --workspace go with --topic; "
                     "with a brief.json set \"recipe\" and \"targetSeconds\" in the brief")
    else:
        brief_path = args.brief
    try:
        result = run_plan(brief_path, writer=args.writer, rewrite=args.rewrite)
    except BriefError as exc:
        _print_errors("brief", exc.errors)
        return 1
    except PipelineError as exc:
        _print_errors("pipeline", exc.errors)
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
    print(f"  '{meta.title}': {result.pipeline.name} pipeline, {len(result.script.beats)} beats, "
          f"{seconds:.1f}s (target {target}s)")
    if abs(seconds - target) > 0.25 * target:
        print(f"  note: {seconds:.0f}s is more than 25% off the {target}s target — edit script.json and re-run")
    ws = Path(result.plan_path).parent
    try:
        sheet = make_contact_sheet(result.plan)
    except (ContactSheetError, ToolMissing) as exc:
        # the plan itself is good; approval will insist on a sheet, so say how to make one
        print(f"contact sheet failed: {exc}\n  retry with: montage contact-sheet \"{ws}\"", file=sys.stderr)
    else:
        print(f"contact sheet: {sheet}")
    print(f"next: review contact_sheet.png, then montage approve --workspace \"{ws}\" "
          f"and montage render \"{result.plan_path}\"")
    return 0


# ── Stage 2: sourcing and the storyboard gate ─────────────────────────────────

def scrape_command(args) -> int:
    from .net import Http
    from .scrape import RANKING, ScrapeError, scrape
    from .sources import SOURCES
    names = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [n for n in names if n not in SOURCES]
    if unknown or not names:
        return _fail(f"--sources: unknown {unknown or 'empty list'}; choose from {sorted(SOURCES)}")
    if args.max_mb <= 0:
        return _fail("--max-mb must be positive")
    try:
        outcomes, notes, manifest = scrape(args.workspace, http=Http(), source_names=names, max_mb=args.max_mb,
                                           min_score=args.min_score, dry_run=args.dry_run)
    except ScrapeError as exc:
        return _fail(str(exc))
    except BriefError as exc:
        _print_errors("brief", exc.errors)
        return 1
    except ToolMissing as exc:
        return _fail(str(exc), 2)
    for note in notes:
        print(f"  note: {note}")
    if not outcomes:
        print("scrape: every visual in brief.json already has a file; nothing to fetch")
        return 0
    print(f"scrape: {len(outcomes)} wanted visual(s); sources {', '.join(names)}; size cap {args.max_mb:g} MB")
    print(f"  ranking is keyword-based, not CLIP: {RANKING}")
    missing, net_failed = 0, False
    for o in outcomes:
        w = o.wanted
        print(f"\n  {w.src} [{w.kind}] search words: {' '.join(w.search_words())}")
        for src_name, q in o.queries.items():
            print(f"    {src_name}: {q}")
        for err in o.errors:
            if err.split(":")[0] in names:  # search failures ("nasa: ..."); download ones come later
                print(f"    search failed: {err}")
        if o.refused:
            reasons = Counter(r.reason for r in o.refused)
            print(f"    refused {len(o.refused)} without a usable license: "
                  + "; ".join(f"{r} x{n}" for r, n in reasons.most_common(4)))
        for k, s in enumerate(o.ranked[:5], 1):
            c = s.candidate
            print(f"    {k}. {s.score:.3f}  {c.source} {c.id!r}: {c.title[:60]!r}  [{c.license.name}]  "
                  f"matched: {', '.join(s.matched) or '-'}")
        if not o.ranked:
            best = f" (best {o.below[0].score:.3f})" if o.below else ""
            print(f"    no licensed candidate scored >= {args.min_score}{best}; "
                  f"add a \"query\" to this asset in brief.json or lower --min-score")
        if args.dry_run:
            continue
        if o.ok:
            e = o.entry
            conv = f", converted from {e['convertedFrom']}" if e["convertedFrom"] else ""
            print(f"    -> saved {w.src}: {e['source']} {e['sourceId']!r}, {e['bytes'] / 1e6:.2f} MB{conv}")
            print(f"       license {e['license']} ({e['licenseBasis']}); attribution: {e['attribution']}")
        else:
            missing += 1
            net_failed = net_failed or bool(o.errors)
            for err in o.errors[-3:]:
                print(f"    failed: {err}")
            print(f"    -> {w.src} NOT sourced")
    if args.dry_run:
        print("\ndry run: nothing downloaded")
        return 0
    if manifest:
        print(f"\nmanifest: {manifest}")
    if missing:
        print(f"{missing} visual(s) still missing; montage plan needs every file", file=sys.stderr)
        return 2 if net_failed else 1
    print(f"next: montage plan \"{Path(args.workspace) / 'brief.json'}\"")
    return 0


def _workspace_plan(target: str) -> Path:
    p = Path(target)
    return p if p.is_file() else p / "scene_plan.json"


def _load(plan_path: Path):
    """load_plan with the CLI's error printing; returns a Plan or an exit code."""
    try:
        return load_plan(plan_path)
    except PlanError as exc:
        _print_errors("plan", exc.errors)
        return 1
    except ToolMissing as exc:
        return _fail(str(exc), 2)


def contact_sheet_command(args) -> int:
    plan = _load(_workspace_plan(args.workspace))
    if isinstance(plan, int):
        return plan
    try:
        sheet = make_contact_sheet(plan, args.output)
    except ContactSheetError as exc:
        return _fail(str(exc), 2)
    except ToolMissing as exc:
        return _fail(str(exc), 2)
    print(f"contact sheet: {sheet} ({len(plan.video.clips)} clips)")
    print(f"next: review it, then montage approve --workspace \"{plan.workspace}\"")
    return 0


def approve_command(args) -> int:
    plan_path = _workspace_plan(args.workspace)
    plan = _load(plan_path)
    if isinstance(plan, int):
        return plan
    try:
        path = approval_mod.approve(plan, plan_path, args.note)
    except approval_mod.ApprovalError as exc:
        return _fail(str(exc))
    assets = len(approval_mod.asset_paths(plan))
    print(f"approved: {path} (scene_plan.json + {assets} asset files pinned by sha256)")
    print(f"next: montage render \"{plan_path}\"")
    return 0


def _gate(plan, plan_path: Path, force: bool) -> int | None:
    """None to go ahead with the render, or the exit code for refusing it."""
    status = approval_mod.check(plan, plan_path)
    if status.approved:
        print(f"approval: ok (approved {status.approved_at})")
        return None
    if force:
        print(f"approval: bypassed with --force ({'; '.join(status.problems)})")
        return None
    print("refusing to render: the storyboard gate has not approved this plan as it is now", file=sys.stderr)
    for p in status.problems:
        print(f"  - {p}", file=sys.stderr)
    ws = plan.workspace
    print(f"review it: montage contact-sheet \"{ws}\", then montage approve --workspace \"{ws}\" "
          f"(or render anyway with --force)", file=sys.stderr)
    return 1


def normalize_command(plan, fix: bool) -> int:
    from .normalize import NormalizeError, normalize_plan
    try:
        reports = normalize_plan(plan, fix=fix)
    except NormalizeError as exc:
        return _fail(str(exc), 2)
    fixable = sum(r.needs_fix for r in reports)
    for r in reports:
        rel = r.src.relative_to(plan.workspace) if r.src.is_relative_to(plan.workspace) else r.src
        print(f"  {'FIX ' if r.needs_fix else 'ok  '} {rel} [{r.role}] {r.summary}")
        for i in r.issues:
            print(f"         {'fix' if i.fixable else 'info'}: {i.kind}: {i.detail}")
        if r.normalized:
            print(f"         -> {r.normalized.relative_to(plan.workspace)}")
    print(f"normalize: {len(reports)} asset uses, {fixable} need fixing"
          + ("" if fix or not fixable else " (render does this automatically; --fix fills the cache now)"))
    return 0


def mcp_command(args) -> int:
    from .mcp_server import run_stdio
    plan_path = _workspace_plan(args.workspace)
    if not plan_path.is_file():
        return _fail(f"no scene_plan.json at {plan_path}; run montage plan first")
    try:
        load_plan(plan_path)
    except PlanError as exc:
        # still serve: plan_summary shows the errors and an edit can only make it valid
        print(f"warning: {plan_path} is invalid as it stands ({len(exc.errors)} errors); edits are refused "
              f"until it validates", file=sys.stderr)
    except ToolMissing as exc:
        return _fail(str(exc), 2)
    return run_stdio(plan_path)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path) -> list[str]:
    """Set KEY=VALUE lines from `path` into os.environ; variables already set win. Returns the keys set.

    Stdlib only: `#` comments, blank lines, an optional `export ` prefix and matching outer quotes.
    """
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def main(argv: list[str] | None = None) -> int:
    if argv is None:  # a real command line; tests pass argv and never see the project's keys
        load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(prog="montage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    pipelines = available()

    i = sub.add_parser("init", help="scaffold a workspace: brief.json template + assets/")
    i.add_argument("dir")
    i.add_argument("--pipeline", default=DEFAULT_PIPELINE, help=f"one of {pipelines}")

    p = sub.add_parser("plan", help="brief.json (or --topic) -> script.json -> narration -> scene_plan.json")
    p.add_argument("brief", nargs="?", help="path to brief.json; omit when using --topic")
    p.add_argument("--topic", help="write <workspace>/brief.json from a pipeline and plan it")
    p.add_argument("--pipeline", help=f"with --topic: one of {pipelines} (default {DEFAULT_PIPELINE})")
    p.add_argument("--target-duration", type=int, help="with --topic: seconds (default: the pipeline's)")
    p.add_argument("--workspace", help="with --topic: workspace dir (default workspace/<topic-slug>)")
    p.add_argument("--writer", choices=["auto", "claude", "ollama"], default="auto",
                   help="auto: Claude when credentials resolve, else local Ollama")
    p.add_argument("--rewrite", action="store_true", help="write a new script even if script.json exists")

    s = sub.add_parser("scrape", help="fetch the brief's missing visuals from Commons / NASA / Archive.org "
                                      "(licensed only) and record them in manifest.json")
    s.add_argument("workspace")
    s.add_argument("--sources", default="commons,nasa,archive", help="comma-separated, in tie-break order")
    s.add_argument("--max-mb", type=float, default=50, help="size cap per download (default 50)")
    s.add_argument("--min-score", type=float, default=0.25, help="lowest lexical score to accept (0-1)")
    s.add_argument("--dry-run", action="store_true", help="search and rank, download nothing")

    cs = sub.add_parser("contact-sheet", help="render contact_sheet.png: one frame per visual clip")
    cs.add_argument("workspace", help="workspace dir (uses its scene_plan.json) or a scene_plan.json")
    cs.add_argument("-o", "--output", help="default: <workspace>/contact_sheet.png")

    ap = sub.add_parser("approve", help="approve the storyboard: pin scene_plan.json + assets for render")
    ap.add_argument("--workspace", required=True, help="workspace dir (or its scene_plan.json)")
    ap.add_argument("--note", help="optional note stored in approval.json")

    v = sub.add_parser("validate", help="check a scene_plan.json against the slice 1 contract")
    v.add_argument("plan")

    n = sub.add_parser("normalize", help="probe every asset the plan uses; report sample-rate, fps, "
                                         "pixel-format and aspect mismatches")
    n.add_argument("plan")
    n.add_argument("--fix", action="store_true", help="also write fixed copies to <workspace>/cache/normalized/")

    r = sub.add_parser("render", help="validate, normalize, render, then QC")
    r.add_argument("plan")
    r.add_argument("-o", "--output", help="default: <workspace>/renders/<title>[-<profile>].<ext>")
    r.add_argument("--profile", choices=sorted(PROFILES),
                   help=f"output size/fps/codec; default: the plan's own size and fps in H.264 "
                        f"(1920x1080@30 for planner plans, i.e. {DEFAULT_PROFILE})")
    r.add_argument("--encoder", choices=ENCODER_MODES, default="software",
                   help="H.264 encoder: software (libx264), auto (first hardware encoder that works), "
                        "or a named one; falls back to libx264 when it is unavailable")
    r.add_argument("--skip-normalize", action="store_true", help="render the original assets as they are")
    r.add_argument("--no-captions", action="store_true", help="do not burn in the plan's caption track")
    r.add_argument("--skip-qc", action="store_true")
    r.add_argument("--force", action="store_true", help="render even if the plan is unapproved or changed "
                                                        "since `montage approve`")

    m = sub.add_parser("mcp", help="MCP server on stdio: timeline edit tools over the workspace's "
                                   "scene_plan.json (every write re-validated)")
    m.add_argument("workspace", help="workspace dir (uses its scene_plan.json) or a scene_plan.json")
    args = parser.parse_args(argv)
    if args.cmd == "mcp":
        return mcp_command(args)
    if args.cmd == "init":
        return init_command(args)
    if args.cmd == "plan":
        return plan_command(args)
    if args.cmd == "scrape":
        return scrape_command(args)
    if args.cmd == "contact-sheet":
        return contact_sheet_command(args)
    if args.cmd == "approve":
        return approve_command(args)

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
    caps = plan.captions
    print(f"plan ok: '{meta.title}', {meta.width}x{meta.height} @ {meta.fps} fps, "
          f"{meta.duration_frames} frames ({meta.duration_frames / meta.fps:.2f}s), {clips} clips"
          + (f", {len(caps.clips)} caption lines ({caps.style})" if caps else ""))
    if args.cmd == "validate":
        return 0
    if args.cmd == "normalize":
        return normalize_command(plan, args.fix)

    refused = _gate(plan, Path(args.plan), args.force)
    if refused is not None:
        return refused
    try:
        spec = resolve(plan, args.profile, args.encoder)
    except ToolMissing as exc:
        return _fail(str(exc), 2)
    out = Path(args.output) if args.output else default_output(plan, spec)
    if out.suffix.lower() != spec.ext:
        return _fail(f"{spec.profile} writes {spec.ext} files; -o {out.name} would not match")
    print(f"output: {spec.profile}, {spec.width}x{spec.height} @ {spec.fps} fps, {spec.encoder}")
    for note in spec.notes:
        print(f"  note: {note}")
    if caps:
        print(f"captions: {'off (--no-captions)' if args.no_captions else f'burned in, {caps.style} style'}")
    try:
        out = render(plan, out, spec, normalize=not args.skip_normalize, captions=not args.no_captions)
    except (RenderError, ToolMissing) as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"rendered: {out}")
    if args.skip_qc:
        return 0

    checks = run_qc(out, plan, spec)
    for c in checks:
        print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name}: {c.detail}")
    return 0 if all(c.ok for c in checks) else 3
