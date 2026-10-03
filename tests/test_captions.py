"""Word timestamps (align.py), captions.json + caption track (captions.py, compose, validator),
ASS generation (subtitles.py) and burn-in (render.py, --no-captions).

Unit tests use synthetic phoneme alignments and a fake Piper voice. Two tests touch real tools:
one patches the real lessac-medium model (skipped without it), one renders with real ffmpeg into
a directory whose name needs filter escaping (skipped without ffmpeg).
"""

import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from montage import align, captions, narrate as narrate_mod, subtitles
from montage.align import Sentence, Word, add_alignment_output, tokens, words_from_sentences
from montage.cli import main
from montage.compose import compose
from montage.narrate import Narration, narrate
from montage.pipeline import PipelineError, load_pipeline, parse_pipeline, Recipe
from montage.plan import CaptionClip, CaptionTrack, CaptionWord, PlanError, load_plan, parse_plan
from montage.probe import MediaInfo
from montage.render import render, render_command
from montage.script import Beat, Script

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


# ── ONNX patch ────────────────────────────────────────────────────────────────

def _ld(field: int, payload: bytes) -> bytes:
    return align._enc_varint(field << 3 | 2) + align._enc_varint(len(payload)) + payload


def _model(*nodes: tuple[str, str], outputs=("output",)) -> bytes:
    graph = b"".join(_ld(1, _ld(2, out.encode()) + _ld(4, op.encode())) for op, out in nodes)
    graph += b"".join(_ld(12, _ld(1, o.encode())) for o in outputs)
    graph += _ld(5, b"\x00" * 300)  # an initializer-sized blob, to cross a multi-byte length
    return b"\x08\x07" + _ld(7, graph) + _ld(8, b"\x08\x0d")  # ir_version, graph, opset_import


def _graph_outputs(model: bytes) -> list[str]:
    (gs, ge), = [(s, e) for num, _, _, s, e in align._fields(model) if num == 7]
    return [align._str(model, s2, e2)
            for num, _, _, s, e in align._fields(model, gs, ge) if num == 12
            for n2, _, _, s2, e2 in align._fields(model, s, e) if n2 == 1]


def test_patch_marks_the_ceil_tensor_as_an_output():
    model = _model(("Add", "a"), ("Ceil", "/Ceil_output_0"), ("Mul", "b"))
    patched, name = add_alignment_output(model)
    assert name == "/Ceil_output_0"
    assert _graph_outputs(patched) == ["output", "/Ceil_output_0"]
    assert patched.startswith(b"\x08\x07") and patched.endswith(_ld(8, b"\x08\x0d"))  # rest untouched
    again, _ = add_alignment_output(patched)  # already patched -> unchanged
    assert again == patched


@pytest.mark.parametrize("nodes", [[("Add", "a")], [("Ceil", "x"), ("Ceil", "y")]])
def test_patch_needs_exactly_one_ceil(nodes):
    with pytest.raises(align.AlignError, match="Ceil"):
        add_alignment_output(_model(*nodes))


def test_patch_rejects_non_onnx_bytes():
    with pytest.raises(align.AlignError):
        add_alignment_output(b"\x0a\xff\x7f")  # declares a field longer than the data


VOICE = narrate_mod.VOICES_DIR / "en_US-lessac-medium.onnx"


@pytest.mark.skipif(not VOICE.is_file(), reason="needs the en_US-lessac-medium voice")
def test_real_voice_gets_a_duration_output():
    voice = align.load_aligned_voice(VOICE)
    assert [o.name for o in voice.session.get_outputs()][1:] == ["/Ceil_output_0"]


# ── phoneme durations -> words ────────────────────────────────────────────────

def _sentence(*runs, lead=50, gap=30, tail=20, punct=None):
    """Phoneme alignment: BOS, words of 100-sample phonemes separated by spaces, EOS."""
    ph = [("^", lead)]
    for i, run in enumerate(runs):
        if i:
            ph.append((" ", gap))
        ph += [(p, 100) for p in run]
    if punct:
        ph.append(punct)
    ph.append(("$", tail))
    return Sentence(tuple(ph), sum(n for _, n in ph))


