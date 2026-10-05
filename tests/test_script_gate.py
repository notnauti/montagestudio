"""The script gate: beat table, brief coverage, hard checks, approval record, and the CLI flow.

No model, no Piper, no ffmpeg: the writer is faked, probes are faked, narration is stubbed.
"""

import json
from functools import partial

import pytest

from montage import cli, planner
from montage import script_gate as gate
from montage import script as script_mod
from montage.brief import parse_brief
from montage.coverage import brief_points, content_words, cover, key_points, number_spellings
from montage.narrate import Narration
from montage.pipeline import load_pipeline
from montage.plan import parse_plan
from montage.probe import MediaInfo
from montage.script import Beat, Script


def fake_probe(path):
    return {".mp4": MediaInfo(True, False, 30.0), ".png": MediaInfo(True, False, None),
            ".wav": MediaInfo(False, True, 120.0)}[path.suffix]


JWST = ("A short, imaginative documentary for an educational audience about the James Webb Space Telescope: "
        "the Christmas 2021 launch, why a telescope too big for any rocket had to fold like origami, the cold it "
        "needs to see infrared light from the first galaxies, the month-long unfolding with 344 single points of "
        "failure, and the first images it sent home.")

AGC = ("A short explainer about the Apollo Guidance Computer: rope memory woven by hand, "
       "the keypad astronauts typed verbs and nouns into, and the 1202 alarm during the landing.")


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "assets").mkdir()
    for name in ("rope.mp4", "dsky.png"):
        (tmp_path / "assets" / name).write_bytes(b"")
    (tmp_path / "brief.json").write_text("{}")
    return tmp_path


def _brief(ws, target=30, text=AGC):
    return parse_brief({"title": "Apollo Guidance Computer", "brief": text, "targetSeconds": target,
                        "recipe": "explainer",
                        "assets": [{"src": "./assets/rope.mp4", "description": "rope memory weaving"},
                                   {"src": "./assets/dsky.png", "description": "DSKY keypad"}]},
                       ws, probe=fake_probe)


def _script(lines, writer="claude-opus-5"):
    return Script("AGC", tuple(Beat(t, "./assets/dsky.png", "zoom_in") for t in lines), writer)


GOOD_LINES = [  # 5 beats, ~85 words: about 27 s of speech + 4.4 s of explainer pauses for a 30 s target
    "In the nineteen sixties, women wove the Apollo program into rope memory by hand, wire by wire.",
    "A wire through a core was a one, a wire around it was a zero, and nothing could erase it.",
    "Astronauts talked to the computer through a keypad, typing verbs and nouns as two digit codes.",
    "Minutes before the landing, the 1202 alarm flashed: the computer had more work than time.",
    "It dropped the low priority jobs, kept flying, and Eagle touched down on the Moon.",
]


# ── coverage: points come from the brief by rule ─────────────────────────────

def test_brief_points_split_the_list_after_the_colon():
    assert brief_points(JWST) == [
        "the Christmas 2021 launch",
        "why a telescope too big for any rocket had to fold like origami",
        "the cold it needs to see infrared light from the first galaxies",
        "the month-long unfolding with 344 single points of failure",
        "the first images it sent home",
    ]


def test_brief_points_without_colon_merge_short_bits_and_drop_audience():
    assert brief_points("Explain why GPS satellite clocks need relativistic corrections, for curious "
                        "non-physicists. Then show the fix.") == \
        ["Explain why GPS satellite clocks need relativistic corrections", "show the fix"]  # "Then" dropped
    # "what it is" has no content words: it is merged, not counted on its own
    assert brief_points("Explain X: what it is, why it matters, and the one idea that makes it click.") == \
        ["what it is, why it matters, the one idea that makes it click"]


def test_title_words_do_not_count_toward_coverage():
    pts = key_points(JWST, "How the James Webb Space Telescope Unfolded")
    assert "telescope" not in pts[1].words and "unfold" not in pts[3].words
    assert [p.need for p in pts] == [2, 2, 2, 2, 2]
    assert content_words("Launches folded galaxies") == ["launch", "fold", "galaxy"]


def test_numbers_match_when_spelled_out():
    assert number_spellings(344) == [["three", "hundred", "forty", "four"]]
    assert ["twenty", "twenty", "one"] in number_spellings(2021)
    pts = key_points("Cover: the 344 single points of failure, the Christmas 2021 launch.")
    cov = cover(pts, ["There were three hundred and forty four single points of failure.",
                      "On Christmas, twenty twenty-one, the launch came."])
    assert cov.beats == ((0,), (1,)) and cov.uncovered == []
    assert cov.matched[1] == ("christmas", "2021", "launch")  # the brief's spelling, not the key "christma"


