"""The candidates sheet: what `montage scrape` almost picked, and a way to pick it instead.

Per wanted asset the top CANDIDATES (licensed, at or above --min-score) are kept:
  cache/candidates/<asset>--<n>.<ext>   a small preview from the source (Commons 330 px thumbnail,
                                        NASA ~thumb, Archive.org item tile), never the full file
  cache/candidates.json                 everything needed to download one later without searching
                                        again: source, id, license, attribution, score, query
  cache/candidates.png                  a grid, one row per asset: asset id, rank, score, title,
                                        license, and vision score / CHOSEN when there is one

`montage scrape <ws> --pick <asset>=<n>` downloads candidate n through the same fetch as a
normal scrape (size cap, conversion, decode check, provenance in manifest.json) into a staging
file, and only replaces the asset once that succeeded. Licensing is checked again on the stored
record, so a hand-edited candidates.json cannot smuggle in an unusable license.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .contact_sheet import ContactSheetError, _font_option, _run, _write_text
from .net import Http, NetError
from .probe import ToolMissing, probe as default_probe, tool
from .scrape import DEFAULT_MAX_MB, Outcome, Wanted, fetch, target_kind, write_manifest
from .sources import Candidate, License, Scored, make_sources, parse_license, SOURCES
from .subtitles import filter_path
from .vision import VisionError

CANDIDATES = 3
PREVIEW_MAX_BYTES = 3_000_000
CAND_DIR = Path("cache") / "candidates"
CAND_JSON = Path("cache") / "candidates.json"
CAND_SHEET = Path("cache") / "candidates.png"
TILE_W, TILE_H, LABEL_H = 320, 180, 64
GAP, HEADER_H = 8, 40
BG, LABEL_BG, EMPTY_BG = "0x111111", "0x222222", "0x333333"


class PickError(ValueError):
    pass


def _key(c: Candidate) -> tuple[str, str]:
    return c.source, c.id


def asset_id(src: str) -> str:
    return Path(src).stem


def _slug(src: str) -> str:
    rel = re.sub(r"^(\./)?(assets/)?", "", Path(src).as_posix())
    return re.sub(r"[^A-Za-z0-9._-]+", "_", rel.replace("/", "__"))


def _sniff(path: Path) -> str | None:
    head = path.read_bytes()[:12]
    if head.startswith(b"\xff\xd8"):
        return ".jpg"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"GIF8"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


# ── previews ──────────────────────────────────────────────────────────────────

def keep_previews(out: Outcome, http: Http, workspace: Path) -> None:
    """Download a preview for each of the top CANDIDATES into cache/candidates/, replacing the
    asset's previews from an earlier run. A preview that fails is recorded as None."""
    d = workspace / CAND_DIR
    d.mkdir(parents=True, exist_ok=True)
    slug = _slug(out.wanted.src)
    for old in d.glob(f"{slug}--*"):
        old.unlink(missing_ok=True)
    for rank, s in enumerate(out.ranked[:CANDIDATES], 1):
        c = s.candidate
        out.previews[_key(c)] = None
        if not c.preview:
            continue
        part = d / f"{slug}--{rank}.part"
        try:
            http.download(c.preview, part, PREVIEW_MAX_BYTES)
        except NetError:
            part.unlink(missing_ok=True)
            continue
        ext = _sniff(part)
        if ext is None:  # an error page or an unknown format; the sheet shows "no preview"
            part.unlink(missing_ok=True)
            continue
        final = d / f"{slug}--{rank}{ext}"
        part.replace(final)
        out.previews[_key(c)] = final


