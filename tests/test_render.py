"""End-to-end: synthetic media -> scene_plan.json -> render -> QC, with real ffmpeg.

Sources deliberately mismatch the plan (25 fps 16:9 video, square still, 22.05 kHz mono
audio) so fps conversion, cover/contain fitting and resampling are all exercised.
"""

import json
import shutil
import subprocess

import pytest

from montage.cli import main
from montage.plan import load_plan
from montage.qc import run_qc
from montage.render import render

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg/ffprobe not on PATH")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    assets = tmp_path_factory.mktemp("media")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=8",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(assets / "clip.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=400x400", "-frames:v", "1", str(assets / "still.png"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=10:sample_rate=22050",
            str(assets / "voice.wav"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=10", "-ac", "2", str(assets / "music.wav"))
    return assets


@pytest.fixture
def workspace(tmp_path, media):
    shutil.copytree(media, tmp_path / "assets")
    return tmp_path


def write_plan(ws, video_clips, audio_tracks, duration=150):
    plan = {
        "meta": {"title": "Render Test", "resolution": [320, 180], "fps": 30, "durationFrames": duration},
        "tracks": [{"id": "visuals", "type": "video", "clips": video_clips}] + audio_tracks,
    }
    path = ws / "scene_plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


VOICE = {"id": "narration", "type": "audio", "clips": [
    {"id": "vo", "src": "./assets/voice.wav", "startFrame": 15, "durationFrames": 120}]}


def assert_qc_passes(out, plan):
    checks = run_qc(out, plan)
    assert all(c.ok for c in checks), [f"{c.name}: {c.detail}" for c in checks if not c.ok]


def test_crossfade_motion_and_music_bed(workspace):
    path = write_plan(workspace, [
        {"id": "a", "src": "./assets/clip.mp4", "startFrame": 0, "durationFrames": 90, "sourceInFrame": 30,
         "transition": {"type": "crossfade", "durationFrames": 15}},
        {"id": "b", "src": "./assets/still.png", "startFrame": 75, "durationFrames": 75,
         "motion": {"scale": [1.0, 1.2], "easing": "easeInOutCubic"}},
    ], [VOICE, {"id": "bed", "type": "audio", "clips": [
        {"id": "music", "src": "./assets/music.wav", "startFrame": 0, "durationFrames": 150, "volume": 0.3}]}])
    plan = load_plan(path)
    out = render(plan)
    assert out == workspace / "renders" / "render-test.mp4"
    assert_qc_passes(out, plan)


def test_hard_cuts_contain_fit_linear_motion(workspace):
    path = write_plan(workspace, [
        {"id": "a", "src": "./assets/still.png", "startFrame": 0, "durationFrames": 50, "fit": "contain"},
        {"id": "b", "src": "./assets/clip.mp4", "startFrame": 50, "durationFrames": 50},
        {"id": "c", "src": "./assets/still.png", "startFrame": 100, "durationFrames": 50,
         "motion": {"scale": [1.1, 1.0], "easing": "linear"}},
    ], [VOICE])
    plan = load_plan(path)
    assert_qc_passes(render(plan, workspace / "out.mp4"), plan)


def test_single_clip_plan(workspace):
    path = write_plan(workspace, [
        {"id": "only", "src": "./assets/clip.mp4", "startFrame": 0, "durationFrames": 60}], [VOICE], duration=60)
    # voice clip ends at 135 > 60 -> invalid; shorten it
    data = json.loads(path.read_text())
    data["tracks"][1]["clips"][0].update(startFrame=0, durationFrames=60)
    path.write_text(json.dumps(data))
    plan = load_plan(path)
    assert_qc_passes(render(plan), plan)


def test_cli_exit_codes(workspace, capsys):
    good = write_plan(workspace, [
        {"id": "a", "src": "./assets/clip.mp4", "startFrame": 0, "durationFrames": 150}], [VOICE])
    assert main(["validate", str(good)]) == 0
    assert main(["render", str(good), "-o", str(workspace / "cli.mp4")]) == 0
    assert "PASS  frame count" in capsys.readouterr().out

    bad = json.loads(good.read_text())
    bad["tracks"][0]["clips"][0]["durationFrames"] = 140
    good.write_text(json.dumps(bad))
    assert main(["validate", str(good)]) == 1
    assert "ends at frame 140" in capsys.readouterr().err