def test_one_run_per_token_gives_exact_spans():
    s = _sentence(["h", "ˈ", "a"], ["ð", "ɛ", "ɹ"], punct=(".", 200))
    words, method = words_from_sentences("Hi there.", [s], sample_rate=1000)
    assert method == align.ALIGNED
    # stress marks are phonemes too (100 each here); punctuation is a pause, not part of the word
    assert words == [Word("Hi", 50, 350), Word("there.", 380, 680)]


def test_sentences_are_offset_by_earlier_audio():
    a, b = _sentence(["a"]), _sentence(["b"])
    words, _ = words_from_sentences("A. B.", [a, b], 1000)
    assert words[1].start_ms == a.samples + 50


def test_merged_run_is_split_at_the_phoneme_boundary():
    # espeak speaks "on the" as one run "ɔnðə"; each token phonemized alone says where it splits
    s = _sentence(["ɔ", "n", "ð", "ə"], ["m", "ˈ", "u", "n"])
    own = {"on": ["ˈ", "ɔ", "n"], "the": ["ð", "ə"], "moon": ["m", "ˈ", "u", "n"]}
    words, method = words_from_sentences("on the moon", [s], 1000, lambda t: own[t])
    assert method == align.ALIGNED
    assert [(w.text, w.start_ms, w.end_ms) for w in words] == [
        ("on", 50, 250), ("the", 250, 450), ("moon", 480, 880)]


def test_number_spoken_as_two_runs_is_one_word():
    s = _sentence(["θ", "ɜ"], ["e", "t"], ["d", "e"])
    own = {"38": ["θ", "ɜ", " ", "e", "t"], "days": ["d", "e"]}
    words, method = words_from_sentences("38 days", [s], 1000, lambda t: own[t])
    assert method == align.ALIGNED
    assert [(w.text, w.start_ms, w.end_ms) for w in words] == [("38", 50, 480), ("days", 510, 710)]


def test_unmatchable_tokens_fall_back_to_an_estimate():
    s = _sentence(["a", "b"], ["c"])
    words, method = words_from_sentences("one two three", [s], 1000, lambda t: ["x", "y", "z"])
    assert method == align.ESTIMATED
    assert words[0].start_ms == 0 and words[-1].end_ms == s.samples


def test_missing_alignment_is_estimated_by_word_length():
    words, method = words_from_sentences("a bbb", [Sentence(None, 4000)], 1000)
    assert method == align.ESTIMATED
    assert [(w.start_ms, w.end_ms) for w in words] == [(0, 1000), (1000, 4000)]


def test_tokens_glue_punctuation_to_words():
    assert tokens("Hello — world ! (yes)") == ["Hello —", "world!", "(yes)"]
    assert tokens("  ") == []


# ── narration writes and reuses word timings ──────────────────────────────────

class _Al:
    def __init__(self, phoneme, num_samples):
        self.phoneme, self.num_samples = phoneme, num_samples


class _Chunk:
    sample_rate, sample_width, sample_channels = 1000, 2, 1

    def __init__(self, sentence):
        n = sentence.samples
        self.audio_int16_array = [0] * n
        self.audio_int16_bytes = b"\x00\x00" * n
        self.phoneme_alignments = [_Al(p, s) for p, s in sentence.phonemes]


class FakeVoice:
    def __init__(self):
        self.config = type("C", (), {"sample_rate": 1000})()
        self.calls = 0

    def synthesize(self, text, include_alignments=False):
        self.calls += 1
        yield _Chunk(_sentence(*[list(w.strip(".").lower()) for w in text.split()]))

    def phonemize(self, text):
        return [list(text.lower())]


