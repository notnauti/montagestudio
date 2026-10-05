"""Pipeline recipes, recipe-aware script prompts, `montage init`, and `montage plan --topic`.

No model, no Piper, no ffmpeg: writers are faked, probes are faked, narration is stubbed.
"""

import io
import json
import types
from functools import partial
from pathlib import Path

import pytest

from montage import cli, planner
from montage import probe as probe_mod
from montage import script as script_mod
from montage.brief import parse_brief
from montage.compose import EXPLAINER, compose
from montage.narrate import Narration
from montage.pipeline import PipelineError, available, load_pipeline, parse_pipeline
from montage.plan import parse_plan
from montage.probe import MediaInfo
from montage.scaffold import WorkspaceError, synthesize_brief
from montage.script import Beat, Script


def fake_probe(path):
    if path.suffix == ".txt":
        raise probe_mod.ProbeError("Invalid data found when processing input")
    return {
        ".mp4": MediaInfo(True, False, 30.0),
        ".png": MediaInfo(True, False, None),
        ".wav": MediaInfo(False, True, 120.0),
    }[path.suffix]


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "assets").mkdir()
    for name in ("rope_memory.mp4", "agc-panel.png", "bed.wav"):
        (tmp_path / "assets" / name).write_bytes(b"")
    return tmp_path


def _brief(ws, recipe="documentary", target=90):
    return parse_brief({
        "title": "Apollo Guidance Computer", "brief": "How the AGC flew to the Moon.",
        "targetSeconds": target, "recipe": recipe,
        "assets": [{"src": "./assets/rope_memory.mp4", "description": "rope memory weaving"},
                   {"src": "./assets/agc-panel.png", "description": "DSKY panel"}],
    }, ws, probe=fake_probe)


VALID = {
    "name": "custom", "description": "d", "defaultTargetSeconds": 60, "beatSeconds": [3, 6],
    "tone": "t", "briefTemplate": "About {topic}.",
    "timing": {"crossfade": 10, "lead": 10, "tail": 5, "endHold": 30, "zoom": 1.05, "easing": "linear"},
}


# ── recipe files ──────────────────────────────────────────────────────────────

def test_shipped_pipelines_load():
    assert {"explainer", "documentary", "promo"} <= set(available())
    for name in ("explainer", "documentary", "promo"):
        p = load_pipeline(name)
        assert p.name == name and p.recipe.crossfade <= p.recipe.lead


def test_explainer_recipe_is_the_slice_one_timing():
    assert load_pipeline("explainer").recipe == EXPLAINER


@pytest.mark.parametrize("name,target,expected", [
    ("explainer", 45, (6, 11)), ("documentary", 90, (8, 12)), ("promo", 30, (8, 15)), ("documentary", 10, (1, 1)),
])
def test_beat_range(name, target, expected):
    assert load_pipeline(name).beat_range(target) == expected


def test_recipes_differ_in_pacing():
    doc, promo = load_pipeline("documentary").recipe, load_pipeline("promo").recipe
    assert doc.crossfade > EXPLAINER.crossfade > promo.crossfade
    assert doc.end_hold > EXPLAINER.end_hold


@pytest.mark.parametrize("change,expected", [
    ({"extra": 1}, "unsupported field 'extra'"),
    ({"name": "other"}, "must match the file name"),
    ({"briefTemplate": "no placeholder"}, "{topic}"),
    ({"beatSeconds": [6, 3]}, "beatSeconds"),
    ({"defaultTargetSeconds": 5}, "defaultTargetSeconds"),
    ({"timing": {**VALID["timing"], "crossfade": 20}}, "must not exceed lead"),
    ({"timing": {**VALID["timing"], "easing": "bounce"}}, "easing"),
    ({"timing": {**VALID["timing"], "speed": 2}}, "unsupported field 'speed'"),
    ({"timing": {k: v for k, v in VALID["timing"].items() if k != "tail"}}, "missing 'tail'"),
])
def test_pipeline_rejects(change, expected):
    with pytest.raises(PipelineError) as exc:
        parse_pipeline(VALID | change, "custom")
    assert any(expected in e for e in exc.value.errors), exc.value.errors


def test_unknown_pipeline_lists_available():
    with pytest.raises(PipelineError, match="available"):
        load_pipeline("infomercial")
    with pytest.raises(PipelineError):
        load_pipeline("../pipelines/explainer")


