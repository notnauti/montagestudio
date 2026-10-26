"""Brief validation, script writers (Claude + Ollama, mocked), composition timeline.

No model is called and no money is spent here: the Anthropic client and Ollama's HTTP
endpoint are replaced with fakes that record what they were sent.
"""

import io
import json
import types
from pathlib import Path

import pytest

from montage import script as script_mod
from montage.brief import BriefError, parse_brief
from montage.compose import EXPLAINER, ComposeError, compose
from montage.narrate import Narration
from montage.plan import parse_plan
from montage.probe import MediaInfo
from montage.script import Beat, Script, ScriptError, WriterUnavailable, parse_script, write_script


def fake_probe(path):
    return {
        ".mp4": MediaInfo(True, False, 6.0),
        ".png": MediaInfo(True, False, None),
        ".wav": MediaInfo(False, True, 120.0),
    }[path.suffix]


BRIEF = {
    "title": "How GPS keeps time",
    "brief": "Explain why GPS satellite clocks need relativistic corrections, for curious non-physicists.",
    "targetSeconds": 45,
    "assets": [
        {"src": "./assets/orbit.mp4", "description": "satellite orbiting Earth"},
        {"src": "./assets/clocks.png", "description": "two clocks drifting apart"},
    ],
    "music": {"src": "./assets/bed.wav", "volume": 0.1},
}


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "assets").mkdir()
    for name in ("orbit.mp4", "clocks.png", "bed.wav"):
        (tmp_path / "assets" / name).write_bytes(b"")
    return tmp_path


def brief(ws, **changes):
    raw = json.loads(json.dumps(BRIEF)) | changes
    return parse_brief(raw, ws, probe=fake_probe)


# ── brief ─────────────────────────────────────────────────────────────────────

def test_valid_brief(ws):
    b = brief(ws)
    assert [a.is_still for a in b.assets] == [False, True]
    assert b.assets[0].duration == 6.0 and b.voice == "en_US-lessac-medium" and b.music_volume == 0.1


@pytest.mark.parametrize("changes,expected", [
    ({"extra": 1}, "unsupported field 'extra'"),
    ({"targetSeconds": 5}, "targetSeconds"),
    ({"recipe": "infomercial"}, "unknown pipeline 'infomercial'"),
    ({"assets": []}, "non-empty array"),
    ({"assets": [{"src": "./assets/orbit.mp4"}]}, "description"),
    ({"assets": [{"src": "./assets/nope.mp4", "description": "x"}]}, "file not found"),
    ({"assets": [{"src": "./assets/bed.wav", "description": "x"}]}, "no video stream"),
    ({"music": {"src": "./assets/bed.wav", "volume": 2}}, "volume"),
])
def test_brief_rejects(ws, changes, expected):
    with pytest.raises(BriefError) as exc:
        brief(ws, **changes)
    assert any(expected in e for e in exc.value.errors), exc.value.errors


# ── script parsing ────────────────────────────────────────────────────────────

def test_parse_script_rejects_unknown_asset_and_motion(ws):
    raw = {"title": "t", "beats": [{"narration": "hi", "asset": "./assets/other.png", "motion": "spin"}]}
    with pytest.raises(ScriptError) as exc:
        parse_script(raw, brief(ws), "test")
    assert "not in the brief's asset list" in str(exc.value) and "spin" in str(exc.value)


def test_prompt_states_video_word_limit(ws):
    prompt = script_mod.build_prompt(brief(ws))
    # 18 = int(6.0 s * 3.1 words/s): the rate was recalibrated from 2.5 against real Piper output
    assert "video, 6.0s, at most 18 words" in prompt and "about 45 seconds" in prompt


def test_schema_restricts_assets_to_brief(ws):
    schema = script_mod.script_schema(brief(ws))
    assert schema["properties"]["beats"]["items"]["properties"]["asset"]["enum"] == \
        ["./assets/orbit.mp4", "./assets/clocks.png"]


# ── Claude writer (fake client) ───────────────────────────────────────────────

GOOD = {"title": "GPS time", "beats": [
    {"narration": "Satellites circle the Earth.", "asset": "./assets/orbit.mp4", "motion": "none"},
    {"narration": "Their clocks drift.", "asset": "./assets/clocks.png", "motion": "zoom_in"},
]}


class FakeAnthropic:
    calls = []
    response = None
    error = None

    def __init__(self):
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        FakeAnthropic.calls.append(kwargs)
        if FakeAnthropic.error:
            raise FakeAnthropic.error
        return FakeAnthropic.response


def _response(stop_reason="end_turn", text=json.dumps(GOOD)):
    content = [types.SimpleNamespace(type="thinking", thinking=""),
               types.SimpleNamespace(type="text", text=text)]
    return types.SimpleNamespace(stop_reason=stop_reason, content=content, model="claude-opus-5",
                                 stop_details=types.SimpleNamespace(category="cyber"))


@pytest.fixture
def fake_claude(monkeypatch):
    import anthropic
    FakeAnthropic.calls, FakeAnthropic.response, FakeAnthropic.error = [], _response(), None
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return FakeAnthropic


