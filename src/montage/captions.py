"""Narration word timings -> timeline frames: the scene_plan caption track and captions.json.

Each beat's words are in ms from the start of its wav (narrate.py). On the timeline a word
starts at voice_clip.startFrame + round(startMs * fps / 1000). Rounding can make a short word
(under one frame) empty or collide with its neighbour, so starts are forced strictly increasing,
every word lasts at least one frame, and a word ends no later than the next one starts. The
caption clip spans the voice clip, stretched to cover its last word if rounding pushed past it.

captions.json is the guide's Stage 1 sibling file (word-level offsets); scene_plan.json carries
the same frames in its caption track, so the renderer needs nothing but the plan.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from .narrate import Narration

CAPTIONS_VERSION = 1


def _frame(ms: int, fps: int) -> int:
    return round(ms * fps / 1000)


def word_frames(narr: Narration, clip_start: int, fps: int, limit: int) -> list[dict]:
    """One beat's words on the timeline: [{text, startMs, endMs, startFrame, endFrame}]."""
    out: list[dict] = []
    for w in narr.words:
        start = clip_start + _frame(w.start_ms, fps)
        if out:
            start = max(start, out[-1]["startFrame"] + 1)
        out.append({"text": w.text, "startMs": w.start_ms, "endMs": w.end_ms,
                    "startFrame": start, "endFrame": max(clip_start + _frame(w.end_ms, fps), start + 1)})
    for cur, nxt in zip(out, out[1:]):
        cur["endFrame"] = min(cur["endFrame"], nxt["startFrame"])
    # never past the end of the video (only reachable with a degenerate, sub-frame last beat)
    out = [w for w in out if w["startFrame"] < limit]
    for w in out:
        w["endFrame"] = min(w["endFrame"], limit)
    return out


def build(voice_clips: Sequence[dict], narration: Sequence[Narration], fps: int, duration_frames: int,
          style: str) -> tuple[dict | None, dict]:
    """-> (scene_plan caption track or None, captions.json document)."""
    beats, clips = [], []
    for i, (vc, narr) in enumerate(zip(voice_clips, narration), 1):
        words = word_frames(narr, vc["startFrame"], fps, duration_frames)
        beats.append({"beat": i, "clip": vc["id"], "src": narr.src, "method": narr.word_method,
                      "offsetFrame": vc["startFrame"], "offsetMs": round(vc["startFrame"] * 1000 / fps),
                      "words": words})
        if not words:
            continue
        start = vc["startFrame"]
        end = max(start + vc["durationFrames"], words[-1]["endFrame"])
        clips.append({"id": f"cap_{i:02d}", "startFrame": start, "durationFrames": end - start,
                      "words": [{"text": w["text"], "startFrame": w["startFrame"], "endFrame": w["endFrame"]}
                                for w in words]})
    doc = {"version": CAPTIONS_VERSION, "fps": fps, "style": style, "beats": beats}
    if style == "none" or not clips:
        return None, doc
    return {"id": "captions", "type": "caption", "style": style, "clips": clips}, doc


def write_captions(doc: dict, workspace: Path) -> Path:
    path = workspace / "captions.json"
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
