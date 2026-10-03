"""timeline_retime_captions: snap caption word edges to where speech starts and stops in the audio.

The guide says "re-aligns subtitle layout timestamps to audio waveform peaks". What a word edge
has in the waveform is an energy *edge*, not a peak, so this snaps to those (inferred reading):

  1. decode the voice clip under each caption clip (ffmpeg -> 16 kHz mono s16le on stdout)
  2. RMS loudness per 10 ms window, in dBFS
  3. an onset = speech starts after at least MIN_GAP_MS below the threshold; an offset = speech
     stops and stays below it for at least MIN_GAP_MS
  4. each word start moves to the nearest onset within toleranceMs, each word end to the nearest
     offset within toleranceMs; words inside connected speech have no edge nearby and stay put
  5. frames are re-made strictly ordered, at least 1 frame long and inside their caption clip

Only the plan's caption words move; audio and video clips are untouched.
"""

from __future__ import annotations

import math
import subprocess
from array import array
from dataclasses import dataclass
from pathlib import Path

from .edit import EditError, EditResult, find_track
from .probe import tool

RATE = 16000
WINDOW_MS = 10
MIN_GAP_MS = 40
DEFAULT_THRESHOLD_DB = -40.0  # same level the captions accuracy check used with silencedetect


@dataclass(frozen=True)
class Edges:
    onsets: list[float]   # ms on the timeline
    offsets: list[float]