def _script(*lines):
    return Script("T", tuple(Beat(t, "./assets/a.png", "none") for t in lines), "test")


def test_narrate_writes_words_next_to_the_wav_and_reuses_them(tmp_path, monkeypatch):
    voice = FakeVoice()
    monkeypatch.setattr(narrate_mod, "_load_voice", lambda v, log=print: (voice, True))
    first = narrate(_script("Hi there.", "Bye."), "v", tmp_path)
    assert voice.calls == 2
    assert [w.text for w in first[0].words] == ["Hi", "there."] and first[0].word_method == align.ALIGNED
    sidecar = first[0].path.with_name(first[0].path.stem + ".words.json")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["words"][0] == {"text": "Hi", "startMs": 50, "endMs": 250}

    again = narrate(_script("Hi there.", "Bye."), "v", tmp_path)
    assert voice.calls == 2 and again == first  # wav + words reused, nothing synthesised

    sidecar.unlink()  # a wav without its words is synthesised again, so both come from one run
    narrate(_script("Hi there.", "Bye."), "v", tmp_path)
    assert voice.calls == 3 and sidecar.is_file()


def test_narrate_without_alignment_estimates(tmp_path, monkeypatch):
    class Plain:
        def synthesize_wav(self, text, wf):
            wf.setframerate(1000), wf.setsampwidth(2), wf.setnchannels(1)
            wf.writeframes(b"\x00\x00" * 2000)
    monkeypatch.setattr(narrate_mod, "_load_voice", lambda v, log=print: (Plain(), False))
    (n,) = narrate(_script("one two"), "v", tmp_path)
    assert n.word_method == align.ESTIMATED
    assert [(w.start_ms, w.end_ms) for w in n.words] == [(0, 1000), (1000, 2000)]


# ── timeline frames, captions.json and the caption track ──────────────────────

def _narr(*words, method=align.ALIGNED):
    return Narration("./assets/narration/b.wav", Path("b.wav"), 3.0, tuple(Word(*w) for w in words), method)


def test_word_frames_are_ordered_non_empty_and_non_overlapping():
    n = _narr(("a", 0, 10), ("b", 12, 20), ("long", 100, 400), ("end", 395, 600))
    frames = captions.word_frames(n, clip_start=12, fps=30, limit=1000)
    spans = [(w["startFrame"], w["endFrame"]) for w in frames]
    assert spans == [(12, 13), (13, 14), (15, 24), (24, 30)]  # sub-frame words still get a frame


def test_build_track_and_document():
    voice = [{"id": "vo_01", "startFrame": 12, "durationFrames": 60}, {"id": "vo_02", "startFrame": 90, "durationFrames": 30}]
    narr = [_narr(("Hi", 50, 300), ("there.", 350, 1990)), _narr(("Bye.", 0, 900), method=align.ESTIMATED)]
    track, doc = captions.build(voice, narr, 30, 140, "pop")
    assert track["type"] == "caption" and track["style"] == "pop"
    assert [c["id"] for c in track["clips"]] == ["cap_01", "cap_02"]
    assert track["clips"][0] == {"id": "cap_01", "startFrame": 12, "durationFrames": 60, "words": [
        {"text": "Hi", "startFrame": 14, "endFrame": 21}, {"text": "there.", "startFrame": 22, "endFrame": 72}]}
    assert doc["beats"][1] | {"words": None} == {"beat": 2, "clip": "vo_02", "src": "./assets/narration/b.wav",
                                                 "method": "estimated", "offsetFrame": 90, "offsetMs": 3000,
                                                 "words": None}
    assert doc["beats"][0]["words"][1] == {"text": "there.", "startMs": 350, "endMs": 1990,
                                           "startFrame": 22, "endFrame": 72}
    assert captions.build(voice, narr, 30, 140, "none")[0] is None


# ── validator ─────────────────────────────────────────────────────────────────