def test_pipelines_dir_is_overridable(tmp_path):
    (tmp_path / "custom.json").write_text(json.dumps(VALID))
    assert available(tmp_path) == ["custom"]
    assert load_pipeline("custom", tmp_path).recipe.easing == "linear"


# ── prompts ───────────────────────────────────────────────────────────────────

def test_explainer_system_prompt_unchanged():
    assert script_mod.system_prompt(load_pipeline("explainer")) == script_mod.SYSTEM
    assert "short explainer videos" in script_mod.SYSTEM


def test_documentary_prompt_carries_tone_and_beats(ws):
    b = _brief(ws)
    prompt = script_mod.build_prompt(b)
    doc = load_pipeline("documentary")
    assert "Format: documentary" in prompt and doc.tone in prompt
    assert "write 8 to 12 beats" in prompt and "about 90 seconds" in prompt
    assert "short documentary videos" in script_mod.system_prompt(doc)


GOOD = {"title": "AGC", "beats": [
    {"narration": "In 1966, women wove the program into rope.", "asset": "./assets/rope_memory.mp4", "motion": "none"},
    {"narration": "The astronauts talked to it through a keypad.", "asset": "./assets/agc-panel.png",
     "motion": "zoom_in"}]}


def test_claude_gets_the_pipeline_prompt(ws, monkeypatch):
    import anthropic
    calls = []

    class Fake:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            calls.append(kw)
            return types.SimpleNamespace(stop_reason="end_turn", model="claude-opus-5",
                                         content=[types.SimpleNamespace(type="text", text=json.dumps(GOOD))])
    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    script_mod.write_with_claude(_brief(ws, recipe="promo", target=30))
    assert "short promo videos" in calls[0]["system"]
    assert load_pipeline("promo").tone in calls[0]["messages"][0]["content"]


def test_ollama_gets_the_pipeline_prompt(ws, monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout):
        sent.update(json.loads(req.data))
        return io.BytesIO(json.dumps({"message": {"content": json.dumps(GOOD)}}).encode())
    monkeypatch.setattr(script_mod.urllib.request, "urlopen", fake_urlopen)
    script_mod.write_with_ollama(_brief(ws))
    assert "short documentary videos" in sent["messages"][0]["content"]
    assert "write 8 to 12 beats" in sent["messages"][1]["content"]


# ── planner uses the recipe's timing ──────────────────────────────────────────

def _stub_narration(ws, n, seconds=3.0):
    (ws / "assets" / "narration").mkdir(exist_ok=True)
    out = []
    for i in range(n):
        p = ws / "assets" / "narration" / f"b{i}.wav"
        p.write_bytes(b"")
        out.append(Narration(f"./assets/narration/b{i}.wav", p, seconds))
    return out


def test_compose_with_documentary_recipe(ws):
    b = _brief(ws)
    script = Script("AGC", (Beat("a", "./assets/rope_memory.mp4", "none"),
                            Beat("b", "./assets/agc-panel.png", "zoom_in")), "test")
    doc = load_pipeline("documentary").recipe
    raw = compose(b, script, _stub_narration(ws, 2), doc)
    plan = parse_plan(raw, ws, probe=fake_probe)
    assert plan.video.clips[0].crossfade_frames == doc.crossfade
    assert plan.video.clips[1].motion.scale_to == doc.zoom
    assert plan.meta.duration_frames == (doc.lead + 90 + doc.tail) + (doc.lead + 90 + doc.end_hold)


def test_run_plan_applies_brief_pipeline(ws, monkeypatch):
    (ws / "brief.json").write_text("{}")
    (ws / "script.json").write_text(json.dumps(GOOD | {"writer": "hand"}))
    monkeypatch.setattr(planner, "load_brief", lambda _p: _brief(ws, recipe="promo", target=20))
    monkeypatch.setattr(planner, "narrate", lambda s, v, w: _stub_narration(ws, len(s.beats)))
    monkeypatch.setattr(planner, "parse_plan", partial(parse_plan, probe=fake_probe))
    logs = []
    result = planner.run_plan(ws / "brief.json", log=logs.append)
    promo = load_pipeline("promo").recipe
    assert result.pipeline.name == "promo" and result.script_reused
    assert result.plan.video.clips[0].crossfade_frames == promo.crossfade
    assert result.plan.video.clips[1].motion.easing == promo.easing
    assert any("asks for" in line for line in logs)  # 2 beats is below promo's range at 20s


