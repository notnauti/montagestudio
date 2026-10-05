"""`montage scrape <workspace>`: source the brief's missing visuals from archives (guide Stage 2).

A brief asset whose `src` file does not exist yet is *wanted*. Its `src` says what to fetch:
the extension picks the kind (.png/.jpg/.jpeg/.webp = image, .mp4/.mov/.mkv/.webm/.m4v =
video) and the file lands exactly there, so brief.json, script.json and the plan never need
rewriting. Search words come from the asset's optional `query` (sent whole, as written, minus
stopwords), else its `description` minus stopwords and minus the words every asset in the brief
shares (the topic, which tells no asset apart), capped at QUERY_TERMS. Ranking also weighs the
narration of any script.json beat that already uses the asset.

Per wanted asset: search every source, drop candidates without a usable license, score the rest
lexically (sources.score, with a scene-type penalty; ties go to the candidate matching more of
the asset's distinctive words, then the larger original), keep the top CANDIDATES as previews
for the candidates sheet (candidates.py; optionally re-ranked by Claude vision), then try the
best ones in order until one downloads under the size cap and ffprobe can read it. A different container (Commons .webm for an .mp4 src, .jpg for a
.png src) is converted with ffmpeg. Provenance goes to <workspace>/manifest.json, keyed by src.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .brief import BriefError
from .net import Http, NetError, TooLarge
from .plan import STILL_EXTS
from .probe import ProbeError, probe as default_probe, tool
from .sources import Refusal, Scored, make_sources, score, stem, terms, url_ext, words

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
MANIFEST = "manifest.json"
DEFAULT_SOURCES = ("commons", "nasa", "archive")
DEFAULT_MAX_MB = 50
DEFAULT_MIN_SCORE = 0.25
QUERY_TERMS = 5        # words sent to a search built from a description (an explicit query is sent whole)
BEAT_WEIGHT = 0.5      # narration words count half as much as the description / query
TRIES = 5              # candidates tried per asset before giving up
RANKING = ("lexical: 2 per weighted term in the title, 1 if only in description/keywords, "
           "divided by the maximum; description/query terms weigh 1, beat narration terms 0.5; "
           "x0.75 per scene type (people/room/illustration) the title names and the asset does not, "
           "x0.9 if only the description names it; ties: more distinctive terms, then larger original")


class ScrapeError(ValueError):
    pass


@dataclass(frozen=True)
class Wanted:
    src: str
    path: Path
    kind: str
    description: str
    query: str | None
    beat_text: str
    shared: frozenset[str] = frozenset()  # stems every asset in the brief uses (the topic)
    unique: frozenset[str] = frozenset()  # stems no other asset in the brief uses

    def search_words(self) -> list[str]:
        """Original spellings, never stems. An explicit query goes whole; a description drops the
        brief-wide topic words first (unless that would leave fewer than two), then is capped."""
        if self.query:
            return words(self.query)
        all_words = words(self.description)
        own = [w for w in all_words if stem(w) not in self.shared]
        return (own if len(own) >= 2 else all_words)[:QUERY_TERMS]

    def context(self) -> str:
        """The asset's own wording, which says what kind of scene it wants."""
        return " ".join(x for x in (self.description, self.query or "", self.beat_text) if x)

    def surface(self, term: str) -> str:
        """The word as the brief or narration wrote it, for a stem ("jame" -> "james")."""
        for w in words(self.context()):
            if stem(w) == term:
                return w
        return term

    def weights(self) -> dict[str, float]:
        w = {t: 1.0 for t in terms(self.query or "") + terms(self.description)}
        for t in terms(self.beat_text):
            w.setdefault(t, BEAT_WEIGHT)
        return w


