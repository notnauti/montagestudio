"""`montage publish-pack <ws>`: <ws>/publish/youtube.md from what the workspace already records.

Inputs: brief.json (title, premise, voice, recipe), script.json (beats -> chapter titles),
scene_plan.json (beat timings), approval.json (the storyboard gate), manifest.json (provenance of
every sourced asset) and the newest file in renders/. Nothing is asked of a model unless `--draft`
(drafts.py) has Claude write the title, description intro, chapter titles and tags.

Refused (PublishRefused, exit 1), with every reason listed:
  - the plan is not approved as it is now (same check as `montage render`);
  - no render, or the newest render is older than scene_plan.json (it shows a different cut);
  - an asset the plan uses (narration wavs aside: montage made them) has no manifest entry, or the
    entry lacks the source URL, author or license, or its license is not one scrape accepts (PD /
    CC0 / CC BY / CC BY-SA, or NASA's source policy for a NASA item), or a CC BY* license has no
    license URL, or the file is not the one the manifest describes (sha256 differs).

Attribution values are copied exactly as manifest.json records them. Chapters follow YouTube's
rules: the first at 0:00, each at least 10 s (short beats are merged into the chapter before),
at least 3, else the list is left out with a note. Chapter titles are the first clause of the
chapter's first beat, a draft to edit. The pack never overwrites publish/youtube.md without
`force`, and never touches <ws>/youtube.md (a hand-written one stays the person's).

Thumbnail candidates: up to THUMB_COUNT frames, one per source file, spread over the video, each
at the middle of the span where its clip is alone on screen and at least THUMB_MARGIN frames from
any cut or crossfade (the JWST pack's hand-picked frame fell inside a crossfade: a double exposure).
`render_thumbnails` grabs them through the renderer's own filter chain, captions off, as 1280 px
JPEGs under YouTube's 2 MB limit in publish/thumb_candidates/. They are suggestions: a failed grab
is listed in the pack, never a refusal.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from . import approval as approval_mod
from .brief import Brief, load_brief
from .plan import Plan, load_plan
from .probe import probe as default_probe
from .script import Script
from .sources import STOPWORDS, parse_license

PACK_DIR = "publish"
PACK_NAME = "youtube.md"
RENDER_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
CHAPTER_MIN_SECONDS = 10
CHAPTER_MIN_COUNT = 3
CHAPTER_TITLE_WORDS = 8
MAX_TAGS = 15
TAGS_CHARS = 500         # YouTube's limit on the tags field
TITLE_CHARS = 100        # YouTube's limit on a title
DESCRIPTION_CHARS = 5000
# words a production brief uses about the video itself, not its subject: never tags
BRIEF_FILLER = frozenset("""short long imaginative educational audience viewers viewer general beginners
video videos documentary explainer promo like need needs made make makes first last little brief""".split())
_NARRATION_FILE = re.compile(r"^beat_\d{2,}_[0-9a-f]{10}\.wav$")
THUMB_DIR = "thumb_candidates"
THUMB_COUNT = 3
THUMB_MARGIN = 6           # frames clear of a cut or a crossfade
THUMB_WIDTH = 1280         # YouTube's recommended thumbnail width
THUMB_MAX_BYTES = 2_000_000
THUMB_QUALITY = (2, 4, 7, 10)   # mjpeg -q:v steps tried until the file fits THUMB_MAX_BYTES


class PublishRefused(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class Credit:
    src: str
    entry: dict


@dataclass(frozen=True)
class Chapter:
    seconds: int
    title: str


@dataclass(frozen=True)
class Thumb:
    frame: int
    clip_id: str
    src: Path


@dataclass(frozen=True)
class Pack:
    path: Path
    text: str
    render: Path
    credits: tuple[Credit, ...]
    chapters: tuple[Chapter, ...]
    tags: tuple[str, ...]
    notes: tuple[str, ...]
    plan: Plan | None = None
    thumbs: tuple[Thumb, ...] = ()
    draft_model: str | None = None    # set when Claude drafted the viewer-facing text
    draft_error: str | None = None    # set when --draft was asked for and failed


# ── checks ───────────────────────────────────────────────────────────────────

def _generated(path: Path, ws: Path) -> bool:
    """Narration wavs montage synthesised: no third-party source to credit."""
    return path.parent == ws / "assets" / "narration" and bool(_NARRATION_FILE.match(path.name))


def used_assets(plan: Plan) -> list[Path]:
    return [p for p in approval_mod.asset_paths(plan) if not _generated(p, plan.workspace)]


def load_manifest(ws: Path) -> dict[Path, tuple[str, dict]]:
    """manifest.json -> {absolute asset path: (src as recorded, entry)}; {} when absent."""
    path = ws / "manifest.json"
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assets = doc["assets"]
        if not isinstance(assets, dict):
            raise TypeError("'assets' must be an object")
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PublishRefused([f"{path}: unreadable ({exc})"]) from exc
    return {(ws / src).resolve(): (src, e) for src, e in assets.items() if isinstance(e, dict)}


def _text(entry: dict, key: str) -> str | None:
    v = entry.get(key)
    return v.strip() if isinstance(v, str) and v.strip() else None


def license_problem(entry: dict) -> str | None:
    """None when the recorded license is one scrape would accept, else why not."""
    name, url, basis, source = (entry.get("license"), entry.get("licenseUrl"), entry.get("licenseBasis"),
                                entry.get("source"))
    if basis == "source-policy":
        return None if source == "nasa" else f"license basis 'source-policy' is only accepted for NASA items, not {source!r}"
    lic = parse_license(name if isinstance(name, str) else None, url if isinstance(url, str) else None)
    if not lic.usable:
        return f"license {name!r} is not allowed ({lic.reason or 'not PD / CC0 / CC BY / CC BY-SA'})"
    if lic.name and lic.name.startswith("CC BY") and not _text(entry, "licenseUrl"):
        return f"{lic.name} requires a link to the license, and manifest.json records no licenseUrl"
    return None


def check_credits(plan: Plan, manifest: dict[Path, tuple[str, dict]], sha=approval_mod.sha256) -> tuple[list[Credit], list[str]]:
    ws = plan.workspace
    credits, errs = [], []
    for path in used_assets(plan):
        rel = path.relative_to(ws).as_posix() if path.is_relative_to(ws) else path.as_posix()
        hit = manifest.get(path)
        if hit is None:
            errs.append(f"{rel}: no manifest.json entry (where it came from, author, license); "
                        f"source it with montage scrape or add the entry by hand")
            continue
        src, e = hit
        missing = [label for key, label in (("pageUrl", "source URL (pageUrl)"), ("attribution", "author (attribution)"),
                                            ("license", "license")) if not _text(e, key)]
        if missing:
            errs.append(f"{rel}: manifest.json entry lacks {', '.join(missing)}")
            continue
        problem = license_problem(e)
        if problem:
            errs.append(f"{rel}: {problem}")
            continue
        if _text(e, "sha256") and sha(path) != e["sha256"]:
            errs.append(f"{rel}: the file is not the one manifest.json describes (sha256 differs); its credit "
                        f"would be for another file. Re-scrape it or update the entry")
            continue
        credits.append(Credit(src, e))
    return credits, errs


def latest_render(ws: Path) -> Path | None:
    d = ws / "renders"
    files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in RENDER_EXTS] if d.is_dir() else []
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


# ── content ──────────────────────────────────────────────────────────────────

def chapter_title(narration: str) -> str:
    """First sentence; when that is long, its first clause; capped at CHAPTER_TITLE_WORDS words."""
    first = re.split(r"(?<=[.!?])\s+", narration.strip(), maxsplit=1)[0]
    words = first.split()
    if len(words) > CHAPTER_TITLE_WORDS:
        clause = re.split(r"[,;:—–]", first, maxsplit=1)[0].split()
        words = clause if 2 <= len(clause) <= CHAPTER_TITLE_WORDS else words[:CHAPTER_TITLE_WORDS] + ["…"]
    return " ".join(words).rstrip(".,;:!?—– ") or narration.strip()[:40]


def _beat_starts(raw_plan: dict, n_beats: int) -> list[int] | None:
    """Timeline frame where each beat starts: the narration clips (one per beat) as planned."""
    tracks = raw_plan.get("tracks") or []
    voice = next((t for t in tracks if t.get("id") == "narration" and t.get("type") == "audio"), None)
    if voice and len(voice.get("clips", [])) == n_beats:
        return [c["startFrame"] for c in voice["clips"]]
    video = next((t for t in tracks if t.get("type") == "video"), None)
    if video and len(video.get("clips", [])) == n_beats:
        return [c["startFrame"] for c in video["clips"]]
    return None


def chapters(raw_plan: dict, script: Script, fps: int, duration_frames: int) -> tuple[list[Chapter], str | None]:
    starts = _beat_starts(raw_plan, len(script.beats))
    if starts is None:
        return [], (f"no chapters: scene_plan.json no longer has one narration clip per beat "
                    f"({len(script.beats)} beats); add them by hand")
    secs = [0] + [f // fps for f in starts[1:]]
    total = duration_frames / fps
    out: list[Chapter] = []
    for i, s in enumerate(secs):
        if out and s - out[-1].seconds < CHAPTER_MIN_SECONDS:
            continue  # this beat joins the chapter before it
        out.append(Chapter(s, chapter_title(script.beats[i].narration)))
    while len(out) > 1 and total - out[-1].seconds < CHAPTER_MIN_SECONDS:
        out.pop()  # a short last chapter joins the one before
    if len(out) < CHAPTER_MIN_COUNT:
        return [], (f"no chapters: YouTube needs at least {CHAPTER_MIN_COUNT} chapters of {CHAPTER_MIN_SECONDS} s "
                    f"or more, and this {total:.0f} s video gives {len(out)}")
    return out, None


def chapter_narration(raw_plan: dict, script: Script, chs: list[Chapter], fps: int) -> list[tuple[str, list[str]]]:
    """(timestamp, narration of every beat the chapter merged) per chapter, for the drafter."""
    starts = _beat_starts(raw_plan, len(script.beats))
    if not chs or starts is None:
        return []
    secs = [0] + [f // fps for f in starts[1:]]
    first = [secs.index(c.seconds) for c in chs]
    ends = first[1:] + [len(script.beats)]
    return [(stamp(c.seconds), [b.narration for b in script.beats[a:z]]) for c, a, z in zip(chs, first, ends)]


def stamp(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _topic(brief: Brief) -> str | None:
    """The words every asset description shares, in the first description's order and spelling."""
    if len(brief.assets) < 2:
        return None
    sets = [set(re.findall(r"[a-z0-9]+", a.description.lower())) for a in brief.assets]
    shared = set.intersection(*sets) - STOPWORDS
    words = [w for w in re.findall(r"[A-Za-z0-9]+", brief.assets[0].description) if w.lower() in shared]
    seen, out = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return " ".join(out) if len(out) >= 2 else None