def fake_probe(path):
    return MediaInfo(path.suffix != ".wav", path.suffix == ".wav", None if path.suffix == ".png" else 60.0)


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "assets").mkdir()
    for name in ("a.png", "vo.wav"):
        (tmp_path / "assets" / name).write_bytes(b"")
    return tmp_path


def _plan(caption_tracks):
    return {"meta": {"title": "t", "resolution": [320, 180], "fps": 30, "durationFrames": 100},
            "tracks": [{"id": "v", "type": "video", "clips": [
                           {"id": "s", "src": "./assets/a.png", "startFrame": 0, "durationFrames": 100}]},
                       {"id": "n", "type": "audio", "clips": [
                           {"id": "vo", "src": "./assets/vo.wav", "startFrame": 10, "durationFrames": 60}]}]
            + caption_tracks}


def _cap(**over):
    clip = {"id": "c1", "startFrame": 10, "durationFrames": 60, "words": [
        {"text": "Hello", "startFrame": 12, "endFrame": 30}, {"text": "world.", "startFrame": 30, "endFrame": 55}]}
    return {"id": "captions", "type": "caption", "style": "highlight", "clips": [clip]} | over


def test_caption_track_parses(ws):
    plan = parse_plan(_plan([_cap()]), ws, probe=fake_probe)
    assert plan.captions == CaptionTrack("captions", "highlight", (CaptionClip("c1", 10, 60, (
        CaptionWord("Hello", 12, 30), CaptionWord("world.", 30, 55))),))
    assert parse_plan(_plan([]), ws, probe=fake_probe).captions is None
    no_style = _cap()
    del no_style["style"]
    assert parse_plan(_plan([no_style]), ws, probe=fake_probe).captions.style == "highlight"


def _words(*w):
    return [{"text": t, "startFrame": s, "endFrame": e} for t, s, e in w]


@pytest.mark.parametrize("track, message", [
    (_cap(style="karaoke"), "style: must be one of"),
    (_cap(font="Arial"), "unsupported field 'font'"),
    (_cap(clips=[{"id": "c1", "startFrame": 10, "durationFrames": 60, "words": [], "src": "x"}]),
     "unsupported field 'src'"),
    (_cap(clips=[{"id": "c1", "startFrame": 10, "durationFrames": 60, "words": []}]), "words: must be a non-empty"),
    (_cap(clips=[{"id": "c1", "startFrame": 10, "durationFrames": 60,
                  "words": _words(("a", 20, 30), ("b", 25, 40))}]), "must not overlap"),
    (_cap(clips=[{"id": "c1", "startFrame": 10, "durationFrames": 60,
                  "words": _words(("a", 5, 30))}]), "outside the clip"),
    (_cap(clips=[{"id": "c1", "startFrame": 10, "durationFrames": 60,
                  "words": _words(("a", 30, 30))}]), "must be after startFrame"),
    (_cap(clips=[{"id": "c1", "startFrame": 10, "durationFrames": 60,
                  "words": _words(("two\nlines", 20, 30))}]), "single-line"),
    (_cap(clips=[{"id": "c1", "startFrame": 60, "durationFrames": 60,
                  "words": _words(("a", 70, 80))}]), "past meta.durationFrames"),
    (_cap(clips=[{"id": "vo", "startFrame": 10, "durationFrames": 60,
                  "words": _words(("a", 20, 30))}]), "duplicate clip id 'vo'"),
    (_cap(clips=[{"id": "c1", "startFrame": 10, "durationFrames": 30, "words": _words(("a", 10, 20))},
                 {"id": "c2", "startFrame": 30, "durationFrames": 30, "words": _words(("b", 30, 40))}]),
     "must be sorted and must not overlap"),
])
def test_caption_track_rejections(ws, track, message):
    with pytest.raises(PlanError) as exc:
        parse_plan(_plan([track]), ws, probe=fake_probe)
    assert any(message in e for e in exc.value.errors), exc.value.errors