def test_claude_request_shape(ws, fake_claude):
    s = script_mod.write_with_claude(brief(ws))
    assert s.writer == "claude-opus-5" and len(s.beats) == 2
    call = fake_claude.calls[0]
    assert call["model"] == script_mod.CLAUDE_MODEL == "claude-opus-5-5"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "budget_tokens" not in json.dumps(call) and "temperature" not in call


def test_claude_refusal_is_an_error_not_a_fallback(ws, fake_claude):
    fake_claude.response = _response(stop_reason="refusal")
    with pytest.raises(ScriptError) as exc:
        script_mod.write_with_claude(brief(ws))
    assert not isinstance(exc.value, WriterUnavailable) and "declined" in str(exc.value)


def test_claude_max_tokens(ws, fake_claude):
    fake_claude.response = _response(stop_reason="max_tokens")
    with pytest.raises(ScriptError, match="max_tokens"):
        script_mod.write_with_claude(brief(ws))


def test_claude_without_credentials_is_unavailable(ws, fake_claude):
    fake_claude.error = TypeError("Could not resolve authentication method. Expected one of api_key, ...")
    with pytest.raises(WriterUnavailable):
        script_mod.write_with_claude(brief(ws))


# ── auto fallback ─────────────────────────────────────────────────────────────

def test_auto_falls_back_only_when_unavailable(ws, monkeypatch):
    b, logs = brief(ws), []
    ollama = Script("t", (Beat("x", "./assets/clocks.png", "none"),), "ollama:qwen2.5:7b")

    def unavailable(_):
        raise WriterUnavailable("no Claude credentials")
    monkeypatch.setattr(script_mod, "write_with_claude", unavailable)
    monkeypatch.setattr(script_mod, "write_with_ollama", lambda _: ollama)
    assert write_script(b, "auto", log=logs.append) is ollama
    assert "falling back" in logs[0]

    def broken(_):
        raise ScriptError("Claude API error 500")
    monkeypatch.setattr(script_mod, "write_with_claude", broken)
    with pytest.raises(ScriptError, match="500"):
        write_script(b, "auto", log=logs.append)


def test_ollama_request_and_parse(ws, monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout):
        sent.update(json.loads(req.data))
        return io.BytesIO(json.dumps({"message": {"content": json.dumps(GOOD)}}).encode())
    monkeypatch.setattr(script_mod.urllib.request, "urlopen", fake_urlopen)
    s = script_mod.write_with_ollama(brief(ws))
    assert s.writer.startswith("ollama:") and len(s.beats) == 2
    assert sent["stream"] is False and sent["format"]["required"] == ["title", "beats"]


# ── composition ───────────────────────────────────────────────────────────────

def _narr(seconds):
    return [Narration(f"./assets/narration/b{i}.wav", Path(f"b{i}.wav"), s) for i, s in enumerate(seconds)]


def _plan_ws(ws, n):
    (ws / "assets" / "narration").mkdir(exist_ok=True)
    for i in range(n):
        (ws / "assets" / "narration" / f"b{i}.wav").write_bytes(b"")


def test_compose_timeline_passes_the_contract(ws):
    b = brief(ws)
    script = Script("GPS time", (Beat("a", "./assets/orbit.mp4", "none"),
                                 Beat("b", "./assets/clocks.png", "zoom_in"),
                                 Beat("c", "./assets/clocks.png", "zoom_out")), "test")
    _plan_ws(ws, 3)
    raw = compose(b, script, _narr([2.0, 3.0, 1.5]))
    plan = parse_plan(raw, ws, probe=fake_probe)
    r, fps = EXPLAINER, 30
    beats = [r.lead + 60 + r.tail, r.lead + 90 + r.tail, r.lead + 45 + r.end_hold]
    assert plan.meta.duration_frames == sum(beats)
    vo = plan.audio[0].clips
    assert [c.start_frame for c in vo] == [r.lead, beats[0] + r.lead, beats[0] + beats[1] + r.lead]
    shots = plan.video.clips
    assert shots[1].motion.scale_to == r.zoom and shots[2].motion.scale_from == r.zoom
    assert shots[0].crossfade_frames == r.crossfade and shots[2].crossfade_frames == 0
    music = plan.audio[1].clips[0]
    assert music.duration_frames == plan.meta.duration_frames and music.volume == 0.1


def test_compose_advances_and_wraps_video_in_point(ws):
    b = brief(ws)  # orbit.mp4 is 6.0s = 180 frames
    beats = tuple(Beat(str(i), "./assets/orbit.mp4", "none") for i in range(3))
    _plan_ws(ws, 3)
    raw = compose(b, Script("t", beats, "test"), _narr([2.0, 2.0, 2.0]))
    shots = raw["tracks"][0]["clips"]
    need = shots[0]["durationFrames"]  # 93 frames; the source has 180
    assert "sourceInFrame" not in shots[0]
    assert shots[1]["sourceInFrame"] == 180 - need  # 87 fresh frames left: take the source's last stretch
    assert "sourceInFrame" not in shots[2]  # source used up: starts over
    parse_plan(raw, ws, probe=fake_probe)


def test_compose_rejects_line_longer_than_video(ws):
    b = brief(ws)
    _plan_ws(ws, 1)
    with pytest.raises(ComposeError, match="only 6.00s"):
        compose(b, Script("t", (Beat("long", "./assets/orbit.mp4", "none"),), "test"), _narr([9.0]))
