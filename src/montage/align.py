"""Word timestamps from Piper's own phoneme durations (guide §2 "Piper TTS + Word Timestamps").

Piper's VITS voices compute a duration per phoneme id (the `Ceil` tensor, in spectrogram
frames) and then throw it away. piper-tts 1.8 can return it (`include_alignments=True`), but only
after marking that tensor as a graph output, which it does with the `onnx` package. This module
makes the same one-field edit with a tiny protobuf walker instead (stdlib, no new dependency):
append `ValueInfoProto{name: <ceil tensor>}` to `GraphProto.output` and fix the enclosing
length prefixes. The model is patched in memory on load; the file on disk is never changed.

Phoneme durations -> words: espeak separates words with a " " phoneme and keeps punctuation as
its own phonemes, so each run of other phonemes is one spoken word. Punctuation and spaces are
pauses: a word ends where its last phoneme ends. Text tokens are matched to those runs one to
one; when the counts differ (espeak speaks "38" as two words and merges "on the" into one),
each token is phonemized alone and that phoneme sequence is aligned to the utterance's timed
phonemes (match_tokens), so a boundary falls on the phoneme where one word's sounds end. If
that still does not line up, or a sentence came back without alignments, the words are spread
over the audio in proportion to their length and the result is marked `estimated`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable, Sequence

BOS, EOS, PAD = "^", "$", "_"  # piper.const
PAUSE_PHONEMES = set(" ,.;:!?¡¿—…\"'()-")  # espeak punctuation passes through as phonemes
STRESS = set("ˈˌ")
MIN_MATCH = 0.6
ALIGNED = "piper-alignment"
ESTIMATED = "estimated"


class AlignError(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    start_ms: int  # from the start of the beat's wav
    end_ms: int


@dataclass(frozen=True)
class Sentence:
    """One Piper chunk: its phonemes' sample counts (None = no alignment) and its audio length."""
    phonemes: tuple[tuple[str, int], ...] | None
    samples: int


# ── ONNX patch (protobuf wire format, stdlib) ────────────────────────────────

def _varint(buf, i: int) -> tuple[int, int]:
    shift = val = 0
    while True:
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if b < 0x80:
            return val, i
        shift += 7


def _enc_varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b, v = v & 0x7F, v >> 7
        out.append(b | 0x80 if v else b)
        if not v:
            return bytes(out)


def _fields(buf, start: int = 0, end: int | None = None):
    """Yield (field number, wire type, key offset, payload start, payload end)."""
    end = len(buf) if end is None else end
    i = start
    while i < end:
        k = i
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            _, j = _varint(buf, i)
        elif wt == 1:
            j = i + 8
        elif wt == 5:
            j = i + 4
        elif wt == 2:
            ln, i = _varint(buf, i)
            j = i + ln
        else:
            raise AlignError(f"unsupported protobuf wire type {wt} in the voice model")
        if j > end:
            raise AlignError("voice model is truncated or not an ONNX file")
        yield num, wt, k, i, j
        i = j


def _str(buf, s: int, e: int) -> str:
    return bytes(buf[s:e]).decode("utf-8", "replace")


def add_alignment_output(model: bytes) -> tuple[bytes, str]:
    """ModelProto bytes -> (patched bytes, tensor name). Mirrors piper.patch_voice_with_alignment."""
    mv = memoryview(model)
    graph = [(k, s, e) for num, wt, k, s, e in _fields(mv) if num == 7 and wt == 2]  # ModelProto.graph
    if len(graph) != 1:
        raise AlignError("voice model has no single ONNX graph")
    gk, gs, ge = graph[0]
    ceil: list[str] = []
    outputs: list[str] = []
    for num, wt, _, s, e in _fields(mv, gs, ge):
        if num == 1 and wt == 2:                       # GraphProto.node
            op, outs = None, []
            for n2, w2, _, s2, e2 in _fields(mv, s, e):
                if n2 == 4 and w2 == 2:                # NodeProto.op_type
                    op = _str(mv, s2, e2)
                elif n2 == 2 and w2 == 2:              # NodeProto.output
                    outs.append(_str(mv, s2, e2))
            if op == "Ceil":
                ceil += outs
        elif num == 12 and wt == 2:                    # GraphProto.output
            outputs += [_str(mv, s2, e2) for n2, w2, _, s2, e2 in _fields(mv, s, e) if n2 == 1 and w2 == 2]
    if len(ceil) != 1:
        raise AlignError(f"expected one Ceil tensor (phoneme durations) in the voice model, found {len(ceil)}")
    name = ceil[0]
    if name in outputs:
        return model, name  # already patched on disk
    name_b = name.encode()
    value_info = b"\x0a" + _enc_varint(len(name_b)) + name_b          # ValueInfoProto.name = 1
    extra = b"\x62" + _enc_varint(len(value_info)) + value_info       # GraphProto.output = 12
    new_graph = bytes(mv[gs:ge]) + extra
    head = b"\x3a" + _enc_varint(len(new_graph))                      # ModelProto.graph = 7
    return bytes(mv[:gk]) + head + new_graph + bytes(mv[ge:]), name


