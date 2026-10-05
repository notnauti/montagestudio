"""Brief coverage: which of the brief's points each beat of a script says. No model call.

Key points come from the brief text by rule, not by a model:
  - the brief is split into sentences;
  - in a sentence with a colon, the part before it is framing ("A short documentary about X:")
    and is dropped; the list after it is what the video must say;
  - the rest is split at commas and semicolons, a leading "and" / "or" / "then" is dropped,
    and a fragment with fewer than two content words is merged into the one before it
    ("A short, imaginative ..." stays one piece);
  - a fragment starting "for " names the audience or purpose ("for curious non-physicists"),
    not something the narration says, so it is dropped.

A point's content words are its words minus stopwords and minus the title's words (the topic
is in every point, so it proves nothing). A point is covered by a beat that says enough of
them: all of them when there are one or two, else at least max(2, a third). Two neighbouring
beats may share a point. Words are compared folded (plural, -ed, -ing), and a number in the
brief also matches when the narration spells it out ("344" = "three hundred and forty four",
"2021" = "twenty twenty-one").
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .sources import STOPWORDS, stem

EXTRA_STOPWORDS = frozenset("""
had like too need needs needed make makes made one get gets got way thing things really much many
lot lots its it's ever every just why what how whom whose let lets explain explains describe tell
""".split())
LEADING = ("and ", "or ", "then ", "plus ", "but ", "also ")

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
         "sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _below_1000(n: int) -> list[str]:
    out: list[str] = []
    if n >= 100:
        out += [_ONES[n // 100], "hundred"]
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10])
        n %= 10
        if n:
            out.append(_ONES[n])
    elif n or not out:
        out.append(_ONES[n])
    return out


def number_spellings(n: int) -> list[list[str]]:
    """Ways a voice script may spell `n` out: 344 -> three hundred forty four; years also as
    pairs (2021 -> twenty twenty one). "and" is ignored on both sides."""
    if n >= 1_000_000:
        return []
    plain = (_below_1000(n // 1000) + ["thousand"] + (_below_1000(n % 1000) if n % 1000 else [])
             if n >= 1000 else _below_1000(n))
    forms = [plain]
    if 1100 <= n <= 2099 and n % 100:
        forms.append(_below_1000(n // 100) + _below_1000(n % 100))
    return forms


def fold(word: str) -> str:
    """The comparison key: plural folded (sources.stem), then a trailing -ing / -ed dropped."""
    s = stem(word.lower())
    for suffix in ("ing", "ed"):
        if s.endswith(suffix) and len(s) - len(suffix) >= 3:
            return s[: -len(suffix)]
    return s


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _content(text: str) -> dict[str, str]:
    """Folded content word -> its first spelling in `text`, in order."""
    out: dict[str, str] = {}
    for w in _tokens(text):
        if w in STOPWORDS or w in EXTRA_STOPWORDS or (len(w) < 2 and not w.isdigit()):
            continue
        out.setdefault(fold(w), w)
    return out


def content_words(text: str) -> list[str]:
    """Folded content words in order, each once."""
    return list(_content(text))


def brief_points(text: str) -> list[str]:
    points: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        sentence = sentence.strip().rstrip(".!?").strip()
        if not sentence:
            continue
        if ":" in sentence:
            _, _, after = sentence.partition(":")
            sentence = after.strip() or sentence
        pieces: list[str] = []
        for frag in re.split(r"[;,]\s+", sentence):
            frag = frag.strip(" ,;")
            low = frag.lower()
            for lead in LEADING:
                if low.startswith(lead):
                    frag = frag[len(lead):].strip()
                    break
            if not frag or (pieces and low.startswith("for ")):
                continue
            if pieces and len(content_words(frag)) < 2:
                pieces[-1] = f"{pieces[-1]}, {frag}"
            elif pieces and len(content_words(pieces[-1])) < 2:
                pieces[-1] = f"{pieces[-1]}, {frag}"
            else:
                pieces.append(frag)
        points += pieces
    return points


@dataclass(frozen=True)
class Point:
    text: str
    words: tuple[str, ...]   # folded content words that count for coverage
    need: int                # how many of them a beat must say
    spelled: tuple[str, ...] = ()  # the same words as the brief wrote them, for display

    def surface(self, word: str) -> str:
        return dict(zip(self.words, self.spelled)).get(word, word)


def key_points(brief_text: str, title: str = "") -> list[Point]:
    topic = set(content_words(title))
    out = []
    for p in brief_points(brief_text):
        spelled = _content(p)
        own = [w for w in spelled if w not in topic] or list(spelled)
        n = len(own)
        out.append(Point(p, tuple(own), n if n <= 2 else max(2, math.ceil(n / 3)),
                         tuple(spelled[w] for w in own)))
    return out


def _said(word: str, beat_keys: set[str], beat_seq: list[str]) -> bool:
    if word in beat_keys:
        return True
    if word.isdigit():
        for form in number_spellings(int(word)):
            if _contains(beat_seq, form):
                return True
    return False


def _contains(seq: list[str], form: list[str]) -> bool:
    n = len(form)
    return any(seq[i:i + n] == form for i in range(len(seq) - n + 1))


def _beat_words(text: str) -> tuple[set[str], list[str]]:
    seq = [w for w in _tokens(text) if w != "and"]
    return {fold(w) for w in seq}, seq


@dataclass(frozen=True)
class Coverage:
    points: tuple[Point, ...]
    beats: tuple[tuple[int, ...], ...]   # per point: 0-based beats that cover it (empty = uncovered)
    matched: tuple[tuple[str, ...], ...]  # per point: the words those beats said, as the brief spells them

    @property
    def uncovered(self) -> list[int]:
        return [i for i, b in enumerate(self.beats) if not b]

    def by_beat(self, n_beats: int) -> list[list[int]]:
        out: list[list[int]] = [[] for _ in range(n_beats)]
        for p, beats in enumerate(self.beats):
            for b in beats:
                out[b].append(p)
        return out

    def out_of_order(self) -> list[int]:
        """Points whose first covering beat comes before the previous covered point's."""
        late, last = [], -1
        for p, beats in enumerate(self.beats):
            if not beats:
                continue
            if beats[0] < last:
                late.append(p)
            last = max(last, beats[0])
        return late


def cover(points: list[Point], narrations: list[str]) -> Coverage:
    said = [_beat_words(t) for t in narrations]
    beats_out, matched_out = [], []
    for p in points:
        hits = [[w for w in p.words if _said(w, keys, seq)] for keys, seq in said]
        best: tuple[int, ...] = ()
        best_hits: list[str] = []
        for i, h in enumerate(hits):  # one beat that says enough, the one saying most
            if len(h) >= p.need and len(h) > len(best_hits):
                best, best_hits = (i,), h
        if not best:
            for i in range(len(hits) - 1):  # or two neighbours between them
                both = [w for w in p.words if w in hits[i] or w in hits[i + 1]]
                if len(both) >= p.need and len(both) > len(best_hits):
                    best, best_hits = (i, i + 1), both
        beats_out.append(best)
        matched_out.append(tuple(p.surface(w) for w in best_hits))
    return Coverage(tuple(points), tuple(beats_out), tuple(matched_out))
