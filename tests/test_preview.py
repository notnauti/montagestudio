"""`montage preview`: the 480p animatic of an unapproved plan, and approval accepting it."""

import json
import shutil
import subprocess

import pytest

from montage import approval, render as render_mod, review
from montage.cli import main
from montage.plan import load_plan
from montage.profiles import PREVIEW_PROFILE, preview_spec, resolve
from montage.qc import run_qc

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe not on PATH")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("preview_media")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(d / "clip.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=400x400", "-frames:v", "1", str(d / "still.png"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=6:sample_rate=22050", str(d / "voice.wav"))
    return d


@pytest.fixture
def ws(tmp_path, media):
    shutil.copytree(media, tmp_path / "assets")
    return tmp_path


def _plan(ws, resolution=(320, 180)):
    raw = {"meta": {"title": "Preview Test", "resolution": list(resolution), "fps": 30, "durationFrames": 120},
           "tracks": [
               {"id": "v", "type": "video", "clips": [
                   {"id": "a", "src": "./assets/clip.mp4", "startFrame": 0, "durationFrames": 70,
                    "transition": {"type": "crossfade", "durationFrames": 10}},
                   {"id": "b", "src": "./assets/still.png", "startFrame": 60, "durationFrames": 60,
                    "motion": {"scale": [1.0, 1.1], "easing": "linear"}}]},
               {"id": "n", "type": "audio", "clips": [
                   {"id": "vo", "src": "./assets/voice.wav", "startFrame": 6, "durationFrames": 100}]},
               {"id": "captions", "type": "caption", "clips": [
                   {"id": "c1", "startFrame": 6, "durationFrames": 50, "words": [
                       {"text": "Hello", "startFrame": 6, "endFrame": 30},
                       {"text": "preview", "startFrame": 30, "endFrame": 56}]}]}]}
    path = ws / "scene_plan.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


# ── spec and command (no ffmpeg run) ──────────────────────────────────────────

def _fake_plan(tmp_path, resolution=(1920, 1080)):
    from montage.plan import parse_plan
    raw = {"meta": {"title": "T", "resolution": list(resolution), "fps": 30, "durationFrames": 30},
           "tracks": [{"id": "v", "type": "video", "clips": [
               {"id": "a", "src": "./assets/s.png", "startFrame": 0, "durationFrames": 30,
                "motion": {"scale": [1.0, 1.2], "easing": "linear"}}]},
               {"id": "n", "type": "audio", "clips": [
                   {"id": "vo", "src": "./assets/v.wav", "startFrame": 0, "durationFrames": 30}]}]}
    (tmp_path / "assets").mkdir(exist_ok=True)
    (tmp_path / "assets" / "s.png").write_bytes(b"png")
    (tmp_path / "assets" / "v.wav").write_bytes(b"wav")

    def probe(path):
        from montage.probe import MediaInfo
        return MediaInfo(has_video=path.suffix == ".png", has_audio=path.suffix == ".wav",
                         duration=None if path.suffix == ".png" else 10.0)
    return parse_plan(raw, tmp_path, probe=probe)


def test_preview_spec_is_480p_ultrafast_at_the_plan_fps(tmp_path):
    spec = preview_spec(_fake_plan(tmp_path))
    assert (spec.profile, spec.width, spec.height, spec.fps) == (PREVIEW_PROFILE, 854, 480, 30)
    assert spec.ext == ".mp4" and spec.encoder == "libx264" and spec.supersample == 1
    assert spec.video_args[spec.video_args.index("-preset") + 1] == "ultrafast"
    assert (preview_spec(_fake_plan(tmp_path, (1080, 1920))).width,
            preview_spec(_fake_plan(tmp_path, (1080, 1920))).height) == (480, 854)
    assert PREVIEW_PROFILE not in __import__("montage.profiles").profiles.PROFILES  # not a delivery profile


def test_preview_command_is_one_pass_without_supersampling(tmp_path):
    plan = _fake_plan(tmp_path)
    spec = preview_spec(plan)
    cmd = render_mod.render_command(plan, tmp_path / "p.mp4", None, spec)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert f"loudnorm=I={render_mod.TARGET_LUFS}:TP={render_mod.TARGET_TP}:LRA={render_mod.TARGET_LRA}," in graph
    assert "measured_I" not in graph and "print_format" not in graph
    assert "scale=854:480:flags=lanczos" in graph  # zoompan input at 1x, not 4x
    # the still is read once and looped on the output grid, not re-decoded per frame by image2 -loop
    assert "-loop" not in cmd and "loop=loop=29:size=1,setpts=N/30/TB" in graph
    assert "[0:v]setpts=PTS-STARTPTS,scale=" in graph  # no fps filter ahead of the loop
    # the delivery render keeps its supersampling and two-pass loudness
    full = resolve(plan, "1080p30-h264")
    measured = {"input_i": -20, "input_tp": -3, "input_lra": 5, "input_thresh": -30, "target_offset": 0}
    graph = render_mod.render_command(plan, tmp_path / "f.mp4", measured, full)
    graph = graph[graph.index("-filter_complex") + 1]
    assert "scale=7680:4320:flags=lanczos" in graph and "linear=true" in graph
    assert "loop=loop=" not in graph


