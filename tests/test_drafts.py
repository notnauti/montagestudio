"""publish-pack --draft: Claude drafts title, description intro, chapter titles and tags; nothing else."""

import json
import types

import pytest

from montage import cli, drafts, publish, vision
from montage.drafts import Draft, DraftError, parse_draft
from montage.script import CLAUDE_MODEL
from test_publish import LINES, ws  # noqa: F401 -- ws is the publishable-workspace fixture

GOOD = {"title": "How the Webb telescope unfolded in space",
        "description": "Webb launched folded. Here is how it opened, step by step.",
        "chapterTitles": ["Launch day", "A gold mirror", "Learning to fold", "It worked"],
        "tags": ["james webb space telescope", "jwst deployment"]}


def _pack_text(ws):
    return (ws / "publish" / "youtube.md").read_text(encoding="utf-8")


# ── parsing and limits ────────────────────────────────────────────────────────

def test_parse_a_good_draft():
    d = parse_draft(json.dumps(GOOD), 4, "m")
    assert d == Draft(GOOD["title"], GOOD["description"], tuple(GOOD["chapterTitles"]), tuple(GOOD["tags"]), "m")


@pytest.mark.parametrize("change, reason", [
    ({"title": "x" * 71}, "title is 71 characters"),
    ({"title": "  "}, "title is 0 characters"),
    ({"description": ""}, "description is 0 characters"),
    ({"chapterTitles": ["a", "b", "c"]}, "3 chapter titles for 4 chapters"),
    ({"chapterTitles": ["a", "b", "c", "x" * 61]}, "over 60 characters"),
    ({"tags": [f"tag {i}" for i in range(16)]}, "16 tags"),
    ({"description": "Webb <3"}, "'<' or '>'"),
])
def test_a_draft_over_a_limit_is_rejected_whole(change, reason):
    with pytest.raises(DraftError, match=reason):
        parse_draft(json.dumps(dict(GOOD, **change)), 4, "m")


def test_non_json_is_rejected():
    with pytest.raises(DraftError, match="not JSON"):
        parse_draft("Sure! Here is", 4, "m")


# ── the request ───────────────────────────────────────────────────────────────

class FakeClient:
    def __init__(self, response=None, error=None):
        self.calls, self.response, self.error = [], response, error
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.response


def _response(text=json.dumps(GOOD), stop_reason="end_turn"):
    return types.SimpleNamespace(stop_reason=stop_reason, model=CLAUDE_MODEL,
                                 content=[types.SimpleNamespace(type="text", text=text)])


def test_request_shape_and_prompt():
    client = FakeClient(_response(json.dumps(dict(GOOD, chapterTitles=["Launch and mirror", "The fold"]))))
    d = drafts.ClaudeDrafter(client=client)("T", "A short documentary about X.", LINES,
                                           [("0:00", LINES[:2]), ("0:24", LINES[2:])])
    assert d.model == CLAUDE_MODEL == "claude-opus-5-5"
    k = client.calls[0]
    assert k["model"] == CLAUDE_MODEL and k["fallbacks"] == "default"
    assert k["output_config"]["format"]["schema"] == drafts.schema()
    prompt = k["messages"][0]["content"]
    assert "write exactly 2 chapterTitles" in prompt and f"1. at 0:00: {LINES[0]} {LINES[1]}" in prompt


@pytest.mark.parametrize("response, reason", [
    (_response(stop_reason="refusal"), "declined"),
    (_response(stop_reason="max_tokens"), "max_tokens"),
])
def test_a_refusal_or_cutoff_is_a_draft_error(response, reason):
    with pytest.raises(DraftError, match=reason):
        drafts.ClaudeDrafter(client=FakeClient(response))("T", "B", LINES, [])


# ── the pack ──────────────────────────────────────────────────────────────────

