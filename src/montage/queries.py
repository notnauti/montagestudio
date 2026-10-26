"""`montage scrape --suggest-queries`: Claude writes each wanted asset's archive search query.

A description-derived search ("primary mirror fully unfolded gold hexagonal") is a sentence cut at
QUERY_TERMS words; archives match titles, so on JWST every asset got a hand-written `query`
("webb gold mirror revealed"). This asks Claude for those instead, from the brief alone: sourcing
runs before `montage plan`, so there is no script to read yet.

Precedence, per asset: a `query` in brief.json (hand-written) > a suggestion in
<workspace>/cache/queries.json > the description. Suggestions are saved, so a later scrape reuses
them with no model call; `--suggest-queries` asks again for every asset without a brief query.
Opt-in like `--vision`: refused with exit 2 before any request when no credential source
resolves; a failed request leaves the description-derived search and the run exits 2.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .script import CLAUDE_MODEL

SUGGESTIONS = ("cache", "queries.json")
QUERY_WORDS = (2, 6)

SYSTEM = """You write search queries for public image and video archives (Wikimedia Commons, \
NASA Image and Video Library, Internet Archive). These archives match words in an item's title \
and description, so a query is a short keyword phrase, not a sentence.

For each wanted asset write one query of 2 to 6 words:
- the subject's short common name first (the name an archive title would use), then the one or \
two words that tell this shot apart from the video's other shots;
- archive vocabulary: "illustration" or "diagram" for a drawing, "test" or "assembly" for \
hardware on the ground, the proper name of an event or object;
- no articles, no adjectives about quality ("stunning", "high resolution"), no quotes, no \
operators.

Examples from a James Webb Space Telescope video:
- primary mirror fully unfolded, gold segments, in the clean room -> webb gold mirror revealed
- sunshield deployment test, five silver layers stretched out -> webb sunshield deployment test
- orbit around the second Lagrange point L2 -> webb orbit illustration"""


class QueryError(RuntimeError):
    """Claude could not suggest queries, or its suggestions broke the rules (every reason listed)."""


def path(ws: Path) -> Path:
    return ws.joinpath(*SUGGESTIONS)


def load(ws: Path) -> dict[str, str]:
    """{src: query} saved by an earlier run; {} when none or unreadable."""
    try:
        doc = json.loads(path(ws).read_text(encoding="utf-8"))
        return {src: e["query"] for src, e in doc["assets"].items()
                if isinstance(e, dict) and isinstance(e.get("query"), str) and e["query"].strip()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def save(ws: Path, suggestions: dict[str, str], model: str) -> Path:
    """Merge into cache/queries.json (other assets' earlier suggestions are kept)."""
    p = path(ws)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        assets = doc["assets"] if isinstance(doc.get("assets"), dict) else {}
    except (OSError, ValueError, KeyError, AttributeError):
        assets = {}
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    assets.update({src: {"query": q, "model": model, "at": at} for src, q in suggestions.items()})
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": 1, "assets": assets}, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    return p


def apply(wanted: list, suggestions: dict[str, str]) -> list:
    """Wanted assets with a suggestion where the brief gave no query (the brief's query wins)."""
    return [replace(w, query=suggestions[w.src], query_from="suggested")
            if w.query is None and w.src in suggestions else w for w in wanted]


def schema(srcs: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {"queries": {"type": "array", "items": {
            "type": "object",
            "properties": {"asset": {"type": "string", "enum": srcs}, "query": {"type": "string"}},
            "required": ["asset", "query"], "additionalProperties": False}}},
        "required": ["queries"],
        "additionalProperties": False,
    }


def build_prompt(premise: str, wanted: list) -> str:
    lines = [f"The video: {premise}", "", "Wanted assets (one query each):"]
    lines += [f"- {w.src} ({'video clip' if w.kind == 'video' else 'still image'}): {w.description}"
              for w in wanted]
    return "\n".join(lines)


def parse(text: str, srcs: list[str]) -> dict[str, str]:
    try:
        raw = json.loads(text)
        items = raw["queries"]
    except (ValueError, KeyError, TypeError) as exc:
        raise QueryError(f"Claude's queries are not the expected JSON ({exc})") from exc
    out, errs = {}, []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("asset") not in srcs or not isinstance(item.get("query"), str):
            errs.append(f"unusable item {item!r}")
            continue
        q = " ".join(item["query"].replace('"', " ").split())
        n = len(q.split())
        if not QUERY_WORDS[0] <= n <= QUERY_WORDS[1]:
            errs.append(f"{item['asset']}: {n} words in {q!r} ({QUERY_WORDS[0]}-{QUERY_WORDS[1]})")
        elif item["asset"] in out:
            errs.append(f"{item['asset']}: more than one query")
        else:
            out[item["asset"]] = q
    missing = [s for s in srcs if s not in out and not any(e.startswith(s) for e in errs)]
    if missing:
        errs.append(f"no query for {', '.join(missing)}")
    if errs:
        raise QueryError("Claude's queries broke the rules: " + "; ".join(errs))
    return out


class ClaudeQueries:
    """Callable: (premise, [Wanted]) -> {src: query}. One request for all of them."""

    def __init__(self, client=None, model: str = CLAUDE_MODEL):
        self._client = client
        self.model = model

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def __call__(self, premise: str, wanted: list) -> dict[str, str]:
        import anthropic
        srcs = [w.src for w in wanted]
        try:
            response = self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=2000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # a safety decline is retried server-side on the recommended model
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema(srcs)}},
                system=SYSTEM,
                messages=[{"role": "user", "content": build_prompt(premise, wanted)}],
            )
        except anthropic.AuthenticationError as exc:
            raise QueryError(f"Claude credentials rejected: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise QueryError(f"could not reach the Claude API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise QueryError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except TypeError as exc:  # no credential source resolved at request time
            raise QueryError(f"no Claude credentials: {exc}") from exc
        if response.stop_reason == "refusal":
            raise QueryError("Claude declined to suggest queries")
        if response.stop_reason == "max_tokens":
            raise QueryError("Claude hit max_tokens before finishing the queries")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise QueryError(f"Claude returned no text (stop_reason {response.stop_reason})")
        return parse(text, srcs)


def suggest(ws: Path, suggester) -> tuple[dict[str, str], str | None]:
    """Ask `suggester` for every wanted asset without a brief query and save the answers.
    Returns (the new suggestions, None) or ({}, why it failed). Nothing to ask: ({}, None)."""
    from .scrape import wanted_assets
    wanted, _ = wanted_assets(ws)
    todo = [w for w in wanted if w.query is None]
    if not todo:
        return {}, None
    try:
        premise = json.loads((ws / "brief.json").read_text(encoding="utf-8")).get("brief") or ""
    except (OSError, ValueError, AttributeError):
        premise = ""
    try:
        got = suggester(premise, todo)
    except QueryError as exc:
        return {}, str(exc)
    save(ws, got, getattr(suggester, "model", "model"))
    return got, None
