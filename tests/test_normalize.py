"""Normalization: probe-result rules (pure), then real ffmpeg fixes into the cache and a render.

The real fixtures are deliberately awkward: a variable-frame-rate clip, a 4:4:4 clip with
non-square pixels, a 16-bit-per-channel PNG, and 22.05 kHz mono narration.
"""

import hashlib
import json
import shutil
import subprocess

import pytest

from montage.cli import main
from montage.normalize import (CACHE_DIR, NormalizeError, apply, inspect, normalize_plan, probe_streams)
from montage.plan import load_plan, parse_plan
from montage.probe import MediaInfo
from montage.qc import run_qc
from montage.render import render

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def fake_probe(path):
    return {".mp4": MediaInfo(True, False, 10.0), ".png": MediaInfo(True, False, None),
            ".wav": MediaInfo(False, True, 10.0)}[path.suffix]


@pytest.fixture
def plan_16x9(tmp_path):
    (tmp_path / "assets").mkdir()
    for n in ("a.mp4", "s.png", "v.wav"):
        (tmp_path / "assets" / n).write_bytes(b"")
    raw = {"meta": {"title": "N", "resolution": [1920, 1080], "fps": 30, "durationFrames": 60},
           "tracks": [{"id": "v", "type": "video", "clips": [
               {"id": "a", "src": "./assets/a.mp4", "startFrame": 0, "durationFrames": 30},
               {"id": "s", "src": "./assets/s.png", "startFrame": 30, "durationFrames": 30}]},
               {"id": "n", "type": "audio", "clips": [
                   {"id": "vo", "src": "./assets/v.wav", "startFrame": 0, "durationFrames": 60}]}]}
    return parse_plan(raw, tmp_path, probe=fake_probe)


def V(**kw):
    s = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p",
         "sample_aspect_ratio": "1:1", "r_frame_rate": "30/1", "avg_frame_rate": "30/1"}
    return [s | kw]


