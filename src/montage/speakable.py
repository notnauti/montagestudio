"""Narration text -> what Piper should read: numbers, units, symbols, abbreviations, pronunciations.

espeak (inside Piper) has its own idea of "2021", "6.5 m" or "°C", and it is not always the one
a narrator would use. This module rewrites each display token deterministically before TTS:

  344 -> three hundred forty-four     6.5 -> six point five       2021 -> twenty twenty-one
  1990s -> nineteen nineties          21st -> twenty-first        -40 / −40 -> minus forty
  50% -> fifty percent                5 °C / 5°C -> five degrees Celsius
  3 km, 10 µs, 6.5-meter -> three kilometers, ten microseconds, six point five meter
  $10 billion -> ten billion dollars  10-20 -> ten to twenty      e.g. / Dr. / vs. / & -> ...

plus a per-workspace `pronunciations.json` ({"Webb": "web", "JWST": "J W S T"}): a word in it is
read as given, before any other rule. Keys are single words, matched exactly first, then
case-insensitively; "Webb's" uses the "Webb" entry.

Captions keep showing the words as written. The mapping is per display token (align.tokens):
each one becomes one or more spoken tokens, `groups` records how many, and to_display() folds
Piper's spoken-word timings back into one timing per written word (first start, last end).
A token no rule touches is passed through as written, so a script with nothing to rewrite gives
`spoken == text` and its narration cache (keyed by the spoken text) stays valid.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .align import Word, tokens

PRONUNCIATIONS = "pronunciations.json"


class PronunciationError(ValueError):
    """pronunciations.json is unreadable or malformed: invalid input (exit 1)."""


@dataclass(frozen=True)
class Speakable:
    text: str                 # as written: captions show these words
    spoken: str               # what Piper reads
    groups: tuple[int, ...]   # per written token: how many spoken tokens it became
    changes: tuple[tuple[str, str], ...] = ()  # (written token, spoken form) for every rewrite

    def to_display(self, words: list[Word]) -> list[Word]:
        """Spoken-word timings -> one timing per written word."""
        if len(words) != sum(self.groups):
            raise ValueError(f"{len(words)} spoken words for {sum(self.groups)} expected")
        out, i = [], 0
        for tok, n in zip(tokens(self.text), self.groups):
            part = words[i:i + n]
            out.append(Word(tok, part[0].start_ms, part[-1].end_ms))
            i += n
        return out


# ── numbers ──────────────────────────────────────────────────────────────────

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
SCALES = ((10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand"))
ORDINAL = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
           "nine": "ninth", "twelve": "twelfth"}
SCALE_WORDS = {"thousand", "million", "billion", "trillion"}


def _below_100(n: int) -> str:
    if n < 20:
        return ONES[n]
    t, o = divmod(n, 10)
    return TENS[t] + (f"-{ONES[o]}" if o else "")


def _below_1000(n: int) -> str:
    h, r = divmod(n, 100)
    parts = [f"{ONES[h]} hundred"] if h else []
    if r or not h:
        parts.append(_below_100(r))
    return " ".join(parts)


def number_words(n: int) -> str:
    """Cardinal, no "and": 344 -> "three hundred forty-four". Past the trillions: digit by digit."""
    if n < 0:
        return "minus " + number_words(-n)
    if n >= 10 ** 15:
        return " ".join(ONES[int(d)] for d in str(n))
    parts = []
    for size, name in SCALES:
        if n >= size:
            q, n = divmod(n, size)
            parts.append(f"{_below_1000(q)} {name}")
    if n or not parts:
        parts.append(_below_1000(n))
    return " ".join(parts)


def ordinal_words(n: int) -> str:
    words = number_words(n)
    head, sep, last = words.rpartition(" ")
    pre, dash, tail = last.rpartition("-")
    if tail in ORDINAL:
        tail = ORDINAL[tail]
    elif tail.endswith("y"):
        tail = tail[:-1] + "ieth"
    else:
        tail += "th"
    return head + sep + pre + dash + tail


def year_words(y: int) -> str:
    """1984 -> nineteen eighty-four, 1905 -> nineteen oh five, 1900 -> nineteen hundred,
    2000 -> two thousand, 2009 -> two thousand nine, 2021 -> twenty twenty-one."""
    if 2000 <= y <= 2009:
        return number_words(y)
    hi, lo = divmod(y, 100)
    if lo == 0:
        return f"{_below_100(hi)} hundred"
    return f"{_below_100(hi)} {'oh ' + ONES[lo] if lo < 10 else _below_100(lo)}"


def _plural_tens(words: str) -> str:
    """'nineteen ninety' -> 'nineteen nineties'; 'two thousand' -> 'two thousands'."""
    if words.endswith("y"):
        return words[:-1] + "ies"
    return words + ("es" if words.endswith("x") else "s")


def decade_words(y: int) -> str:
    """1990s -> nineteen nineties, 2020s -> twenty twenties, 2000s -> two thousands, 1900s -> nineteen hundreds."""
    return _plural_tens(year_words(y))


def _is_year(n: int) -> bool:
    return 1100 <= n <= 2099


# ── units, symbols, abbreviations ────────────────────────────────────────────

def _u(singular: str, plural: str | None = None) -> tuple[str, str]:
    return singular, plural or singular + "s"


UNITS = {
    "%": _u("percent", "percent"), "°": _u("degree"), "°C": _u("degree Celsius", "degrees Celsius"),
    "°F": _u("degree Fahrenheit", "degrees Fahrenheit"), "K": _u("kelvin"), "C": _u("degree Celsius", "degrees Celsius"),
    "nm": _u("nanometer"), "µm": _u("micrometer"), "μm": _u("micrometer"), "mm": _u("millimeter"),
    "cm": _u("centimeter"), "m": _u("meter"), "km": _u("kilometer"),
    "mi": _u("mile"), "ft": _u("foot", "feet"), "yd": _u("yard"),
    "mg": _u("milligram"), "g": _u("gram"), "kg": _u("kilogram"), "lb": _u("pound"), "lbs": _u("pound"),
    "oz": _u("ounce"), "t": _u("tonne"),
    "ns": _u("nanosecond"), "µs": _u("microsecond"), "μs": _u("microsecond"), "ms": _u("millisecond"),
    "s": _u("second"), "sec": _u("second"), "min": _u("minute"), "h": _u("hour"), "hr": _u("hour"),
    "hrs": _u("hour"),
    "m/s": _u("meter per second", "meters per second"), "km/s": _u("kilometer per second", "kilometers per second"),
    "km/h": _u("kilometer per hour", "kilometers per hour"), "kph": _u("kilometer per hour", "kilometers per hour"),
    "mph": _u("mile per hour", "miles per hour"),
    "Hz": _u("hertz", "hertz"), "kHz": _u("kilohertz", "kilohertz"), "MHz": _u("megahertz", "megahertz"),
    "GHz": _u("gigahertz", "gigahertz"),
    "W": _u("watt"), "kW": _u("kilowatt"), "MW": _u("megawatt"), "GW": _u("gigawatt"), "V": _u("volt"),
    "AU": _u("astronomical unit"), "ly": _u("light-year"), "pc": _u("parsec"),
    "x": _u("times", "times"), "×": _u("times", "times"),
    "B": _u("billion", "billion"), "M": _u("million", "million"), "bn": _u("billion", "billion"),
}
CURRENCY = {"$": _u("dollar"), "€": _u("euro"), "£": _u("pound")}
SCALE_UNITS = {"B", "M", "bn"}  # "$10B" reads "ten billion dollars"; without a currency they stay as written
ATTACHED_EXCLUDE = {"K", "C", "s", "t"} | SCALE_UNITS  # "4K", "60s": only a unit when written apart
SEPARATE_EXCLUDE = {"x", "C"} | SCALE_UNITS

# key: lowercased, with its own period(s); value: (spoken, keeps a sentence-final period)
ABBREVIATIONS = {
    "dr.": ("Doctor", False), "mr.": ("Mister", False), "mrs.": ("Missus", False),
    "prof.": ("Professor", False), "mt.": ("Mount", False), "jr.": ("Junior", True), "sr.": ("Senior", True),
    "e.g.": ("for example", True), "i.e.": ("that is", True), "etc.": ("et cetera", True),
    "vs.": ("versus", False), "vs": ("versus", False), "approx.": ("approximately", False),
    "ca.": ("circa", False), "fig.": ("figure", False), "&": ("and", False), "~": ("about", False),
    "≈": ("approximately", False),
}

NUM = r"\d{1,3}(?:,\d{3})+|\d+"
_LEAD = "\"'“‘([{¿¡«"
_TRAIL = "\"'”’)]}.,;:!?…»"
_MINUS = "-−–"
_NUMERIC = re.compile(rf"^(?P<cur>[$€£])?(?P<sign>[{_MINUS}])?(?P<int>{NUM})(?P<dec>\.\d+)?"
                      rf"(?P<rest>.*)$")
_PLAIN = re.compile(rf"^[{_MINUS}]?(?:{NUM})(?:\.\d+)?$")


def _split(token: str) -> tuple[str, str, str]:
    """'"(6.5%),' -> ('"(', '6.5%', '),'). A glued dash (' —') stays in the suffix."""
    m = re.match(r"^(.*?)( [—–-])$", token)
    dash = ""
    if m:
        token, dash = m.group(1), m.group(2)
    i = 0
    while i < len(token) and token[i] in _LEAD:
        i += 1
    j = len(token)
    while j > i and token[j - 1] in _TRAIL:
        j -= 1
    return token[:i], token[i:j], token[j:] + dash


@dataclass
class _Num:
    value: float
    words: str
    is_one: bool


def _num(int_part: str, dec: str | None, sign: str | None, *, year_ok: bool) -> _Num:
    n = int(int_part.replace(",", ""))
    if dec:
        words = number_words(n) + " point " + " ".join(ONES[int(d)] for d in dec[1:])
    elif year_ok and "," not in int_part and len(int_part) == 4 and _is_year(n):
        words = year_words(n)
    else:
        words = number_words(n)
    if sign:
        words = "minus " + words
    return _Num(float(f"{int_part.replace(',', '')}{dec or ''}"), words, n == 1 and not dec)


def _unit(unit: str, num: _Num) -> str:
    singular, plural = UNITS[unit]
    return singular if num.is_one else plural


def _numeric(core: str, nxt: str) -> str | None:
    """A token that starts with a number (maybe signed or with a currency) -> words, or None."""
    m = _NUMERIC.match(core)
    if not m:
        return None
    cur, sign, int_part, dec, rest = m.group("cur", "sign", "int", "dec", "rest")
    next_unit = nxt in UNITS or nxt in SCALE_WORDS
    plain = not cur and not rest
    num = _num(int_part, dec, sign, year_ok=plain and not sign and not next_unit)
    money = CURRENCY.get(cur) if cur else None

    def money_tail(n: _Num) -> str:
        if not money:
            return ""
        if nxt in SCALE_WORDS:  # "$10 billion": "dollars" goes after the scale word
            return ""
        return " " + (money[0] if n.is_one else money[1])

    if not rest:
        return num.words + money_tail(num)
    # ordinals: 1st, 22nd, 103rd, 4th
    om = re.fullmatch(r"(st|nd|rd|th)", rest)
    if om and not dec and not cur and not sign:
        return ordinal_words(int(int_part.replace(",", "")))
    # decades: 1990s, 2020s
    if rest == "s" and not dec and not cur and not sign and len(int_part) == 4 and int(int_part) % 10 == 0 \
            and _is_year(int(int_part)):
        return decade_words(int(int_part))
    # attached unit: 50%, 5°C, 3km, 10x, $10B (never "4K", "60s", "5t": those mean something else)
    if rest in UNITS and (rest not in ATTACHED_EXCLUDE or (money and rest in SCALE_UNITS)):
        if money and rest in SCALE_UNITS | {"m"}:
            return f"{num.words} {'million' if rest == 'm' else UNITS[rest][0]} {money[1]}"
        return f"{num.words} {_unit(rest, num)}{money_tail(num)}"
    if rest.startswith("°") and rest[1:] in ("C", "F") or rest in ("° C", "° F"):
        return f"{num.words} {_unit('°' + rest[-1], num)}"
    # ranges: 10-20, 10–20, 1990-2000, 10-20%
    rm = re.fullmatch(rf"[–-]({NUM})(\.\d+)?(.*)", rest)
    if rm and not money:
        hi_int, hi_dec, tail = rm.groups()
        both_years = not dec and not hi_dec and not tail and len(int_part) == 4 and len(hi_int) == 4
        hi = _num(hi_int, hi_dec, None, year_ok=both_years)
        lo = _num(int_part, dec, sign, year_ok=both_years)
        if not tail:
            return f"{lo.words} to {hi.words}"
        if tail in UNITS:
            return f"{lo.words} to {hi.words} {_unit(tail, hi)}"
        if tail.startswith("-") and re.fullmatch(r"-[A-Za-z]+", tail):
            return f"{lo.words} to {hi.words} {tail[1:]}"
        return None
    # compounds: 6.5-meter, 18-segment, 3-km
    cm = re.fullmatch(r"-([A-Za-zµμ°/]+(?:-[A-Za-z]+)*)", rest)
    if cm:
        word = cm.group(1)
        head, _, more = word.partition("-")
        spoken = UNITS[head][0] if head in UNITS else head
        return f"{num.words}{money_tail(num)} " + " ".join([spoken] + more.split("-") if more else [spoken])
    return None


class Lexicon:
    """pronunciations.json: {"word": "spoken form"}; exact match first, then case-insensitive."""

    def __init__(self, entries: dict[str, str] | None = None):
        self.exact = dict(entries or {})
        self.folded = {k.lower(): v for k, v in self.exact.items()}

    def lookup(self, core: str) -> str | None:
        forms = [(core, "")]
        if core[-2:] in ("'s", "’s"):
            forms.append((core[:-2], core[-2:]))  # "Webb's" -> the "Webb" entry + 's
        for base, suffix in forms:
            hit = self.exact.get(base) or self.folded.get(base.lower())
            if base and hit:
                return hit + suffix
        return None

    def __bool__(self) -> bool:
        return bool(self.exact)


def parse_lexicon(raw: object, where: str = PRONUNCIATIONS) -> Lexicon:
    errs = []
    if not isinstance(raw, dict):
        raise PronunciationError(f"{where}: must be a JSON object of \"word\": \"spoken form\"")
    for k, v in raw.items():
        if not k.strip() or any(ch.isspace() for ch in k):
            errs.append(f"{where}: key {k!r} must be one word (no spaces)")
        if not isinstance(v, str) or not re.search(r"\w", v) or any(ord(c) < 32 for c in v):
            errs.append(f"{where}[{k!r}]: must be a single-line string with at least one letter or digit")
    if errs:
        raise PronunciationError("\n".join(errs))
    return Lexicon({k: " ".join(v.split()) for k, v in raw.items()})


def load_lexicon(workspace: Path) -> Lexicon:
    """The workspace's pronunciations.json, or an empty lexicon when there is none."""
    path = Path(workspace) / PRONUNCIATIONS
    if not path.is_file():
        return Lexicon()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PronunciationError(f"{path}: {exc}") from exc
    return parse_lexicon(raw, str(path))


