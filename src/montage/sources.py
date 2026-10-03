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

Ranking is lexical, not CLIP (torch is out of scope): see `score`.
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


def _stem(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def terms(text: str) -> list[str]:
    """Lowercase alphanumeric words, stopwords dropped, plural 's' stripped, de-duplicated in order."""
    out: list[str] = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in STOPWORDS or (len(w) < 2 and not w.isdigit()):
            continue
        w = _stem(w)
        if w not in out:
            out.append(w)
    return out


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
    matched: tuple[str, ...]


def score(c: Candidate, weights: dict[str, float]) -> Scored:
    """Transparent lexical score in [0, 1]: each weighted term counts 2 if it is in the title, 1 if
    only in the description / keywords, 0 otherwise; divided by the best possible (2 x weight sum)."""
    title = set(terms(c.title))
    body = set(terms(c.description + " " + " ".join(c.keywords)))
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
    return Scored(c, round(got / best, 3) if best else 0.0, tuple(matched))


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
            cands.append(Candidate(self.name, file_title, title, strip_html(meta.get("ImageDescription"))[:600],
                                   kind, info.get("descriptionurl", ""), lic, f"{artist}, via Wikimedia Commons",
                                   size=size, extra={"urls": [url]}))
        return SearchResult(cands, refused, q)

    def downloads(self, http: Http, c: Candidate, max_bytes: int) -> list[str]:
        return list(c.extra.get("urls", []))


# ── NASA Image and Video Library ──────────────────────────────────────────────

_THIRD_PARTY = re.compile(r"©|\(c\)\s*\d{4}|copyright|courtesy of|getty|reuters|associated press|\bap photo",
                          re.I)


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
                                   extra={"links": links, "collection": item.get("href")}))
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
                                   tuple(str(s) for s in subjects)))
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
