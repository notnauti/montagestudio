"""`montage init|scrape|plan|approve-script|contact-sheet|preview|approve|validate|normalize|render|publish-pack|stats|mcp`.

Every command run on a workspace is appended to <workspace>/production_log.jsonl (production.py).

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
from .production import LEDGER
from .profiles import DEFAULT_PROFILE, ENCODER_MODES, PROFILES, resolve
from .qc import run_qc
from .render import RenderError, default_output, render
from .scaffold import WorkspaceError, init_workspace, slug, synthesize_brief
from .script import ScriptError
from .speakable import PronunciationError


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


def _script_rejected(exc) -> int:
    for err in exc.errors:
        print(err, file=sys.stderr)
    return 1


def plan_command(args) -> int:
    from .planner import ScriptHeld, ScriptRejected, run_plan
    if args.approve_script and args.rewrite:
        return _fail("--approve-script approves the script.json you reviewed; run --rewrite first, "
                     "review the new draft, then --approve-script")
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
        result = run_plan(brief_path, writer=args.writer, rewrite=args.rewrite,
                          approve_script=args.approve_script, script_gate=not args.no_script_gate)
    except ScriptRejected as exc:
        return _script_rejected(exc)
    except BriefError as exc:
        _print_errors("brief", exc.errors)
        return 1
    except PipelineError as exc:
        _print_errors("pipeline", exc.errors)
        return 1
    except PlanError as exc:
        _print_errors("generated plan", exc.errors)
        return 1
    except (ComposeError, PronunciationError) as exc:
        print(exc, file=sys.stderr)
        return 1
    except (ScriptError, NarrationError, ToolMissing) as exc:
        print(exc, file=sys.stderr)
        return 2
    if isinstance(result, ScriptHeld):
        why = ("script.json changed since it was approved" if result.changed
               else f"script.json is a draft by {result.script.writer}")
        print(f"script gate: stopped before narration; {why}")
        print(f"next: read the table above (and {result.script_path.name}), edit it if needed, then "
              f"montage plan \"{brief_path}\" --approve-script "
              f"(or montage approve-script \"{result.brief.workspace}\" and re-run montage plan)")
        return 0
    meta, target = result.plan.meta, result.brief.target_seconds
    seconds = meta.duration_frames / meta.fps
    print(f"scene plan: {result.plan_path}")
    print(f"  '{meta.title}': {result.pipeline.name} pipeline, {len(result.script.beats)} beats, "
          f"{seconds:.1f}s (target {target}s)")
    if abs(seconds - target) > 0.25 * target:
        print(f"  note: {seconds:.0f}s is more than 25% off the {target}s target — edit script.json and re-run")
    ws = Path(result.plan_path).parent
    try:
        sheet = make_contact_sheet(result.plan)  # records <workspace>/scene_plan.json, which plan just wrote
    except (ContactSheetError, ToolMissing) as exc:
        # the plan itself is good; approval will insist on a sheet, so say how to make one
        print(f"contact sheet failed: {exc}\n  retry with: montage contact-sheet \"{ws}\"", file=sys.stderr)
    else:
        print(f"contact sheet: {sheet}")
    print(f"next: review contact_sheet.png (or watch montage preview \"{ws}\"), then "
          f"montage approve --workspace \"{ws}\" and montage render \"{result.plan_path}\"")
    return 0


# ── Stage 2: sourcing and the storyboard gate ─────────────────────────────────

def pick_command(args) -> int:
    """`scrape <ws> --pick <asset>=<n>`: download a candidate from cache/candidates.json."""
    from . import net
    from .candidates import PickError, parse_pick, pick
    if args.dry_run or args.vision or args.suggest_queries:
        return _fail("--pick downloads a candidate the last scrape kept; it does not search, "
                     "so it takes no --dry-run, --vision or --suggest-queries")
    ws = Path(args.workspace)
    if not ws.is_dir():
        return _fail(f"no workspace at {ws}")
    try:
        picks = [parse_pick(spec) for spec in args.pick]
    except PickError as exc:
        return _fail(str(exc))
    code = 0
    for key, n in picks:
        try:
            out, notes = pick(ws, key, n, http=net.Http(), max_mb=args.max_mb)
        except PickError as exc:
            print(exc, file=sys.stderr)
            code = max(code, 1)
            continue
        except ToolMissing as exc:
            return _fail(str(exc), 2)
        c = out.ranked[0].candidate
        if out.ok:
            e = out.entry
            print(f"pick {key}={n}: saved {out.wanted.src}: {e['source']} {e['sourceId']!r} "
                  f"{c.title[:60]!r}, {e['bytes'] / 1e6:.2f} MB")
            print(f"  license {e['license']} ({e['licenseBasis']}); attribution: {e['attribution']}")
        else:
            for err in out.errors[-3:]:
                print(f"  failed: {err}")
            print(f"pick {key}={n}: {out.wanted.src} NOT replaced (the previous file is untouched)",
                  file=sys.stderr)
            code = max(code, 2 if out.errors else 1)
        for note in notes:
            print(f"  note: {note}")
    return code


def scrape_command(args) -> int:
    from .net import Http
    from .scrape import RANKING, ScrapeError, scrape
    from .sources import SOURCES
    if args.max_mb <= 0:
        return _fail("--max-mb must be positive")
    if args.pick:
        return pick_command(args)
    names = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [n for n in names if n not in SOURCES]
    if unknown or not names:
        return _fail(f"--sources: unknown {unknown or 'empty list'}; choose from {sorted(SOURCES)}")
    scorer = None
    if args.vision or args.suggest_queries:
        from . import vision
        reason = vision.unavailable_reason()
        if reason:
            flag = "--vision" if args.vision else "--suggest-queries"
            return _fail(f"{flag} needs Claude: {reason}", 2)
    if args.vision:
        scorer = vision.ClaudeVision()
    query_failed = None
    if args.suggest_queries:
        from . import queries
        try:
            got, query_failed = queries.suggest(Path(args.workspace).resolve(), queries.ClaudeQueries())
        except BriefError as exc:
            _print_errors("brief", exc.errors)
            return 1
        if query_failed:
            print(f"suggest-queries failed ({query_failed}); searching from the descriptions", file=sys.stderr)
        elif got:
            print(f"suggested queries ({len(got)}), saved to cache/queries.json:")
            for src, q in got.items():
                print(f"  {src}: {q}")
        else:
            print("suggest-queries: every wanted visual already has a query in brief.json")
    try:
        outcomes, notes, manifest = scrape(args.workspace, http=Http(), source_names=names, max_mb=args.max_mb,
                                           min_score=args.min_score, dry_run=args.dry_run,
                                           candidates=not args.no_candidates, vision=scorer)
    except ScrapeError as exc:
        return _fail(str(exc))
    except BriefError as exc:
        _print_errors("brief", exc.errors)
        return 1
    except ToolMissing as exc:
        return _fail(str(exc), 2)
    if not outcomes:
        for note in notes:
            print(f"  note: {note}")
        print("scrape: every visual in brief.json already has a file; nothing to fetch")
        return 0
    print(f"scrape: {len(outcomes)} wanted visual(s); sources {', '.join(names)}; size cap {args.max_mb:g} MB")
    print(f"  ranking is keyword-based, not CLIP: {RANKING}")
    missing, net_failed = 0, False
    vision_failed = next((o.vision_error for o in outcomes if o.vision_error), None)
    for o in outcomes:
        w = o.wanted
        origin = {"brief": "query from brief.json", "suggested": "suggested query, cache/queries.json"}
        print(f"\n  {w.src} [{w.kind}] search words: {' '.join(w.search_words())}"
              f"  ({origin.get(w.query_from, 'from the description')})")
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
            vis = o.vision.get((c.source, c.id))
            print(f"    {k}. {s.score:.3f}  {c.source} {c.id!r}: {c.title[:60]!r}  [{c.license.name}]  "
                  f"matched: {', '.join(w.surface(t) for t in s.matched) or '-'}"
                  + (f"  penalised: {', '.join(s.penalties)}" if s.penalties else "")
                  + (f"  vision {vis[0]}/10: {vis[1]}" if vis else ""))
        if o.vision_error:
            print(f"    vision failed: {o.vision_error}; kept the lexical order")
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
    for note in notes:
        print(f"  note: {note}")
    if args.dry_run:
        print("\ndry run: no assets downloaded" + ("" if args.no_candidates else " (candidate previews only)"))
        return 2 if vision_failed or query_failed else 0
    if manifest:
        print(f"\nmanifest: {manifest}")
    if missing:
        print(f"{missing} visual(s) still missing; montage plan needs every file", file=sys.stderr)
        return 2 if net_failed or vision_failed or query_failed else 1
    print(f"next: montage plan \"{Path(args.workspace) / 'brief.json'}\"")
    if vision_failed:
        print(f"vision re-ranking failed ({vision_failed}); assets were chosen lexically", file=sys.stderr)
        return 2
    return 2 if query_failed else 0


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
    plan_path = _workspace_plan(args.workspace)
    plan = _load(plan_path)
    if isinstance(plan, int):
        return plan
    try:
        sheet = make_contact_sheet(plan, args.output, plan_path=plan_path)
    except ContactSheetError as exc:
        return _fail(str(exc), 2)
    except ToolMissing as exc:
        return _fail(str(exc), 2)
    print(f"contact sheet: {sheet} ({len(plan.video.clips)} clips)")
    print(f"next: review it, then montage approve --workspace \"{plan.workspace}\"")
    return 0


def preview_command(args) -> int:
    """`montage preview <ws|plan>`: a fast 480p animatic of the plan as it is, approved or not."""
    import time
    from .profiles import preview_spec
    from .render import default_preview, preview
    plan_path = _workspace_plan(args.workspace)
    plan = _load(plan_path)
    if isinstance(plan, int):
        return plan
    spec = preview_spec(plan)
    out = Path(args.output) if args.output else default_preview(plan)
    if out.suffix.lower() != spec.ext:
        return _fail(f"the preview is an {spec.ext} file; -o {out.name} would not match")
    meta = plan.meta
    print(f"preview: {spec.width}x{spec.height} @ {spec.fps} fps, libx264 ultrafast, one-pass loudness; "
          f"{meta.duration_frames / meta.fps:.1f}s"
          + (f"; captions {'off' if args.no_captions else 'burned in'}" if plan.captions else ""))
    started = time.monotonic()
    try:
        out = preview(plan, plan_path, out, normalize=not args.skip_normalize, captions=not args.no_captions)
    except (RenderError, ToolMissing) as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"rendered: {out} in {time.monotonic() - started:.1f}s")
    code = 0
    if not args.skip_qc:
        checks = run_qc(out, plan, spec, loudness=False)
        for c in checks:
            print(f"  {'PASS' if c.ok else 'FAIL'}  {c.name}: {c.detail}")
        code = 0 if all(c.ok for c in checks) else 3
    default = out.resolve() == default_preview(plan).resolve()
    status = approval_mod.check(plan, plan_path)
    if status.approved:
        print("approval: this plan is already approved")
    elif default:
        print(f"next: watch it, then montage approve --workspace \"{plan.workspace}\"")
    else:
        print(f"note: montage approve only accepts the preview at {default_preview(plan)}; "
              f"run without -o to approve from a preview")
    return code


def approve_script_command(args) -> int:
    from .planner import ScriptRejected, approve_script
    ws = Path(args.workspace)
    if ws.name == "brief.json" or ws.name == "script.json":
        ws = ws.parent
    try:
        path = approve_script(ws, args.note)
    except ScriptRejected as exc:
        return _script_rejected(exc)
    except BriefError as exc:
        _print_errors("brief", exc.errors)
        return 1
    except PipelineError as exc:
        _print_errors("pipeline", exc.errors)
        return 1
    except ToolMissing as exc:
        return _fail(str(exc), 2)
    print(f"script approved: {path}")
    print(f"next: montage plan \"{ws / 'brief.json'}\"")
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
    print(f"review it: montage contact-sheet \"{ws}\" (or montage preview \"{ws}\"), then "
          f"montage approve --workspace \"{ws}\" (or render anyway with --force)", file=sys.stderr)
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


def publish_pack_command(args) -> int:
    from .publish import PACK_DIR, PACK_NAME, PublishRefused, build, render_thumbnails, with_thumbnails, write
    ws = Path(args.workspace)
    if ws.is_file():
        ws = ws.parent
    if not (ws / "brief.json").is_file():
        return _fail(f"no brief.json in {ws}; publish-pack needs a planned, approved and rendered workspace")
    existing = ws / PACK_DIR / PACK_NAME
    if existing.exists() and not args.force:   # before the model call and the grabs (they replace the old ones)
        return _fail(f"{existing} already exists; re-run with --force to replace it")
    drafter = None
    if args.draft:
        from . import vision
        from .drafts import ClaudeDrafter
        reason = vision.unavailable_reason()
        if reason:
            return _fail(f"--draft needs Claude: {reason}", 2)
        drafter = ClaudeDrafter()
    try:
        pack = build(ws, drafter=drafter)
        made, failed = render_thumbnails(pack)
        pack = with_thumbnails(pack, made, failed)
        path = write(pack, force=args.force)
    except PublishRefused as exc:
        print("refusing to write the publish pack:", file=sys.stderr)
        for err in exc.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    except FileExistsError as exc:
        return _fail(str(exc))
    except BriefError as exc:
        _print_errors("brief", exc.errors)
        return 1
    except PlanError as exc:
        _print_errors("plan", exc.errors)
        return 1
    except ToolMissing as exc:
        return _fail(str(exc), 2)
    print(f"publish pack: {path}")
    print(f"  video: {pack.render.relative_to(path.parents[1]).as_posix()}; {len(pack.credits)} credited asset(s); "
          f"{len(pack.chapters)} chapter(s); {len(pack.tags)} tag(s); "
          f"{len(made)} of {len(pack.thumbs)} thumbnail candidate(s)")
    for n, why in failed.items():
        print(f"  warning: thumbnail {n} not rendered: {why}", file=sys.stderr)
    for note in pack.notes:
        print(f"  note: {note}")
    minutes = args.hands_on_min
    if minutes is None and sys.stdin.isatty():
        minutes = _ask_minutes()
    from .production import build_summary
    _save_summary(ws, build_summary(ws, render=pack.render, hands_on_min=minutes), args.ledger)
    if minutes is None:
        print(f"  note: hands-on minutes not recorded; add them with montage stats \"{ws}\" --hands-on-min N")
    print("next: read it, edit the description, chapter titles and tags, then upload; after 7 and 28 days, "
          f"montage stats \"{ws}\" --day 7 --views N --avg-view-seconds S")
    if pack.draft_error:
        print(f"--draft failed ({pack.draft_error}); the pack was written with the brief's text", file=sys.stderr)
        return 2
    return 0


def _ask_minutes() -> float | None:
    try:
        raw = input("hands-on minutes on this video, brief to publish pack (Enter to skip): ").strip()
    except EOFError:
        return None
    try:
        value = float(raw) if raw else None
    except ValueError:
        print(f"  '{raw}' is not a number; skipped", file=sys.stderr)
        return None
    return value if value is None or value >= 0 else None


def _save_summary(ws: Path, summary: dict, ledger: Path | None) -> None:
    """Write <ws>/publish/production_summary.json and mirror it into the ledger (when there is one)."""
    from . import production
    print(f"production summary: {production.write_summary(ws, summary)}")
    for line in production.describe(summary):
        print(f"  {line}")
    if ledger:
        print(f"  ledger: {production.upsert_ledger(ledger, summary)}")


def stats_command(args) -> int:
    """`montage stats <ws>`: show the production summary; record hands-on minutes, URL and YouTube stats."""
    from . import production
    ws = Path(args.workspace)
    if ws.is_file():
        ws = ws.parent
    if not (ws / "brief.json").is_file():
        return _fail(f"no brief.json in {ws}; montage stats needs a workspace")
    if (args.views is None) != (args.day is None):
        return _fail("--day and --views go together (e.g. --day 7 --views 120 --avg-view-seconds 41)")
    if args.avg_view_seconds is not None and args.day is None:
        return _fail("--avg-view-seconds needs --day and --views")
    for name in ("views", "avg_view_seconds", "hands_on_min"):
        value = getattr(args, name)
        if value is not None and value < 0:
            return _fail(f"--{name.replace('_', '-')} cannot be negative")
    changed = any(v is not None for v in (args.day, args.url, args.hands_on_min))
    summary = production.build_summary(ws, hands_on_min=args.hands_on_min)
    if args.url:
        summary["url"] = args.url
    if args.day is not None:
        production.record_youtube(summary, args.day, args.views, args.avg_view_seconds)
    if not changed:   # show only; nothing is written
        saved = production.summary_path(ws).is_file()
        print(f"production summary: {production.summary_path(ws)}" if saved
              else "no production summary yet (publish-pack writes one); derived from the workspace now:")
        for line in production.describe(summary):
            print(f"  {line}")
        return 0
    _save_summary(ws, summary, args.ledger)
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
    p.add_argument("--approve-script", action="store_true",
                   help="approve script.json as it is now (sha256 in script_approval.json) and go on to narration")
    p.add_argument("--no-script-gate", action="store_true",
                   help="do not stop for script approval (automation); a model draft over the hard limits "
                        "is still rejected")

    asc = sub.add_parser("approve-script", help="approve the workspace's script.json for narration "
                                                "(records its sha256 in script_approval.json)")
    asc.add_argument("workspace", help="workspace dir (or its brief.json)")
    asc.add_argument("--note", help="optional note stored in script_approval.json")

    s = sub.add_parser("scrape", help="fetch the brief's missing visuals from Commons / NASA / Archive.org "
                                      "(licensed only) and record them in manifest.json")
    s.add_argument("workspace")
    s.add_argument("--sources", default="commons,nasa,archive", help="comma-separated, in tie-break order")
    s.add_argument("--max-mb", type=float, default=50, help="size cap per download (default 50)")
    s.add_argument("--min-score", type=float, default=0.25, help="lowest lexical score to accept (0-1)")
    s.add_argument("--dry-run", action="store_true",
                   help="search and rank; download only the candidate previews, no assets")
    s.add_argument("--no-candidates", action="store_true",
                   help="skip the previews, cache/candidates.json and cache/candidates.png")
    s.add_argument("--pick", action="append", metavar="ASSET=N",
                   help="download candidate N (1-3, from the last scrape's candidates sheet) for ASSET "
                        "(asset id, file name or src) and record it in manifest.json; repeatable")
    s.add_argument("--suggest-queries", action="store_true",
                   help="have Claude write an archive search query for every wanted visual without a "
                        "brief.json query (saved to cache/queries.json and reused; needs Claude credentials)")
    s.add_argument("--vision", action="store_true",
                   help="re-rank the top candidates with Claude vision from their previews "
                        "(needs Claude credentials; exit 2 without them)")

    cs = sub.add_parser("contact-sheet", help="render contact_sheet.png: one frame per visual clip")
    cs.add_argument("workspace", help="workspace dir (uses its scene_plan.json) or a scene_plan.json")
    cs.add_argument("-o", "--output", help="default: <workspace>/contact_sheet.png")

    pv = sub.add_parser("preview", help="fast 480p animatic of scene_plan.json (approved or not) with captions "
                                        "and narration -> <workspace>/cache/preview.mp4")
    pv.add_argument("workspace", help="workspace dir (uses its scene_plan.json) or a scene_plan.json")
    pv.add_argument("-o", "--output", help="default: <workspace>/cache/preview.mp4 (the one approve accepts)")
    pv.add_argument("--no-captions", action="store_true", help="do not burn in the caption track")
    pv.add_argument("--skip-normalize", action="store_true", help="read the original assets as they are")
    pv.add_argument("--skip-qc", action="store_true", help="skip the light QC (frames, size, fps, codecs)")

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

    pp = sub.add_parser("publish-pack", help="write <workspace>/publish/youtube.md: title, description with "
                                             "chapters and full asset credits, tags, AI-narration disclosure")
    pp.add_argument("workspace", help="workspace dir (needs an approved plan, a render and manifest.json)")
    pp.add_argument("--force", action="store_true", help="replace an existing publish/youtube.md")
    pp.add_argument("--draft", action="store_true",
                    help="have Claude draft the title, description intro, chapter titles and tags from the "
                         "approved script (needs Claude credentials; exit 2 without them)")
    pp.add_argument("--hands-on-min", type=float, metavar="N",
                    help="minutes you spent hands-on, brief to publish pack (asked on a terminal when omitted)")

    st = sub.add_parser("stats", help="show a video's production summary; record hands-on minutes, its URL "
                                      "and YouTube stats (mirrored into the project's production ledger)")
    st.add_argument("workspace")
    st.add_argument("--hands-on-min", type=float, metavar="N", help="minutes spent hands-on (replaces the record)")
    st.add_argument("--url", help="the published video's URL")
    st.add_argument("--day", type=int, choices=[7, 28], help="days since publishing the stats were read at")
    st.add_argument("--views", type=int, help="views at --day")
    st.add_argument("--avg-view-seconds", type=float, metavar="S", help="average view duration at --day")

    m = sub.add_parser("mcp", help="MCP server on stdio: timeline edit tools over the workspace's "
                                   "scene_plan.json (every write re-validated)")
    m.add_argument("workspace", help="workspace dir (uses its scene_plan.json) or a scene_plan.json")
    args = parser.parse_args(argv)
    # The ledger is versioned project state: a real command line writes it, a test only when it names one.
    ledger = os.environ.get("MONTAGE_LEDGER")
    args.ledger = Path(ledger) if ledger else (PROJECT_ROOT.joinpath(*LEDGER) if argv is None else None)
    return _logged(args, sys.argv[1:] if argv is None else list(argv))


def _command_workspace(args) -> Path | None:
    """The workspace a command works on, for production_log.jsonl; None when it has none yet."""
    if args.cmd == "init":
        return Path(args.dir)
    if args.cmd == "plan":
        if args.topic is not None:
            return Path(args.workspace) if args.workspace else Path("workspace") / slug(args.topic)
        return Path(args.brief).parent if args.brief else None
    if args.cmd in ("contact-sheet", "preview", "mcp"):
        return _workspace_plan(args.workspace).parent
    if args.cmd in ("validate", "normalize", "render"):
        return Path(args.plan).parent
    ws = Path(args.workspace)
    return ws.parent if ws.is_file() else ws


def _logged(args, argv: list[str]) -> int:
    """Run the command and append it to <ws>/production_log.jsonl, whatever the outcome."""
    import time
    from datetime import datetime, timezone
    from .production import log_command
    started, t0 = datetime.now(timezone.utc), time.monotonic()
    code: int | str = "exception"
    try:
        code = _dispatch(args)
        return code
    except KeyboardInterrupt:
        code = "interrupted"
        raise
    finally:
        try:
            ws = _command_workspace(args)
        except Exception:  # noqa: BLE001 -- measurement must never fail the command it measures
            ws = None
        if ws is not None:
            log_command(ws, args.cmd, argv, started, time.monotonic() - t0, code)


def _dispatch(args) -> int:
    if args.cmd == "stats":
        return stats_command(args)
    if args.cmd == "mcp":
        return mcp_command(args)
    if args.cmd == "init":
        return init_command(args)
    if args.cmd == "plan":
        return plan_command(args)
    if args.cmd == "scrape":
        return scrape_command(args)
    if args.cmd == "approve-script":
        return approve_script_command(args)
    if args.cmd == "contact-sheet":
        return contact_sheet_command(args)
    if args.cmd == "preview":
        return preview_command(args)
    if args.cmd == "approve":
        return approve_command(args)
    if args.cmd == "publish-pack":
        return publish_pack_command(args)

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