def load_aligned_voice(model_path: Path):
    """PiperVoice whose session also returns phoneme durations. Raises AlignError when it can't."""
    try:
        import onnxruntime
        from piper import PiperVoice
        from piper.config import PiperConfig
    except ImportError as exc:
        raise AlignError("piper-tts not installed in this environment (use the project .venv)") from exc
    config = json.loads(Path(f"{model_path}.json").read_text(encoding="utf-8"))
    patched, _ = add_alignment_output(Path(model_path).read_bytes())
    session = onnxruntime.InferenceSession(patched, sess_options=onnxruntime.SessionOptions(),
                                           providers=["CPUExecutionProvider"])
    if len(session.get_outputs()) < 2:
        raise AlignError("patched voice model still has one output")
    return PiperVoice(config=PiperConfig.from_dict(config), session=session)


# ── phoneme durations -> words ───────────────────────────────────────────────

def phoneme_words(phonemes: Sequence[tuple[str, int]], offset: int = 0) -> list[tuple[int, int]]:
    """(phoneme, samples) runs -> [(start_sample, end_sample)] per spoken word, from `offset`."""
    spans, cursor, start = [], offset, None
    for ph, n in phonemes:
        if ph in (BOS, EOS, PAD) or ph in PAUSE_PHONEMES:
            if start is not None:
                spans.append((start, cursor))
                start = None
        elif start is None:
            start = cursor
        cursor += n
    if start is not None:
        spans.append((start, cursor))
    return spans


_WORDY = re.compile(r"\w", re.UNICODE)


def tokens(text: str) -> list[str]:
    """Display tokens: whitespace-split, with punctuation-only tokens glued to their neighbour."""
    out: list[str] = []
    pending = ""
    for raw in text.split():
        if not _WORDY.search(raw):
            if out:
                out[-1] += " " + raw if raw in ("—", "–", "-") else raw
            else:
                pending += raw
            continue
        out.append(pending + raw)
        pending = ""
    if pending and out:
        out[-1] += pending
    return out