def test_uncovered_and_out_of_order_points():
    pts = key_points(AGC, "Apollo Guidance Computer")
    cov = cover(pts, ["The 1202 alarm flashed during the landing.", "Women wove rope memory by hand."])
    assert cov.beats[0] == (1,) and cov.beats[2] == (0,)
    assert cov.uncovered == [1]           # the keypad is never mentioned
    assert cov.out_of_order() == [2]      # the alarm comes before rope memory in the script


def test_two_neighbouring_beats_may_share_a_point():
    pts = key_points("About it: rope memory woven by hand.")
    cov = cover(pts, ["It was rope.", "Memory woven by hand."])
    assert cov.beats == ((1,),)  # one beat says three of four words: enough on its own
    pts = key_points("About it: rope memory woven tightly by hand at Raytheon in Waltham.")  # 7 words, need 3
    cov = cover(pts, ["Rope memory.", "Woven in Waltham."])
    assert cov.beats == ((0, 1),)


# ── report and hard checks ────────────────────────────────────────────────────

def test_report_estimates_runtime_with_recipe_pauses(ws):
    b, p = _brief(ws), load_pipeline("explainer")
    rep = gate.build_report(b, p, _script(GOOD_LINES), model_draft=True)
    words = sum(gate.word_count(t) for t in GOOD_LINES)
    r = p.recipe
    assert rep.speech == pytest.approx(words / script_mod.WORDS_PER_SECOND)
    assert rep.runtime == pytest.approx(rep.speech + (5 * r.lead + 4 * r.tail + r.end_hold) / r.fps)
    assert not rep.problems and not rep.rejected
    assert rep.coverage.uncovered == []
    lines = gate.format_report(rep)
    assert lines[0].split()[:4] == ["#", "est", "s", "words"]
    assert any(line.startswith("  total: 5 beats (recipe 4-7)") for line in lines)
    assert any("3. the 1202 alarm during the landing -> beat 4" in line for line in lines)


def test_model_draft_over_limits_is_rejected_hand_edit_only_warned(ws):
    b, p = _brief(ws), load_pipeline("explainer")
    long = _script([line + " " + line for line in GOOD_LINES] + GOOD_LINES * 2)  # 15 beats, ~3x the words
    rep = gate.build_report(b, p, long, model_draft=True)
    assert rep.rejected and len(rep.problems) == 2
    assert "over the 30s target" in rep.problems[0] and "at most 7" in rep.problems[1]
    assert any(line.startswith("  rejected: ") for line in gate.format_report(rep))
    hand = gate.build_report(b, p, long, model_draft=False)
    assert hand.problems and not hand.rejected
    assert any(line.startswith("  warning: ") for line in gate.format_report(hand))


def test_report_warns_short_script_uncovered_points_and_long_video_lines(ws):
    b, p = _brief(ws), load_pipeline("explainer")
    s = Script("AGC", (Beat("Rope memory was woven by hand. " * 20, "./assets/rope.mp4", "none"),), "hand")
    rep = gate.build_report(b, p, s, model_draft=False)
    text = "\n".join(rep.warnings)
    assert "asks for 4-7 beats" in text and "under the 30s target" not in text  # it is long, not short
    assert "brief point 2 is not covered" in text and "brief point 3 is not covered" in text
    assert "a 30.0s video" in text
    short = gate.build_report(b, p, _script(GOOD_LINES[:3]), model_draft=False)
    assert any("under the 30s target" in w for w in short.warnings)


def test_sha_ignores_formatting_and_writer_but_not_words():
    a = _script(GOOD_LINES)
    assert gate.script_sha(a) == gate.script_sha(_script(GOOD_LINES, writer="hand"))
    assert gate.script_sha(a) != gate.script_sha(_script(GOOD_LINES[:-1] + ["It landed."]))


def test_prompt_lists_points_in_order_and_word_limits(ws):
    prompt = script_mod.build_prompt(_brief(ws))
    assert "Points to cover, in this order" in prompt
    assert "1. rope memory woven by hand\n2. the keypad" in prompt and "3. the 1202 alarm" in prompt
    assert "never more than" in prompt and "more than 7 is rejected" in prompt
    assert "no filler" in script_mod.SYSTEM and "none may be skipped" in script_mod.SYSTEM