# ── per token ────────────────────────────────────────────────────────────────

def _core_of(token: str | None) -> str:
    return _split(token)[1] if token else ""


def _plain_num(core: str) -> _Num | None:
    """A bare number token ("5", "-40", "6.5", "1,000", "~5", or a range's upper end "10-20") as a
    count, never a year."""
    core = core.lstrip("~≈")
    rng = re.fullmatch(rf"[{_MINUS}]?(?:{NUM})(?:\.\d+)?[–-]((?:{NUM})(?:\.\d+)?)", core)
    if rng:
        core = rng.group(1)
    m = _NUMERIC.match(core)
    if not core or not _PLAIN.match(core) or not m:
        return None
    return _num(m.group("int"), m.group("dec"), m.group("sign"), year_ok=False)


def _speak_token(token: str, prev: str | None, nxt: str | None, lex: Lexicon) -> str:
    if len(token) > 1 and token[-1] in "&≈":  # align.tokens glues a lone "&" onto the word before it
        return _speak_token(token[:-1], prev, nxt, lex) + " " + ABBREVIATIONS[token[-1]][0]
    lead, core, trail = _split(token)
    if not core:
        return token
    nxt_core, prev_core = _core_of(nxt), _core_of(prev)
    hit = lex.lookup(core) if lex else None
    if hit:
        return lead + hit + trail
    # abbreviations: their own period belongs to them
    whole = core + trail
    low = whole.lower()
    for key, (spoken, keep_period) in ABBREVIATIONS.items():
        if low.startswith(key) and (key.endswith(".") or len(low) == len(key) or low[len(key)] in _TRAIL):
            rest = whole[len(key):]
            final = keep_period and not rest and (nxt is None or nxt_core[:1].isupper())
            return lead + spoken + ("." if final else "") + rest
    if low.startswith("no.") and re.match(r"\d", nxt_core):
        return lead + "number" + whole[3:]
    if core[:1] in "~≈" and len(core) > 1:  # "~5 km" -> "about five km"
        inner = _speak_token(core[1:] + trail, prev, nxt, lex)
        if inner != core[1:] + trail:
            return lead + ABBREVIATIONS[core[0]][0] + " " + inner
    # a unit or scale word written after a number: "5 km", "-40 °C", "$10 billion"
    prev_num = _plain_num(prev_core)
    if (prev_num or prev_core in SCALE_WORDS) and core in UNITS and core not in SEPARATE_EXCLUDE:
        return lead + (_unit(core, prev_num) if prev_num else UNITS[core][1]) + trail  # "1.5 million km"
    if core in SCALE_WORDS and prev_core[:1] in CURRENCY and _PLAIN.match(prev_core[1:]):
        return lead + core + " " + CURRENCY[prev_core[0]][1] + trail
    spoken = _numeric(core, nxt_core)
    if spoken is not None:
        return lead + spoken + trail
    return token


def speak(text: str, lexicon: Lexicon | None = None) -> Speakable:
    """Narration -> Speakable. `spoken == text` (byte for byte) when no rule applies."""
    lex = lexicon or Lexicon()
    toks = tokens(text)
    pieces = [_speak_token(t, toks[i - 1] if i else None, toks[i + 1] if i + 1 < len(toks) else None, lex)
              for i, t in enumerate(toks)]
    changes = tuple((t, p) for t, p in zip(toks, pieces) if t != p)
    if not changes:
        return Speakable(text, text, tuple(1 for _ in toks))
    groups = tuple(len(tokens(p)) for p in pieces)
    spoken = " ".join(pieces)
    if any(g < 1 for g in groups) or len(tokens(spoken)) != sum(groups):
        return Speakable(text, text, tuple(1 for _ in toks))  # never risk the caption mapping
    return Speakable(text, spoken, groups, changes)
