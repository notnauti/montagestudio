"""Render profiles: output sizing, fps remapping, encoder selection (faked), and real renders.

The real renders use a 2-second plan so 4k60-prores stays quick. Hardware encoders are
faked for the selection logic; one real `--encoder auto` render checks that whatever this
machine has (or the libx264 fallback) still passes QC.
"""

import json
import shutil
import subprocess
import types

import pytest

from montage import profiles
from montage.cli import main
from montage.plan import load_plan, parse_plan
from montage.probe import MediaInfo
from montage.profiles import output_size, resolve, select_h264_encoder
from montage.qc import run_qc
from montage.render import _video_graph, default_output, out_frames, render, render_command

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture(autouse=True)
def fresh_encoder_cache():
    profiles.reset_encoder_cache()
    yield
    profiles.reset_encoder_cache()


def fake_probe(path):
    return {".mp4": MediaInfo(True, False, 10.0), ".png": MediaInfo(True, False, None),
            ".wav": MediaInfo(False, True, 10.0)}[path.suffix]


def _raw(fps=30, res=(320, 180), duration=60, crossfade=15):
    a_len = 30 + crossfade
    return {
        "meta": {"title": "Profile Test", "resolution": list(res), "fps": fps, "durationFrames": duration},
        "tracks": [
            {"id": "visuals", "type": "video", "clips": [
                {"id": "a", "src": "./assets/clip.mp4", "startFrame": 0, "durationFrames": a_len,
                 "transition": {"type": "crossfade", "durationFrames": crossfade}},
                {"id": "b", "src": "./assets/still.png", "startFrame": 30, "durationFrames": duration - 30},
            ]},
            {"id": "narration", "type": "audio", "clips": [
                {"id": "vo", "src": "./assets/voice.wav", "startFrame": 0, "durationFrames": duration}]},
        ],
    }


@pytest.fixture
def fake_ws(tmp_path):
    (tmp_path / "assets").mkdir()
    for n in ("clip.mp4", "still.png", "voice.wav"):
        (tmp_path / "assets" / n).write_bytes(b"")
    return tmp_path


# ── sizing and specs ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("plan_size,edge,expected", [
    ((1920, 1080), 720, (1280, 720)),
    ((1920, 1080), 2160, (3840, 2160)),
    ((1080, 1920), 720, (720, 1280)),      # vertical stays vertical
    ((320, 180), 1080, (1920, 1080)),
    ((1000, 1000), 720, (720, 720)),
])
def test_output_size_keeps_aspect(plan_size, edge, expected):
    assert output_size(*plan_size, edge) == expected


def test_native_spec_is_the_slice_one_output(fake_ws):
    plan = parse_plan(_raw(), fake_ws, probe=fake_probe)
    spec = resolve(plan)
    assert (spec.profile, spec.width, spec.height, spec.fps) == ("native", 320, 180, 30)
    assert spec.encoder == "libx264" and "-crf" in spec.video_args and spec.ext == ".mp4" and not spec.notes
    assert default_output(plan, spec) == fake_ws / "renders" / "profile-test.mp4"


def test_prores_spec(fake_ws):
    plan = parse_plan(_raw(), fake_ws, probe=fake_probe)
    spec = resolve(plan, "4k60-prores", encoder="auto")
    assert (spec.width, spec.height, spec.fps, spec.ext) == (3840, 2160, 60, ".mov")
    assert spec.encoder == "prores_ks" and spec.pix_fmt == "yuv422p10le" and spec.audio_codec == "pcm_s24le"
    assert "H.264 only" in spec.notes[0]
    assert default_output(plan, spec).name == "profile-test-4k60-prores.mov"
    cmd = render_command(plan, fake_ws / "x.mov", {"input_i": -20, "input_tp": -3, "input_lra": 4,
                                                    "input_thresh": -30, "target_offset": 0}, spec)
    assert cmd[cmd.index("-c:v") + 1] == "prores_ks" and cmd[cmd.index("-r") + 1] == "60"
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s24le"


def test_unknown_profile(fake_ws):
    with pytest.raises(ValueError, match="available"):
        resolve(parse_plan(_raw(), fake_ws, probe=fake_probe), "8k120-av1")


def test_profile_does_not_mutate_plan(fake_ws):
    plan = parse_plan(_raw(), fake_ws, probe=fake_probe)
    resolve(plan, "720p30-h264")
    assert (plan.meta.width, plan.meta.height, plan.meta.fps) == (320, 180, 30)


# ── fps remapping ─────────────────────────────────────────────────────────────

def test_double_fps_doubles_every_boundary(fake_ws):
    plan = parse_plan(_raw(), fake_ws, probe=fake_probe)
    spec = resolve(plan, "4k60-prores")
    graph = ";".join(_video_graph(plan, spec))
    assert "trim=end_frame=90" in graph and "trim=end_frame=60" in graph  # 45 and 30 plan frames
    assert "xfade=transition=fade:duration=0.500000:offset=1.000000" in graph
    assert out_frames(plan.meta.duration_frames, plan, spec) == 120


def test_non_integer_fps_ratio_keeps_timeline_consistent(fake_ws):
    plan = parse_plan(_raw(fps=25, duration=50, crossfade=7), fake_ws, probe=fake_probe)  # 25 -> 30 fps
    spec = resolve(plan, "720p30-h264")
    graph = ";".join(_video_graph(plan, spec))
    # a: 0..37 plan frames -> 0..44; b: 30..50 -> 36..60; crossfade = 44 - 36 = 8 output frames
    assert "trim=end_frame=44" in graph and "trim=end_frame=24" in graph
    assert "duration=0.266667:offset=1.200000" in graph
    assert out_frames(50, plan, spec) == 60