def apply_vision(out: Outcome, scorer) -> bool:
    """Score the previewed top candidates with `scorer` and re-order them: vision score first,
    lexical order breaking ties; candidates without a preview (so without a vision score) go after
    the scored ones. Returns False when the scorer failed (recorded in out.vision_error)."""
    top = out.ranked[:CANDIDATES]
    items = [(rank, s.candidate, out.previews.get(_key(s.candidate)))
             for rank, s in enumerate(top, 1)]
    items = [i for i in items if i[2] is not None]
    if not items:
        return True
    try:
        scores = scorer(out.wanted, items)
    except VisionError as exc:
        out.vision_error = str(exc)
        return False
    for rank, s in enumerate(top, 1):
        if rank in scores:
            out.vision[_key(s.candidate)] = scores[rank]
    order = sorted(range(len(top)), key=lambda i: (_key(top[i].candidate) not in out.vision,
                                                   -out.vision.get(_key(top[i].candidate), (0, ""))[0], i))
    out.ranked = [top[i] for i in order] + out.ranked[CANDIDATES:]
    return True


# ── candidates.json ───────────────────────────────────────────────────────────

def _short_license(lic: dict) -> str:
    if lic.get("basis") == "source-policy":
        return "NASA media (source policy)"
    return lic.get("name") or "?"


def _candidate_json(rank: int, s: Scored, out: Outcome, workspace: Path) -> dict:
    c = s.candidate
    prev = out.previews.get(_key(c))
    vis = out.vision.get(_key(c))
    return {
        "rank": rank, "source": c.source, "id": c.id, "title": c.title, "description": c.description,
        "kind": c.kind, "pageUrl": c.page_url,
        "license": {"name": c.license.name, "url": c.license.url, "basis": c.license.basis},
        "attribution": c.attribution, "keywords": list(c.keywords), "size": c.size, "pixels": c.pixels,
        "preview": c.preview, "previewFile": prev.relative_to(workspace).as_posix() if prev else None,
        "extra": c.extra, "score": s.score, "matchedTerms": [out.wanted.surface(t) for t in s.matched],
        "distinctive": s.distinctive, "penalties": list(s.penalties), "query": out.queries.get(c.source, ""),
        "vision": {"score": vis[0], "reason": vis[1]} if vis else None,
    }


def load_candidates(workspace: Path) -> dict:
    path = workspace / CAND_JSON
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "assets": {}}
    if not isinstance(doc, dict) or not isinstance(doc.get("assets"), dict):
        return {"version": 1, "assets": {}}
    return doc


def _save(workspace: Path, doc: dict) -> Path:
    path = workspace / CAND_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def write_candidates(workspace: Path, outcomes: list[Outcome]) -> list[str]:
    """Merge this run's candidates into cache/candidates.json and redraw cache/candidates.png.
    Returns notes for the CLI."""
    doc = load_candidates(workspace)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = False
    for out in outcomes:
        top = out.ranked[:CANDIDATES]
        if not top:
            continue
        chosen = next((r for r, s in enumerate(top, 1) if out.chosen is not None
                       and _key(s.candidate) == _key(out.chosen.candidate)), None)
        doc["assets"][out.wanted.src] = {
            "kind": out.wanted.kind, "description": out.wanted.description, "updatedAt": now,
            "chosen": chosen, "visionError": out.vision_error,
            "candidates": [_candidate_json(r, s, out, workspace) for r, s in enumerate(top, 1)],
        }
        changed = True
    if not changed:
        return []
    _save(workspace, doc)
    return _sheet_notes(workspace, doc)


def _sheet_notes(workspace: Path, doc: dict) -> list[str]:
    try:
        sheet = draw_sheet(workspace, doc)
    except (ContactSheetError, ToolMissing) as exc:
        return [f"candidates sheet not drawn: {exc}"]
    return [f"candidates: {sheet} (top {CANDIDATES} per asset; take another with "
            f"montage scrape \"{workspace}\" --pick <asset>=<n>)"]


# ── the sheet ─────────────────────────────────────────────────────────────────

def _label(aid: str, entry: dict, cand: dict | None, chosen: int | None) -> str:
    if cand is None:
        return f"{aid}\n(no candidate)"
    head = f"{aid[:26]}  #{cand['rank']}  {cand['score']:.3f}"
    if cand.get("vision"):
        head += f"  vision {cand['vision']['score']}/10"
    if chosen == cand["rank"]:
        head += "  CHOSEN"
    title = cand["title"] if len(cand["title"]) <= 44 else cand["title"][:41].rstrip() + "..."
    return "\n".join([head, title, f"{_short_license(cand['license'])}  [{cand['source']}]"])