# ── run_plan: the stop, approval, change, rejection ──────────────────────────

@pytest.fixture
def planning(ws, monkeypatch):
    """run_plan wired to fakes; returns (ws, state) where state['draft'] is what the writer returns."""
    state = {"draft": _script(GOOD_LINES), "narrated": 0, "writes": 0}

    def fake_write(brief, writer, log=print):
        state["writes"] += 1
        return state["draft"]

    def fake_narrate(script, voice, workspace):
        state["narrated"] += 1
        (ws / "assets" / "narration").mkdir(exist_ok=True)
        out = []
        for i in range(len(script.beats)):
            path = ws / "assets" / "narration" / f"b{i}.wav"
            path.write_bytes(b"")
            out.append(Narration(f"./assets/narration/b{i}.wav", path, 3.0))
        return out

    monkeypatch.setattr(planner, "load_brief", lambda _p: _brief(ws))
    monkeypatch.setattr(planner, "write_script", fake_write)
    monkeypatch.setattr(planner, "narrate", fake_narrate)
    monkeypatch.setattr(planner, "parse_plan", partial(parse_plan, probe=fake_probe))
    return ws, state


def test_model_draft_stops_before_narration_until_approved(planning):
    ws, state = planning
    logs = []
    held = planner.run_plan(ws / "brief.json", log=logs.append)
    assert isinstance(held, planner.ScriptHeld) and not held.changed
    assert state["narrated"] == 0 and (ws / "script.json").is_file()
    record = json.loads((ws / gate.RECORD).read_text())
    assert record["draftSha256"] == gate.script_sha(state["draft"]) and "approvedSha256" not in record
    assert any("brief points (3/3 covered)" in line for line in logs)

    # a plain re-run reuses the draft and stops again; the model is not asked twice
    assert isinstance(planner.run_plan(ws / "brief.json", log=logs.append), planner.ScriptHeld)
    assert state["writes"] == 1

    result = planner.run_plan(ws / "brief.json", approve_script=True, log=logs.append)
    assert isinstance(result, planner.PlanResult) and state["narrated"] == 1
    assert json.loads((ws / gate.RECORD).read_text())["approvedSha256"] == record["draftSha256"]

    # approved: no table, straight through; reformatting script.json does not void it
    raw = json.loads((ws / "script.json").read_text())
    (ws / "script.json").write_text(json.dumps(raw))
    logs.clear()
    assert isinstance(planner.run_plan(ws / "brief.json", log=logs.append), planner.PlanResult)
    assert any("script gate: approved" in line for line in logs) and not any("total:" in l for l in logs)

    # a changed word needs re-approval
    raw["beats"][4]["narration"] = "It shed the low priority work, and Eagle landed."
    (ws / "script.json").write_text(json.dumps(raw))
    held = planner.run_plan(ws / "brief.json", log=logs.append)
    assert isinstance(held, planner.ScriptHeld) and held.changed and state["narrated"] == 2


def test_rewrite_voids_the_old_approval(planning):
    ws, state = planning
    planner.run_plan(ws / "brief.json", log=lambda *_: None)  # the draft, read, then approved
    planner.run_plan(ws / "brief.json", approve_script=True, log=lambda *_: None)
    state["draft"] = _script(GOOD_LINES[::-1])
    held = planner.run_plan(ws / "brief.json", rewrite=True, log=lambda *_: None)
    assert isinstance(held, planner.ScriptHeld) and not held.changed and state["writes"] == 2
    with pytest.raises(planner.ScriptRejected, match="--rewrite first"):
        planner.run_plan(ws / "brief.json", rewrite=True, approve_script=True, log=lambda *_: None)


def test_approve_script_never_approves_a_draft_nobody_has_seen(planning):
    # regression: with no script.json, --approve-script used to let the model write a draft and
    # approve it in the same run, so narration went ahead on an unread script
    ws, state = planning
    with pytest.raises(planner.ScriptRejected, match="there is none yet"):
        planner.run_plan(ws / "brief.json", approve_script=True, log=lambda *_: None)
    assert state["writes"] == 0 and state["narrated"] == 0
    assert not (ws / "script.json").exists() and not (ws / gate.RECORD).exists()
    assert isinstance(planner.run_plan(ws / "brief.json", log=lambda *_: None), planner.ScriptHeld)
    assert isinstance(planner.run_plan(ws / "brief.json", approve_script=True, log=lambda *_: None),
                      planner.PlanResult)


