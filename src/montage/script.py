"""Brief -> script: a title plus beats, each a line of narration and the asset shown under it.

Two writers behind one call. Claude (claude-opus-5) when credentials resolve; local Ollama
(qwen2.5:7b) otherwise. Both get the same prompt and the same JSON schema, and both results
go through the same validation, so the planner never sees an asset that isn't in the brief.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .brief import Brief
from .pipeline import Pipeline, load_pipeline

CLAUDE_MODEL = "claude-opus-5"
OLLAMA_MODEL = os.environ.get("MONTAGE_OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
WORDS_PER_SECOND = 2.5  # Piper's en_US voices at default speed
MOTIONS = ("none", "zoom_in", "zoom_out")


class ScriptError(RuntimeError):
    pass


class WriterUnavailable(ScriptError):
    """This writer can't run here (no package, no credentials, no server) — try the next one."""


@dataclass(frozen=True)
class Beat:
    narration: str
    asset: str  # brief asset src
    motion: str


@dataclass(frozen=True)
class Script:
    title: str
    beats: tuple[Beat, ...]
    writer: str  # which model wrote it

    def to_json(self) -> dict:
        return {"title": self.title, "writer": self.writer,
                "beats": [{"narration": b.narration, "asset": b.asset, "motion": b.motion} for b in self.beats]}


# ── prompt ────────────────────────────────────────────────────────────────────

SYSTEM_TEMPLATE = """You write narration scripts for short {kind} videos. The narration is read aloud by a \
text-to-speech voice over the visuals you choose, so write for the ear: plain spoken sentences, no \
headings, markdown, stage directions, emoji or bracketed notes. Spell out anything a voice would \
stumble on (symbols, abbreviations nobody says aloud).

Split the script into beats. A beat is one to three sentences said over one visual. Pick each beat's \
visual from the asset list only, by matching what is being said to the asset's description; reuse an \
asset when nothing else fits. For still images choose a slow zoom_in or zoom_out to keep the frame \
alive; for videos choose none. A video asset can only cover as much narration as its length allows, \
so keep beats over video assets within their word limit."""

SYSTEM = SYSTEM_TEMPLATE.format(kind="explainer")  # the explainer prompt, unchanged from slice 1


def system_prompt(pipeline: Pipeline) -> str:
    return SYSTEM_TEMPLATE.format(kind=pipeline.name)


def _pipeline(brief: Brief, pipeline: Pipeline | None) -> Pipeline:
    return pipeline or load_pipeline(brief.recipe)


def _asset_lines(brief: Brief) -> str:
    lines = []
    for a in brief.assets:
        if a.is_still:
            lines.append(f"- {a.src} (still image): {a.description}")
        else:
            words = int((a.duration or 0) * WORDS_PER_SECOND)
            lines.append(f"- {a.src} (video, {a.duration:.1f}s, at most {words} words over it): {a.description}")
    return "\n".join(lines)


def build_prompt(brief: Brief, pipeline: Pipeline | None = None) -> str:
    p = _pipeline(brief, pipeline)
    words = int(brief.target_seconds * WORDS_PER_SECOND)
    fewest, most = p.beat_range(brief.target_seconds)
    lo_s, hi_s = p.beat_seconds
    return (f"Title: {brief.title}\n\nBrief:\n{brief.text}\n\n"
            f"Format: {p.name} — {p.description}\n"
            f"Tone and pacing: {p.tone}\n\n"
            f"Target length: about {brief.target_seconds} seconds of narration, roughly {words} words in total.\n"
            f"Beats: write {fewest} to {most} beats, each {lo_s:g} to {hi_s:g} seconds "
            f"({int(lo_s * WORDS_PER_SECOND)} to {int(hi_s * WORDS_PER_SECOND)} words) of narration.\n\n"
            f"Assets:\n{_asset_lines(brief)}")


def script_schema(brief: Brief) -> dict:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "beats": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string"},
                    "asset": {"type": "string", "enum": [a.src for a in brief.assets]},
                    "motion": {"type": "string", "enum": list(MOTIONS)},
                },
                "required": ["narration", "asset", "motion"],
                "additionalProperties": False,
            }},
        },
        "required": ["title", "beats"],
        "additionalProperties": False,
    }


# ── validation (shared by both writers and by hand-edited script.json) ─────────