def A(**kw):
    return [{"codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "48000", "channels": 2} | kw]


def kinds(issues, fixable=None):
    return sorted(i.kind for i in issues if fixable is None or i.fixable == fixable)


# ── rules ─────────────────────────────────────────────────────────────────────

def test_clean_assets_have_no_issues(plan_16x9, tmp_path):
    assert inspect(tmp_path / "a.mp4", "video", plan_16x9, V())[1] == []
    assert inspect(tmp_path / "v.wav", "audio", plan_16x9, A())[1] == []
    assert inspect(tmp_path / "s.png", "still", plan_16x9, V(codec_name="png", pix_fmt="rgba"))[1] == []


def test_audio_sample_rate(plan_16x9, tmp_path):
    summary, issues = inspect(tmp_path / "v.wav", "audio", plan_16x9, A(sample_rate="22050", channels=1))
    assert kinds(issues) == ["sample_rate"] and issues[0].fixable and "22050 Hz" in summary


@pytest.mark.parametrize("change,expected", [
    ({"r_frame_rate": "30/1", "avg_frame_rate": "300/19"}, ["vfr"]),
    ({"pix_fmt": "yuv444p"}, ["pix_fmt"]),
    ({"pix_fmt": "yuv420p10le"}, ["pix_fmt"]),
    ({"sample_aspect_ratio": "4:3", "width": 1440}, ["sar"]),  # 1440x1080 anamorphic = 16:9 display
    ({"sample_aspect_ratio": "0:1"}, []),                       # "unknown" SAR is treated as square
])
def test_video_fixable_issues(plan_16x9, tmp_path, change, expected):
    _, issues = inspect(tmp_path / "a.mp4", "video", plan_16x9, V(**change))
    assert kinds(issues, fixable=True) == expected
    assert kinds(issues, fixable=False) == []


def test_aspect_mismatch_is_reported_not_fixed(plan_16x9, tmp_path):
    _, issues = inspect(tmp_path / "a.mp4", "video", plan_16x9, V(width=1080, height=1080))
    assert kinds(issues) == ["aspect"] and not issues[0].fixable
    assert "cover crops 44% of the height" in issues[0].detail


def test_still_rules(plan_16x9, tmp_path):
    _, issues = inspect(tmp_path / "s.png", "still", plan_16x9, V(codec_name="png", pix_fmt="rgb48be"))
    assert kinds(issues, fixable=True) == ["pix_fmt"]
    _, issues = inspect(tmp_path / "s.png", "still", plan_16x9,
                        V(codec_name="png", pix_fmt="rgb24", r_frame_rate="25/1", avg_frame_rate="0/0"))
    assert issues == []  # stills have no frame rate to be variable


def test_missing_stream_is_an_error(plan_16x9, tmp_path):
    with pytest.raises(NormalizeError, match="no audio stream"):
        inspect(tmp_path / "a.mp4", "audio", plan_16x9, V())


def test_report_only_does_not_write(plan_16x9):
    streams = {"a.mp4": V(pix_fmt="yuv444p"), "s.png": V(codec_name="png", pix_fmt="rgb24"),
               "v.wav": A(sample_rate="44100")}
    reports = normalize_plan(plan_16x9, fix=False, probe=lambda p: streams[p.name],
                             run=lambda *a, **k: pytest.fail("report mode must not run ffmpeg"))
    assert [(r.src.name, r.role, r.needs_fix) for r in reports] == [
        ("a.mp4", "video", True), ("s.png", "still", False), ("v.wav", "audio", True)]
    assert all(r.normalized is None for r in reports)
    assert apply(plan_16x9, reports) is plan_16x9


# ── real ffmpeg ───────────────────────────────────────────────────────────────

def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def awkward(tmp_path_factory):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    d = tmp_path_factory.mktemp("awkward")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=4",
            "-vf", r"select='lt(n\,30)+not(mod(n\,3))'", "-fps_mode", "vfr",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "vfr.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=240x180:rate=30:duration=3",
            "-vf", "setsar=4/3", "-c:v", "libx264", "-pix_fmt", "yuv444p", str(d / "anamorphic444.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=300x300", "-frames:v", "1", "-pix_fmt", "rgb48be",
            str(d / "deep.png"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=4:sample_rate=22050", str(d / "voice22k.wav"))
    return d


@pytest.fixture
def awkward_ws(tmp_path, awkward):
    shutil.copytree(awkward, tmp_path / "assets")
    raw = {"meta": {"title": "Awkward", "resolution": [320, 180], "fps": 30, "durationFrames": 90},
           "tracks": [
               {"id": "visuals", "type": "video", "clips": [
                   {"id": "a", "src": "./assets/vfr.mp4", "startFrame": 0, "durationFrames": 40,
                    "sourceInFrame": 10, "transition": {"type": "crossfade", "durationFrames": 10}},
                   {"id": "b", "src": "./assets/anamorphic444.mp4", "startFrame": 30, "durationFrames": 35},
                   {"id": "c", "src": "./assets/deep.png", "startFrame": 65, "durationFrames": 25,
                    "fit": "contain"}]},
               {"id": "narration", "type": "audio", "clips": [
                   {"id": "vo", "src": "./assets/voice22k.wav", "startFrame": 0, "durationFrames": 90}]}]}
    (tmp_path / "scene_plan.json").write_text(json.dumps(raw), encoding="utf-8")
    return tmp_path


def _digest(folder):
    return {p.name: hashlib.sha1(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_fix_writes_cache_and_fixed_files_probe_clean(awkward_ws):
    plan = load_plan(awkward_ws / "scene_plan.json")
    originals = _digest(awkward_ws / "assets")
    reports = {r.src.name: r for r in normalize_plan(plan)}
    assert kinds(reports["vfr.mp4"].issues, True) == ["vfr"]
    assert kinds(reports["anamorphic444.mp4"].issues, True) == ["pix_fmt", "sar"]
    assert kinds(reports["deep.png"].issues, True) == ["pix_fmt"]
    assert kinds(reports["voice22k.wav"].issues, True) == ["sample_rate"]

    fixed = normalize_plan(plan, fix=True)
    for r in fixed:
        assert r.normalized and r.normalized.is_file()
        assert r.normalized.parent == awkward_ws / CACHE_DIR
        _, again = inspect(r.normalized, r.role, plan, probe_streams(r.normalized))
        assert kinds(again, fixable=True) == [], (r.src.name, again)
    assert _digest(awkward_ws / "assets") == originals  # sources untouched

    mtimes = {r.normalized: r.normalized.stat().st_mtime_ns for r in fixed}
    for r in normalize_plan(plan, fix=True):  # second run reuses the cache
        assert r.normalized.stat().st_mtime_ns == mtimes[r.normalized]

    swapped = apply(plan, fixed)
    assert all(c.src.parent == awkward_ws / CACHE_DIR for c in swapped.video.clips)
    assert swapped.audio[0].clips[0].src.suffix == ".wav" and swapped.meta == plan.meta
    assert swapped.video.clips[2].is_still and swapped.video.clips[2].fit == "contain"


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_render_uses_normalized_assets(awkward_ws):
    plan_path = awkward_ws / "scene_plan.json"
    before = plan_path.read_text()
    plan = load_plan(plan_path)
    out = render(plan)
    checks = run_qc(out, plan)
    assert all(c.ok for c in checks), [f"{c.name}: {c.detail}" for c in checks if not c.ok]
    assert plan_path.read_text() == before
    assert len(list((awkward_ws / CACHE_DIR).iterdir())) == 4


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_cli_normalize_reports(awkward_ws, capsys):
    assert main(["normalize", str(awkward_ws / "scene_plan.json")]) == 0
    out = capsys.readouterr().out
    assert "FIX  assets\\vfr.mp4 [video]" in out or "FIX  assets/vfr.mp4 [video]" in out
    assert "fix: sample_rate: 22050 Hz" in out and "4 asset uses, 4 need fixing" in out
    assert not (awkward_ws / CACHE_DIR).exists()  # report only
    assert main(["normalize", str(awkward_ws / "scene_plan.json"), "--fix"]) == 0
    assert len(list((awkward_ws / CACHE_DIR).iterdir())) == 4