def test_bloated_model_draft_is_rejected_even_with_approval(planning):
    ws, state = planning
    state["draft"] = _script(GOOD_LINES * 3)  # 15 beats, ~255 words for a 30 s explainer
    with pytest.raises(planner.ScriptRejected) as exc:
        planner.run_plan(ws / "brief.json", log=lambda *_: None)
    assert "over the 30s target" in str(exc.value) and "at most 7" in str(exc.value)
    assert state["narrated"] == 0 and (ws / "script.json").is_file()  # kept, so it can be edited
    with pytest.raises(planner.ScriptRejected):
        planner.run_plan(ws / "brief.json", approve_script=True, log=lambda *_: None)
    with pytest.raises(planner.ScriptRejected):
        planner.run_plan(ws / "brief.json", script_gate=False, log=lambda *_: None)

    # once a person edits it, the same limits are warnings and approval goes through
    raw = json.loads((ws / "script.json").read_text())
    raw["beats"] = raw["beats"][:9]
    (ws / "script.json").write_text(json.dumps(raw))
    logs = []
    held = planner.run_plan(ws / "brief.json", log=logs.append)
    assert isinstance(held, planner.ScriptHeld)
    assert any(line.startswith("  warning: ") for line in logs)
    assert isinstance(planner.run_plan(ws / "brief.json", approve_script=True, log=logs.append),
                      planner.PlanResult)


def test_no_script_gate_goes_on_unapproved(planning):
    ws, state = planning
    logs = []
    result = planner.run_plan(ws / "brief.json", script_gate=False, log=logs.append)
    assert isinstance(result, planner.PlanResult) and state["narrated"] == 1
    assert any("skipped (--no-script-gate)" in line for line in logs)
    assert "approvedSha256" not in json.loads((ws / gate.RECORD).read_text())


def test_hand_written_script_without_record_is_not_held(planning):
    ws, state = planning
    (ws / "script.json").write_text(json.dumps(_script(GOOD_LINES, writer="hand").to_json()))
    logs = []
    assert isinstance(planner.run_plan(ws / "brief.json", log=logs.append), planner.PlanResult)
    assert state["writes"] == 0 and any(line.startswith("  total:") for line in logs)
    assert not (ws / gate.RECORD).exists()


def test_invalid_reused_script_is_invalid_input(planning):
    ws, _ = planning
    (ws / "script.json").write_text("{not json")
    with pytest.raises(planner.ScriptRejected, match="script.json"):
        planner.run_plan(ws / "brief.json", log=lambda *_: None)


# ── CLI ───────────────────────────────────────────────────────────────────────

def test_cli_plan_stops_then_approve_script_then_plans(planning, monkeypatch, capsys):
    ws, state = planning
    monkeypatch.setattr(cli, "make_contact_sheet", lambda plan: ws / "contact_sheet.png")
    brief = str(ws / "brief.json")
    assert cli.main(["plan", brief]) == 0
    out = capsys.readouterr().out
    assert "stopped before narration" in out and "--approve-script" in out and state["narrated"] == 0
    assert cli.main(["approve-script", str(ws), "--note", "read it"]) == 0
    assert "script approved" in capsys.readouterr().out
    assert json.loads((ws / gate.RECORD).read_text())["note"] == "read it"
    assert cli.main(["plan", brief]) == 0
    assert "scene plan:" in capsys.readouterr().out and state["narrated"] == 1


def test_cli_plan_approve_script_flag_and_rejections(planning, monkeypatch, capsys):
    ws, state = planning
    monkeypatch.setattr(cli, "make_contact_sheet", lambda plan: ws / "contact_sheet.png")
    brief = str(ws / "brief.json")
    assert cli.main(["plan", brief, "--rewrite", "--approve-script"]) == 1
    assert "--rewrite first" in capsys.readouterr().err
    state["draft"] = _script(GOOD_LINES * 3)
    assert cli.main(["plan", brief]) == 1
    err = capsys.readouterr().err
    assert "fails: estimated runtime" in err and "edit script.json" in err
    assert cli.main(["approve-script", str(ws)]) == 1
    state["draft"] = _script(GOOD_LINES)
    assert cli.main(["plan", brief, "--rewrite", "--no-script-gate"]) == 0
    assert state["narrated"] == 1


def test_cli_approve_script_needs_a_script(ws, monkeypatch, capsys):
    monkeypatch.setattr(planner, "load_brief", lambda _p: _brief(ws))
    assert cli.main(["approve-script", str(ws / "brief.json")]) == 1
    assert "no script.json" in capsys.readouterr().err
