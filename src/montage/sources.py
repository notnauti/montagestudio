"""Archive sources for `montage scrape`: Wikimedia Commons, NASA Image and Video Library, Archive.org.

Each source turns a keyword search into Candidates that carry a *usable* license, and turns a
chosen Candidate into download URLs (best rendition first). Candidates without one are refused
with the reason, never downloaded.

Usable = public domain, CC0, CC BY or CC BY-SA. Refused: no license field, NonCommercial (a
published video may be monetised), NoDerivatives (cropping, zooming and cutting are
derivatives), anything not recognised.

Where the license comes from (API shapes extracted from live responses on 2026-10-02):
  Commons   imageinfo.extmetadata.LicenseShortName / LicenseUrl (per file)
  Archive   the item's `licenseurl` (per item; most items have none and are refused)
  NASA      the API has no per-item license field. NASA's media guidelines say NASA material is
            generally not copyrighted, but third-party material is (extracted, nasa.gov media
            guidelines). We apply that as a *source policy* and refuse any item whose metadata
            credits a third party (©, "courtesy of", wire services). The manifest marks these
            `licenseBasis: source-policy` so they can be told apart from per-item licenses.

Ranking is lexical, not CLIP (torch is out of scope): see `score`. Search APIs receive words as
written (`words`); stems (`terms`) are only compared locally.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Iterable

from .net import Http

RESULTS = 20  # per source per search
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
NASA_API = "https://images-api.nasa.gov"
ARCHIVE = "https://archive.org"
NASA_GUIDELINES = "https://www.nasa.gov/nasa-brand-center/images-and-media/"

STOPWORDS = frozenset("""
a an and are as at be but by for from has have how in into is it its of on or over that the their
then there these this those to under up was were what when where which while who why will with
without your you we our they them he she his her than so such very can could would should shot
footage image photo picture video clip view slow pan close closeup wide still showing shows show
off out about all also just more most some any each other only into onto been being do does did not
""".split())


# ── text ──────────────────────────────────────────────────────────────────────

def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(text)))).strip()


def stem(word: str) -> str:
    """A plural-folding key for *local* matching only; never sent to a search API (a stem like
    "jame" for "james" is fine as a key because both sides of a comparison go through it).

    -ies -> -y (galaxies); -es after s/x/z/ch/sh -> drop "es" (launches, boxes, glasses);
    words ending -ss / -us / -is are not plurals (glass, nucleus, analysis); any other final s
    on a word of 4+ letters is dropped (rockets, images, stars)."""
    if len(word) < 4 or not word.endswith("s") or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    return word[:-1]


_stem = stem  # older name


def words(text: str) -> list[str]:
    """Lowercase alphanumeric words as written, stopwords dropped, one per stem (first spelling
    wins). This is what search APIs receive."""
    out: list[str] = []
    seen: set[str] = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in STOPWORDS or (len(w) < 2 and not w.isdigit()):
            continue
        s = stem(w)
        if s not in seen:
            seen.add(s)
            out.append(w)
    return out


def terms(text: str) -> list[str]:
    """`words` folded to their stems: the keys local scoring compares."""
    return [stem(w) for w in words(text)]


# ── licenses ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class License:
    name: str | None   # canonical: "CC0", "Public domain", "CC BY 4.0", "CC BY-SA 3.0", ...
    url: str | None
    usable: bool
    reason: str = ""   # why it is refused
    basis: str = "item-field"  # or "source-policy" (NASA)


_CC_URL = re.compile(r"creativecommons\.org/licenses/([a-z-]+)/([0-9.]+)", re.I)
_CC_NAME = re.compile(r"^CC[- ]?(BY(?:[- ](?:SA|NC|ND))*)(?:[- ]([0-9.]+))?", re.I)


def _cc(kind: str, version: str | None, url: str | None) -> License:
    parts = kind.upper().replace(" ", "-").split("-")
    name = "CC " + "-".join(parts) + (f" {version}" if version else "")
    if "NC" in parts:
        return License(name, url, False, f"{name} is NonCommercial")
    if "ND" in parts:
        return License(name, url, False, f"{name} is NoDerivatives (editing is a derivative)")
    return License(name, url, True)


def parse_license(short_name: str | None, url: str | None) -> License:
    short = strip_html(short_name)
    url = (url or "").strip() or None
    if url:
        low = url.lower()
        if "publicdomain/zero" in low:
            return License("CC0", url, True)
        if "publicdomain/mark" in low or re.search(r"creativecommons\.org/licenses/publicdomain", low):
            return License("Public domain", url, True)  # the PD Mark, and CC's retired PD dedication URL
        m = _CC_URL.search(url)
        if m:
            return _cc(m.group(1), m.group(2), url)
    if short:
        up = short.upper()
        if up.startswith("CC0") or up.startswith("CC-ZERO"):
            return License("CC0", url, True)
        if up.startswith("PUBLIC DOMAIN") or up.startswith("PD") or up == "NO RESTRICTIONS":
            return License("Public domain", url, True)
        m = _CC_NAME.match(short)
        if m:
            return _cc(m.group(1), m.group(2), url)
        return License(short, url, False, f"unrecognised license {short!r}")
    if url:
        return License(None, url, False, f"unrecognised license URL {url}")
    return License(None, None, False, "no license field")


# ── candidates and ranking ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Candidate:
    source: str
    id: str
    title: str
    description: str
    kind: str  # "image" | "video"
    page_url: str
    license: License
    attribution: str
    keywords: tuple[str, ...] = ()
    size: int | None = None  # bytes, when the search result says
    extra: dict = field(default_factory=dict, compare=False, hash=False)
    preview: str | None = None  # a small rendition for the candidates sheet, when the source has one
    pixels: int | None = None   # width x height of the original, when the search result says


@dataclass(frozen=True)
class Refusal:
    source: str
    id: str
    title: str
    reason: str


@dataclass(frozen=True)
class Scored:
    candidate: Candidate
    score: float
    matched: tuple[str, ...]          # stems
    distinctive: int = 0              # matched stems no other asset in the brief uses
    penalties: tuple[str, ...] = ()   # scene-type mismatches, e.g. "people (title)"


# Scene types a keyword match cannot see: a photo of engineers in a control room titled "Mirror
# Deployment" matches "mirror" as well as the mirror does. A candidate whose text names one of
# these and whose asset's own wording (description, query, narration) does not is marked down.
SCENE_TYPES = {
    "people": re.compile(r"\b(engineers?|technicians?|scientists?|workers?|people|crew|staff|employees?|"
                         r"officials?|administrators?|visitors?|students?|portraits?|pos(?:es|ed|ing)|"
                         r"ceremony|audience|team members?)\b"),
    "room": re.compile(r"\b(control rooms?|mission control|operations? cent(?:er|re)s?|mission operations|"
                       r"auditorium|offices?|press conference|news conference|briefing)\b"),
    "illustration": re.compile(r"\b(illustrations?|illustrated|artists?|renderings?|animations?|animated|"
                               r"diagrams?|infographics?|graphics?|sketch(?:es|ed|ing)?|drawings?|cartoons?|"
                               r"concept art)\b"),
}
TITLE_PENALTY = 0.75  # scene type named in the title: the picture is most likely that
BODY_PENALTY = 0.9    # only in the description / keywords: weaker evidence, captions mention people often


def scene_types(text: str) -> set[str]:
    low = (text or "").lower()
    return {name for name, rx in SCENE_TYPES.items() if rx.search(low)}


def score(c: Candidate, weights: dict[str, float], context: str | None = None,
          unique: frozenset[str] = frozenset()) -> Scored:
    """Transparent lexical score in [0, 1]: each weighted term counts 2 if it is in the title, 1 if
    only in the description / keywords, 0 otherwise; divided by the best possible (2 x weight sum).

    With `context` (the asset's own wording), each scene type (people, room, illustration) that
    the candidate names and the context does not multiplies the score by TITLE_PENALTY when the
    title names it, else BODY_PENALTY when the description / keywords do. `unique` are the
    asset's distinctive stems; how many matched is kept for tie-breaking."""
    title = set(terms(c.title))
    body_text = c.description + " " + " ".join(c.keywords)
    body = set(terms(body_text))
    got = best = 0.0
    matched = []
    for t, w in weights.items():
        best += 2 * w
        if t in title:
            got += 2 * w
            matched.append(t)
        elif t in body:
            got += w
            matched.append(t)
    value = got / best if best else 0.0
    penalties = []
    if context is not None:
        allowed = scene_types(context)
        in_title = scene_types(c.title) - allowed
        in_body = scene_types(body_text) - allowed - in_title
        for name in sorted(in_title):
            value *= TITLE_PENALTY
            penalties.append(f"{name} (title)")
        for name in sorted(in_body):
            value *= BODY_PENALTY
            penalties.append(f"{name} (description)")
    return Scored(c, round(value, 3), tuple(matched), sum(1 for t in matched if t in unique), tuple(penalties))


@dataclass(frozen=True)
class SearchResult:
    candidates: list[Candidate]
    refused: list[Refusal]
    query: str


def url_ext(url: str) -> str:
    path = urllib.parse.urlsplit(url).path
    return ("." + path.rsplit(".", 1)[-1].lower()) if "." in path.rsplit("/", 1)[-1] else ""


def _first(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v)
    return str(value) if value else ""


# ── Wikimedia Commons ─────────────────────────────────────────────────────────

PREVIEW_WIDTH = 330  # one of Wikimedia's standard thumbnail widths (inferred: other widths are rate-limited)


def commons_preview(thumburl: str | None) -> str | None:
    """The search asks for 1920 px thumbnails; a sheet preview is the same URL at PREVIEW_WIDTH.
    An original narrower than 1920 px comes back unscaled (no width step in the URL); its preview
    is built on Wikimedia's thumbnail host and path, thumb.wikimedia.org/.../thumb/<h>/<hh>/<name>/
    <w>px-<name> (inferred from the URLs the API returned on 2026-10-04; upload.wikimedia.org
    answered the same path, and the original, with HTTP 429), instead of fetching the original."""
    if not thumburl:
        return None
    if re.search(r"/\d+px-", thumburl):
        return re.sub(r"/\d+px-", f"/{PREVIEW_WIDTH}px-", thumburl, count=1)
    m = re.match(r"https?://[^/]+/wikipedia/commons/([0-9a-f]/[0-9a-f]{2}/)([^/?#]+)", thumburl)
    if m:
        return (f"https://thumb.wikimedia.org/wikipedia/commons/thumb/{m.group(1)}{m.group(2)}/"
                f"{PREVIEW_WIDTH}px-{m.group(2)}")
    return thumburl


class Commons:
    name = "commons"

    def search(self, http: Http, words: list[str], kind: str) -> SearchResult:
        q = " ".join(words) + (" filetype:bitmap" if kind == "image" else " filetype:video")
        params = {"action": "query", "format": "json", "formatversion": "2", "generator": "search",
                  "gsrnamespace": "6", "gsrsearch": q, "gsrlimit": str(RESULTS), "prop": "imageinfo",
                  "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "1920",
                  "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|ObjectName|ImageDescription"}
        data = http.json(f"{COMMONS_API}?{urllib.parse.urlencode(params)}")
        pages = sorted((data.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 0))
        cands, refused = [], []
        for p in pages:
            info = (p.get("imageinfo") or [{}])[0]
            meta = {k: (v or {}).get("value") for k, v in (info.get("extmetadata") or {}).items()}
            file_title = p.get("title", "")
            title = strip_html(meta.get("ObjectName")) or re.sub(r"^File:|\.[A-Za-z0-9]+$", "", file_title)
            lic = parse_license(meta.get("LicenseShortName"), meta.get("LicenseUrl"))
            if not lic.usable:
                refused.append(Refusal(self.name, file_title, title, lic.reason))
                continue
            mime = info.get("mime", "")
            if kind == "video" and not mime.startswith("video/"):
                continue
            artist = strip_html(meta.get("Artist")) or "unknown author"
            if kind == "image":
                url = info.get("thumburl") or info.get("url")
                size = None if info.get("thumburl") else info.get("size")
            else:
                url, size = info.get("url"), info.get("size")
            if not url:
                continue
            w, h = info.get("width"), info.get("height")
            cands.append(Candidate(self.name, file_title, title, strip_html(meta.get("ImageDescription"))[:600],
                                   kind, info.get("descriptionurl", ""), lic, f"{artist}, via Wikimedia Commons",
                                   size=size, extra={"urls": [url]}, preview=commons_preview(info.get("thumburl")),
                                   pixels=w * h if isinstance(w, int) and isinstance(h, int) and w * h else None))
        return SearchResult(cands, refused, q)

    def downloads(self, http: Http, c: Candidate, max_bytes: int) -> list[str]:
        return list(c.extra.get("urls", []))


# ── NASA Image and Video Library ──────────────────────────────────────────────

_THIRD_PARTY = re.compile(r"©|\(c\)\s*\d{4}|copyright|courtesy of|getty|reuters|associated press|\bap photo",
                          re.I)


def _nasa_preview(links: list) -> str | None:
    for link in links:
        href = link.get("href") or ""
        if link.get("rel") == "preview" or "~thumb" in href:
            return href or None
    return None


class Nasa:
    name = "nasa"

    def search(self, http: Http, words: list[str], kind: str) -> SearchResult:
        q = " ".join(words)
        params = {"q": q, "media_type": kind, "page_size": str(RESULTS)}
        data = http.json(f"{NASA_API}/search?{urllib.parse.urlencode(params)}")
        cands, refused = [], []
        for item in ((data.get("collection") or {}).get("items") or [])[:RESULTS]:
            d = (item.get("data") or [{}])[0]
            nasa_id = d.get("nasa_id")
            if not nasa_id or d.get("media_type") != kind:
                continue
            title = strip_html(d.get("title")) or nasa_id
            credit = " ".join(_first(d.get(k)) for k in ("photographer", "secondary_creator"))
            text = " ".join([title, strip_html(d.get("description")), credit])
            if _THIRD_PARTY.search(text):
                refused.append(Refusal(self.name, nasa_id, title,
                                       "metadata credits a third party; NASA's no-copyright guideline "
                                       "does not cover third-party material"))
                continue
            lic = License("NASA media (not copyrighted, per NASA media guidelines)", NASA_GUIDELINES, True,
                          basis="source-policy")
            who = ", ".join(x for x in ("NASA", d.get("center"), _first(d.get("photographer"))) if x)
            links = [link for link in item.get("links") or [] if link.get("render") == "image"]
            cands.append(Candidate(self.name, nasa_id, title, strip_html(d.get("description"))[:600], kind,
                                   f"https://images.nasa.gov/details/{urllib.parse.quote(nasa_id)}", lic, who,
                                   tuple(_first(k) for k in d.get("keywords") or []),
                                   extra={"links": links, "collection": item.get("href")},
                                   preview=_nasa_preview(item.get("links") or [])))
        return SearchResult(cands, refused, q)

    def downloads(self, http: Http, c: Candidate, max_bytes: int) -> list[str]:
        if c.kind == "image":
            by_name = {re.sub(r".*~", "~", link["href"].rsplit(".", 1)[0]): link for link in c.extra["links"]}
            order = ["~large", "~orig", "~medium"]
            return [by_name[k]["href"] for k in order if k in by_name
                    and (by_name[k].get("size") or 0) <= max_bytes]
        if not c.extra.get("collection"):
            return []
        urls = http.json(c.extra["collection"])
        urls = urls if isinstance(urls, list) else [i.get("href") for i in
                                                     (urls.get("collection") or {}).get("items", [])]
        order = ["~large.mp4", "~medium.mp4", "~orig.mp4", "~small.mp4"]
        return [u for o in order for u in urls if isinstance(u, str) and u.endswith(o)]


# ── Archive.org ───────────────────────────────────────────────────────────────

class Archive:
    name = "archive"
    FIELDS = ("identifier", "title", "description", "licenseurl", "creator", "subject", "mediatype")

    def search(self, http: Http, words: list[str], kind: str) -> SearchResult:
        media = "image" if kind == "image" else "movies"
        q = f"({' '.join(words)}) AND mediatype:({media}) AND licenseurl:*"
        params = [("q", q), *(("fl[]", f) for f in self.FIELDS), ("rows", str(RESULTS)), ("output", "json")]
        data = http.json(f"{ARCHIVE}/advancedsearch.php?{urllib.parse.urlencode(params)}")
        cands, refused = [], []
        for doc in (data.get("response") or {}).get("docs") or []:
            ident = doc.get("identifier")
            if not ident:
                continue
            title = strip_html(_first(doc.get("title"))) or ident
            lic = parse_license(None, _first(doc.get("licenseurl")) or None)
            if not lic.usable:
                refused.append(Refusal(self.name, ident, title, lic.reason))
                continue
            subjects = doc.get("subject") or []
            subjects = subjects if isinstance(subjects, list) else [subjects]
            creator = strip_html(_first(doc.get("creator")))
            cands.append(Candidate(self.name, ident, title, strip_html(_first(doc.get("description")))[:600], kind,
                                   f"{ARCHIVE}/details/{ident}", lic,
                                   f"{creator or 'unknown creator'}, via Internet Archive",
                                   tuple(str(s) for s in subjects),
                                   preview=f"{ARCHIVE}/services/img/{urllib.parse.quote(ident)}"))
        return SearchResult(cands, refused, q)

    def downloads(self, http: Http, c: Candidate, max_bytes: int) -> list[str]:
        meta = http.json(f"{ARCHIVE}/metadata/{urllib.parse.quote(c.id)}")
        exts = (".jpg", ".jpeg", ".png") if c.kind == "image" else (".mp4",)
        want = set(terms(c.title))
        files = []
        for f in meta.get("files") or []:
            name = f.get("name") or ""
            if not name.lower().endswith(exts) or f.get("format") in ("Thumbnail", "Item Tile") \
                    or ".thumbs/" in name or name.startswith("__ia_thumb"):
                continue
            size = int(f["size"]) if str(f.get("size") or "").isdigit() else None
            if size is not None and size > max_bytes:
                continue
            hits = len(want & set(terms(name)))
            files.append((-hits, f.get("source") != "original", -(size or 0), name))
        files.sort()
        return [f"{ARCHIVE}/download/{urllib.parse.quote(c.id)}/{urllib.parse.quote(name)}"
                for *_, name in files]


SOURCES = {"commons": Commons, "nasa": Nasa, "archive": Archive}


def make_sources(names: Iterable[str]) -> list:
    return [SOURCES[n]() for n in names]
