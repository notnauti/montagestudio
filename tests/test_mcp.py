"""The MCP server, driven as a real subprocess (`python -m montage mcp <ws>`) over stdio with
newline-delimited JSON-RPC 2.0, against a real workspace (synthetic media, real ffmpeg/ffprobe).
"""

import base64
import json
import os
import queue
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from montage import edit as edit_mod
from montage import retime
from montage.mcp_server import PROTOCOL_VERSIONS, check_args
from montage.plan import load_plan
from montage.qc import run_qc
from montage.render import render

SRC = Path(__file__).resolve().parents[1] / "src"
pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg/ffprobe not on PATH")
TOOLS = {"plan_summary", "timeline_split_clip", "timeline_trim_boundary", "timeline_apply_easing",
         "timeline_retime_captions", "asset_replace_broll", "inspect_frame_context"}


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("mcp_media")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=8",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "clip.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=400x400", "-frames:v", "1", str(d / "still.png"))
    _ffmpeg("-f", "lavfi", "-i", "color=c=0x2060c0:s=400x300", "-frames:v", "1", str(d / "blue.png"))
    # "speech": a tone at 0.5-1.0 s and 1.3-2.0 s, silence elsewhere (3 s, 22.05 kHz like Piper)
    _ffmpeg("-f", "lavfi", "-i",
            "aevalsrc=exprs='0.5*sin(2*PI*440*t)*(between(t,0.5,1.0)+between(t,1.3,2.0))':s=22050:d=3",
            str(d / "voice.wav"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=6", "-ac", "2", str(d / "music.wav"))
    return d


PLAN = {
    "meta": {"title": "MCP Test", "resolution": [320, 180], "fps": 30, "durationFrames": 150},
    "tracks": [
        {"id": "visuals", "type": "video", "clips": [
            {"id": "a", "src": "./assets/clip.mp4", "startFrame": 0, "durationFrames": 90, "sourceInFrame": 30,
             "transition": {"type": "crossfade", "durationFrames": 15}},
            {"id": "b", "src": "./assets/still.png", "startFrame": 75, "durationFrames": 75,
             "motion": {"scale": [1.0, 1.2], "easing": "linear"}}]},
        {"id": "narration", "type": "audio", "clips": [
            {"id": "vo", "src": "./assets/voice.wav", "startFrame": 15, "durationFrames": 90}]},
        {"id": "music", "type": "audio", "clips": [
            {"id": "bed", "src": "./assets/music.wav", "startFrame": 0, "durationFrames": 150, "volume": 0.2}]},
        # tone edges on the timeline: 15 + 0.5 s = frame 30, 45; 15 + 1.3 s = 54, 75.
        # The words are deliberately a few frames off.
        {"id": "captions", "type": "caption", "style": "highlight", "clips": [
            {"id": "cap_01", "startFrame": 15, "durationFrames": 90, "words": [
                {"text": "Hello", "startFrame": 33, "endFrame": 47},
                {"text": "world.", "startFrame": 52, "endFrame": 78}]}]},
    ],
}


@pytest.fixture
def ws(tmp_path, media):
    w = tmp_path / "ws"
    shutil.copytree(media, w / "assets")
    (w / "scene_plan.json").write_text(json.dumps(PLAN, indent=2), encoding="utf-8")
    return w


class Client:
    """A real MCP client session: one subprocess, JSON lines on its stdin/stdout."""

    def __init__(self, target):
        env = dict(os.environ, PYTHONPATH=str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        self.proc = subprocess.Popen([sys.executable, "-m", "montage", "mcp", str(target)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=env)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.next_id = 0

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send_raw(self, data: bytes):
        self.proc.stdin.write(data + b"\n")
        self.proc.stdin.flush()

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg).encode())

    def read(self, timeout=60):
        line = self.lines.get(timeout=timeout)
        assert line is not None, f"server exited: {self.proc.stderr.read().decode(errors='replace')}"
        return json.loads(line)

    def request(self, method, params=None):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg).encode())
        reply = self.read()
        assert reply["id"] == self.next_id, reply
        return reply

    def call(self, name, **arguments):
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        assert "result" in reply, reply
        return reply["result"]

    def init(self, version=PROTOCOL_VERSIONS[0]):
        r = self.request("initialize", {"protocolVersion": version, "capabilities": {},
                                        "clientInfo": {"name": "pytest", "version": "0"}})
        self.notify("notifications/initialized")
        return r

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                return self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                raise
        return self.proc.returncode


