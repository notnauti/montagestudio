"""Brief + script + narration -> scene_plan.json (the slice 1 contract).

Timeline, per beat i (in frames; LEAD / TAIL / END_HOLD / CROSSFADE come from the pipeline recipe):
  beat_i  = LEAD + narration_i + TAIL          (last beat holds END_HOLD instead of TAIL)
  T_i     = sum of earlier beats                (where beat i starts)
  voice   : starts at T_i + LEAD, lasts narration_i
  visual  : starts at T_i, lasts beat_i + CROSSFADE (last: beat_i), crossfades into the next
Every crossfade therefore sits inside the next beat's LEAD, before its narration begins, and
next.start == prev.end - CROSSFADE holds by construction.

Captions: each beat's narration word timings become one caption clip on a `caption` track
(captions.py), styled by the recipe's caption_style; "none" or no word timings = no track.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from . import captions as captions_mod
from .brief import Brief
from .narrate import Narration
from .pipeline import Recipe
from .script import Script

EXPLAINER = Recipe()  # the explainer recipe's timing; pipelines/explainer.json carries the same numbers


class ComposeError(ValueError):
    pass


def compose(brief: Brief, script: Script, narration: list[Narration], recipe: Recipe = EXPLAINER) -> dict:
    if len(narration) != len(script.beats):
        raise ComposeError(f"{len(script.beats)} beats but {len(narration)} narration files")
    fps = recipe.fps
    assets = {a.src: a for a in brief.assets}
    last = len(script.beats) - 1

    voice_clips, video_clips, errors = [], [], []
    cursor: dict[str, int] = {}  # next unused frame per video asset, so reuse doesn't repeat footage
    t = 0
    for i, (beat, narr) in enumerate(zip(script.beats, narration)):
        spoken = max(1, math.floor(narr.seconds * fps))  # floor: never ask for more audio than the wav has
        beat_frames = recipe.lead + spoken + (recipe.end_hold if i == last else recipe.tail)
        visual_frames = beat_frames + (0 if i == last else recipe.crossfade)

        voice_clips.append({"id": f"vo_{i + 1:02d}", "src": narr.src,
                            "startFrame": t + recipe.lead, "durationFrames": spoken})

        asset = assets[beat.asset]
        clip = {"id": f"shot_{i + 1:02d}", "src": asset.src, "startFrame": t, "durationFrames": visual_frames}
        if asset.is_still:
            if beat.motion != "none":
                scale = [1.0, recipe.zoom] if beat.motion == "zoom_in" else [recipe.zoom, 1.0]
                clip["motion"] = {"scale": scale, "easing": recipe.easing}
        else:
            available = math.floor((asset.duration or 0) * fps)
            if visual_frames > available:
                errors.append(f"beat {i + 1}: needs {visual_frames / fps:.2f}s of '{asset.src}' but it is only "
                              f"{available / fps:.2f}s — shorten the line in script.json or point it at a still")
            else:
                used = cursor.get(asset.src, 0)
                if used + visual_frames <= available:
                    start = used                          # next fresh stretch
                elif used < available:
                    start = available - visual_frames     # the source's last stretch: mostly unseen footage
                else:
                    start = 0                             # source used up: start it over
                if start:
                    clip["sourceInFrame"] = start
                cursor[asset.src] = start + visual_frames
        if i != last:
            clip["transition"] = {"type": "crossfade", "durationFrames": recipe.crossfade}
        video_clips.append(clip)
        t += beat_frames

    if errors:
        raise ComposeError("script does not fit the assets:\n" + "\n".join(f"  - {e}" for e in errors))

    tracks = [
        {"id": "visuals", "type": "video", "clips": video_clips},
        {"id": "narration", "type": "audio", "clips": voice_clips},
    ]
    caption_track, _ = captions_mod.build(voice_clips, narration, fps, t, recipe.caption_style)
    if caption_track:
        tracks.append(caption_track)
    if brief.music_src:
        music_frames = min(t, math.floor((brief.music_duration or 0) * fps))
        if music_frames > 0:
            tracks.append({"id": "music", "type": "audio", "clips": [
                {"id": "music_bed", "src": brief.music_src, "startFrame": 0,
                 "durationFrames": music_frames, "volume": brief.music_volume}]})

    return {"meta": {"title": script.title, "resolution": [recipe.width, recipe.height],
                     "fps": fps, "durationFrames": t},
            "tracks": tracks}


def write_plan(plan: dict, workspace: Path) -> Path:
    path = workspace / "scene_plan.json"
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return path
