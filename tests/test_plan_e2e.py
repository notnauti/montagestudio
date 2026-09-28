"""brief.json + hand-written script.json -> real Piper -> scene_plan.json -> render -> QC.

No model call: a present script.json is reused, which is also the edit-and-replan path.
"""

import json
import shutil
import subprocess

import pytest

from montage.cli import main
from montage.narrate import VOICES_DIR
from montage.plan import load_plan
from montage.qc import run_qc
from montage.render import render

try:
    import piper  # noqa: F401
    HAVE_PIPER = True
except ImportError:
    HAVE_PIPER = False

pytestmark = pytest.mark.skipif(
    not (HAVE_PIPER and (VOICES_DIR / "en_US-lessac-medium.onnx").is_file() and shutil.which("ffmpeg")),
    reason="needs piper-tts, the en_US-lessac-medium voice, and ffmpeg")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture
def workspace(tmp_path):
    a = tmp_path / "assets"
    a.mkdir()
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=12",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(a / "orbit.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "mandelbrot=size=800x600", "-frames:v", "1", str(a / "clocks.png"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=60", "-ac", "2", str(a / "bed.wav"))
    (tmp_path / "brief.json").write_text(json.dumps({
        "title": "GPS time",
        "brief": "Why GPS clocks need relativity corrections.",
        "targetSeconds": 15,
        "assets": [{"src": "./assets/orbit.mp4", "description": "satellite orbit"},
                   {"src": "./assets/clocks.png", "description": "two clocks"}],
        "music": {"src": "./assets/bed.wav", "volume": 0.1},
    }), encoding="utf-8")
    (tmp_path / "script.json").write_text(json.dumps({
        "title": "GPS time", "writer": "hand",
        "beats": [
            {"narration": "Every GPS satellite carries an atomic clock.", "asset": "./assets/orbit.mp4",
             "motion": "none"},
            {"narration": "Up there, those clocks run fast by about thirty eight microseconds a day.",
             "asset": "./assets/clocks.png", "motion": "zoom_in"},
            {"narration": "Correct for it, and your phone knows where you are.", "asset": "./assets/orbit.mp4",
             "motion": "none"},
        ]}), encoding="utf-8")
    return tmp_path


def test_plan_then_render(workspace, capsys):
    assert main(["plan", str(workspace / "brief.json")]) == 0
    out = capsys.readouterr().out
    assert "reused script.json" in out and "3 beats" in out
    wavs = sorted((workspace / "assets" / "narration").glob("beat_*.wav"))
    assert len(wavs) == 3

    plan = load_plan(workspace / "scene_plan.json")
    assert [c.src.name for c in plan.audio[0].clips] == [w.name for w in wavs]
    assert plan.video.clips[1].motion is not None

    # Re-planning with an unchanged script re-uses every wav (no re-synthesis).
    mtimes = [w.stat().st_mtime_ns for w in wavs]
    assert main(["plan", str(workspace / "brief.json")]) == 0
    assert [w.stat().st_mtime_ns for w in wavs] == mtimes

    small = json.loads((workspace / "scene_plan.json").read_text())
    small["meta"]["resolution"] = [320, 180]  # keep the render fast; timing is unchanged
    (workspace / "scene_plan.json").write_text(json.dumps(small))
    plan = load_plan(workspace / "scene_plan.json")
    checks = run_qc(render(plan), plan)
    assert all(c.ok for c in checks), [f"{c.name}: {c.detail}" for c in checks if not c.ok]


def test_video_line_too_long_is_reported(workspace, capsys):
    script = json.loads((workspace / "script.json").read_text())
    script["beats"][0]["narration"] = " ".join(["This line is far too long for twelve seconds of footage."] * 6)
    (workspace / "script.json").write_text(json.dumps(script))
    assert main(["plan", str(workspace / "brief.json")]) == 1
    assert "shorten the line in script.json" in capsys.readouterr().err