@pytest.fixture
def client(ws):
    c = Client(ws)
    c.init()
    yield c
    assert c.close() == 0


def text_of(result):
    return "\n".join(c["text"] for c in result["content"] if c["type"] == "text")


def plan_json(ws):
    return json.loads((ws / "scene_plan.json").read_text(encoding="utf-8"))


def clip(raw, cid):
    return next(c for t in raw["tracks"] for c in t["clips"] if c["id"] == cid)


# ── protocol ──────────────────────────────────────────────────────────────────

def test_initialize_negotiates_and_initialized_gets_no_reply(ws):
    c = Client(ws)
    r = c.init("2024-11-05")["result"]
    assert r["protocolVersion"] == "2024-11-05"
    assert r["serverInfo"]["name"] == "montage"
    assert set(r["capabilities"]) == {"tools", "resources"}
    # the initialized notification produced no line: the next line is the ping's reply
    assert c.request("ping") == {"jsonrpc": "2.0", "id": c.next_id, "result": {}}
    assert c.close() == 0

    c = Client(ws)
    assert c.init("1999-01-01")["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]
    assert c.close() == 0


def test_tools_list_and_schemas(client):
    tools = client.request("tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == TOOLS
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"
    split = next(t for t in tools if t["name"] == "timeline_split_clip")
    assert split["inputSchema"]["required"] == ["trackId", "clipId", "splitFrame"]


def test_json_rpc_errors(client):
    client.send_raw(b"{not json")
    r = client.read()
    assert r["id"] is None and r["error"]["code"] == -32700
    client.send_raw(json.dumps([{"jsonrpc": "2.0", "id": 9, "method": "ping"}]).encode())
    assert client.read()["error"]["code"] == -32600
    client.send_raw(json.dumps({"id": 5, "method": "ping"}).encode())  # no "jsonrpc"
    r = client.read()
    assert r["id"] == 5 and r["error"]["code"] == -32600
    assert client.request("no/such/method")["error"]["code"] == -32601
    assert client.request("tools/call", {"name": "nope", "arguments": {}})["error"]["code"] == -32602
    r = client.request("resources/read", {"uri": "montage://nope"})
    assert r["error"]["code"] == -32602
    client.notify("notifications/cancelled", {"requestId": 1})  # ignored, no reply
    client.notify("some/unknown/notification")
    assert "result" in client.request("ping")


def test_bad_arguments_are_tool_errors_the_agent_can_read(client, ws):
    before = (ws / "scene_plan.json").read_bytes()
    r = client.call("timeline_split_clip", trackId="visuals", clipId="a", splitFrame="40", bogus=1)
    assert r["isError"] is True
    t = text_of(r)
    assert "'splitFrame' must be integer" in t and "unknown argument 'bogus'" in t
    r = client.call("timeline_trim_boundary", clipId="a", edge="middle", deltaFrames=3)
    assert r["isError"] and "must be one of ['start', 'end']" in text_of(r)
    assert (ws / "scene_plan.json").read_bytes() == before


def test_resources_and_summary(client, ws):
    res = client.request("resources/list")["result"]["resources"]
    assert [r["uri"] for r in res] == ["montage://scene_plan.json"]
    contents = client.request("resources/read", {"uri": "montage://scene_plan.json"})["result"]["contents"]
    assert json.loads(contents[0]["text"]) == PLAN
    s = text_of(client.call("plan_summary"))
    assert "valid" in s and "'a' [0-90) ./assets/clip.mp4 in 30" in s and '"Hello world."' in s
    assert "approval: NOT current" in s


# ── edits ─────────────────────────────────────────────────────────────────────

def test_split_video_clip_continues_the_source_and_zoom(client, ws):
    r = client.call("timeline_split_clip", trackId="visuals", clipId="a", splitFrame=40)
    assert r["isError"] is False, text_of(r)
    raw = plan_json(ws)
    a, a2 = clip(raw, "a"), clip(raw, "a_b")
    assert (a["startFrame"], a["durationFrames"], a["sourceInFrame"]) == (0, 40, 30)
    assert "transition" not in a
    assert (a2["startFrame"], a2["durationFrames"], a2["sourceInFrame"]) == (40, 50, 70)
    assert a2["transition"] == {"type": "crossfade", "durationFrames": 15}
    load_plan(ws / "scene_plan.json")
    assert list((ws / "cache" / "plan-history").glob("scene_plan-*.json"))

    # linear zoom split: both halves reproduce the original per-frame values
    r = client.call("timeline_split_clip", trackId="visuals", clipId="b", splitFrame=100, newClipId="b_tail")
    assert r["isError"] is False, text_of(r)
    raw = plan_json(ws)
    b, tail = clip(raw, "b"), clip(raw, "b_tail")
    orig = PLAN["tracks"][0]["clips"][1]["motion"]
    for on in range(25):
        assert edit_mod.scale_at(b["motion"], on, 25) == pytest.approx(edit_mod.scale_at(orig, on, 75), abs=1e-5)
    for on in range(50):
        assert edit_mod.scale_at(tail["motion"], on, 50) == pytest.approx(
            edit_mod.scale_at(orig, on + 25, 75), abs=1e-5)
    assert "sourceInFrame" not in tail  # a still has no in-point


def test_split_caption_and_audio_clips(client, ws):
    r = client.call("timeline_split_clip", trackId="captions", clipId="cap_01", splitFrame=50)
    assert r["isError"] is False, text_of(r)
    raw = plan_json(ws)
    assert [w["text"] for w in clip(raw, "cap_01")["words"]] == ["Hello"]
    assert [w["text"] for w in clip(raw, "cap_01_b")["words"]] == ["world."]
    r = client.call("timeline_split_clip", trackId="narration", clipId="vo", splitFrame=60)
    assert r["isError"] is False, text_of(r)
    assert clip(plan_json(ws), "vo_b")["sourceInFrame"] == 45
    # wrong track and an out-of-range frame are refused
    r = client.call("timeline_split_clip", trackId="narration", clipId="a", splitFrame=10)
    assert r["isError"] and "is on track 'visuals'" in text_of(r)
    r = client.call("timeline_split_clip", trackId="visuals", clipId="a", splitFrame=0)
    assert r["isError"] and "strictly inside" in text_of(r)


def test_trim_video_is_a_roll_edit(client, ws):
    r = client.call("timeline_trim_boundary", clipId="a", edge="end", deltaFrames=-10)
    assert r["isError"] is False, text_of(r)
    raw = plan_json(ws)
    assert clip(raw, "a")["durationFrames"] == 80
    b = clip(raw, "b")
    assert (b["startFrame"], b["durationFrames"]) == (65, 85)
    assert raw["meta"]["durationFrames"] == 150 and load_plan(ws / "scene_plan.json")

    before = (ws / "scene_plan.json").read_bytes()
    for args, msg in [({"clipId": "a", "edge": "start", "deltaFrames": 5}, "first video clip"),
                      ({"clipId": "b", "edge": "end", "deltaFrames": 5}, "last video clip"),
                      ({"clipId": "b", "edge": "start", "deltaFrames": 75}, "not longer than the 15-frame crossfade"),
                      ({"clipId": "bed", "edge": "end", "deltaFrames": 1}, "past meta.durationFrames"),
                      ({"clipId": "vo", "edge": "start", "deltaFrames": -16}, "sourceInFrame -16"),
                      ({"clipId": "cap_01", "edge": "start", "deltaFrames": 20}, "words would fall outside")]:
        r = client.call("timeline_trim_boundary", **args)
        assert r["isError"], args
        assert msg in text_of(r), (args, text_of(r))
    assert (ws / "scene_plan.json").read_bytes() == before


def test_trim_audio_collision(client, ws):
    client.call("timeline_split_clip", trackId="narration", clipId="vo", splitFrame=60)
    r = client.call("timeline_trim_boundary", clipId="vo", edge="end", deltaFrames=3)
    assert r["isError"] and "collision: 'vo' would end at 63, inside 'vo_b'" in text_of(r)
    r = client.call("timeline_trim_boundary", clipId="vo", edge="end", deltaFrames=-5)
    assert r["isError"] is False
    r = client.call("timeline_trim_boundary", clipId="vo_b", edge="start", deltaFrames=-3)
    assert r["isError"] is False, text_of(r)
    vo_b = clip(plan_json(ws), "vo_b")
    assert (vo_b["startFrame"], vo_b["sourceInFrame"]) == (57, 42)  # content stays where it was


def test_apply_easing_scale_only(client, ws):
    r = client.call("timeline_apply_easing", clipId="a", property="scale",
                    keyframes=[{"frame": 0, "value": 1.0}, {"frame": 89, "value": 1.1}], curve="easeInOutCubic")
    assert r["isError"] is False, text_of(r)
    assert clip(plan_json(ws), "a")["motion"] == {"scale": [1.0, 1.1], "easing": "easeInOutCubic"}
    r = client.call("timeline_apply_easing", clipId="b", property="scale", keyframes=[1.2, 1.0],
                    curve=[0.65, 0, 0.35, 1])
    assert r["isError"] is False
    assert clip(plan_json(ws), "b")["motion"]["easing"] == "easeInOutCubic"

    before = (ws / "scene_plan.json").read_bytes()
    for args, msg in [
        ({"property": "opacity", "keyframes": [0, 1], "curve": "linear"}, "not in the scene_plan contract"),
        ({"property": "position", "keyframes": [0, 1], "curve": "linear"}, "Not widened"),
        ({"property": "scale", "keyframes": [{"frame": 30, "value": 1.0}, {"frame": 89, "value": 1.1}],
          "curve": "linear"}, "intermediate keyframes"),
        ({"property": "scale", "keyframes": [1.0, 1.1, 1.2], "curve": "linear"}, "exactly two keyframes"),
        ({"property": "scale", "keyframes": [1.0, 1.1], "curve": [0.1, 0.7, 0.1, 1]}, "Arbitrary bezier"),
        ({"property": "scale", "keyframes": [0.5, 1.1], "curve": "linear"}, "between 1.0 and"),
    ]:
        r = client.call("timeline_apply_easing", clipId="a", **args)
        assert r["isError"], args
        assert msg in text_of(r), (args, text_of(r))
    r = client.call("timeline_apply_easing", clipId="vo", property="scale", keyframes=[1, 1.1], curve="linear")
    assert r["isError"] and "video clips only" in text_of(r)
    assert (ws / "scene_plan.json").read_bytes() == before


def test_replace_broll_keeps_timing_and_copies_into_assets(client, ws, tmp_path, media):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    shutil.copy(media / "blue.png", outside / "new shot.png")
    uri = (outside / "new shot.png").as_uri()

    r = client.call("asset_replace_broll", clipId="a", sourceUri=uri, dryRun=True)
    assert r["isError"] is False and "dry run, NOT written" in text_of(r)
    assert not (ws / "assets" / "broll").exists() or not any((ws / "assets" / "broll").iterdir())
    assert clip(plan_json(ws), "a")["src"] == "./assets/clip.mp4"

    r = client.call("asset_replace_broll", clipId="a", sourceUri=uri)
    assert r["isError"] is False, text_of(r)
    a = clip(plan_json(ws), "a")
    assert a["src"] == "./assets/broll/new shot.png"
    assert (a["startFrame"], a["durationFrames"], a["transition"]) == (0, 90, PLAN["tracks"][0]["clips"][0]["transition"])
    assert "sourceInFrame" not in a  # a still
    r = client.call("asset_replace_broll", clipId="a", sourceUri="./assets/clip.mp4", sourceInFrame=10)
    assert r["isError"] is False
    assert clip(plan_json(ws), "a")["sourceInFrame"] == 10


def test_rejected_replace_leaves_plan_and_assets_unchanged(client, ws, tmp_path, media):
    before = (ws / "scene_plan.json").read_bytes()
    shutil.copy(media / "music.wav", tmp_path / "not_video.wav")
    r = client.call("asset_replace_broll", clipId="a", sourceUri=str(tmp_path / "not_video.wav"))
    assert r["isError"] and "has no video stream" in text_of(r) and "scene_plan.json unchanged" in text_of(r)
    assert not (ws / "assets" / "broll" / "not_video.wav").exists()  # the copy was removed
    r = client.call("asset_replace_broll", clipId="a", sourceUri="https://example.org/x.mp4")
    assert r["isError"] and "license gate" in text_of(r)
    r = client.call("asset_replace_broll", clipId="a", sourceUri="./assets/missing.mp4")
    assert r["isError"] and "file not found" in text_of(r)
    r = client.call("asset_replace_broll", clipId="vo", sourceUri="./assets/clip.mp4")
    assert r["isError"] and "video clips only" in text_of(r)
    assert (ws / "scene_plan.json").read_bytes() == before
    assert not (ws / "cache" / "plan-history").exists()  # nothing was ever written


def test_retime_captions_snaps_to_speech_edges(client, ws):
    r = client.call("timeline_retime_captions", captionsTrackId="captions", toleranceMs=150)
    assert r["isError"] is False, text_of(r)
    t = text_of(r)
    assert "against 'narration'" in t  # picked the voice track, not the music bed
    words = clip(plan_json(ws), "cap_01")["words"]
    assert [(w["startFrame"], w["endFrame"]) for w in words] == [(30, 45), (54, 75)]
    # tolerance too small to reach any edge: nothing moves
    (ws / "scene_plan.json").write_text(json.dumps(PLAN, indent=2), encoding="utf-8")
    r = client.call("timeline_retime_captions", captionsTrackId="captions", toleranceMs=20)
    assert "retimed 0 of 2 words" in text_of(r)
    r = client.call("timeline_retime_captions", captionsTrackId="visuals", toleranceMs=50)
    assert r["isError"] and "must be the caption track" in text_of(r)


def _png_size(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_inspect_frame_context(client, ws):
    r = client.call("inspect_frame_context", frameNumber=80)  # inside the a -> b crossfade
    assert r["isError"] is False, text_of(r)
    img = next(c for c in r["content"] if c["type"] == "image")
    assert img["mimeType"] == "image/png"
    assert _png_size(base64.b64decode(img["data"])) == (320, 180)
    t = text_of(r)
    assert "crossfade 'a' -> 'b', 33% of the way" in t
    assert "'a' ./assets/clip.mp4" in t and "source 3.667s" in t   # (30 + 80) / 30
    assert "zoom 1.0135" in t                                     # 1 + 0.2 * 5/74
    assert "'vo' on 'narration'" in t and "'bed' on 'music'" in t
    assert "current word 'world.' [52-78) spoken (ended 78); pause" in t
    t40 = text_of(client.call("inspect_frame_context", frameNumber=40))
    assert "page \"Hello world.\"; current word 'Hello' [33-47) being spoken" in t40
    assert "video:\n  - 'a'" in t40
    assert "no OCR" in t
    small = client.call("inspect_frame_context", frameNumber=80, maxWidth=160)
    img = next(c for c in small["content"] if c["type"] == "image")
    assert _png_size(base64.b64decode(img["data"])) == (160, 90)
    r = client.call("inspect_frame_context", frameNumber=150)
    assert r["isError"] and "outside the plan" in text_of(r)


def test_frame_grab_matches_the_render(ws):
    """inspect_frame_context's picture is the renderer's frame (same filter chains)."""
    from montage.frame_context import grab
    plan = load_plan(ws / "scene_plan.json")
    out = render(plan, ws / "full.mp4", normalize=False, captions=False)
    def psnr(a, b):
        res = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi", "psnr",
                              "-f", "null", "-"], capture_output=True, text=True)
        return float(res.stderr.split("average:")[1].split()[0])

    for f in (10, 80, 140):  # plain clip, inside the crossfade, zooming still
        png = grab(plan, f, ws / f"g{f}.png", normalize=False, captions=False)
        scores = {}
        for r in (f - 1, f, f + 1):
            ref = ws / f"r{r}.png"
            _ffmpeg("-i", str(out), "-vf", f"select=eq(n\\,{r})", "-frames:v", "1", str(ref))
            scores[r] = psnr(png, ref)
        # the H.264 render's own frame f is the closest match, and close in absolute terms
        assert max(scores, key=scores.get) == f and scores[f] > 30, (f, scores)


def test_edited_plan_renders_and_passes_qc(client, ws):
    assert not client.call("timeline_split_clip", trackId="visuals", clipId="b", splitFrame=110)["isError"]
    assert not client.call("timeline_apply_easing", clipId="b_b", property="scale", keyframes=[1.0, 1.15],
                           curve="easeInOutCubic")["isError"]
    assert not client.call("timeline_trim_boundary", clipId="a", edge="end", deltaFrames=6)["isError"]
    plan = load_plan(ws / "scene_plan.json")
    out = render(plan)
    checks = run_qc(out, plan)
    assert all(c.ok for c in checks), [f"{c.name}: {c.detail}" for c in checks if not c.ok]


def test_cli_refuses_a_missing_plan(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(SRC))
    r = subprocess.run([sys.executable, "-m", "montage", "mcp", str(tmp_path)], capture_output=True, text=True,
                       env=env, stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 1 and "no scene_plan.json" in r.stderr and r.stdout == ""


# ── in-process units ──────────────────────────────────────────────────────────

def test_check_args():
    schema = {"type": "object", "required": ["a"], "additionalProperties": False,
              "properties": {"a": {"type": "integer", "minimum": 0}, "b": {"type": "string", "enum": ["x"]}}}
    assert check_args(schema, {"a": 1, "b": "x"}) == []
    assert check_args(schema, {"a": True}) == ["'a' must be integer, got bool True"]
    assert check_args(schema, {"a": -1, "b": "y", "c": 0}) == [
        "'a' must be >= 0, got -1", "'b' must be one of ['x'], got 'y'", "unknown argument 'c' (accepted: a, b)"]
    assert check_args(schema, {}) == ["missing 'a'"]


def test_edges_need_a_real_gap():
    db = [-90] * 10 + [-20] * 5 + [-90] * 2 + [-20] * 5 + [-90] * 10  # 20 ms dip inside speech is not a gap
    e = retime.edges(db, 1000.0, -40)
    assert e.onsets == [1100.0] and e.offsets == [1220.0]


def test_file_uri_and_relative_paths(tmp_path):
    (tmp_path / "assets").mkdir()
    f = tmp_path / "assets" / "x y.png"
    f.write_bytes(b"")
    assert edit_mod.resolve_source(f.as_uri(), tmp_path) == f.resolve()
    assert edit_mod.resolve_source("./assets/x y.png", tmp_path) == f.resolve()
    with pytest.raises(edit_mod.EditError, match="unsupported scheme"):
        edit_mod.resolve_source("s3://bucket/x.png", tmp_path)