def test_one_caption_track_only_and_style_only_on_captions(ws):
    with pytest.raises(PlanError, match="at most one caption track"):
        parse_plan(_plan([_cap(), _cap(id="more")]), ws, probe=fake_probe)
    raw = _plan([])
    raw["tracks"][0]["style"] = "highlight"
    with pytest.raises(PlanError, match="unsupported field 'style'"):
        parse_plan(raw, ws, probe=fake_probe)


# ── recipes and compose ───────────────────────────────────────────────────────

def test_recipes_choose_a_caption_style():
    assert [load_pipeline(n).recipe.caption_style for n in ("explainer", "documentary", "promo")] == \
        ["highlight", "highlight", "pop"]
    assert Recipe().caption_style == "highlight"
    base = json.loads((Path(__file__).parents[1] / "pipelines" / "explainer.json").read_text(encoding="utf-8"))
    base.pop("captionStyle")
    assert parse_pipeline(base | {"name": "x"}, "x").recipe.caption_style == "highlight"  # optional
    with pytest.raises(PipelineError, match="captionStyle"):
        parse_pipeline(base | {"name": "x", "captionStyle": "karaoke"}, "x")


def test_compose_adds_a_caption_track_the_validator_accepts(ws):
    from montage.brief import parse_brief
    brief = parse_brief({"title": "t", "brief": "b", "targetSeconds": 10,
                         "assets": [{"src": "./assets/a.png", "description": "a"}]}, ws, probe=fake_probe)
    (ws / "assets" / "narration").mkdir()
    narr = []
    for i in range(2):
        p = ws / "assets" / "narration" / f"b{i}.wav"
        p.write_bytes(b"")
        narr.append(Narration(f"./assets/narration/b{i}.wav", p, 2.0,
                              (Word("Hello", 40, 700), Word("world.", 760, 1990)), align.ALIGNED))
    script = Script("t", (Beat("Hello world.", "./assets/a.png", "none"),) * 2, "test")
    raw = compose(brief, script, narr)
    plan = parse_plan(raw, ws, probe=fake_probe)
    assert [c.id for c in plan.captions.clips] == ["cap_01", "cap_02"]
    assert plan.captions.clips[1].words[0].start_frame == plan.audio[0].clips[1].start_frame + 1
    assert all(t["type"] != "caption" for t in compose(brief, script, narr, Recipe(caption_style="none"))["tracks"])


# ── ASS ───────────────────────────────────────────────────────────────────────

def _clip(*texts, start=0, step=10):
    words = tuple(CaptionWord(t, start + i * step, start + (i + 1) * step) for i, t in enumerate(texts))
    return CaptionClip("c", start, len(texts) * step + 5, words)


def test_pages_split_at_sentences_and_balance_long_lines():
    clip = _clip(*"Every GPS satellite carries an atomic clock. Then it stops.".split())
    texts = [[clip.words[i].text for i in p] for p in subtitles.pages(clip)]
    assert texts == [["Every", "GPS", "satellite"], ["carries", "an", "atomic", "clock."], ["Then", "it", "stops."]]
    short = _clip("Up", "there,", "those", "clocks")
    assert len(subtitles.pages(short)) == 1  # a 2-word clause is not worth its own page


def test_highlight_events_cover_each_word_until_the_next():
    track = CaptionTrack("c", "highlight", (_clip("one", "two", start=30),))  # words 30-40, 40-50; clip to 55
    assert subtitles.events(track, 30, lambda f: f) == [
        (30, 40, r"{\c&H0000D7FF&}one{\r} two"),
        (40, 55, r"one {\c&H0000D7FF&}two{\r}"),  # the last word holds to the clip's end
    ]


def test_pop_hides_unspoken_words_without_reflowing():
    track = CaptionTrack("c", "pop", (_clip("one", "two", "three"),))
    first = subtitles.events(track, 30, lambda f: f)[0][2]
    assert first.startswith(r"{\c&H0000D7FF&\fscx125\fscy125\t(0,120,\fscx100\fscy100)}one")
    assert r"{\alpha&HFF&}two{\r}" in first and r"{\alpha&HFF&}three{\r}" in first


