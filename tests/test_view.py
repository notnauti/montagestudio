"""`montage view`: read-only page over scene_plan.json; host/origin checks, frames, preview ranges."""

import http.client
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from montage import cli, view as view_mod
from montage.probe import probe as real_probe
from test_thumbnails import _load

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
COLORS = ["red", "green", "blue"]


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    d = tmp_path_factory.mktemp("viewws")
    (d / "assets").mkdir()
    run = lambda *a: subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *a], check=True)  # noqa: E731
    for c in COLORS:
        run("-f", "lavfi", "-i", f"color=c={c}:s=640x360", "-frames:v", "1", str(d / "assets" / f"{c}.png"))
    run("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "10", str(d / "assets" / "bed.wav"))
    _load(d, [f"./assets/{c}.png" for c in COLORS], probe=real_probe)
    return d


@pytest.fixture
def server(ws):
    srv = view_mod.make_server(ws)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _req(srv, method, path, headers=None, host=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    conn.request(method, path, headers=h)
    res = conn.getresponse()
    body = res.read()
    conn.close()
    return res, body


def test_page_and_plan(server, ws):
    res, body = _req(server, "GET", "/")
    assert res.status == 200 and b"montage view" in body
    assert "default-src 'self'" in res.getheader("Content-Security-Policy")
    assert b"innerHTML" not in body                      # plan text only ever goes through textContent
    res, body = _req(server, "GET", "/api/plan")
    snap = json.loads(body)
    assert snap["fps"] == 30 and [c["id"] for c in snap["tracks"][0]["clips"]] == ["shot_01", "shot_02", "shot_03"]
    assert snap["storyboard"]["approved"] is False and "no approval.json" in snap["storyboard"]["problems"][0]
    assert snap["preview"] == {"exists": False, "stale": []} and snap["script"] == {"state": "none"}


@pytest.mark.parametrize("host", ["evil.example:80", "attacker.test", None])
def test_a_foreign_host_is_refused(server, host):
    port = server.server_address[1]
    res, _ = _req(server, "GET", "/api/plan", host=host or f"127.0.0.1:{port + 1}")
    assert res.status == 403


def test_a_cross_site_post_is_refused(server, monkeypatch):
    started = []
    monkeypatch.setattr(server.view, "start_preview", lambda: started.append(1) or True)
    res, _ = _req(server, "POST", "/api/preview", {"Origin": "https://evil.example"})
    assert res.status == 403 and not started
    port = server.server_address[1]
    res, body = _req(server, "POST", "/api/preview", {"Origin": f"http://127.0.0.1:{port}"})
    assert res.status == 202 and json.loads(body) == {"started": True} and started


@pytest.mark.parametrize("path", ["/../brief.json", "/assets/red.png", "/scene_plan.json", "/api/nope"])
def test_only_fixed_routes_are_served(server, path):
    assert _req(server, "GET", path)[0].status == 404


@needs_ffmpeg
def test_frames_are_rendered_once_and_cached_by_content(server, ws, monkeypatch):
    before = (ws / "scene_plan.json").read_bytes()
    res, body = _req(server, "GET", "/api/frame?n=20")
    assert res.status == 200 and res.getheader("Content-Type") == "image/png" and body[:4] == b"\x89PNG"
    key = server.view.snapshot()["frameKey"]
    assert (ws / "cache" / "view" / key / "f_000020.png").is_file()
    import montage.frame_context as fc
    monkeypatch.setattr(fc, "grab", lambda *a, **k: pytest.fail("a cached frame is not grabbed again"))
    assert _req(server, "GET", "/api/frame?n=20")[0].status == 200
    assert (ws / "scene_plan.json").read_bytes() == before            # read-only


@needs_ffmpeg
def test_a_failed_grab_leaves_no_half_written_frame(server, ws, monkeypatch):
    # ffmpeg writes the PNG as it goes: the served path must never see it, and a failure must not cache it
    import montage.frame_context as fc
    real_run, written = fc.subprocess.run, []

    def dies_halfway(cmd, **kw):
        if cmd[0] != fc.tool("ffmpeg"):
            return real_run(cmd, **kw)
        written.append(Path(cmd[-1]))
        written[-1].write_bytes(b"\x89PNG\r\n\x1a\n half")
        return subprocess.CompletedProcess(cmd, 1, "", "killed")

    monkeypatch.setattr(fc.subprocess, "run", dies_halfway)
    out = ws / "cache" / "view" / server.view.snapshot()["frameKey"] / "f_000021.png"
    assert _req(server, "GET", "/api/frame?n=21")[0].status == 500
    assert written and written[0] != out and not out.exists() and not written[0].exists()
    monkeypatch.undo()
    res, body = _req(server, "GET", "/api/frame?n=21")
    assert res.status == 200 and len(body) > 100 and out.read_bytes() == body


@needs_ffmpeg
def test_a_hung_grab_times_out_and_frees_the_next_frame(server, ws, monkeypatch):
    # a stuck ffmpeg held view.job forever, so every frame after it stalled
    import montage.frame_context as fc
    monkeypatch.setattr(fc, "GRAB_TIMEOUT", 1)
    monkeypatch.setattr(fc, "grab_command", lambda *a: [sys.executable, "-c", "import time; time.sleep(30)"])
    t0 = time.monotonic()
    res, body = _req(server, "GET", "/api/frame?n=22")
    assert res.status == 500 and b"timed out" in body and time.monotonic() - t0 < 10
    out = ws / "cache" / "view" / server.view.snapshot()["frameKey"] / "f_000022.png"
    assert not out.exists() and not out.with_name(out.stem + ".part.png").exists()
    monkeypatch.undo()
    res, body = _req(server, "GET", "/api/frame?n=22")
    assert res.status == 200 and body[:4] == b"\x89PNG"


@needs_ffmpeg
def test_a_hung_preview_times_out_and_frees_the_frames(server, monkeypatch):
    # Re-render holds view.job like a frame grab does; a stuck preview ffmpeg stalled every frame after it
    import montage.render as render_mod
    monkeypatch.setattr(render_mod, "preview_timeout", lambda plan: 1)
    monkeypatch.setattr(render_mod, "render_command",
                        lambda *a, **k: [sys.executable, "-c", "import time; time.sleep(30)"])
    t0 = time.monotonic()
    assert server.view.start_preview()
    while server.view.preview_busy and time.monotonic() - t0 < 15:
        time.sleep(0.1)
    assert not server.view.preview_busy and "timed out" in (server.view.preview_error or "")
    assert time.monotonic() - t0 < 10
    monkeypatch.undo()
    res, body = _req(server, "GET", "/api/frame?n=23")
    assert res.status == 200 and body[:4] == b"\x89PNG"


def test_the_preview_timeout_scales_with_the_video():
    from montage import render as render_mod
    plan = lambda frames: type("P", (), {"meta": type("M", (), {"duration_frames": frames, "fps": 30})()})()  # noqa: E731
    assert render_mod.preview_timeout(plan(300)) == 120                 # 10 s video -> the floor
    assert render_mod.preview_timeout(plan(30 * 600)) == 6000           # 10 min video -> 10x


@pytest.mark.parametrize("q", ["n=-1", "n=99999", "n=x", ""])
def test_bad_frame_numbers(server, q):
    res, body = _req(server, "GET", f"/api/frame?{q}")
    assert res.status == 400 and body


def test_an_edit_changes_the_state_key_and_the_snapshot(server, ws):
    plan_path = ws / "scene_plan.json"
    original = plan_path.read_text(encoding="utf-8")
    try:
        k1 = json.loads(_req(server, "GET", "/api/state")[1])["key"]
        doc = json.loads(original)
        doc["meta"]["title"] = "Edited by an agent"
        plan_path.write_text(json.dumps(doc), encoding="utf-8")
        k2 = json.loads(_req(server, "GET", "/api/state")[1])["key"]
        assert k1 != k2 and json.loads(_req(server, "GET", "/api/plan")[1])["title"] == "Edited by an agent"
        plan_path.write_text("{broken", encoding="utf-8")
        snap = json.loads(_req(server, "GET", "/api/plan")[1])
        assert snap["errors"] and _req(server, "GET", "/api/frame?n=1")[0].status == 400
    finally:
        plan_path.write_text(original, encoding="utf-8")


def test_preview_is_served_with_byte_ranges(server, ws):
    assert _req(server, "GET", "/preview.mp4")[0].status == 404
    p = ws / "cache" / "preview.mp4"
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(b"0123456789")
    try:
        res, body = _req(server, "GET", "/preview.mp4")
        assert res.status == 200 and body == b"0123456789" and res.getheader("Accept-Ranges") == "bytes"
        res, body = _req(server, "GET", "/preview.mp4", {"Range": "bytes=2-5"})
        assert (res.status, body, res.getheader("Content-Range")) == (206, b"2345", "bytes 2-5/10")
        res, body = _req(server, "GET", "/preview.mp4", {"Range": "bytes=-3"})
        assert (res.status, body) == (206, b"789")
        assert _req(server, "GET", "/preview.mp4", {"Range": "bytes=20-"})[0].status == 416
        snap = json.loads(_req(server, "GET", "/api/plan")[1])
        assert snap["preview"]["exists"] and "no record" in snap["preview"]["stale"][0]
    finally:
        p.unlink()


def test_script_status(tmp_path):
    assert view_mod.script_status(tmp_path) == {"state": "none"}
    (tmp_path / "script.json").write_text("{}")
    assert view_mod.script_status(tmp_path)["state"] == "untracked"
    (tmp_path / "script_approval.json").write_text(json.dumps({"draftSha256": "a", "writer": "claude-x"}))
    assert view_mod.script_status(tmp_path)["state"] == "unknown"     # no brief.json to read the script with


def test_cli_view_needs_a_plan(tmp_path, capsys):
    assert cli.main(["view", str(tmp_path)]) == 1
    assert "no scene_plan.json" in capsys.readouterr().err