def tags(brief: Brief) -> list[str]:
    """Title, topic, recipe, then the brief's own content words in order, minus words about the
    video itself (a draft: edit freely)."""
    out: list[str] = []

    def add(t: str | None) -> None:
        t = (t or "").strip().strip(",")
        if t and t.lower() not in {x.lower() for x in out} and len(out) < MAX_TAGS \
                and len(", ".join(out + [t])) <= TAGS_CHARS:
            out.append(t)

    add(brief.title)
    add(_topic(brief))
    add(brief.recipe)
    covered = {w.lower() for t in out for w in t.split()}
    for w in re.findall(r"[A-Za-z][A-Za-z0-9'-]+|\d{4}", brief.text):
        low = w.lower()
        if low in STOPWORDS or low in BRIEF_FILLER or low in covered or len(low) < 4:
            continue
        covered.add(low)
        add(w)
    return out


def _credit_lines(c: Credit) -> list[str]:
    e = c.entry
    title = _text(e, "title") or Path(c.src).name
    url = _text(e, "licenseUrl")
    return [f"- {title}",
            f"  Author: {e['attribution']}",
            f"  Source: {e['pageUrl']}",
            f"  License: {e['license']}" + (f" — {url}" if url else " (no license URL recorded)")]


def disclosure(brief: Brief, ws: Path) -> str:
    line = (f"Disclosure: the narration is synthetic speech generated with Piper TTS (voice {brief.voice}); "
            f"no human voice is used.")
    record = ws / "script_approval.json"
    if record.is_file():
        try:
            rec = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rec = {}
        writer = rec.get("writer") if isinstance(rec, dict) else None
        if writer and rec.get("approvedSha256"):
            line += f" The script was drafted by an AI model ({writer}) and reviewed by a person before narration."
        elif writer:
            line += f" The script was drafted by an AI model ({writer})."
    return line