def test_ass_document():
    track = CaptionTrack("c", "highlight", (_clip("a{b}", "c\\d", start=31),))
    doc = subtitles.to_ass(track, 1280, 720, 30, lambda f: f * 2)
    assert "PlayResX: 1280\nPlayResY: 720" in doc
    assert "Style: Caption,Arial,43," in doc  # 6% of the short edge
    assert "Dialogue: 0,0:00:02.06,0:00:02.73,Caption" in doc  # frames mapped x2, floored to centiseconds
    assert "a(b)" in doc and "c/d" in doc  # no stray override braces or backslash escapes


def test_ass_timestamps_floor_to_the_frame():
    assert subtitles._ts(1, 30) == "0:00:00.03"
    assert subtitles._ts(30 * 3725 + 15, 30) == "1:02:05.50"


def test_filter_value_escaping():
    assert subtitles.escape_filter_value("C:/My Files/it's [1], a;b.ass") == \
        r"C\\:/My Files/it\\\'s \[1\]\, a\;b.ass"


# ── burn-in with real ffmpeg ──────────────────────────────────────────────────

def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _gray_frame(path, n):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                        "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True, check=True)
    return r.stdout


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_burn_in_through_an_awkward_path(tmp_path, capsys):
    ws = tmp_path / "it's a [cap], test;"  # every character the filter escaping has to survive
    (ws / "assets").mkdir(parents=True)
    _ffmpeg("-f", "lavfi", "-i", "color=c=0x404040:s=640x360:r=30:d=4", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(ws / "assets" / "bg.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=4", str(ws / "assets" / "vo.wav"))
    raw = {"meta": {"title": "Caption Test", "resolution": [640, 360], "fps": 30, "durationFrames": 90},
           "tracks": [{"id": "v", "type": "video", "clips": [
                          {"id": "bg", "src": "./assets/bg.mp4", "startFrame": 0, "durationFrames": 90}]},
                      {"id": "n", "type": "audio", "clips": [
                          {"id": "vo", "src": "./assets/vo.wav", "startFrame": 0, "durationFrames": 90}]},
                      {"id": "captions", "type": "caption", "style": "highlight", "clips": [
                          {"id": "c1", "startFrame": 5, "durationFrames": 80,
                           "words": _words(("HELLO", 5, 40), ("WORLD", 40, 85))}]}]}
    (ws / "scene_plan.json").write_text(json.dumps(raw), encoding="utf-8")
    plan = load_plan(ws / "scene_plan.json")

    measured = {"input_i": "-20", "input_tp": "-3", "input_lra": "5", "input_thresh": "-30", "target_offset": "0"}
    cmd = render_command(plan, ws / "x.mp4", measured, None, ws / "cache" / "captions" / "x.ass")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert r"ass=filename=" in graph and r"it\\\'s a \[cap\]\, test\;" in graph

    with_caps = render(plan, ws / "with.mp4")
    assert (ws / "cache" / "captions" / "caption-test-native.ass").is_file()
    assert main(["render", str(ws / "scene_plan.json"), "--no-captions", "--force",  # gate: not under test here
                 "-o", str(ws / "without.mp4")]) == 0
    assert "captions: off (--no-captions)" in capsys.readouterr().out

    a, b = _gray_frame(with_caps, 20), _gray_frame(ws / "without.mp4", 20)  # mid "HELLO"
    rows = [(abs(x - y) for x, y in zip(a[r * 640:(r + 1) * 640], b[r * 640:(r + 1) * 640])) for r in range(360)]
    diff = [sum(r) / 640 for r in rows]
    assert max(diff[270:345]) > 20  # caption band: text drawn
    assert max(diff[:200]) < 2      # rest of the frame: same picture
