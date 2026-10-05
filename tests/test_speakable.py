"""Speakable narration (speakable.py): numbers, units, symbols, abbreviations, pronunciations, the
written-word <-> spoken-word timing map, narration keyed by spoken text, and stale-wav pruning.

Unit tests use a fake Piper voice; one test runs the real lessac-medium voice (skipped without it).
"""

import json
from functools import partial
from pathlib import Path

import pytest

from montage import align, cli, narrate as narrate_mod, planner
from montage.align import Word
from montage.narrate import Narration, narrate, prune_stale, wav_name
from montage.script import Beat, Script
from montage.speakable import (Lexicon, PronunciationError, decade_words, load_lexicon, number_words,
                               ordinal_words, parse_lexicon, speak, year_words)

from test_captions import FakeVoice


# ── numbers ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n, words", [
    (0, "zero"), (7, "seven"), (13, "thirteen"), (40, "forty"), (44, "forty-four"), (100, "one hundred"),
    (344, "three hundred forty-four"), (1000, "one thousand"), (1_000_000, "one million"),
    (13_800_000_000, "thirteen billion eight hundred million"), (-5, "minus five")])
def test_number_words(n, words):
    assert number_words(n) == words


@pytest.mark.parametrize("y, words", [
    (2021, "twenty twenty-one"), (2000, "two thousand"), (2009, "two thousand nine"),
    (2010, "twenty ten"), (1984, "nineteen eighty-four"), (1905, "nineteen oh five"),
    (1900, "nineteen hundred"), (1500, "fifteen hundred")])
def test_year_words(y, words):
    assert year_words(y) == words


def test_ordinals_and_decades():
    assert [ordinal_words(n) for n in (1, 2, 3, 5, 8, 12, 20, 21, 44, 100)] == [
        "first", "second", "third", "fifth", "eighth", "twelfth", "twentieth", "twenty-first",
        "forty-fourth", "one hundredth"]
    assert [decade_words(y) for y in (1990, 2020, 2000, 1900)] == [
        "nineteen nineties", "twenty twenties", "two thousands", "nineteen hundreds"]


@pytest.mark.parametrize("text, spoken", [
    ("There were 344 failures.", "There were three hundred forty-four failures."),
    ("Christmas morning, 2021.", "Christmas morning, twenty twenty-one."),
    ("A 6.5 meter mirror", "A six point five meter mirror"),
    ("about 0.25 of it", "about zero point two five of it"),
    ("1,000,000 stars", "one million stars"),
    ("1500 kg", "one thousand five hundred kilograms"),  # a unit after it: a count, not a year
    ("the 1990s and the 21st century", "the nineteen nineties and the twenty-first century"),
    ("at -40 and −233", "at minus forty and minus two hundred thirty-three"),
    ("from 10-20 km", "from ten to twenty kilometers"),
    ("1990-2000", "nineteen ninety to two thousand"),
])
def test_numbers_in_text(text, spoken):
    assert speak(text).spoken == spoken


@pytest.mark.parametrize("text, spoken", [
    ("50% of it", "fifty percent of it"),
    ("at -233 °C", "at minus two hundred thirty-three degrees Celsius"),
    ("at 5°C and 1°F", "at five degrees Celsius and one degree Fahrenheit"),
    ("1 km, 3 km, 3km", "one kilometer, three kilometers, three kilometers"),
    ("10 µs later", "ten microseconds later"),
    ("1.5 million km away", "one point five million kilometers away"),
    ("a 6.5-meter mirror", "a six point five meter mirror"),
    ("travels 30 km/s", "travels thirty kilometers per second"),
    ("10x sharper", "ten times sharper"),
    ("$10 billion", "ten billion dollars"),
    ("$1 and $5", "one dollar and five dollars"),
    ("$10B", "ten billion dollars"),
    ("~5 km", "about five kilometers"),
])
def test_units_and_symbols(text, spoken):
    assert speak(text).spoken == spoken


@pytest.mark.parametrize("text", ["shot in 4K", "the 60s", "an L2 orbit", "COVID-19", "5G"])
def test_ambiguous_forms_are_left_as_written(text):
    assert speak(text).spoken == text


@pytest.mark.parametrize("text, spoken", [
    ("Dr. Rigby said", "Doctor Rigby said"),
    ("mirrors, e.g., beryllium", "mirrors, for example, beryllium"),
    ("gold, silver, etc.", "gold, silver, et cetera."),
    ("Webb vs. Hubble", "Webb versus Hubble"),
    ("light & dust", "light and dust"),
    ("No. 5 is next", "number five is next"),
])
def test_abbreviations(text, spoken):
    assert speak(text).spoken == spoken


def test_untouched_text_is_returned_byte_for_byte():
    text = "Every one of them worked.  That July,\nWebb sent home the Cosmic Cliffs."
    sp = speak(text)
    assert sp.spoken == text and not sp.changes and sp.groups == (1,) * len(align.tokens(text))


