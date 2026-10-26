"""Production measurement: the per-command log, the per-video summary, `montage stats`, the ledger."""

import json
from datetime import datetime, timezone

import pytest

from montage import cli, production
from test_publish import _write, ws  # noqa: F401 -- ws is the publishable-workspace fixture


def _log(ws):
    return production.read_log(ws)


def _summary(ws):
    return json.loads(production.summary_path(ws).read_text(encoding="utf-8"))


@pytest.fixture
def ledger(tmp_path_factory, monkeypatch):
    path = tmp_path_factory.mktemp("project") / "logs" / "production-ledger.jsonl"
    monkeypatch.setenv("MONTAGE_LEDGER", str(path))
    return path


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ── the command log ───────────────────────────────────────────────────────────

def test_every_command_on_a_workspace_is_logged_with_its_exit_code(ws):
    assert cli.main(["publish-pack", str(ws), "--hands-on-min", "95"]) == 0
    assert cli.main(["publish-pack", str(ws)]) == 1            # exists, no --force
    log = _log(ws)
    assert [(e["cmd"], e["exit"]) for e in log] == [("publish-pack", 0), ("publish-pack", 1)]
    assert log[0]["argv"] == ["publish-pack", str(ws), "--hands-on-min", "95"]
    assert log[0]["seconds"] >= 0
    datetime.fromisoformat(log[0]["start"])


def test_a_command_without_a_workspace_logs_nothing(tmp_path):
    missing = tmp_path / "nowhere" / "scene_plan.json"
    assert cli.main(["validate", str(missing)]) == 1
    assert not (tmp_path / "nowhere").exists()


def test_a_directory_that_is_no_workspace_gets_no_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                # `validate x.json` resolves to the cwd
    assert cli.main(["validate", "does-not-exist.json"]) == 1
    assert cli.main(["plan", "b.json", "--pipeline", "promo"]) == 1
    assert not (tmp_path / production.LOG).exists()


def test_logging_never_fails_the_command(tmp_path):
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x")
    assert production.log_command(not_a_dir, "render", [], datetime.now(timezone.utc), 1.0, 0) is False
    (tmp_path / "brief.json").write_text("{}")
    (tmp_path / production.LOG).mkdir()                        # the log path is a directory: write fails
    assert production.log_command(tmp_path, "render", [], datetime.now(timezone.utc), 1.0, 0) is False


def test_a_crashing_command_is_logged_as_an_exception(ws, monkeypatch):
    def boom(args):
        raise RuntimeError("boom")
    monkeypatch.setattr(cli, "publish_pack_command", boom)
    with pytest.raises(RuntimeError):
        cli.main(["publish-pack", str(ws)])
    assert _log(ws)[-1]["exit"] == "exception"


def test_unreadable_log_lines_are_skipped(tmp_path):
    (tmp_path / production.LOG).write_text('{"cmd": "plan", "seconds": 2}\nnot json\n[1]\n', encoding="utf-8")
    assert [e["cmd"] for e in _log(tmp_path)] == ["plan"]


# ── hand edits and timings ────────────────────────────────────────────────────

def test_hand_edits_come_from_the_existing_records(tmp_path):
    _write(tmp_path / "script_approval.json", {"draftSha256": "a", "approvedSha256": "b", "writer": "claude-x"})
    _write(tmp_path / "manifest.json", {"version": 1, "assets": {
        "./assets/a.jpg": {"ranking": "lexical", "pickedRank": 2},
        "./assets/b.jpg": {"ranking": "lexical"},
        "./assets/c.jpg": {"source": "hand"}}})
    hist = tmp_path / "cache" / "plan-history"
    hist.mkdir(parents=True)
    for n in range(3):
        (hist / f"{n}.json").write_text("{}")
    log = [{"cmd": "render", "argv": ["render", "p", "--force"], "exit": 0},
           {"cmd": "render", "argv": ["render", "p", "--force"], "exit": 1},   # refused/failed: not counted
           {"cmd": "plan", "argv": ["plan", "b", "--rewrite"], "exit": 0},
           {"cmd": "approve", "argv": [], "exit": 0}, {"cmd": "approve", "argv": [], "exit": 0}]
    assert production.hand_edits(tmp_path, log) == {
        "scriptWriter": "claude-x", "scriptEdited": True, "picks": 1, "planEdits": 3,
        "handManifestEntries": 1, "forcedRenders": 1, "scriptRewrites": 1, "storyboardApprovals": 2}


def test_script_edit_status(tmp_path):
    _write(tmp_path / "script_approval.json", {"draftSha256": "a", "approvedSha256": "a", "writer": "claude-x"})
    assert production.hand_edits(tmp_path, [])["scriptEdited"] is False
    (tmp_path / "script_approval.json").unlink()                # hand-written: no draft to compare
    _write(tmp_path / "script.json", {"writer": "hand"})
    h = production.hand_edits(tmp_path, [])
    assert h["scriptEdited"] is None and h["scriptWriter"] == "hand"


