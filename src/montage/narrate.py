"""Script beats -> one Piper wav per beat, under <workspace>/assets/narration/, plus its word timings.

Files are named by a hash of voice + text, so re-planning after a script edit only
re-synthesises the beats whose text changed. Next to each wav, `<wav stem>.words.json` holds
the beat's word timestamps (ms into the wav) from Piper's phoneme durations (align.py). A wav
without a matching words file is synthesised again, so wav and timings always come from the
same run (Piper's output varies slightly between runs).

Piper reads a *speakable* form of each line (speakable.py: numbers, units, symbols and
abbreviations spelled out, plus the workspace's pronunciations.json), and the wav is keyed by
that spoken text. The words file keeps the line as written: its word timings are Piper's
spoken-word timings folded back onto the written words, so captions show "344" while the voice
says "three hundred forty-four". A line no rule changes is keyed exactly as before.

prune_stale() removes the beat wavs (and their words files) no current beat uses; it only
touches files named the way narrate() names them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path

from . import align
from .align import Word
from .script import Script
from .speakable import Lexicon, Speakable, load_lexicon, speak

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VOICES_DIR = Path(os.environ.get("MONTAGE_VOICES_DIR", PROJECT_ROOT / "voices"))
WORDS_VERSION = 1


class NarrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Narration:
    src: str  # workspace-relative, for the scene plan
    path: Path
    seconds: float
    words: tuple[Word, ...] = field(default=())
    word_method: str = align.ESTIMATED  # align.ALIGNED (Piper phoneme durations) or align.ESTIMATED


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def _load_voice(voice: str, log=print):
    """-> (PiperVoice, aligned). Aligned = its session also returns phoneme durations."""
    model = VOICES_DIR / f"{voice}.onnx"
    if not model.is_file():
        raise NarrationError(
            f"Piper voice '{voice}' not found in {VOICES_DIR}. Download it with:\n"
            f"  .venv/Scripts/python.exe -m piper.download_voices --download-dir voices {voice}")
    try:
        from piper import PiperVoice
    except ImportError as exc:
        raise NarrationError("piper-tts not installed in this environment (use the project .venv)") from exc
    try:
        return align.load_aligned_voice(model), True
    except (align.AlignError, OSError, ValueError) as exc:
        log(f"  note: no word alignment from this voice ({exc}); caption timings will be estimated")
        return PiperVoice.load(model), False


def _words_path(wav: Path) -> Path:
    return wav.with_name(f"{wav.stem}.words.json")


def _read_words(path: Path, sp: Speakable) -> tuple[tuple[Word, ...], str] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if (raw.get("version") != WORDS_VERSION or raw.get("text") != sp.text
                or raw.get("spoken", raw.get("text")) != sp.spoken):
            return None
        words = tuple(Word(w["text"], int(w["startMs"]), int(w["endMs"])) for w in raw["words"])
        return words, str(raw["method"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_words(path: Path, sp: Speakable, words: list[Word], method: str) -> None:
    data = {"version": WORDS_VERSION, "text": sp.text, "method": method,
            "words": [{"text": w.text, "startMs": w.start_ms, "endMs": w.end_ms} for w in words]}
    if sp.spoken != sp.text:
        data["spoken"] = sp.spoken
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def _synthesize(loaded, aligned: bool, sp: Speakable, path: Path) -> tuple[list[Word], str]:
    """Piper reads sp.spoken; the returned words are sp.text's, timed by the spoken words."""
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as wf:
        if aligned:
            words, method = align.synthesize_with_words(loaded, sp.spoken, wf)
        else:
            loaded.synthesize_wav(sp.spoken, wf)
    if not aligned:
        with wave.open(str(tmp), "rb") as r:
            n, rate = r.getnframes(), r.getframerate()
        words, method = align.words_from_sentences(sp.spoken, [align.Sentence(None, n)], rate)
    tmp.replace(path)
    return sp.to_display(words), method


def wav_name(index: int, voice: str, spoken: str) -> str:
    digest = hashlib.sha1(f"{voice}\n{spoken}".encode()).hexdigest()[:10]
    return f"beat_{index:02d}_{digest}.wav"


# what narrate() writes: beat_NN_<10 hex>.wav, its .words.json, and their .tmp siblings
_OWN = re.compile(r"^(beat_\d{2,}_[0-9a-f]{10})(\.wav|\.words\.json|\.tmp|\.words\.tmp)$")


def prune_stale(workspace: Path, keep, log=print) -> list[Path]:
    """Delete narration files montage wrote that none of `keep` (the current beats' wavs) uses."""
    out_dir = Path(workspace) / "assets" / "narration"
    if not out_dir.is_dir():
        return []
    keep_stems = {Path(p).stem for p in keep}
    removed = []
    for f in sorted(out_dir.iterdir()):
        m = _OWN.match(f.name)
        if m and f.is_file() and m.group(1) not in keep_stems:
            f.unlink()
            removed.append(f)
    if removed:
        wavs = sum(f.suffix == ".wav" for f in removed)
        log(f"  narration: pruned {wavs} stale wav(s) no beat uses ({len(removed)} files)")
    return removed


def narrate(script: Script, voice: str, workspace: Path, log=print,
            lexicon: Lexicon | None = None) -> list[Narration]:
    """`lexicon`: pronunciations; default the workspace's pronunciations.json (PronunciationError if bad)."""
    out_dir = workspace / "assets" / "narration"
    out_dir.mkdir(parents=True, exist_ok=True)
    lex = load_lexicon(workspace) if lexicon is None else lexicon
    loaded = None
    result = []
    for i, beat in enumerate(script.beats, 1):
        sp = speak(beat.narration, lex)
        if sp.changes:
            log(f"  speak beat {i}: " + "; ".join(f"{a!r} -> {b!r}" for a, b in sp.changes))
        path = out_dir / wav_name(i, voice, sp.spoken)
        words_path = _words_path(path)
        cached = _read_words(words_path, sp) if path.is_file() else None
        if cached is None:
            if loaded is None:
                loaded = _load_voice(voice, log=log)
            words, method = _synthesize(*loaded, sp, path)
            _write_words(words_path, sp, words, method)
            cached = tuple(words), method
        seconds = wav_seconds(path)
        if seconds <= 0:
            raise NarrationError(f"beat {i}: Piper produced empty audio for {beat.narration!r}")
        result.append(Narration(f"./assets/narration/{path.name}", path, seconds, *cached))
    return result