def test_chapter_narration_carries_every_merged_beat():
    raw = {"tracks": [{"id": "narration", "type": "audio", "clips": [
        {"startFrame": f} for f in (12, 312, 400, 900)]}]}
    script = types.SimpleNamespace(beats=[types.SimpleNamespace(narration=n) for n in "abcd"])
    chs = [publish.Chapter(0, "a"), publish.Chapter(10, "b"), publish.Chapter(30, "d")]
    assert publish.chapter_narration(raw, script, chs, 30) == [("0:00", ["a"]), ("0:10", ["b", "c"]), ("0:30", ["d"])]


def _drafter(result):
    def call(title, text, narration, chapters):
        call.args = (title, text, narration, chapters)
        if isinstance(result, Exception):
            raise result
        return result
    return call


def test_a_draft_replaces_only_the_viewer_text(ws):
    d = parse_draft(json.dumps(GOOD), 4, CLAUDE_MODEL)
    fake = _drafter(d)
    pack = publish.build(ws, drafter=fake)
    assert fake.args[2] == LINES and len(fake.args[3]) == 4
    t = pack.text
    assert f"## Title\n{GOOD['title']}\n<!-- the brief's title: How Webb Unfolded -->" in t
    assert t.split("## Description\n")[1].startswith(GOOD["description"] + "\n")
    assert "A short documentary for an educational audience" not in t          # the brief is not viewer text
    assert "0:00 Launch day" in t and "0:36 It worked" in t
    assert "## Tags\njames webb space telescope, jwst deployment" in t
    assert "Author: Jane Doe" in t and "NASA does not endorse this video." in t   # records, untouched
    assert "drafted by claude-opus-5-5 from the approved script" in t
    assert pack.draft_model == CLAUDE_MODEL and pack.draft_error is None


def test_a_failed_draft_keeps_the_deterministic_text(ws):
    pack = publish.build(ws, drafter=_drafter(DraftError("Claude API error 400: credit balance too low")))
    assert pack.draft_error.startswith("Claude API error 400")
    assert "## Title\nHow Webb Unfolded\n" in pack.text and "drafted by" not in pack.text


# ── the CLI ───────────────────────────────────────────────────────────────────

def test_draft_without_credentials_exits_2_before_writing(ws, monkeypatch, capsys):
    monkeypatch.setattr(vision, "unavailable_reason", lambda: "no Claude credentials")
    assert cli.main(["publish-pack", str(ws), "--draft"]) == 2
    assert "--draft needs Claude: no Claude credentials" in capsys.readouterr().err
    assert not (ws / "publish" / "youtube.md").exists()


def test_draft_through_the_cli(ws, monkeypatch):
    monkeypatch.setattr(vision, "unavailable_reason", lambda: None)
    monkeypatch.setattr(drafts, "ClaudeDrafter", lambda: _drafter(parse_draft(json.dumps(GOOD), 4, CLAUDE_MODEL)))
    assert cli.main(["publish-pack", str(ws), "--draft"]) == 0
    assert GOOD["title"] in _pack_text(ws)


def test_a_failed_draft_writes_the_pack_and_exits_2(ws, monkeypatch, capsys):
    monkeypatch.setattr(vision, "unavailable_reason", lambda: None)
    monkeypatch.setattr(drafts, "ClaudeDrafter", lambda: _drafter(DraftError("Claude API error 529: overloaded")))
    assert cli.main(["publish-pack", str(ws), "--draft"]) == 2
    assert "## Title\nHow Webb Unfolded" in _pack_text(ws)
    assert "--draft failed (Claude API error 529" in capsys.readouterr().err


def test_an_existing_pack_is_refused_before_any_model_call(ws, monkeypatch):
    assert cli.main(["publish-pack", str(ws)]) == 0
    monkeypatch.setattr(vision, "unavailable_reason", lambda: None)
    monkeypatch.setattr(drafts, "ClaudeDrafter", lambda: pytest.fail("no drafter for a refused overwrite"))
    assert cli.main(["publish-pack", str(ws), "--draft"]) == 1