def _proportional(toks: Sequence[str], start: int, end: int) -> list[tuple[int, int]]:
    weights = [max(1, len(_WORDY.findall(t))) for t in toks]
    total = sum(weights)
    spans, acc = [], 0
    for w in weights:
        a = start + (end - start) * acc // total
        acc += w
        spans.append((a, start + (end - start) * acc // total))
    return spans


def timed_phonemes(phonemes: Sequence[tuple[str, int]], offset: int = 0) -> list[tuple[str, int, int]]:
    """Spoken phonemes with their (start, end) samples. Pauses, spaces and BOS/EOS are dropped;
    a stress mark's time is given to the phoneme it stresses."""
    out, cursor, pending = [], offset, None
    for ph, n in phonemes:
        if ph in (BOS, EOS, PAD) or ph in PAUSE_PHONEMES:
            pending = None
        elif ph in STRESS:
            pending = cursor if pending is None else pending
        else:
            out.append((ph, cursor if pending is None else pending, cursor + n))
            pending = None
        cursor += n
    return out


def _spoken(phonemes: Iterable[str]) -> list[str]:
    return [p for p in phonemes if p not in STRESS and p not in PAUSE_PHONEMES and p not in (BOS, EOS, PAD)]


def match_tokens(toks: Sequence[str], timed: Sequence[tuple[str, int, int]],
                 phonemize_token: Callable[[str], list[str]]) -> list[tuple[int, int]] | None:
    """Token -> sample span by aligning each token's own phonemes to the utterance's timed phonemes.

    Handles espeak merging words ("on the" -> one run "ɔnðə") and splitting them ("38" -> two
    runs): the boundary falls on the phoneme where the token's phonemes stop matching. Context
    changes pronunciation a little ("the" ðə/ði), so this is a fuzzy match (difflib); below
    MIN_MATCH of the phonemes matching, the result is not trusted (None).
    """
    a: list[str] = []
    owner: list[int] = []
    for ti, t in enumerate(toks):
        ph = _spoken(phonemize_token(t))
        a += ph
        owner += [ti] * len(ph)
    b = [p for p, _, _ in timed]
    if not a or not b:
        return None
    sm = SequenceMatcher(None, a, b, autojunk=False)
    first: list[int | None] = [None] * len(toks)
    last: list[int | None] = [None] * len(toks)
    matched = 0
    for blk in sm.get_matching_blocks():
        matched += blk.size
        for k in range(blk.size):
            ti, bi = owner[blk.a + k], blk.b + k
            if first[ti] is None:
                first[ti] = bi
            last[ti] = bi
    if matched < MIN_MATCH * max(len(a), len(b)):
        return None
    spans: list[tuple[int, int] | None] = [
        (timed[f][1], timed[l][2]) if f is not None else None for f, l in zip(first, last)]
    # a token none of whose phonemes matched shares the gap between its matched neighbours
    i = 0
    while i < len(spans):
        if spans[i] is not None:
            i += 1
            continue
        j = i
        while j < len(spans) and spans[j] is None:
            j += 1
        lo = spans[i - 1][1] if i else timed[0][1]
        hi = spans[j][0] if j < len(spans) else timed[-1][2]
        spans[i:j] = _proportional(toks[i:j], lo, max(lo, hi))
        i = j
    return spans  # type: ignore[return-value]


def words_from_sentences(text: str, sentences: Iterable[Sentence], sample_rate: int,
                         phonemize_token: Callable[[str], list[str]] | None = None) -> tuple[list[Word], str]:
    """Beat text + per-sentence alignments -> (words with ms offsets into the wav, method).

    Order of preference: runs match tokens one to one; else token phonemes aligned to the timed
    phonemes (needs `phonemize_token`); else an estimate proportional to word length."""
    toks = tokens(text)
    sentences = list(sentences)
    runs: list[tuple[int, int]] = []
    timed: list[tuple[str, int, int]] = []
    cursor = 0
    aligned = True
    for s in sentences:
        if s.phonemes is None:
            aligned = False
        else:
            runs += phoneme_words(s.phonemes, cursor)
            timed += timed_phonemes(s.phonemes, cursor)
        cursor += s.samples
    if not toks:
        return [], ALIGNED
    total = cursor

    spans: list[tuple[int, int]] | None = None
    if aligned and len(runs) == len(toks):
        spans = runs
    elif aligned and timed and phonemize_token is not None:
        spans = match_tokens(toks, timed, phonemize_token)
    method = ALIGNED
    if spans is None:
        method = ESTIMATED
        spans = _proportional(toks, 0, total) if total else [(0, 0)] * len(toks)

    def ms(samples: int) -> int:
        return round(samples * 1000 / sample_rate)

    return [Word(t, ms(a), max(ms(b), ms(a) + 1)) for t, (a, b) in zip(toks, spans)], method


def synthesize_with_words(voice, text: str, wav_file) -> tuple[list[Word], str]:
    """Write Piper audio for `text` to an open wave writer (same bytes synthesize_wav writes) and
    return its word timings."""
    sentences, rate, first = [], voice.config.sample_rate, True
    for chunk in voice.synthesize(text, include_alignments=True):
        if first:
            wav_file.setframerate(chunk.sample_rate)
            wav_file.setsampwidth(chunk.sample_width)
            wav_file.setnchannels(chunk.sample_channels)
            rate, first = chunk.sample_rate, False
        data = chunk.audio_int16_bytes
        wav_file.writeframes(data)
        n = len(chunk.audio_int16_array)
        al = chunk.phoneme_alignments
        sentences.append(Sentence(tuple((a.phoneme, int(a.num_samples)) for a in al) if al else None, n))

    def phonemize_token(token: str) -> list[str]:
        return [p for sent in voice.phonemize(token) for p in sent]

    return words_from_sentences(text, sentences, rate, phonemize_token)