def envelope(src: Path, start_s: float, dur_s: float) -> list[float]:
    """dBFS per WINDOW_MS window of [start_s, start_s + dur_s) of the file's audio."""
    cmd = [tool("ffmpeg"), "-v", "error", "-ss", f"{start_s:.6f}", "-t", f"{dur_s:.6f}", "-i", str(src),
           "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise EditError([f"could not decode audio from {src.name}: {msg[-1] if msg else r.returncode}"])
    samples = array("h")
    samples.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    n = RATE * WINDOW_MS // 1000
    out = []
    for i in range(0, len(samples) - n + 1, n):
        chunk = samples[i:i + n]
        rms = math.sqrt(sum(s * s for s in chunk) / n)
        out.append(20 * math.log10(rms / 32768) if rms > 0 else -120.0)
    return out


def edges(db: list[float], origin_ms: float, threshold_db: float) -> Edges:
    """Speech onsets / offsets in a dB envelope whose first window starts at origin_ms."""
    gap = max(1, MIN_GAP_MS // WINDOW_MS)
    voiced = [d >= threshold_db for d in db]
    onsets, offsets = [], []
    for i in range(1, len(voiced)):
        if voiced[i] and not voiced[i - 1] and i >= gap and not any(voiced[i - gap:i]):
            onsets.append(origin_ms + i * WINDOW_MS)
        if not voiced[i] and voiced[i - 1] and not any(voiced[i:i + gap]):
            offsets.append(origin_ms + i * WINDOW_MS)
    # the clip's own first / last window counts when speech is already on there
    if voiced and voiced[0]:
        onsets.insert(0, origin_ms)
    if voiced and voiced[-1]:
        offsets.append(origin_ms + len(voiced) * WINDOW_MS)
    return Edges(onsets, offsets)


def _nearest(target: float, points: list[float], tol: float) -> float | None:
    best = min(points, key=lambda p: abs(p - target), default=None)
    return best if best is not None and abs(best - target) <= tol else None


def _voice_track(raw: dict, caption_clips: list[dict], audio_track_id: str | None) -> dict:
    audio = [t for t in raw.get("tracks") or [] if t.get("type") == "audio"]
    if audio_track_id is not None:
        t = find_track(raw, audio_track_id)
        if t.get("type") != "audio":
            raise EditError([f"audioTrackId '{audio_track_id}' is a {t.get('type')} track, not audio"])
        return t
    starts = {c["startFrame"] for c in caption_clips}
    scored = sorted(((sum(c["startFrame"] in starts for c in t["clips"]), n, t) for n, t in enumerate(audio)),
                    key=lambda x: (-x[0], x[1]))
    if scored and scored[0][0] > 0:
        return scored[0][2]  # the planner's caption clips start exactly on their voice clip
    if len(audio) == 1:
        return audio[0]
    raise EditError([f"cannot tell which audio track the captions follow; pass audioTrackId "
                     f"(one of {', '.join(t['id'] for t in audio)})"])


def retime_captions(raw: dict, workspace: Path, track_id: str, tolerance_ms: float,
                    audio_track_id: str | None = None, threshold_db: float = DEFAULT_THRESHOLD_DB,
                    envelope_fn=envelope) -> EditResult:
    if isinstance(tolerance_ms, bool) or not isinstance(tolerance_ms, (int, float)) or not 0 < tolerance_ms <= 1000:
        raise EditError([f"toleranceMs: must be a number in (0, 1000], got {tolerance_ms!r}"])
    if isinstance(threshold_db, bool) or not isinstance(threshold_db, (int, float)) or not -90 <= threshold_db < 0:
        raise EditError([f"thresholdDb: must be a number in [-90, 0), got {threshold_db!r}"])
    track = find_track(raw, track_id)
    if track.get("type") != "caption":
        raise EditError([f"track '{track_id}' is a {track.get('type')} track; captionsTrackId must be the "
                         f"caption track"])
    fps = raw["meta"]["fps"]
    voice = _voice_track(raw, track["clips"], audio_track_id)
    to_ms = lambda f: f * 1000 / fps  # noqa: E731
    to_frame = lambda ms: round(ms * fps / 1000)  # noqa: E731

    changes, notes, moved, shifts = [], [], 0, []
    for cap in track["clips"]:
        c0, c1 = cap["startFrame"], cap["startFrame"] + cap["durationFrames"]
        under = [a for a in voice["clips"] if a["startFrame"] < c1 and a["startFrame"] + a["durationFrames"] > c0]
        if not under:
            notes.append(f"'{cap['id']}': no clip on '{voice['id']}' under it; left as is")
            continue
        found = Edges([], [])
        for a in under:
            src = (workspace / a["src"]).resolve()
            db = envelope_fn(src, a.get("sourceInFrame", 0) / fps, a["durationFrames"] / fps)
            e = edges(db, to_ms(a["startFrame"]), threshold_db)
            found.onsets.extend(e.onsets)
            found.offsets.extend(e.offsets)
        words = cap["words"]
        cand = []
        for w in words:
            s = _nearest(to_ms(w["startFrame"]), found.onsets, tolerance_ms)
            e = _nearest(to_ms(w["endFrame"]), found.offsets, tolerance_ms)
            cand.append((to_frame(s) if s is not None else w["startFrame"],
                         to_frame(e) if e is not None else w["endFrame"]))
        prev_end = c0
        new = []
        for n, (w, (s, e)) in enumerate(zip(words, cand)):
            nxt = cand[n + 1][0] if n + 1 < len(cand) else c1
            s = min(max(s, prev_end), c1 - 1)
            e = max(min(e, nxt, c1), s + 1)
            if e > c1 or s < prev_end:  # cannot fit: keep the original
                s, e = w["startFrame"], w["endFrame"]
            new.append({"text": w["text"], "startFrame": s, "endFrame": e})
            prev_end = e
        for w, nw in zip(words, new):
            if (w["startFrame"], w["endFrame"]) != (nw["startFrame"], nw["endFrame"]):
                moved += 1
                shifts += [abs(nw["startFrame"] - w["startFrame"]), abs(nw["endFrame"] - w["endFrame"])]
                changes.append(f"'{cap['id']}' '{w['text']}': {w['startFrame']}-{w['endFrame']} -> "
                               f"{nw['startFrame']}-{nw['endFrame']}")
        cap["words"] = new
    total = sum(len(c["words"]) for c in track["clips"])
    head = (f"retimed {moved} of {total} words on '{track_id}' against '{voice['id']}' "
            f"(tolerance {tolerance_ms:g} ms, speech threshold {threshold_db:g} dBFS"
            + (f", largest shift {max(shifts)} frames)" if shifts else ")"))
    if not moved:
        notes.append("no word edge had a speech onset/offset within the tolerance that differs from its "
                     "current frame; nothing to change")
    return EditResult(raw, [head] + changes, notes)