# ── init / --topic ────────────────────────────────────────────────────────────

def test_init_scaffolds_and_never_overwrites(tmp_path, capsys):
    ws = tmp_path / "demo"
    assert cli.main(["init", str(ws), "--pipeline", "documentary"]) == 0
    brief = json.loads((ws / "brief.json").read_text())
    assert (ws / "assets").is_dir() and brief["recipe"] == "documentary" and brief["targetSeconds"] == 90
    assert "<your topic>" in brief["brief"]
    assert cli.main(["init", str(ws)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert cli.main(["init", str(tmp_path / "x"), "--pipeline", "nope"]) == 1


def test_synthesize_brief_from_assets(ws):
    (ws / "assets" / "notes.txt").write_text("x")
    path, notes = synthesize_brief(ws, load_pipeline("documentary"), "The Apollo Guidance Computer", None,
                                   probe=fake_probe)
    brief = json.loads(path.read_text())
    assert brief["recipe"] == "documentary" and brief["targetSeconds"] == 90
    assert "The Apollo Guidance Computer" in brief["brief"]
    assert [a["description"] for a in brief["assets"]] == ["agc panel", "rope memory"]
    assert brief["music"] == {"src": "./assets/bed.wav", "volume": 0.12}
    assert any("notes.txt" in n for n in notes)
    parse_brief(brief, ws, probe=fake_probe)  # what it wrote is a valid brief


def test_synthesize_brief_needs_visuals(tmp_path):
    (tmp_path / "assets").mkdir()
    with pytest.raises(WorkspaceError, match="Stage 2"):
        synthesize_brief(tmp_path, load_pipeline("promo"), "x", 30, probe=fake_probe)
    with pytest.raises(WorkspaceError, match="10-600"):
        synthesize_brief(tmp_path, load_pipeline("promo"), "x", 5, probe=fake_probe)


def test_cli_plan_topic_writes_brief_then_plans(ws, monkeypatch, capsys):
    monkeypatch.setattr(probe_mod, "probe", fake_probe)
    seen = {}

    def fake_run_plan(path, writer, rewrite, **_gate):  # approve_script / script_gate flags (script gate)
        seen["brief"] = json.loads(Path(path).read_text())
        meta = types.SimpleNamespace(duration_frames=2400, fps=30, title="AGC")
        return types.SimpleNamespace(plan=types.SimpleNamespace(meta=meta), plan_path=ws / "scene_plan.json",
                                     brief=types.SimpleNamespace(target_seconds=80),
                                     script=types.SimpleNamespace(beats=[1] * 9),
                                     pipeline=types.SimpleNamespace(name="documentary"))
    monkeypatch.setattr(planner, "run_plan", fake_run_plan)
    sheets = []  # plan now ends by drawing the contact sheet; the fake plan has no clips to draw
    monkeypatch.setattr(cli, "make_contact_sheet", lambda plan: sheets.append(plan) or ws / "contact_sheet.png")
    code = cli.main(["plan", "--pipeline", "documentary", "--topic", "The Apollo Guidance Computer",
                     "--target-duration", "80", "--workspace", str(ws)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert seen["brief"]["recipe"] == "documentary" and seen["brief"]["targetSeconds"] == 80
    assert "documentary pipeline, 9 beats" in out
    assert len(sheets) == 1 and "contact sheet:" in out and "montage approve --workspace" in out
    # a second run must not clobber the brief the user may have edited
    assert cli.main(["plan", "--topic", "again", "--workspace", str(ws)]) == 1


@pytest.mark.parametrize("argv,expected", [
    (["plan"], "needs a brief.json"),
    (["plan", "b.json", "--topic", "x"], "not both"),
    (["plan", "b.json", "--pipeline", "promo"], "go with --topic"),
    (["plan", "--topic", "x", "--pipeline", "nope"], "unknown pipeline"),
])
def test_cli_plan_argument_errors(argv, expected, capsys):
    assert cli.main(argv) == 1
    assert expected in capsys.readouterr().err