def thumbnail_frames(plan: Plan, count: int = THUMB_COUNT, margin: int = THUMB_MARGIN) -> list[Thumb]:
    """One frame per source file, spread over the video, each mid-way through the span where its clip is
    alone on screen and `margin` frames clear of any cut or crossfade. Fewer when fewer clips qualify."""
    clips = sorted(plan.video.clips, key=lambda c: c.start_frame)
    seen: set[Path] = set()
    eligible: list[Thumb] = []
    for i, c in enumerate(clips):
        lo = c.start_frame + (clips[i - 1].crossfade_frames if i else 0) + margin  # the previous fade is over
        hi = c.end_frame - (c.crossfade_frames if i < len(clips) - 1 else 0) - margin - 1  # the next not begun
        hi = min(hi, plan.meta.duration_frames - 1)
        if lo <= hi and c.src not in seen:
            seen.add(c.src)
            eligible.append(Thumb((lo + hi) // 2, c.id, c.src))
    if len(eligible) <= count:
        return eligible
    step = len(eligible) / count
    return [eligible[int(step * k + step / 2)] for k in range(count)]


def _to_jpeg(png: Path, out: Path) -> None:
    from .probe import tool
    for q in THUMB_QUALITY:
        r = subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(png),
                            "-q:v", str(q), "-frames:v", "1", "-update", "1", str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not out.is_file():
            raise RuntimeError(f"jpeg encode failed: {r.stderr.strip()[-300:]}")
        if out.stat().st_size <= THUMB_MAX_BYTES:
            return
    raise RuntimeError(f"still {out.stat().st_size / 1e6:.1f} MB at the lowest quality tried; "
                       f"YouTube allows {THUMB_MAX_BYTES / 1e6:g} MB")


def render_thumbnails(pack: Pack) -> tuple[dict[int, Path], dict[int, str]]:
    """Grab pack.thumbs into publish/thumb_candidates/thumb_<n>.jpg (old ones removed first).
    Returns ({n: path}, {n: why it failed}); never raises for a failed frame."""
    from .frame_context import FrameError, grab
    from .probe import ToolMissing
    out_dir = pack.path.parent / THUMB_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("thumb_*.jpg"):
        old.unlink()
    made, failed = {}, {}
    for n, t in enumerate(pack.thumbs, 1):
        png = pack.plan.workspace / "cache" / "inspect" / f"thumb_{n}.png"
        out = out_dir / f"thumb_{n}.jpg"
        try:
            grab(pack.plan, t.frame, png, captions=False, max_width=THUMB_WIDTH)
            _to_jpeg(png, out)
            made[n] = out
        except (FrameError, ToolMissing, RuntimeError, OSError) as exc:
            out.unlink(missing_ok=True)
            failed[n] = str(exc).splitlines()[0]
    return made, failed


def with_thumbnails(pack: Pack, made: dict[int, Path], failed: dict[int, str]) -> Pack:
    """The pack with its '## Thumbnail' section (before the upload settings)."""
    ws, fps = pack.plan.workspace, pack.plan.meta.fps
    lines = ["## Thumbnail",
             f"Candidates: one frame per source file, at least {THUMB_MARGIN} frames clear of any cut or "
             f"crossfade, captions off, {THUMB_WIDTH} px JPEG. Pick one, or make your own.", ""]
    for n, t in enumerate(pack.thumbs, 1):
        src = t.src.relative_to(ws).as_posix() if t.src.is_relative_to(ws) else t.src.name
        where = f"{stamp(t.frame // fps)} (frame {t.frame}), clip {t.clip_id}, {src}"
        if n in made:
            lines.append(f"- `{made[n].relative_to(ws).as_posix()}` — {where}")
        else:
            lines.append(f"- thumb_{n}: not rendered ({failed.get(n, 'skipped')}) — {where}")
    if not pack.thumbs:
        lines.append("- none: no clip is alone on screen long enough; make one by hand")
    section = "\n".join(lines) + "\n\n"
    return replace(pack, text=pack.text.replace("## Upload settings", section + "## Upload settings", 1))


def build(workspace: str | Path, probe=None, now: datetime | None = None, drafter=None) -> Pack:
    """Everything the pack needs, checked; PublishRefused lists every reason it cannot be written.
    `drafter` (drafts.ClaudeDrafter) replaces title, description intro, chapter titles and tags with a
    model draft; when it fails the deterministic text stays and Pack.draft_error says why."""
    from .drafts import DraftError
    from .planner import ScriptRejected, _read_script
    probe = probe or default_probe
    ws = Path(workspace).resolve()
    brief = load_brief(ws / "brief.json", probe=probe)
    errs: list[str] = []
    script_path = ws / "script.json"
    if not script_path.is_file():
        raise PublishRefused([f"no script.json in {ws}; run montage plan first"])
    try:
        script = _read_script(script_path, brief)
    except ScriptRejected as exc:
        raise PublishRefused(exc.errors) from exc
    plan_path = ws / "scene_plan.json"
    if not plan_path.is_file():
        raise PublishRefused([f"no scene_plan.json in {ws}; run montage plan first"])
    plan = load_plan(plan_path, probe=probe)
    raw_plan = json.loads(plan_path.read_text(encoding="utf-8"))

    status = approval_mod.check(plan, plan_path)
    if not status.approved:
        errs += [f"not approved: {p}" for p in status.problems]
        errs.append(f"review contact_sheet.png or montage preview, then montage approve --workspace \"{ws}\" (and render again)")
    render = latest_render(ws)
    if render is None:
        errs.append(f"no render in {ws / 'renders'}; run montage render \"{plan_path}\" first")
    elif render.stat().st_mtime < plan_path.stat().st_mtime:
        errs.append(f"the newest render ({render.name}) is older than scene_plan.json, so it shows a "
                    f"different cut; render again")
    credits, credit_errs = check_credits(plan, load_manifest(ws))
    errs += credit_errs
    if errs:
        raise PublishRefused(errs)

    notes: list[str] = []
    info = probe(render)
    meta = plan.meta
    chs, why = chapters(raw_plan, script, meta.fps, meta.duration_frames)
    if why:
        notes.append(why)
    tag_list = tags(brief)
    title, intro, draft, draft_error = brief.title, brief.text, None, None
    if drafter is not None:
        try:
            draft = drafter(brief.title, brief.text, [b.narration for b in script.beats],
                            chapter_narration(raw_plan, script, chs, meta.fps))
        except DraftError as exc:
            draft_error = str(exc)
            notes.append(f"draft failed ({exc}); the pack uses the brief's text")
    if draft:
        title, intro, tag_list = draft.title, draft.description, list(draft.tags)
        chs = [Chapter(c.seconds, t) for c, t in zip(chs, draft.chapter_titles)]
        notes.append(f"title, description intro, chapter titles and tags drafted by {draft.model}: "
                     f"check every fact against the script")
    nasa = any(c.entry.get("source") == "nasa" or c.entry.get("licenseBasis") == "source-policy" for c in credits)

    desc = [intro, ""]
    if chs:
        desc += ["Chapters:"] + [f"{stamp(c.seconds)} {c.title}" for c in chs] + [""]
    desc.append("Credits:")
    for c in credits:
        desc += _credit_lines(c)
    if not credits:
        desc.append("- All visuals are original to this video.")
    if nasa:
        desc += ["", "NASA does not endorse this video."]
    desc += ["", disclosure(brief, ws), "Made with MontageStudio."]
    description = "\n".join(desc)

    if len(title) > TITLE_CHARS:
        notes.append(f"title is {len(title)} characters; YouTube allows {TITLE_CHARS}")
    if len(description) > DESCRIPTION_CHARS:
        notes.append(f"description is {len(description)} characters; YouTube allows {DESCRIPTION_CHARS}")
    if "<" in description or ">" in description:
        notes.append("the description contains '<' or '>', which YouTube rejects; edit them out")

    when = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    seconds = info.duration if info.duration else meta.duration_frames / meta.fps
    rel_render = render.relative_to(ws).as_posix()
    lines = [
        f"# YouTube upload — {brief.title}",
        "",
        f"<!-- written by montage publish-pack {when}; a draft: edit freely. Re-running it needs --force "
        f"and replaces this file. -->",
        *([f"<!-- title, description intro, chapter titles and tags drafted by {draft.model} from the "
           f"approved script: check every fact. Credits, NASA line and disclosure are copied from the "
           f"records. -->"] if draft else []),
        "",
        f"- Video file: `{rel_render}` ({seconds:.1f} s)",
        f"- Storyboard approved: {status.approved_at or 'yes'}",
        "",
        "## Title",
        title,
        *([f"<!-- the brief's title: {brief.title} -->"] if draft and title != brief.title else []),
        "",
        "## Description",
        description,
        "",
        "## Tags",
        ", ".join(tag_list),
        "",
        "## Upload settings",
        "- Altered or synthetic content: yes (the narration is a synthetic voice)",
        "- Captions: burned into the video" if plan.captions else "- Captions: none",
        "",
    ]
    return Pack(ws / PACK_DIR / PACK_NAME, "\n".join(lines), render, tuple(credits), tuple(chs),
                tuple(tag_list), tuple(notes), plan, tuple(thumbnail_frames(plan)),
                draft.model if draft else None, draft_error)


def write(pack: Pack, force: bool = False) -> Path:
    if pack.path.exists() and not force:
        raise FileExistsError(f"{pack.path} already exists; re-run with --force to replace it")
    pack.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pack.path.with_suffix(".tmp")
    tmp.write_text(pack.text, encoding="utf-8")
    tmp.replace(pack.path)
    return pack.path
