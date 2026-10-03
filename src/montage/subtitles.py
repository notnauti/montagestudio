"""Caption track -> ASS subtitle file -> burned in by FFmpeg's `ass` filter (libass).

Each caption clip (one beat) is split into pages of a few words so a long line never fills the
screen. A page is on screen from its first word's start until the next page starts (or the clip
ends). Within a page there is one ASS event per word, from that word's start to the next word's
start, so exactly one word is "current" on every frame. Styles:

  highlight  the whole page is visible; the current word is gold
  pop        words appear one by one; the current word is gold and pops from 125% to 100%.
             Unspoken words are drawn fully transparent, so the line never reflows.

Frame numbers are mapped onto the output grid first (render.out_frames), then written as ASS
centiseconds floor(frame * 100 / fps): libass shows an event when start <= t < end, so an event
starting at frame f is first drawn on frame f (exact for fps <= 100).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Callable

from .plan import CaptionClip, CaptionTrack

MAX_PAGE_WORDS = 6
MAX_PAGE_CHARS = 32
FONT = "Arial"
WHITE = "&H00FFFFFF&"
GOLD = "&H0000D7FF&"  # ASS colours are &HAABBGGRR: this is #FFD700
POP_MS = 120

_SENTENCE_END = re.compile(r"[.!?;:]['\")\]]*$")
_CLAUSE_END = re.compile(r"[,—–]['\")\]]*$")


def pages(clip: CaptionClip) -> list[list[int]]:
    """Word indexes grouped into on-screen pages: split at sentence (and long-clause) ends, then
    each segment over MAX_PAGE_CHARS / MAX_PAGE_WORDS into the fewest pages of near-equal length,
    so a line never strands one short word on a page of its own."""
    words = clip.words
    segments: list[list[int]] = [[]]
    for i, w in enumerate(words):
        segments[-1].append(i)
        if _SENTENCE_END.search(w.text) or (_CLAUSE_END.search(w.text) and len(segments[-1]) >= 3):
            segments.append([])
    out: list[list[int]] = []
    for seg in (s for s in segments if s):
        lens = [len(words[i].text) for i in seg]
        chars = sum(lens) + len(seg) - 1
        k = min(len(seg), max(math.ceil(chars / MAX_PAGE_CHARS), math.ceil(len(seg) / MAX_PAGE_WORDS)))
        target = chars / k
        page: list[int] = []
        made = 0
        acc = 0  # chars of the segment up to the end of the previous word
        for j, i in enumerate(seg):
            after = acc + (1 if j else 0) + lens[j]
            boundary = target * (made + 1)
            if made < k - 1 and after >= boundary:
                # cut on whichever side of this word lands nearer the boundary
                if page and boundary - acc < after - boundary:
                    out.append(page)
                    page = [i]
                else:
                    out.append(page + [i])
                    page = []
                made += 1
            else:
                page.append(i)
            acc = after
        if page:
            out.append(page)
    return out


def _ts(frame: int, fps: int) -> str:
    cs = frame * 100 // fps
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    # ASS has no escape for override braces, and backslash starts \N / \h; swap them out.
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def _line(texts: list[str], current: int, style: str) -> str:
    parts = []
    for k, t in enumerate(texts):
        t = _clean(t)
        if k == current:
            pop = rf"\fscx125\fscy125\t(0,{POP_MS},\fscx100\fscy100)" if style == "pop" else ""
            parts.append(rf"{{\c{GOLD}{pop}}}{t}{{\r}}")
        elif style == "pop" and k > current:
            parts.append(rf"{{\alpha&HFF&}}{t}{{\r}}")
        else:
            parts.append(t)
    return " ".join(parts)


def events(track: CaptionTrack, fps: int, to_out: Callable[[int], int]) -> list[tuple[int, int, str]]:
    """-> [(start output frame, end output frame, ASS text)], contiguous within each page."""
    out = []
    for clip in track.clips:
        groups = pages(clip)
        clip_end = to_out(clip.end_frame)
        for g, page in enumerate(groups):
            page_end = to_out(clip.words[groups[g + 1][0]].start_frame) if g + 1 < len(groups) else clip_end
            texts = [clip.words[i].text for i in page]
            for k, i in enumerate(page):
                start = to_out(clip.words[i].start_frame)
                end = to_out(clip.words[page[k + 1]].start_frame) if k + 1 < len(page) else page_end
                if end > start:
                    out.append((start, end, _line(texts, k, track.style)))
    return out


def to_ass(track: CaptionTrack, width: int, height: int, fps: int, to_out: Callable[[int], int]) -> str:
    edge = min(width, height)
    size = max(12, round(edge * 0.06))
    outline = max(1, round(edge / 360))
    margin_v = round(height * 0.07)
    margin_h = round(width * 0.06)
    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{FONT},{size},{WHITE},{WHITE},&H00000000&,&H80000000&,-1,0,0,0,100,100,0,0,1,"
        f"{outline},{max(1, outline // 2)},2,{margin_h},{margin_h},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    body = [f"Dialogue: 0,{_ts(s, fps)},{_ts(e, fps)},Caption,,0,0,0,,{text}"
            for s, e, text in events(track, fps, to_out)]
    return "\n".join(head + body) + "\n"


def escape_filter_value(value: str) -> str:
    """A string as an FFmpeg filter option value inside -filter_complex (two escaping levels).

    Level 1 (option value): \\ ' : are escaped -- "C:/x" -> "C\\:/x".
    Level 2 (filtergraph): \\ ' [ ] , ; are escaped again -- "C\\:/x" -> "C\\\\:/x".
    Spaces need nothing: no shell is involved (the command is an argv list) and only leading /
    trailing whitespace is trimmed, which an absolute path never has.
    """
    level1 = re.sub(r"([\\':])", r"\\\1", value)
    return re.sub(r"([\\'\[\],;])", r"\\\1", level1)


def filter_path(path: str | Path) -> str:
    # forward slashes, so Windows separators are not one more thing to escape
    return escape_filter_value(Path(path).resolve().as_posix())


def write_ass(track: CaptionTrack, path: Path, width: int, height: int, fps: int,
              to_out: Callable[[int], int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_ass(track, width, height, fps, to_out), encoding="utf-8")
    return path