@dataclass
class Outcome:
    wanted: Wanted
    ranked: list[Scored] = field(default_factory=list)  # licensed and at or above min_score, best first
    below: list[Scored] = field(default_factory=list)   # licensed but under min_score
    refused: list[Refusal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # per source / per download
    queries: dict[str, str] = field(default_factory=dict)
    chosen: Scored | None = None
    entry: dict | None = None  # manifest entry when downloaded
    previews: dict = field(default_factory=dict)  # (source, id) -> local preview Path, or None
    vision: dict = field(default_factory=dict)    # (source, id) -> (score 0-10, reason)
    vision_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.entry is not None


def target_kind(src: str) -> str | None:
    ext = Path(src).suffix.lower()
    return "image" if ext in STILL_EXTS else "video" if ext in VIDEO_EXTS else None


def _term_sets(assets: list) -> tuple[frozenset[str], dict[int, set[str]]]:
    """Per brief asset (by index), the stems of its description + query; and the stems all of
    them share. A single-asset brief shares nothing (there is no other asset to tell it from)."""
    own = {i: set(terms(f"{a.get('description') or ''} {a.get('query') or ''}"))
           for i, a in enumerate(assets) if isinstance(a, dict) and isinstance(a.get("description"), str)
           and (a.get("query") is None or isinstance(a.get("query"), str))}
    sets = [t for t in own.values() if t]
    shared = frozenset(set.intersection(*sets)) if len(sets) >= 2 else frozenset()
    return shared, own


def wanted_assets(workspace: Path) -> tuple[list[Wanted], list[str]]:
    """Read brief.json (and script.json if any) as JSON and list the visuals with no file yet.
    Only the fields scraping needs are checked; `montage plan` validates the whole brief."""
    path = workspace / "brief.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BriefError([f"{path}: {exc}"]) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("assets"), list):
        raise BriefError([f"{path}: needs an \"assets\" array"])
    beats = []
    script = workspace / "script.json"
    if script.is_file():
        try:
            beats = json.loads(script.read_text(encoding="utf-8")).get("beats") or []
        except (OSError, json.JSONDecodeError, AttributeError):
            beats = []
    assets_dir = (workspace / "assets").resolve()
    shared, own = _term_sets(raw["assets"])
    errs, wanted, notes = [], [], []
    for i, a in enumerate(raw["assets"]):
        where = f"brief.assets[{i}]"
        if not isinstance(a, dict) or not isinstance(a.get("src"), str) or not a["src"]:
            errs.append(f"{where}: needs a \"src\"")
            continue
        src = a["src"]
        p = (workspace / src).resolve()
        if not p.is_relative_to(assets_dir):
            errs.append(f"{where}.src: must be inside assets/, got {src!r}")
            continue
        if p.is_file():
            continue
        desc, query = a.get("description"), a.get("query")
        if not isinstance(desc, str) or not desc.strip():
            errs.append(f"{where}.description: needed to search for {src!r}")
            continue
        if query is not None and (not isinstance(query, str) or not query.strip()):
            errs.append(f"{where}.query: must be a non-empty string")
            continue
        kind = target_kind(src)
        if kind is None:
            errs.append(f"{where}.src: cannot source {Path(src).suffix or 'an extensionless'} files; "
                        f"name it .jpg/.png for a still or .mp4 for video")
            continue
        if Path(src).stem == "replace-me":
            notes.append(f"{src}: still the `montage init` placeholder; give it a real name and description")
            continue
        beat_text = " ".join(b.get("narration", "") for b in beats
                             if isinstance(b, dict) and b.get("asset") == src)
        others = set().union(*(t for j, t in own.items() if j != i))
        wanted.append(Wanted(src, p, kind, desc.strip(), query.strip() if query else None, beat_text,
                             shared, frozenset(own.get(i, set()) - others)))
    music = raw.get("music")
    if isinstance(music, dict) and isinstance(music.get("src"), str) \
            and not (workspace / music["src"]).is_file():
        notes.append(f"music {music['src']}: missing; audio is not sourced (only visuals are)")
    if errs:
        raise BriefError(errs)
    return wanted, notes


# ── search + rank ─────────────────────────────────────────────────────────────

def search(w: Wanted, http: Http, sources: list, min_score: float = DEFAULT_MIN_SCORE) -> Outcome:
    out = Outcome(w)
    weights = w.weights()
    words = w.search_words()
    pool: list[Scored] = []
    for src_obj in sources:
        result = None
        tried = list(words)
        while tried:  # back off a term at a time when a search finds nothing
            try:
                result = src_obj.search(http, tried, w.kind)
            except NetError as exc:
                out.errors.append(f"{src_obj.name}: {exc}")
                result = None
                break
            if result.candidates or result.refused or len(tried) <= 2:
                break
            tried = tried[:-1]
        if result is None:
            continue
        out.queries[src_obj.name] = result.query
        out.refused += result.refused
        pool += [score(c, weights, w.context(), w.unique) for c in result.candidates]
    order = {s.name: i for i, s in enumerate(sources)}
    pool.sort(key=lambda s: (-s.score, -s.distinctive, -(s.candidate.pixels or 0), -(s.candidate.size or 0),
                             order[s.candidate.source]))
    out.ranked = [s for s in pool if s.score >= min_score]
    out.below = [s for s in pool if s.score < min_score]
    return out


# ── download + conform ────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ext_of(url: str) -> str:
    ext = url_ext(url)
    return ".jpg" if ext == ".jpeg" else ext


def conform(download: Path, target: Path, kind: str) -> str | None:
    """Move the download to target, converting with ffmpeg when the container differs.
    Returns the original extension when a conversion happened."""
    src_ext = ".jpg" if download.suffix.lower() == ".jpeg" else download.suffix.lower()
    dst_ext = ".jpg" if target.suffix.lower() == ".jpeg" else target.suffix.lower()
    target.parent.mkdir(parents=True, exist_ok=True)
    if src_ext == dst_ext:
        shutil.move(str(download), str(target))
        return None
    tmp = target.with_name(f".{target.stem}.part{target.suffix}")
    if kind == "image":
        args = ["-frames:v", "1"]
    else:
        args = ["-map", "0:v:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an"]
    r = subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(download), *args,
                        str(tmp)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    download.unlink(missing_ok=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise ProbeError(f"ffmpeg could not convert {src_ext} to {dst_ext}: {r.stderr.strip()[-300:]}")
    tmp.replace(target)
    return src_ext


def decodes(path: Path) -> None:
    """Raise ProbeError unless ffmpeg decodes a first video frame cleanly. ffprobe alone is not
    enough: it names a stream for any bytes behind a .jpg extension without decoding them."""
    r = subprocess.run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-xerror", "-i", str(path),
                        "-frames:v", "1", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or r.stderr.strip():
        raise ProbeError(f"does not decode: {r.stderr.strip()[-200:] or f'ffmpeg exit {r.returncode}'}")


def fetch(out: Outcome, http: Http, sources: list, workspace: Path, max_bytes: int, probe=default_probe) -> None:
    """Try the ranked candidates in order; the first that downloads, converts and probes wins.
    Candidates the search already reported as over the cap are skipped without a request and do
    not count toward the TRIES attempts."""
    w = out.wanted
    by_name = {s.name: s for s in sources}
    cache = workspace / "cache" / "scrape"
    attempts = 0
    for scored in out.ranked:
        c = scored.candidate
        if c.size and c.size > max_bytes:
            out.errors.append(f"{c.source} {c.id}: {c.size / 1e6:.1f} MB, over the {max_bytes / 1e6:.0f} MB cap")
            continue
        if attempts == TRIES:
            break
        attempts += 1
        try:
            urls = by_name[c.source].downloads(http, c, max_bytes)
        except NetError as exc:
            out.errors.append(f"{c.source} {c.id}: {exc}")
            continue
        if not urls:
            out.errors.append(f"{c.source} {c.id}: no {w.kind} file under the size cap")
        for url in urls:
            ext = _ext_of(url) or w.path.suffix.lower()
            part = cache / f"{w.path.stem}-{c.source}{ext}"
            try:
                size = http.download(url, part, max_bytes)
            except TooLarge as exc:
                out.errors.append(f"{c.source} {c.id}: {exc}")
                continue
            except NetError as exc:
                out.errors.append(f"{c.source} {c.id}: {exc}")
                continue
            try:
                converted = conform(part, w.path, w.kind)
                info = probe(w.path)
                if not info.has_video:
                    raise ProbeError("no video stream")
                decodes(w.path)
            except ProbeError as exc:
                out.errors.append(f"{c.source} {c.id}: unusable media: {exc}")
                part.unlink(missing_ok=True)
                w.path.unlink(missing_ok=True)
                continue
            out.chosen = scored
            out.entry = {
                "source": c.source, "sourceId": c.id, "title": c.title, "pageUrl": c.page_url,
                "downloadUrl": url, "license": c.license.name, "licenseUrl": c.license.url,
                "licenseBasis": c.license.basis, "attribution": c.attribution,
                "query": out.queries.get(c.source, ""), "score": scored.score,
                "matchedTerms": [w.surface(t) for t in scored.matched], "ranking": RANKING,
                "bytes": w.path.stat().st_size, "downloadedBytes": size, "sha256": _sha256(w.path),
                "convertedFrom": converted,
                "retrievedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            return


def write_manifest(workspace: Path, outcomes: list[Outcome]) -> Path | None:
    entries = {o.wanted.src: o.entry for o in outcomes if o.entry}
    path = workspace / MANIFEST
    if not entries:
        return None
    doc = {"version": 1, "assets": {}}
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, dict) and isinstance(old.get("assets"), dict):
                doc["assets"] = old["assets"]
        except (OSError, json.JSONDecodeError):
            pass
    doc["assets"].update(entries)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def scrape(workspace: str | Path, *, http: Http | None = None, source_names=DEFAULT_SOURCES,
           max_mb: float = DEFAULT_MAX_MB, min_score: float = DEFAULT_MIN_SCORE, dry_run: bool = False,
           probe=default_probe, candidates: bool = False, vision=None,
           ) -> tuple[list[Outcome], list[str], Path | None]:
    """`candidates`: keep the top CANDIDATES per asset as previews in cache/candidates/ and write
    cache/candidates.json + cache/candidates.png (a dry run still fetches those small previews).
    `vision`: a scorer (see vision.py) that re-ranks them from their previews before anything is
    downloaded; its first failure is recorded and the rest of the run stays lexical."""
    ws = Path(workspace).resolve()
    if not (ws / "brief.json").is_file():
        raise ScrapeError(f"no brief.json in {ws}; run `montage init \"{ws}\"` and list the visuals you want")
    wanted, notes = wanted_assets(ws)
    http = http or Http()
    sources = make_sources(source_names)
    outcomes = []
    keep = candidates or vision is not None
    if keep:
        from . import candidates as cand_mod
    for w in wanted:
        out = search(w, http, sources, min_score)
        if keep:
            cand_mod.keep_previews(out, http, ws)
            if vision is not None and not cand_mod.apply_vision(out, vision):
                vision = None  # it failed once (credits, network): the rest of the run stays lexical
        if not dry_run:
            fetch(out, http, sources, ws, int(max_mb * 1_000_000), probe=probe)
        outcomes.append(out)
    manifest = None if dry_run else write_manifest(ws, outcomes)
    if keep:
        notes += cand_mod.write_candidates(ws, outcomes)
    return outcomes, notes, manifest
