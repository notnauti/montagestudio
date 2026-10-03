"""The storyboard gate: contact_sheet.png, `montage approve`, and render refusing unapproved plans."""

import json
import os
import shutil
import subprocess
import time

import pytest

from montage import approval, contact_sheet as cs
from montage.cli import main
from montage.plan import load_plan

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg/ffprobe not on PATH")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("gate_media")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=8", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(d / "clip.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=400x400", "-frames:v", "1", str(d / "still.png"))
    # a true grayscale still (decodes as `gray`, like the live Commons F-1 photo): its tile had another
    # pixel format, which re-initialised the grid's tile filter and dropped the tiles before it
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=300x200", "-frames:v", "1", "-vf", "format=gray",
            "-update", "1", str(d / "gray.png"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=10", str(d / "voice.wav"))
    return d


def _words(*spec):
    return [{"text": t, "startFrame": s, "endFrame": e} for t, s, e in spec]


def _plan(ws, captions=True):
    clips = [
        {"id": "a", "src": "./assets/clip.mp4", "startFrame": 0, "durationFrames": 40, "sourceInFrame": 30,
         "transition": {"type": "crossfade", "durationFrames": 10}},
        {"id": "b", "src": "./assets/still.png", "startFrame": 30, "durationFrames": 40,
         "motion": {"scale": [1.0, 1.1], "easing": "linear"}},
        {"id": "c", "src": "./assets/gray.png", "startFrame": 70, "durationFrames": 30, "fit": "contain"},
        {"id": "d", "src": "./assets/clip.mp4", "startFrame": 100, "durationFrames": 30},
        {"id": "e", "src": "./assets/still.png", "startFrame": 130, "durationFrames": 20},
    ]
    tracks = [{"id": "v", "type": "video", "clips": clips},
              {"id": "n", "type": "audio", "clips": [
                  {"id": "vo", "src": "./assets/voice.wav", "startFrame": 0, "durationFrames": 150}]}]
    if captions:
        tracks.append({"id": "captions", "type": "caption", "clips": [
            {"id": "c1", "startFrame": 2, "durationFrames": 60,
             # 'second' starts at 32 < 40 - 10: clip a's span ends where its crossfade begins, so it is b's
             "words": _words(("First", 2, 20), ("line,", 20, 31), ("second", 32, 50))},
            {"id": "c2", "startFrame": 72, "durationFrames": 60, "words": _words(("Gray", 72, 90), ("card", 90, 99))}]})
    raw = {"meta": {"title": "Gate Test", "resolution": [320, 180], "fps": 30, "durationFrames": 150}, "tracks": tracks}
    path = ws / "scene_plan.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


@pytest.fixture
def ws(tmp_path, media):
    shutil.copytree(media, tmp_path / "assets")
    return tmp_path


def _gray(path):
    w, h = (int(x) for x in subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip().split(","))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return w, h, raw


# ── contact sheet ─────────────────────────────────────────────────────────────

def test_tiles_take_narration_from_the_caption_track(ws):
    plan = load_plan(_plan(ws))
    tiles = cs.tiles(plan)
    assert [t.narration for t in tiles] == ["First line,", "second", "Gray card", "", ""]
    assert tiles[0].frame_seconds == pytest.approx((30 + 20) / 30)  # middle of the used stretch
    assert tiles[1].frame_seconds is None  # a still
    label = cs.label_text(tiles[1], 30, 480)
    assert label.startswith("#2  b  @1.0s  1.3s  still  zoom 1-1.1\nsecond")
    assert cs.label_text(tiles[4], 30, 480).endswith("(no narration)")


def test_narration_falls_back_to_script_json(ws):
    plan_path = _plan(ws, captions=False)
    (ws / "script.json").write_text(json.dumps({"beats": [{"narration": f"line {i}"} for i in range(5)]}))
    assert [t.narration for t in cs.tiles(load_plan(plan_path))] == [f"line {i}" for i in range(5)]
    (ws / "script.json").write_text(json.dumps({"beats": [{"narration": "only one"}]}))
    assert [t.narration for t in cs.tiles(load_plan(plan_path))] == [""] * 5  # no 1:1 mapping, no guessing


def test_long_narration_is_cut_to_two_lines(ws):
    plan = load_plan(_plan(ws))
    tile = cs.Tile(1, plan.video.clips[0], 1.0, "word " * 80)
    lines = cs.label_text(tile, 30, 480).split("\n")
    assert len(lines) == 3 and lines[2].endswith("...")


def test_contact_sheet_draws_every_tile(ws):
    plan = load_plan(_plan(ws))
    out = cs.make_contact_sheet(plan)
    assert out == ws / "contact_sheet.png"
    w, h, px = _gray(out)
    tw, th = cs.tile_size(plan)
    assert (tw, th) == (480, 270)
    cols, rows = 4, 2
    assert w == cols * tw + (cols + 1) * cs.GAP
    assert h == rows * (th + cs.LABEL_HEIGHT) + (rows + 1) * cs.GAP + cs.HEADER_HEIGHT
    # each of the 5 tiles has picture in it (not the 0x11 background), the 3 empty slots do not
    for k in range(8):
        r, c = divmod(k, cols)
        x = cs.GAP + c * (tw + cs.GAP) + tw // 2
        y = cs.HEADER_HEIGHT + cs.GAP + r * (th + cs.LABEL_HEIGHT + cs.GAP) + th // 2
        patch = [px[(y + dy) * w + x + dx] for dy in range(-20, 21, 4) for dx in range(-40, 41, 4)]
        drawn = max(patch) - min(patch) > 30 or abs(sum(patch) / len(patch) - 0x11) > 10
        assert drawn == (k < 5), f"tile slot {k}"
    # label text drawn under tile 1: the label band is not flat
    band = px[(cs.HEADER_HEIGHT + cs.GAP + th + 10) * w: (cs.HEADER_HEIGHT + cs.GAP + th + 30) * w]
    assert max(band) - min(band) > 100


def test_vertical_plan_tiles_are_vertical(ws):
    raw = json.loads(_plan(ws).read_text())
    raw["meta"]["resolution"] = [180, 320]
    (ws / "scene_plan.json").write_text(json.dumps(raw))
    assert cs.tile_size(load_plan(ws / "scene_plan.json")) == (270, 480)


def test_label_file_is_lf_only_and_paths_survive_escaping(tmp_path, media):
    ws = tmp_path / "it's a [sheet], ok;"
    shutil.copytree(media, ws / "assets")
    plan = load_plan(_plan(ws))
    assert main(["contact-sheet", str(ws)]) == 0
    assert b"\r" not in (ws / "cache" / "contact_sheet" / "label_001.txt").read_bytes()
    assert (ws / "contact_sheet.png").is_file() and plan


# ── approval ──────────────────────────────────────────────────────────────────

def _approved(ws):
    path = _plan(ws)
    plan = load_plan(path)
    cs.make_contact_sheet(plan)
    approval.approve(plan, path, "looks right")
    return path


def test_approve_needs_a_fresh_contact_sheet(ws):
    path = _plan(ws)
    plan = load_plan(path)
    with pytest.raises(approval.ApprovalError, match="no contact_sheet.png"):
        approval.approve(plan, path)
    cs.make_contact_sheet(plan)
    old = time.time() - 100
    os.utime(ws / "contact_sheet.png", (old, old))
    with pytest.raises(approval.ApprovalError, match="older than the plan"):
        approval.approve(plan, path)


def test_approval_pins_plan_and_assets(ws):
    path = _approved(ws)
    doc = json.loads((ws / "approval.json").read_text())
    assert doc["note"] == "looks right" and doc["plan"]["path"] == "scene_plan.json"
    assert sorted(doc["assets"]) == ["assets/clip.mp4", "assets/gray.png", "assets/still.png", "assets/voice.wav"]
    assert doc["assets"]["assets/clip.mp4"] == approval.sha256(ws / "assets" / "clip.mp4")
    assert doc["contactSheet"]["sha256"] == approval.sha256(ws / "contact_sheet.png")
    assert approval.check(load_plan(path), path).approved


def test_any_change_after_approval_unapproves(ws):
    path = _approved(ws)
    raw = json.loads(path.read_text())
    raw["tracks"][0]["clips"][2]["fit"] = "cover"
    path.write_text(json.dumps(raw))
    status = approval.check(load_plan(path), path)
    assert not status.approved and status.problems == ("scene_plan.json changed since it was approved",)

    path = _approved(ws)
    with open(ws / "assets" / "voice.wav", "ab") as fh:
        fh.write(b"\0\0")  # same name, different bytes
    assert approval.check(load_plan(path), path).problems == ("assets/voice.wav changed since approval",)

    path = _approved(ws)
    shutil.copy(ws / "assets" / "gray.png", ws / "assets" / "new.png")
    raw = json.loads(path.read_text())
    raw["tracks"][0]["clips"][2]["src"] = "./assets/new.png"  # gray.png no longer used anywhere
    path.write_text(json.dumps(raw))
    assert approval.check(load_plan(path), path).problems == (
        "scene_plan.json changed since it was approved", "assets/new.png is new since approval",
        "assets/gray.png was approved but the plan no longer uses it")


def test_missing_or_broken_approval(ws):
    path = _plan(ws)
    assert approval.check(load_plan(path), path).problems == ("not approved: no approval.json",)
    (ws / "approval.json").write_text("{not json")
    assert "unreadable" in approval.check(load_plan(path), path).problems[0]
    # valid JSON of the wrong shape is unreadable too, not a crash (AttributeError / TypeError before)
    for doc in ({"plan": "scene_plan.json", "assets": {}},
                {"plan": {"path": "scene_plan.json", "sha256": "0"}, "assets": None},
                {"plan": {"path": "scene_plan.json", "sha256": "0"}, "assets": ["assets/clip.mp4"]}):
        (ws / "approval.json").write_text(json.dumps(doc))
        status = approval.check(load_plan(path), path)
        assert not status.approved and "unreadable" in status.problems[0]


def test_contact_sheet_in_a_workspace_path_with_a_percent_sign(tmp_path, media):
    # the grid reads tile_%03d.png as an image2 sequence; a '%' in the workspace path used to be
    # parsed as part of that pattern, so the grid input did not open and approval was impossible
    ws = tmp_path / "50% off, it's [v2]"
    shutil.copytree(media, ws / "assets")
    plan = load_plan(_plan(ws))
    sheet = cs.make_contact_sheet(plan)
    assert sheet == ws / "contact_sheet.png" and sheet.is_file()
    w, h, _ = _gray(sheet)
    tw, th = cs.tile_size(plan)
    assert w > 4 * tw and h > th  # all five tiles: a 4-column grid


# ── CLI ───────────────────────────────────────────────────────────────────────

def test_cli_gate_refuses_then_renders(ws, capsys):
    path = _plan(ws)
    out = ws / "o.mp4"
    assert main(["render", str(path), "-o", str(out)]) == 1
    err = capsys.readouterr().err
    assert "refusing to render" in err and "montage approve --workspace" in err and not out.exists()

    assert main(["approve", "--workspace", str(ws)]) == 1  # no contact sheet yet
    assert "contact-sheet" in capsys.readouterr().err
    assert main(["contact-sheet", str(ws)]) == 0
    assert main(["approve", "--workspace", str(ws), "--note", "ok"]) == 0
    assert "4 asset files pinned" in capsys.readouterr().out
    assert main(["render", str(path), "-o", str(out), "--skip-qc"]) == 0
    assert "approval: ok" in capsys.readouterr().out and out.is_file()

    raw = json.loads(path.read_text())
    raw["meta"]["title"] = "Gate Test 2"
    path.write_text(json.dumps(raw))
    assert main(["render", str(path), "-o", str(out), "--skip-qc"]) == 1
    assert "scene_plan.json changed since it was approved" in capsys.readouterr().err
    assert main(["render", str(path), "-o", str(out), "--skip-qc", "--force"]) == 0
    assert "bypassed with --force (scene_plan.json changed" in capsys.readouterr().out


def test_cli_gate_commands_report_invalid_plans(tmp_path, capsys):
    (tmp_path / "scene_plan.json").write_text("{}")
    assert main(["contact-sheet", str(tmp_path)]) == 1
    assert main(["approve", "--workspace", str(tmp_path)]) == 1
    assert "invalid plan" in capsys.readouterr().err