def _tile(workspace: Path, work: Path, idx: int, text: str, preview: str | None, font: str) -> Path:
    label = work / f"label_{idx:03d}.txt"
    _write_text(label, text)
    out = work / f"tile_{idx:03d}.png"
    vf = (f"scale={TILE_W}:{TILE_H}:force_original_aspect_ratio=decrease,"
          f"pad={TILE_W}:{TILE_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
          f"pad={TILE_W}:{TILE_H + LABEL_H}:0:0:color={LABEL_BG},"
          f"drawtext={font}:textfile={filter_path(label)}:expansion=none:x=8:y={TILE_H + 6}:"
          f"fontsize=13:fontcolor=white:line_spacing=5,format=rgb24")
    empty = ["-f", "lavfi", "-i", f"color=c={EMPTY_BG}:s={TILE_W}x{TILE_H}"]
    src = workspace / preview if preview else None
    inp = ["-i", str(src)] if src is not None and src.is_file() else empty
    cmd = lambda i: [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *i, "-frames:v", "1",
                     "-update", "1", "-vf", vf, str(out)]
    try:
        _run(cmd(inp), f"candidate tile {idx}")
    except ContactSheetError:
        if inp is empty:
            raise
        _run(cmd(empty), f"candidate tile {idx}")  # an unreadable preview: draw the label anyway
    return out


def draw_sheet(workspace: Path, doc: dict | None = None) -> Path:
    """cache/candidates.png: CANDIDATES columns, one row per asset in candidates.json."""
    doc = doc or load_candidates(workspace)
    work = workspace / "cache" / "candidates_sheet"
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("*"):
        old.unlink()
    font = _font_option()
    idx = 0
    rows = 0
    for src, entry in doc["assets"].items():
        cands = entry.get("candidates") or []
        rows += 1
        for k in range(CANDIDATES):
            cand = cands[k] if k < len(cands) else None
            idx += 1
            _tile(workspace, work, idx, _label(asset_id(src), entry, cand, entry.get("chosen")),
                  cand.get("previewFile") if cand else None, font)
    if not idx:
        raise ContactSheetError("no candidates to draw")
    header = work / "header.txt"
    _write_text(header, f"scrape candidates  |  {rows} asset(s), top {CANDIDATES} each  |  "
                        f"pick: montage scrape <ws> --pick <asset>=<n>")
    vf = (f"tile={CANDIDATES}x{rows}:padding={GAP}:margin={GAP}:color={BG},"
          f"pad=iw:ih+{HEADER_H}:0:{HEADER_H}:color={BG},"
          f"drawtext={font}:textfile={filter_path(header)}:expansion=none:x={GAP + 4}:y=12:"
          f"fontsize=16:fontcolor=white")
    out = (workspace / CAND_SHEET).resolve()
    _run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-framerate", "1",
          "-i", "tile_%03d.png", "-vf", vf, "-frames:v", "1", "-update", "1", str(out)],
         "candidates sheet grid", cwd=work)
    return out


# ── --pick ────────────────────────────────────────────────────────────────────

def parse_pick(spec: str) -> tuple[str, int]:
    asset, sep, n = spec.rpartition("=")
    if not sep or not asset.strip() or not n.strip().isdigit() or int(n) < 1:
        raise PickError(f"--pick {spec!r}: expected <asset>=<n>, e.g. jwst-mirror-wing=2")
    return asset.strip(), int(n)