# ── hardware encoder selection (ffmpeg faked) ─────────────────────────────────

ENCODERS_OUT = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder
 V..... h264_qsv             H.264 (Intel Quick Sync Video acceleration)
"""


def fake_ffmpeg(works: set[str]):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "-encoders" in cmd:
            return types.SimpleNamespace(returncode=0, stdout=ENCODERS_OUT, stderr="")
        enc = cmd[cmd.index("-c:v") + 1]
        ok = enc in works
        return types.SimpleNamespace(returncode=0 if ok else 1, stdout="",
                                     stderr="" if ok else f"[{enc}] Cannot load driver\nError while opening encoder")
    run.calls = calls
    return run


def test_software_never_probes(monkeypatch):
    run = fake_ffmpeg({"h264_nvenc"})
    assert select_h264_encoder("software", 1920, 1080, 30, run) == ("libx264", [])
    assert run.calls == []


def test_auto_picks_first_working_hardware_encoder():
    run = fake_ffmpeg({"h264_qsv"})
    enc, notes = select_h264_encoder("auto", 1920, 1080, 30, run)
    assert enc == "h264_qsv" and "passed a 1-frame test encode" in notes[0]
    tried = [c[c.index("-c:v") + 1] for c in run.calls if "-c:v" in c]
    assert tried == ["h264_nvenc", "h264_qsv"]          # amf never reached
    assert "-frames:v" in run.calls[1] and "1" in run.calls[1]


def test_listed_but_broken_falls_back_and_says_why():
    enc, notes = select_h264_encoder("h264_nvenc", 1280, 720, 30, fake_ffmpeg(set()))
    assert enc == "libx264"
    assert "h264_nvenc listed but its 1-frame test encode failed" in notes[0] and "Cannot load driver" in notes[0]


def test_unlisted_encoder_falls_back():
    enc, notes = select_h264_encoder("h264_amf", 1280, 720, 30, fake_ffmpeg({"h264_amf"}))
    assert enc == "libx264" and "h264_amf not in `ffmpeg -encoders`" in notes[0]


def test_hardware_spec_uses_encoder_args(fake_ws, monkeypatch):
    plan = parse_plan(_raw(), fake_ws, probe=fake_probe)
    spec = resolve(plan, "720p30-h264", "auto", run=fake_ffmpeg({"h264_nvenc"}))
    assert spec.encoder == "h264_nvenc" and spec.video_args[1] == "h264_nvenc" and spec.codec == "h264"


# ── real renders ──────────────────────────────────────────────────────────────

def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    d = tmp_path_factory.mktemp("profile_media")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "clip.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=400x400", "-frames:v", "1", str(d / "still.png"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=48000", "-ac", "2", str(d / "voice.wav"))
    return d


@pytest.fixture
def real_ws(tmp_path, media):
    shutil.copytree(media, tmp_path / "assets")
    return tmp_path


def _write(ws, raw):
    p = ws / "scene_plan.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return p


def _assert_qc(out, plan, spec):
    checks = run_qc(out, plan, spec)
    assert all(c.ok for c in checks), [f"{c.name}: {c.detail}" for c in checks if not c.ok]
    return checks


@needs_ffmpeg
def test_render_720p30_h264(real_ws):
    plan = load_plan(_write(real_ws, _raw()))
    spec = resolve(plan, "720p30-h264")
    out = render(plan, None, spec)
    assert out.name == "profile-test-720p30-h264.mp4"
    _assert_qc(out, plan, spec)
    # QC is judged against the profile: the plan's own 320x180 is now a failure
    assert not all(c.ok for c in run_qc(out, plan))


@needs_ffmpeg
def test_render_4k60_prores(real_ws):
    plan = load_plan(_write(real_ws, _raw()))
    spec = resolve(plan, "4k60-prores")
    out = render(plan, None, spec)
    checks = _assert_qc(out, plan, spec)
    assert out.suffix == ".mov"
    assert any(c.name == "frame count" and "120" in c.detail for c in checks)


@needs_ffmpeg
def test_render_25fps_plan_at_30fps_profile(real_ws):
    plan = load_plan(_write(real_ws, _raw(fps=25, duration=50, crossfade=7)))
    spec = resolve(plan, "720p30-h264")
    _assert_qc(render(plan, None, spec), plan, spec)


@needs_ffmpeg
def test_cli_profile_and_encoder_fallback(real_ws, capsys):
    path = _write(real_ws, _raw())
    before = path.read_text()
    # --force: the storyboard gate is tested in test_gate.py, not here
    assert main(["render", str(path), "--profile", "720p30-h264", "--encoder", "auto", "--force"]) == 0
    out = capsys.readouterr().out
    assert "output: 720p30-h264, 1280x720 @ 30 fps" in out and "PASS  resolution: 1280x720" in out
    assert ("falling back to libx264" in out) or ("passed a 1-frame test encode" in out)
    assert path.read_text() == before  # the plan on disk is never rewritten by a profile

    assert main(["render", str(path), "--profile", "4k60-prores", "--force", "-o", str(real_ws / "x.mp4")]) == 1
    assert ".mov" in capsys.readouterr().err
