"""`montage view <ws>`: a local, read-only page over the workspace's scene_plan.json.

The refine loop is an agent editing the plan through `montage mcp` and a person watching the
result here. The page shows the tracks as blocks on a frame scale (crossfades marked), the caption
words, real frames at a clip's first / middle / last frame, the 480p preview with a button that
re-renders it, and the script and storyboard gates with the reasons `approve` would give. It
polls the plan's sha256 and redraws when an edit lands.

Never writes the plan: there is no endpoint that could. The only action is re-rendering
cache/preview.mp4 through `render.preview`, the same call as `montage preview`.

Frames come from `frame_context.grab`, the renderer's own filter chains with captions burned in,
cached under cache/view/<key>/ where the key hashes the plan and every asset (review.fingerprint):
content, not mtime. The block layout is drawn in the browser and is labelled approximate; the
frames and the preview are what ffmpeg renders. One ffmpeg job runs at a time.

Stdlib http.server on 127.0.0.1 only (a random free port unless --port). A request whose Host is
not this server's address is refused (DNS rebinding), and so is a POST whose Origin is another
site; only the page, its JSON, frame PNGs and the preview are served, by fixed routes.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import approval as approval_mod
from . import normalize as normalize_mod
from . import review
from .plan import PlanError, load_plan
from .probe import ToolMissing

FRAME_WIDTH = 640


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def script_status(ws: Path) -> dict:
    """The script gate as `montage plan` sees it: none / hand-written (no record) / draft / approved / changed."""
    from . import script_gate
    if not (ws / "script.json").is_file():
        return {"state": "none"}
    record = script_gate.load_record(ws)
    if record is None:
        return {"state": "untracked", "detail": "no script_approval.json: hand-written or from before the gate"}
    try:
        from .brief import load_brief
        from .planner import _read_script
        brief = load_brief(ws / "brief.json")
        status = script_gate.status(ws, _read_script(ws / "script.json", brief))
    except Exception as exc:  # noqa: BLE001 -- a status line, never a reason the page fails
        return {"state": "unknown", "detail": str(exc).splitlines()[0]}
    state = "approved" if status.approved else "draft" if status.model_draft else "changed"
    return {"state": state, "writer": record.get("writer"), "approvedAt": record.get("approvedAt")}


class View:
    """Workspace state for the page, recomputed only when the plan or the preview changes."""

    def __init__(self, workspace: str | Path):
        self.ws = Path(workspace).resolve()
        self.plan_path = self.ws / "scene_plan.json"
        self.job = threading.Lock()        # one ffmpeg at a time: grabs and the preview
        self._snap_lock = threading.Lock()
        self._snap_key = None
        self._snap: dict = {}
        self._plan = None                  # the loaded plan behind the snapshot
        self._normalized = None            # and its normalized copy, made on the first frame request
        self.preview_busy = False
        self.preview_error: str | None = None

    def state_key(self) -> str:
        return f"{_sha(self.plan_path)}:{_mtime(review.preview_path(self.ws))}"

    def state(self) -> dict:
        return {"key": self.state_key(), "previewBusy": self.preview_busy, "previewError": self.preview_error}

    def snapshot(self) -> dict:
        key = self.state_key()
        with self._snap_lock:
            if key != self._snap_key:
                self._snap, self._plan = self._build(key)
                self._snap_key = key
            return self._snap

    def _build(self, key: str):
        snap = {"key": key, "workspace": self.ws.name}
        if not self.plan_path.is_file():
            return {**snap, "errors": [f"no scene_plan.json in {self.ws}; run montage plan"]}, None
        try:
            plan = load_plan(self.plan_path)
            raw = json.loads(self.plan_path.read_text(encoding="utf-8"))
        except PlanError as exc:
            return {**snap, "errors": exc.errors}, None
        except (ToolMissing, OSError, ValueError) as exc:
            return {**snap, "errors": [str(exc)]}, None
        fp = review.fingerprint(plan, self.plan_path)
        gate = approval_mod.check(plan, self.plan_path)
        prev = review.preview_path(self.ws)
        meta = plan.meta
        return ({**snap,
                "title": meta.title, "width": meta.width, "height": meta.height, "fps": meta.fps,
                "durationFrames": meta.duration_frames, "tracks": raw.get("tracks") or [],
                "frameKey": hashlib.sha256(json.dumps(fp, sort_keys=True).encode()).hexdigest()[:16],
                "storyboard": {"approved": gate.approved, "approvedAt": gate.approved_at,
                               "problems": list(gate.problems)},
                "preview": {"exists": prev.is_file(),
                            "stale": review.stale_reasons(prev, fp) if prev.is_file() else []},
                "script": script_status(self.ws)}, plan)

    def frame(self, n: int) -> Path:
        """frame n as a PNG (cached by plan + asset content); raises ValueError for a bad n."""
        from .frame_context import grab
        snap = self.snapshot()
        with self._snap_lock:   # the plan that belongs to this snapshot, not one an edit swapped in since
            plan = self._plan if self._snap is snap else None
        if "errors" in snap:
            raise ValueError("the plan is invalid; no frames until it validates")
        if plan is None:
            raise ValueError("the plan changed while this frame was asked for; the page will reload")
        if not 0 <= n < snap["durationFrames"]:
            raise ValueError(f"frame {n} is outside the plan (0..{snap['durationFrames'] - 1})")
        out = self.ws / "cache" / "view" / snap["frameKey"] / f"f_{n:06d}.png"
        if out.is_file():
            return out
        with self.job:
            if out.is_file():
                return out
            if self._normalized is None or self._normalized[0] != snap["frameKey"]:
                fixed = normalize_mod.apply(plan, normalize_mod.normalize_plan(plan, fix=True))
                self._normalized = (snap["frameKey"], fixed)
            grab(self._normalized[1], n, out, normalize=False, captions=True, max_width=FRAME_WIDTH)
        return out

    def start_preview(self) -> bool:
        """Re-render cache/preview.mp4 in the background; False when one is already running."""
        from .render import preview
        if self.preview_busy:
            return False
        self.preview_busy, self.preview_error = True, None

        def run():
            try:
                with self.job:
                    preview(load_plan(self.plan_path), self.plan_path)
            except Exception as exc:  # noqa: BLE001 -- reported on the page
                self.preview_error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            finally:
                self.preview_busy = False
        threading.Thread(target=run, daemon=True).start()
        return True


def _handler(view: View):
    from .view_page import PAGE

    class Handler(BaseHTTPRequestHandler):
        server_version = "montage-view"

        def log_message(self, *args):  # quiet: the terminal shows the URL only
            pass

        def _allowed_host(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            if not self._allowed_host():
                return self._send(403, b"forbidden host", "text/plain")
            url = urlsplit(self.path)
            if url.path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; "
                                                              "script-src 'unsafe-inline'; img-src 'self' blob:; "
                                                              "media-src 'self'"})
            if url.path == "/api/state":
                return self._json(view.state())
            if url.path == "/api/plan":
                return self._json(view.snapshot())
            if url.path == "/api/frame":
                try:
                    n = int(parse_qs(url.query).get("n", [""])[0])
                    out = view.frame(n)
                except ValueError as exc:
                    return self._send(400, str(exc).encode(), "text/plain; charset=utf-8")
                except Exception as exc:  # noqa: BLE001 -- the page shows the reason under the frame
                    return self._send(500, (str(exc).splitlines() or [type(exc).__name__])[0].encode(),
                                      "text/plain; charset=utf-8")
                return self._send(200, out.read_bytes(), "image/png")
            if url.path == "/preview.mp4":
                return self._file(review.preview_path(view.ws), "video/mp4")
            return self._send(404, b"not found", "text/plain")

        do_HEAD = do_GET

        def do_POST(self):
            if not self._allowed_host():
                return self._send(403, b"forbidden host", "text/plain")
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                return self._send(403, b"forbidden origin", "text/plain")
            if urlsplit(self.path).path == "/api/preview":
                return self._json({"started": view.start_preview()}, 202)
            return self._send(404, b"not found", "text/plain")

        def _file(self, path: Path, ctype: str):
            """The preview, with byte ranges so the player can seek."""
            try:
                data = path.read_bytes()
            except OSError:
                return self._send(404, b"no preview yet", "text/plain")
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
            if not m or (not m[1] and not m[2]):
                return self._send(200, data, ctype, {"Accept-Ranges": "bytes"})
            size = len(data)
            start, end = (int(m[1]), int(m[2]) if m[2] else size - 1) if m[1] else (size - int(m[2]), size - 1)
            if start >= size or start > end:
                return self._send(416, b"", ctype, {"Content-Range": f"bytes */{size}"})
            end = min(end, size - 1)
            return self._send(206, data[start:end + 1], ctype,
                              {"Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{size}"})

    return Handler


def make_server(workspace: str | Path, port: int = 0) -> ThreadingHTTPServer:
    view = View(workspace)
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(view))
    server.daemon_threads = True
    server.view = view
    return server


def serve(workspace: str | Path, port: int = 0) -> int:
    server = make_server(workspace, port)
    print(f"view: http://127.0.0.1:{server.server_address[1]}/  ({server.view.ws.name}; read-only; Ctrl+C stops)")
    print(f"  edit the plan with an agent over montage mcp \"{server.view.ws}\"; the page follows its sha256")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