def find_asset(doc: dict, key: str) -> str:
    """The candidates.json src for `key`: the src itself, or its path without ./, its file name,
    or its stem (the asset id the sheet prints)."""
    norm = lambda s: Path(s).as_posix().removeprefix("./")
    hits = [src for src in doc["assets"]
            if key in (src, norm(src), Path(src).name, Path(src).stem) or norm(key) == norm(src)]
    if not hits:
        known = ", ".join(asset_id(s) for s in doc["assets"]) or "none; run montage scrape first"
        raise PickError(f"--pick: no candidates for {key!r} in {CAND_JSON.as_posix()} (have: {known})")
    if len(hits) > 1:
        raise PickError(f"--pick: {key!r} matches {len(hits)} assets ({', '.join(hits)}); give the full src")
    return hits[0]


def _license_usable(source: str, lic: dict) -> bool:
    if lic.get("basis") == "source-policy":
        return source == "nasa"  # NASA's media guidelines are the only source policy we apply
    return parse_license(lic.get("name"), lic.get("url")).usable


def rebuild(cand: dict) -> Scored:
    lic = cand.get("license") or {}
    if cand.get("source") not in SOURCES:
        raise PickError(f"unknown source {cand.get('source')!r} in candidates.json")
    if not _license_usable(cand["source"], lic):
        raise PickError(f"{cand['source']} {cand.get('id')!r}: license {lic.get('name')!r} is not usable "
                        f"(PD / CC0 / CC BY / CC BY-SA only); refusing")
    c = Candidate(cand["source"], cand["id"], cand.get("title", ""), cand.get("description", ""), cand["kind"],
                  cand.get("pageUrl", ""), License(lic.get("name"), lic.get("url"), True,
                                                   basis=lic.get("basis", "item-field")),
                  cand.get("attribution", ""), tuple(cand.get("keywords") or ()), cand.get("size"),
                  cand.get("extra") or {}, cand.get("preview"), cand.get("pixels"))
    return Scored(c, float(cand.get("score", 0.0)), tuple(cand.get("matchedTerms") or ()),
                  int(cand.get("distinctive", 0)), tuple(cand.get("penalties") or ()))


def pick(workspace: str | Path, key: str, n: int, *, http: Http | None = None,
         max_mb: float = DEFAULT_MAX_MB, probe=default_probe) -> tuple[Outcome, list[str]]:
    """Download candidate n for one asset and record it in manifest.json. The asset file is only
    replaced after the download converted and decoded; on failure it is left as it was.
    Raises PickError for input problems; a failed download is `not outcome.ok`."""
    ws = Path(workspace).resolve()
    doc = load_candidates(ws)
    src = find_asset(doc, key)
    entry = doc["assets"][src]
    cands = entry.get("candidates") or []
    if not 1 <= n <= len(cands):
        raise PickError(f"--pick {key}={n}: {asset_id(src)} has candidates 1..{len(cands)}")
    scored = rebuild(cands[n - 1])
    target = (ws / src).resolve()
    if not target.is_relative_to((ws / "assets").resolve()):
        raise PickError(f"{src}: must be inside assets/")
    kind = target_kind(src)
    if kind is None or kind != scored.candidate.kind:
        raise PickError(f"{src}: candidate is a {scored.candidate.kind}, the asset wants {kind or 'an unknown kind'}")
    staging = ws / "cache" / "pick" / target.name
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.unlink(missing_ok=True)
    w = Wanted(src, staging, kind, entry.get("description", ""), None, "")
    out = Outcome(w, ranked=[scored], queries={scored.candidate.source: cands[n - 1].get("query", "")})
    fetch(out, http or Http(), make_sources([scored.candidate.source]), ws, int(max_mb * 1_000_000), probe=probe)
    notes = []
    if not out.ok:
        return out, notes
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(target)
    out.wanted = replace(w, path=target)
    out.entry["matchedTerms"] = list(cands[n - 1].get("matchedTerms") or [])
    out.entry["pickedRank"] = n
    write_manifest(ws, [out])
    entry["chosen"] = n
    _save(ws, doc)
    notes += _sheet_notes(ws, doc)
    if (ws / "approval.json").is_file():
        notes.append("approval.json pins the previous file: re-run montage plan (or contact-sheet) and approve")
    return out, notes