def test_timings_sum_wall_time_per_command():
    log = [{"cmd": "plan", "seconds": 10.04, "start": "2026-10-27T10:00:00+00:00"},
           {"cmd": "render", "seconds": 80, "start": "2026-10-27T11:00:00+00:00"},
           {"cmd": "plan", "seconds": 5, "start": "2026-10-27T09:00:00+00:00"},
           {"cmd": "mcp", "seconds": 3600, "start": "2026-10-27T10:30:00+00:00"}]
    t = production.timings(log)
    assert t["wallSeconds"] == {"mcp": 3600.0, "plan": 15.0, "render": 80.0}
    assert t["wallSecondsTotal"] == 95.0                       # an open mcp session is not work time
    assert t["commandRuns"] == {"plan": 2, "render": 1, "mcp": 1}
    assert (t["firstCommandAt"], t["lastCommandAt"]) == ("2026-10-27T09:00:00+00:00", "2026-10-27T11:00:00+00:00")


# ── publish-pack and stats ────────────────────────────────────────────────────

def test_publish_pack_writes_the_summary_and_the_ledger(ws, ledger, capsys):
    assert cli.main(["publish-pack", str(ws), "--hands-on-min", "95"]) == 0
    s = _summary(ws)
    assert s["video"] == ws.name and s["title"] == "How Webb Unfolded"
    assert s["handsOnMinutes"] == 95 and s["render"] == "renders/how-webb-unfolded.mp4"
    assert s["handEdits"]["handManifestEntries"] == 4          # the fixture's entries are hand-written
    assert _rows(ledger) == [s]
    out = capsys.readouterr().out
    assert "hands-on: 95 min" in out and str(ledger) in out


def test_without_a_ledger_tests_write_none(ws, monkeypatch, capsys):
    monkeypatch.delenv("MONTAGE_LEDGER", raising=False)
    assert cli.main(["publish-pack", str(ws)]) == 0
    assert production.summary_path(ws).is_file()
    out = capsys.readouterr().out
    assert "ledger:" not in out and "hands-on minutes not recorded" in out


def test_stats_record_youtube_numbers_and_survive_a_new_pack(ws, ledger):
    assert cli.main(["publish-pack", str(ws), "--hands-on-min", "95"]) == 0
    assert cli.main(["stats", str(ws), "--url", "https://youtu.be/x", "--day", "7", "--views", "120",
                     "--avg-view-seconds", "41.5"]) == 0
    assert cli.main(["stats", str(ws), "--day", "28", "--views", "300"]) == 0
    assert cli.main(["publish-pack", str(ws), "--force"]) == 0   # re-derived; recorded numbers carried over
    s = _summary(ws)
    assert s["handsOnMinutes"] == 95 and s["url"] == "https://youtu.be/x"
    assert s["youtube"]["day7"]["views"] == 120 and s["youtube"]["day7"]["avgViewSeconds"] == 41.5
    assert s["youtube"]["day28"]["views"] == 300 and "avgViewSeconds" not in s["youtube"]["day28"]
    assert [r["video"] for r in _rows(ledger)] == [ws.name]     # one row per video, upserted


def test_stats_backfill_a_video_made_before_logging(ws, ledger, capsys):
    # like JWST: published before production_log.jsonl existed, its publish/youtube.md hand-merged
    assert cli.main(["stats", str(ws), "--url", "https://youtu.be/old", "--hands-on-min", "240"]) == 0
    s = _summary(ws)
    assert s["logged"] is False                                # no log before this run; timings unknown
    assert [e["cmd"] for e in _log(ws)] == ["stats"]           # from now on it is logged
    assert "no production log" in capsys.readouterr().out
    assert s["render"] == "renders/how-webb-unfolded.mp4" and s["handsOnMinutes"] == 240
    assert not (ws / "publish" / "youtube.md").exists()        # stats never writes the pack


def test_stats_without_changes_only_shows(ws, ledger, capsys):
    assert cli.main(["stats", str(ws)]) == 0
    assert not production.summary_path(ws).exists() and not ledger.exists()
    assert "no production summary yet" in capsys.readouterr().out


@pytest.mark.parametrize("extra, msg", [
    (["--views", "5"], "--day and --views go together"),
    (["--day", "7"], "--day and --views go together"),
    (["--avg-view-seconds", "3"], "--avg-view-seconds needs --day"),
    (["--day", "7", "--views", "-1"], "cannot be negative"),
    (["--hands-on-min", "-5"], "cannot be negative"),
])
def test_stats_argument_errors(ws, capsys, extra, msg):
    assert cli.main(["stats", str(ws), *extra]) == 1
    assert msg in capsys.readouterr().err
    assert not production.summary_path(ws).exists()


def test_stats_needs_a_workspace(tmp_path, capsys):
    assert cli.main(["stats", str(tmp_path)]) == 1
    assert "no brief.json" in capsys.readouterr().err


def test_ledger_upsert_keeps_other_videos(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    production.upsert_ledger(ledger, {"video": "a", "n": 1})
    production.upsert_ledger(ledger, {"video": "b", "n": 1})
    production.upsert_ledger(ledger, {"video": "a", "n": 2})
    assert _rows(ledger) == [{"video": "b", "n": 1}, {"video": "a", "n": 2}]
