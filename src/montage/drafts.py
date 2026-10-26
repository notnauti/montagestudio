"""`montage publish-pack --draft`: Claude drafts the viewer-facing text of the pack.

Without it the pack's title is the brief's title, the description opens with the brief (written
for montage, not for viewers: "A short documentary for an educational audience about ..."),
chapter titles are each chapter's first clause, and tags are the brief's content words; on JWST
all of it was rewritten by hand. With it, Claude writes a title, a description intro, one title per
chapter and tags from the approved script, and nothing else: credits, the NASA line and the
disclosure stay deterministic, copied from the records.

Opt-in like `scrape --vision`: refused with exit 2 before any request when no credential source
resolves. A draft that breaks YouTube's limits or the chapter count is rejected whole
(DraftError); the pack is then written with the deterministic text and the command exits 2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .script import CLAUDE_MODEL

TITLE_MAX = 70          # YouTube allows 100; past ~70 the title is cut off in search and suggestions
INTRO_MAX = 1500
CHAPTER_TITLE_MAX = 60
TAGS_MAX = 15
TAGS_CHARS = 500        # YouTube's limit on the tags field

SYSTEM = f"""You write YouTube upload text for a short educational video. You get the video's \
brief (written for the production tool, not for viewers), its approved narration in order, and \
its chapters with the narration each one covers.

- title: at most {TITLE_MAX} characters. Specific to what the video shows; no clickbait, no all \
caps, no emoji, no question it never answers.
- description: two or three short sentences for a viewer deciding whether to watch. Plain \
words. Not "in this video".
- chapterTitles: exactly one per chapter, in order, 2 to 6 words, naming what that chapter's \
narration covers.
- tags: up to {TAGS_MAX} search phrases a viewer might type, most specific first.

Use only facts the narration states; add no numbers, dates or names it does not contain. Never \
use the characters < or >."""


class DraftError(RuntimeError):
    """Claude could not draft the text, or its draft broke a limit (every reason in the message)."""


@dataclass(frozen=True)
class Draft:
    title: str
    description: str
    chapter_titles: tuple[str, ...]
    tags: tuple[str, ...]
    model: str


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "chapterTitles": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "description", "chapterTitles", "tags"],
        "additionalProperties": False,
    }


def build_prompt(brief_title: str, brief_text: str, narration: list[str],
                 chapters: list[tuple[str, list[str]]]) -> str:
    lines = [f"Brief title: {brief_title}", f"Brief: {brief_text}", "", "Narration, in order:"]
    lines += [f"{i}. {n}" for i, n in enumerate(narration, 1)]
    lines += ["", f"Chapters ({len(chapters)}; write exactly {len(chapters)} chapterTitles):"]
    for i, (stamp, said) in enumerate(chapters, 1):
        lines.append(f"{i}. at {stamp}: " + " ".join(said))
    if not chapters:
        lines.append("(none: return an empty chapterTitles list)")
    return "\n".join(lines)


def parse_draft(text: str, n_chapters: int, model: str) -> Draft:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DraftError(f"Claude's draft is not JSON ({exc})") from exc
    errs = []
    if not isinstance(raw, dict):
        raise DraftError("Claude's draft is not an object")
    title = raw.get("title") if isinstance(raw.get("title"), str) else ""
    desc = raw.get("description") if isinstance(raw.get("description"), str) else ""
    chapters = [c.strip() for c in raw.get("chapterTitles") or [] if isinstance(c, str)]
    tags = [t.strip() for t in raw.get("tags") or [] if isinstance(t, str) and t.strip()]
    title, desc = title.strip(), desc.strip()
    if not title or len(title) > TITLE_MAX:
        errs.append(f"title is {len(title)} characters (1-{TITLE_MAX})")
    if not desc or len(desc) > INTRO_MAX:
        errs.append(f"description is {len(desc)} characters (1-{INTRO_MAX})")
    if len(chapters) != n_chapters:
        errs.append(f"{len(chapters)} chapter titles for {n_chapters} chapters")
    long_ = [c for c in chapters if not c or len(c) > CHAPTER_TITLE_MAX]
    if long_:
        errs.append(f"{len(long_)} chapter title(s) empty or over {CHAPTER_TITLE_MAX} characters")
    if len(tags) > TAGS_MAX or len(", ".join(tags)) > TAGS_CHARS:
        errs.append(f"{len(tags)} tags, {len(', '.join(tags))} characters (at most {TAGS_MAX}, {TAGS_CHARS})")
    if any(ch in s for s in [title, desc, *chapters, *tags] for ch in "<>"):
        errs.append("'<' or '>' in the text, which YouTube rejects")
    if errs:
        raise DraftError("Claude's draft broke the limits: " + "; ".join(errs))
    return Draft(title, desc, tuple(chapters), tuple(tags), model)


class ClaudeDrafter:
    """Callable: (brief_title, brief_text, narration, chapters) -> Draft. One request."""

    def __init__(self, client=None, model: str = CLAUDE_MODEL):
        self._client = client
        self.model = model

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def __call__(self, brief_title: str, brief_text: str, narration: list[str],
                 chapters: list[tuple[str, list[str]]]) -> Draft:
        import anthropic
        try:
            response = self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # a safety decline is retried server-side on the recommended model
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema()}},
                system=SYSTEM,
                messages=[{"role": "user", "content": build_prompt(brief_title, brief_text, narration, chapters)}],
            )
        except anthropic.AuthenticationError as exc:
            raise DraftError(f"Claude credentials rejected: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise DraftError(f"could not reach the Claude API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise DraftError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except TypeError as exc:  # no credential source resolved at request time
            raise DraftError(f"no Claude credentials: {exc}") from exc
        if response.stop_reason == "refusal":
            raise DraftError("Claude declined to draft the upload text")
        if response.stop_reason == "max_tokens":
            raise DraftError("Claude hit max_tokens before finishing the draft")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise DraftError(f"Claude returned no text (stop_reason {response.stop_reason})")
        return parse_draft(text, len(chapters), getattr(response, "model", None) or self.model)
