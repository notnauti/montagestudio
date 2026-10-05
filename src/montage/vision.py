"""`montage scrape --vision`: Claude looks at each asset's candidate previews and scores how well
each one shows what the asset's description asks for.

The lexical ranker reads titles and captions, so a control-room photo titled "Mirror Deployment"
matches "mirror" as well as the mirror does. Vision sees the picture. It only re-orders the top
candidates the sheet keeps (candidates.py); licensing and provenance are decided before it runs
and are not touched by it.

Gated on credentials: `unavailable_reason()` says why Claude cannot run here (no package, no
credential source); the CLI refuses `--vision` with exit 2 before any request.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

VISION_MODEL = "claude-opus-5-5"
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
               ".webp": "image/webp"}

SYSTEM = """You check stock images for a video editor. For each numbered candidate image, score from 0 \
to 10 how well the picture itself shows what the wanted shot describes: 10 is exactly that subject and \
kind of scene, 5 is the right subject in the wrong kind of shot (people or a room in front of it, a \
diagram instead of a photo, a distant view), 0 is unrelated. Judge the pixels, not the title. Give a \
reason of at most 15 words for each."""


class VisionError(RuntimeError):
    """Claude could not score the candidates (credentials rejected, API error, refusal, bad output)."""


def unavailable_reason() -> str | None:
    """None when a Claude client can be built with some credential source; else why not.
    Makes no request."""
    try:
        import anthropic
    except ImportError:
        return "the anthropic package is not installed in this environment"
    try:
        client = anthropic.Anthropic()
    except Exception as exc:  # a broken profile or config raises at construction
        return f"no usable Claude credentials ({exc})"
    if not (client.api_key or client.auth_token or getattr(client, "credentials", None)):
        return ("no Claude credentials: set ANTHROPIC_API_KEY (the project .env is read by `montage`) "
                "or run `ant auth login`")
    return None


def schema() -> dict:
    return {
        "type": "object",
        "properties": {"scores": {"type": "array", "items": {
            "type": "object",
            "properties": {"candidate": {"type": "integer"}, "score": {"type": "integer"},
                           "reason": {"type": "string"}},
            "required": ["candidate", "score", "reason"],
            "additionalProperties": False,
        }}},
        "required": ["scores"],
        "additionalProperties": False,
    }


def build_content(wanted, items: list) -> list[dict]:
    """items: [(rank, Candidate, preview Path)]. One text block naming the wanted shot, then per
    candidate a label and its preview image."""
    content: list[dict] = [{"type": "text", "text": f"Wanted shot ({wanted.kind}): {wanted.description}"
                            + (f"\nSearch query: {wanted.query}" if wanted.query else "")}]
    for rank, cand, path in items:
        media = MEDIA_TYPES.get(Path(path).suffix.lower())
        if media is None:
            continue
        content.append({"type": "text", "text": f"Candidate {rank} (title: {cand.title[:120]})"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                    "data": base64.standard_b64encode(Path(path).read_bytes())
                                                    .decode("ascii")}})
    return content


def parse_scores(text: str, ranks: set[int]) -> dict[int, tuple[int, str]]:
    try:
        raw = json.loads(text)
        rows = raw["scores"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise VisionError(f"Claude returned unparseable scores: {text[:200]!r}") from exc
    out: dict[int, tuple[int, str]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        n, sc = row.get("candidate"), row.get("score")
        if n in ranks and isinstance(sc, (int, float)) and not isinstance(sc, bool):
            out[n] = (max(0, min(10, int(round(sc)))), str(row.get("reason", ""))[:200])
    if not out:
        raise VisionError("Claude scored none of the candidates")
    return out


class ClaudeVision:
    """Callable scorer: (wanted, [(rank, Candidate, preview Path)]) -> {rank: (score 0-10, reason)}.
    One request per asset, all its previews in it."""

    def __init__(self, client=None, model: str = VISION_MODEL):
        self._client = client
        self.model = model

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def __call__(self, wanted, items: list) -> dict[int, tuple[int, str]]:
        import anthropic
        content = build_content(wanted, items)
        if len(content) == 1:
            return {}
        try:
            response = self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # a safety decline is retried server-side on the recommended model
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema()}},
                system=SYSTEM,
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.AuthenticationError as exc:
            raise VisionError(f"Claude credentials rejected: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise VisionError(f"could not reach the Claude API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise VisionError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except TypeError as exc:  # no credential source resolved at request time
            raise VisionError(f"no Claude credentials: {exc}") from exc
        if response.stop_reason == "refusal":
            raise VisionError("Claude declined to score these images")
        if response.stop_reason == "max_tokens":
            raise VisionError("Claude hit max_tokens before finishing the scores")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise VisionError(f"Claude returned no text (stop_reason {response.stop_reason})")
        return parse_scores(text, {rank for rank, _, _ in items})