def parse_script(raw: object, brief: Brief, writer: str) -> Script:
    errs: list[str] = []
    if not isinstance(raw, dict):
        raise ScriptError("script: must be a JSON object")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        errs.append("script.title: must be a non-empty string")
    beats_raw = raw.get("beats")
    if not isinstance(beats_raw, list) or not beats_raw:
        errs.append("script.beats: must be a non-empty array")
        beats_raw = []
    known = {a.src for a in brief.assets}
    beats = []
    for i, b in enumerate(beats_raw):
        where = f"script.beats[{i}]"
        if not isinstance(b, dict):
            errs.append(f"{where}: must be an object")
            continue
        text, asset, motion = b.get("narration"), b.get("asset"), b.get("motion", "none")
        if not isinstance(text, str) or not text.strip():
            errs.append(f"{where}.narration: must be non-empty text")
        if asset not in known:
            errs.append(f"{where}.asset: {asset!r} is not in the brief's asset list")
        if motion not in MOTIONS:
            errs.append(f"{where}.motion: must be one of {list(MOTIONS)}, got {motion!r}")
        if not errs:
            beats.append(Beat(text.strip(), asset, motion))
    if errs:
        raise ScriptError("invalid script:\n" + "\n".join(f"  - {e}" for e in errs))
    return Script(title.strip(), tuple(beats), writer)


# ── writers ───────────────────────────────────────────────────────────────────

def write_with_claude(brief: Brief, pipeline: Pipeline | None = None) -> Script:
    p = _pipeline(brief, pipeline)
    try:
        import anthropic
    except ImportError as exc:
        raise WriterUnavailable("anthropic package not installed") from exc
    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # a safety decline is retried server-side on Anthropic's recommended model
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": script_schema(brief)}},
            system=system_prompt(p),
            messages=[{"role": "user", "content": build_prompt(brief, p)}],
        )
    except anthropic.AuthenticationError as exc:
        raise WriterUnavailable(f"Claude credentials rejected: {exc.message}") from exc
    except TypeError as exc:  # raised before any request when no credential source resolves
        if "authentication" in str(exc).lower() or "api_key" in str(exc):
            raise WriterUnavailable("no Claude credentials (set ANTHROPIC_API_KEY or run `ant auth login`)") from exc
        raise
    except anthropic.APIConnectionError as exc:
        raise ScriptError(f"could not reach the Claude API: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise ScriptError(f"Claude API error {exc.status_code}: {exc.message}") from exc

    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None)
        raise ScriptError(f"Claude declined to write this script (category: {category}); fallback model declined too")
    if response.stop_reason == "max_tokens":
        raise ScriptError("Claude hit max_tokens before finishing the script")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ScriptError(f"Claude returned no text (stop_reason {response.stop_reason})")
    return parse_script(json.loads(text), brief, writer=response.model)


def write_with_ollama(brief: Brief, pipeline: Pipeline | None = None) -> Script:
    p = _pipeline(brief, pipeline)
    body = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": script_schema(brief),
        "messages": [{"role": "system", "content": system_prompt(p)},
                     {"role": "user", "content": build_prompt(brief, p)}],
        "options": {"temperature": 0.7, "num_ctx": 8192},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_HOST}/api/chat", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise ScriptError(f"Ollama error {exc.code}: {exc.read().decode(errors='replace')[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WriterUnavailable(f"Ollama not reachable at {OLLAMA_HOST}: {exc}") from exc
    try:
        raw = json.loads(data["message"]["content"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ScriptError(f"Ollama returned unparseable output: {str(data)[:300]}") from exc
    return parse_script(raw, brief, writer=f"ollama:{OLLAMA_MODEL}")


WRITERS = {"claude": write_with_claude, "ollama": write_with_ollama}


def write_script(brief: Brief, writer: str = "auto", log=print) -> Script:
    """`auto` tries Claude, then Ollama — but only falls through when a writer is *unavailable*
    (no package / credentials / server). A real failure (API error, refusal, bad output) stops."""
    if writer != "auto":
        return WRITERS[writer](brief)
    try:
        return write_with_claude(brief)
    except WriterUnavailable as exc:
        log(f"Claude unavailable ({exc}); falling back to local {OLLAMA_MODEL}")
    return write_with_ollama(brief)