# ── pronunciations ───────────────────────────────────────────────────────────

def test_lexicon_exact_then_case_insensitive_then_possessive():
    lex = Lexicon({"Webb": "web", "JWST": "J W S T", "nircam": "near cam"})
    assert speak("Webb's JWST, NIRCam (and webb)", lex).spoken == "web's J W S T, near cam (and web)"


def test_lexicon_wins_over_number_rules():
    assert speak("Ariane 5 flew", Lexicon({"5": "five rocket"})).spoken == "Ariane five rocket flew"


def test_parse_lexicon_rejects_bad_entries():
    with pytest.raises(PronunciationError, match="one word"):
        parse_lexicon({"James Webb": "x"})
    with pytest.raises(PronunciationError, match="letter or digit"):
        parse_lexicon({"Webb": ""})
    with pytest.raises(PronunciationError, match="JSON object"):
        parse_lexicon(["Webb"])


def test_load_lexicon(tmp_path):
    assert not load_lexicon(tmp_path)  # optional file
    (tmp_path / "pronunciations.json").write_text('{"Webb": "  web  "}', encoding="utf-8")
    assert load_lexicon(tmp_path).lookup("Webb") == "web"
    (tmp_path / "pronunciations.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(PronunciationError):
        load_lexicon(tmp_path)


# ── written words keep their own timing ──────────────────────────────────────

def test_to_display_folds_spoken_words_onto_written_words():
    sp = speak("Launched in 2021, 344 times.")
    assert sp.spoken == "Launched in twenty twenty-one, three hundred forty-four times."
    assert sp.groups == (1, 1, 2, 3, 1)
    spoken = [Word(t, i * 100, i * 100 + 90) for i, t in enumerate(align.tokens(sp.spoken))]
    assert [(w.text, w.start_ms, w.end_ms) for w in sp.to_display(spoken)] == [
        ("Launched", 0, 90), ("in", 100, 190), ("2021,", 200, 390), ("344", 400, 690), ("times.", 700, 790)]
    with pytest.raises(ValueError):
        sp.to_display(spoken[:-1])


def _script(*lines):
    return Script("T", tuple(Beat(t, "./assets/a.png", "none") for t in lines), "test")


class RecordingVoice(FakeVoice):
    def __init__(self):
        super().__init__()
        self.texts = []

    def synthesize(self, text, include_alignments=False):
        self.texts.append(text)
        yield from super().synthesize(text, include_alignments)


def test_narrate_speaks_the_spoken_form_and_captions_the_written_one(tmp_path, monkeypatch):
    voice = RecordingVoice()
    monkeypatch.setattr(narrate_mod, "_load_voice", lambda v, log=print: (voice, True))
    (tmp_path / "pronunciations.json").write_text('{"Webb": "web"}', encoding="utf-8")
    logs = []
    (n,) = narrate(_script("Webb flew in 2021."), "v", tmp_path, log=logs.append)
    assert voice.texts == ["web flew in twenty twenty-one."]
    assert [w.text for w in n.words] == ["Webb", "flew", "in", "2021."]  # captions: as written
    assert n.word_method == align.ALIGNED
    assert n.path.name == wav_name(1, "v", "web flew in twenty twenty-one.")  # keyed by what Piper read
    side = json.loads(n.path.with_name(n.path.stem + ".words.json").read_text(encoding="utf-8"))
    assert side["text"] == "Webb flew in 2021." and side["spoken"] == "web flew in twenty twenty-one."
    assert any("'2021.' -> 'twenty twenty-one.'" in line for line in logs)

    narrate(_script("Webb flew in 2021."), "v", tmp_path, log=logs.append)
    assert len(voice.texts) == 1  # reused
    (tmp_path / "pronunciations.json").write_text('{"Webb": "webb"}', encoding="utf-8")
    narrate(_script("Webb flew in 2021."), "v", tmp_path, log=logs.append)
    assert voice.texts[-1] == "webb flew in twenty twenty-one."  # a pronunciation change re-synthesises


def test_unchanged_lines_keep_their_old_cache_key(tmp_path, monkeypatch):
    """A line with nothing to rewrite hashes exactly as before speakable existed, and an old words
    file (no "spoken" field) is still valid, so existing workspaces are not re-narrated."""
    import hashlib
    voice = RecordingVoice()
    monkeypatch.setattr(narrate_mod, "_load_voice", lambda v, log=print: (voice, True))
    text = "Every one of them worked."
    old = "beat_01_" + hashlib.sha1(("v\n" + text).encode()).hexdigest()[:10] + ".wav"
    (n,) = narrate(_script(text), "v", tmp_path)
    assert n.path.name == old
    side = json.loads(n.path.with_name(n.path.stem + ".words.json").read_text(encoding="utf-8"))
    assert "spoken" not in side
    narrate(_script(text), "v", tmp_path)
    assert len(voice.texts) == 1


def test_bad_pronunciations_file_is_invalid_input(tmp_path, monkeypatch, capsys):
    (tmp_path / "pronunciations.json").write_text('{"two words": "x"}', encoding="utf-8")
    with pytest.raises(PronunciationError):
        narrate(_script("hi"), "v", tmp_path)

    def boom(*a, **k):
        raise PronunciationError("pronunciations.json: key 'two words' must be one word (no spaces)")
    monkeypatch.setattr(planner, "run_plan", boom)
    (tmp_path / "brief.json").write_text("{}")
    assert cli.main(["plan", str(tmp_path / "brief.json")]) == 1
    assert "one word" in capsys.readouterr().err


# ── pruning stale narration ──────────────────────────────────────────────────

def test_prune_removes_only_unused_files_montage_wrote(tmp_path):
    d = tmp_path / "assets" / "narration"
    d.mkdir(parents=True)
    names = ["beat_01_aaaaaaaaaa.wav", "beat_01_aaaaaaaaaa.words.json",       # current
             "beat_01_bbbbbbbbbb.wav", "beat_01_bbbbbbbbbb.words.json",       # stale
             "beat_12_cccccccccc.tmp", "beat_02_dddddddddd.words.json",       # stale leftovers
             "intro.wav", "beat_01_take2.wav", "beat_01_ABCDEFABCD.wav", "notes.txt"]  # not ours
    for n in names:
        (d / n).write_bytes(b"")
    logs = []
    removed = prune_stale(tmp_path, [d / "beat_01_aaaaaaaaaa.wav"], log=logs.append)
    assert sorted(p.name for p in removed) == ["beat_01_bbbbbbbbbb.wav", "beat_01_bbbbbbbbbb.words.json",
                                               "beat_02_dddddddddd.words.json", "beat_12_cccccccccc.tmp"]
    assert sorted(p.name for p in d.iterdir()) == sorted(set(names) - {p.name for p in removed})
    assert logs == ["  narration: pruned 1 stale wav(s) no beat uses (4 files)"]
    assert prune_stale(tmp_path / "nowhere", []) == []


def test_replan_prunes_wavs_of_edited_lines(tmp_path, monkeypatch):
    from test_pipelines import GOOD, _brief, fake_probe
    from montage.plan import parse_plan
    (tmp_path / "assets").mkdir()
    for name in ("rope_memory.mp4", "agc-panel.png"):
        (tmp_path / "assets" / name).write_bytes(b"")
    (tmp_path / "brief.json").write_text("{}")
    voice = RecordingVoice()
    monkeypatch.setattr(narrate_mod, "_load_voice", lambda v, log=print: (voice, True))
    monkeypatch.setattr(planner, "load_brief", lambda _p: _brief(tmp_path, recipe="documentary", target=20))
    monkeypatch.setattr(planner, "parse_plan", partial(parse_plan, probe=fake_probe))
    script = dict(GOOD, writer="hand")
    (tmp_path / "script.json").write_text(json.dumps(script))
    planner.run_plan(tmp_path / "brief.json", log=lambda *_: None)
    d = tmp_path / "assets" / "narration"
    first = sorted(p.name for p in d.glob("*.wav"))
    script["beats"][0] = dict(script["beats"][0], narration="A different first line.")
    (tmp_path / "script.json").write_text(json.dumps(script))
    planner.run_plan(tmp_path / "brief.json", log=lambda *_: None)
    second = sorted(p.name for p in d.glob("*.wav"))
    assert len(second) == len(first) and first[0] not in second and first[1:] == second[1:]
    plan = json.loads((tmp_path / "scene_plan.json").read_text())
    used = {Path(c["src"]).name for t in plan["tracks"] if t["id"] == "narration" for c in t["clips"]}
    assert used == set(second)


# ── real Piper ───────────────────────────────────────────────────────────────

VOICE = narrate_mod.VOICES_DIR / "en_US-lessac-medium.onnx"


@pytest.mark.skipif(not VOICE.is_file(), reason="needs the en_US-lessac-medium voice")
def test_real_piper_aligns_spoken_numbers_to_written_words(tmp_path):
    (tmp_path / "pronunciations.json").write_text('{"Webb": "web"}', encoding="utf-8")
    line = "In 2021, Webb's 6.5-meter mirror cooled to -233 °C, 1.5 million km away."
    (n,) = narrate(_script(line), "en_US-lessac-medium", tmp_path, log=lambda *_: None)
    assert n.word_method == align.ALIGNED  # Piper's phoneme durations, not an estimate
    assert [w.text for w in n.words] == align.tokens(line)
    spans = [(w.start_ms, w.end_ms) for w in n.words]
    assert all(a < b for a, b in spans) and all(b1 <= a2 for (_, b1), (a2, _) in zip(spans, spans[1:]))
    by = {w.text: w for w in n.words}
    # spelled-out numbers take far longer than one written token would: "minus two hundred thirty-three"
    assert by["-233"].end_ms - by["-233"].start_ms > 800
    assert spans[-1][1] <= n.seconds * 1000 + 1