# ── real renders ──────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_preview_renders_an_unapproved_plan_with_captions_and_a_record(ws):
    path = _plan(ws)
    plan = load_plan(path)
    assert not approval.check(plan, path).approved
    out = render_mod.preview(plan, path)
    assert out == ws / "cache" / "preview.mp4"
    spec = preview_spec(plan)
    checks = run_qc(out, plan, spec, loudness=False)
    assert all(c.ok for c in checks), [f"{c.name}: {c.detail}" for c in checks if not c.ok]
    assert [c.name for c in checks] == ["frame count", "resolution", "fps", "video codec", "audio codec"]
    assert (ws / "cache" / "captions" / "preview-test-preview-480p.ass").is_file()
    doc = json.loads((ws / "cache" / "preview.mp4.source.json").read_text())
    assert doc["kind"] == "preview" and doc["artifact"] == {"path": "cache/preview.mp4", "sha256": review.sha256(out)}
    assert doc["plan"]["sha256"] == review.sha256(path)
    assert not (ws / "approval.json").exists()  # previewing approves nothing


@needs_ffmpeg
def test_approval_accepts_a_fresh_preview_without_a_contact_sheet(ws):
    path = _plan(ws)
    render_mod.preview(load_plan(path), path)
    approval.approve(load_plan(path), path, "watched it")
    doc = json.loads((ws / "approval.json").read_text())
    assert doc["preview"] == {"path": "cache/preview.mp4", "sha256": review.sha256(ws / "cache" / "preview.mp4")}
    assert "contactSheet" not in doc and approval.check(load_plan(path), path).approved

    raw = json.loads(path.read_text())
    raw["tracks"][0]["clips"][1]["fit"] = "contain"
    path.write_text(json.dumps(raw))
    with pytest.raises(approval.ApprovalError) as err:
        approval.approve(load_plan(path), path)
    msg = str(err.value)
    assert "scene_plan.json changed since preview.mp4 was made" in msg and "no contact_sheet.png" in msg
    assert "montage preview" in msg


@needs_ffmpeg
def test_preview_made_before_an_edit_mid_render_is_stale(ws, monkeypatch):
    path = _plan(ws)
    real_run = render_mod._run

    def edit_while_rendering(cmd, what, *rest):
        result = real_run(cmd, what, *rest)
        raw = json.loads(path.read_text())
        raw["meta"]["title"] = "Edited mid-render"
        path.write_text(json.dumps(raw))
        return result
    monkeypatch.setattr(render_mod, "_run", edit_while_rendering)
    render_mod.preview(load_plan(path), path)
    with pytest.raises(approval.ApprovalError, match="scene_plan.json changed since preview.mp4 was made"):
        approval.approve(load_plan(path), path)


@needs_ffmpeg
def test_cli_preview_then_approve_then_render(ws, capsys):
    path = _plan(ws)
    assert main(["preview", str(ws)]) == 0
    out = capsys.readouterr().out
    assert "preview: 854x480 @ 30 fps" in out and "captions burned in" in out
    assert "PASS  frame count" in out and "loudness" not in out.split("rendered:")[1]
    assert "next: watch it, then montage approve --workspace" in out
    assert main(["approve", "--workspace", str(ws)]) == 0
    assert main(["render", str(path), "-o", str(ws / "final.mp4"), "--skip-qc"]) == 0
    assert "approval: ok" in capsys.readouterr().out
    assert main(["preview", str(path), "--skip-qc"]) == 0
    assert "already approved" in capsys.readouterr().out


@needs_ffmpeg
def test_cli_preview_elsewhere_and_bad_output(ws, capsys):
    path = _plan(ws)
    assert main(["preview", str(path), "-o", str(ws / "p.mov")]) == 1
    assert "the preview is an .mp4 file" in capsys.readouterr().err
    assert main(["preview", str(path), "-o", str(ws / "elsewhere" / "p.mp4"), "--no-captions", "--skip-qc"]) == 0
    out = capsys.readouterr().out
    assert "captions off" in out and "approve only accepts the preview at" in out
    assert (ws / "elsewhere" / "p.mp4.source.json").is_file()
    assert main(["approve", "--workspace", str(ws)]) == 1  # not at the default path, so not counted


def test_cli_preview_invalid_plan_and_render_failure(tmp_path, ws, capsys, monkeypatch):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "scene_plan.json").write_text("{}")
    assert main(["preview", str(tmp_path / "bad")]) == 1
    assert "invalid plan" in capsys.readouterr().err

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not on PATH")
    _plan(ws)

    def boom(*a, **k):
        raise render_mod.RenderError("preview failed (ffmpeg exit 1)")
    monkeypatch.setattr(render_mod, "preview", boom)
    assert main(["preview", str(ws)]) == 2
    assert "preview failed" in capsys.readouterr().err


@needs_ffmpeg
def test_cli_preview_light_qc_failure_exits_3(ws, capsys, monkeypatch):
    from montage import cli, qc
    _plan(ws)
    monkeypatch.setattr(cli, "run_qc", lambda *a, **k: [qc.Check("frame count", False, "1 frames, plan says 120")])
    assert main(["preview", str(ws)]) == 3
    assert "FAIL  frame count" in capsys.readouterr().out
