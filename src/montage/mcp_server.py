"""`montage mcp <workspace>`: an MCP server over stdio that edits the workspace's scene_plan.json.

Guide §2 Tier 3 / §5. There is no canvas yet, so the tools edit the contract file directly; every
write is re-validated and atomic (edit.py), and a rejected edit leaves the file unchanged.

Transport (MCP stdio, as I understand the spec -- inferred, not fetched this session): JSON-RPC 2.0
messages, one per line, UTF-8, on stdin / stdout; nothing else may be written to stdout, so
logging goes to stderr and sys.stdout is pointed at stderr while the server runs. Requests get
exactly one response; notifications (no "id") never get one.

Methods: initialize, notifications/initialized, ping, tools/list, tools/call, resources/list,
resources/read. Errors: -32700 parse error, -32600 invalid request, -32601 method not found,
-32602 invalid params (unknown tool or resource, bad params shape), -32603 internal error.
A tool that runs but fails (an edit the validator rejects, a bad argument value) answers with
`isError: true` and the reasons as text, so the agent can read them and correct itself.

Stdlib, not the official `mcp` SDK: the SDK pulls pydantic, anyio, httpx and starlette into a
project that is stdlib + ffmpeg by decision, for a protocol surface of seven methods.
"""

from __future__ import annotations

import base64
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable

from . import approval as approval_mod
from . import edit as edit_mod
from . import frame_context
from . import probe as probe_mod
from .plan import PlanError
from .retime import DEFAULT_THRESHOLD_DB, retime_captions

SERVER_NAME = "montage"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first
PLAN_URI = "montage://scene_plan.json"

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603)

INSTRUCTIONS = (
    "Edits one MontageStudio workspace's scene_plan.json (the Tier 1 -> Tier 2 contract). Frames are "
    "timeline frames at meta.fps. Every edit is re-validated; a rejected edit changes nothing. Pass "
    "dryRun: true to check an edit without writing. Call plan_summary first to see clip ids. Any edit "
    "invalidates the storyboard approval: run `montage contact-sheet` (or `montage preview`) and "
    "`montage approve` before rendering."
)


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    run: Callable[[dict], list[dict]]


def _text(s: str) -> dict:
    return {"type": "text", "text": s}


DRY_RUN = {"type": "boolean", "description": "validate and report the edit without writing it", "default": False}


class MontageServer:
    def __init__(self, plan_path: str | Path, probe: Callable = probe_mod.probe):
        self.file = edit_mod.PlanFile(plan_path, probe=probe)
        self.initialized = False
        self.protocol_version: str | None = None
        self.tools = {t.name: t for t in self._tools()}

    # ── tools ─────────────────────────────────────────────────────────────────

    def _tools(self) -> list[Tool]:
        return [
            Tool("plan_summary",
                 "Read the scene plan: validation status, meta, every track and clip with its frames, "
                 "and whether the storyboard approval still covers it.",
                 {"type": "object", "properties": {}, "additionalProperties": False},
                 self._plan_summary),
            Tool("timeline_split_clip",
                 "Split a video, audio or caption clip into two at a timeline frame. Nothing else moves; the "
                 "second part continues the source (sourceInFrame advances), keeps the outgoing crossfade, and "
                 "a zoom keeps its values at the cut. Caption words go to the part they start in.",
                 {"type": "object", "required": ["trackId", "clipId", "splitFrame"], "additionalProperties": False,
                  "properties": {
                      "trackId": {"type": "string"}, "clipId": {"type": "string"},
                      "splitFrame": {"type": "integer", "description": "timeline frame strictly inside the clip; "
                                                                       "the second part starts here"},
                      "newClipId": {"type": "string", "description": "id for the second part (default <clipId>_b)"},
                      "dryRun": DRY_RUN}},
                 self._split),
            Tool("timeline_trim_boundary",
                 "Move a clip's start or end by deltaFrames. On the video track (gapless, one layer) this is a "
                 "roll edit: the cut moves and the neighbouring clip absorbs it, so nothing downstream shifts. "
                 "Audio and caption clips move the edge in place; collisions with neighbours, the timeline "
                 "ends, the source's in-point and caption words are checked, and the edit is refused if any fail.",
                 {"type": "object", "required": ["clipId", "edge", "deltaFrames"], "additionalProperties": False,
                  "properties": {
                      "clipId": {"type": "string"}, "edge": {"type": "string", "enum": ["start", "end"]},
                      "deltaFrames": {"type": "integer", "description": "positive = later, negative = earlier"},
                      "dryRun": DRY_RUN}},
                 self._trim),
            Tool("timeline_apply_easing",
                 "Set a video clip's zoom (motion.scale): two keyframes at the clip's first and last frame and "
                 "a curve. Only 'scale' is in the scene_plan contract; 'position' and 'opacity' are refused. "
                 "To zoom over part of a clip, split it first.",
                 {"type": "object", "required": ["clipId", "property", "keyframes", "curve"],
                  "additionalProperties": False,
                  "properties": {
                      "clipId": {"type": "string"},
                      "property": {"type": "string", "enum": ["scale", "position", "opacity"],
                                   "description": "only 'scale' is supported by the contract"},
                      "keyframes": {"type": "array", "minItems": 2, "maxItems": 2,
                                    "description": "[from, to] or [{frame: 0, value}, {frame: durationFrames-1, "
                                                   "value}]; clip-relative frames; values 1.0-10.0",
                                    "items": {"anyOf": [{"type": "number"}, {
                                        "type": "object", "required": ["value"],
                                        "properties": {"frame": {"type": "integer"}, "value": {"type": "number"}}}]}},
                      "curve": {"anyOf": [{"type": "string", "enum": ["linear", "easeInOutCubic"]},
                                          {"type": "array", "items": {"type": "number"}, "minItems": 4,
                                           "maxItems": 4, "description": "cubic-bezier points; only "
                                           "[0,0,1,1] (linear) and [0.65,0,0.35,1] (easeInOutCubic)"}]},
                      "dryRun": DRY_RUN}},
                 self._easing),
            Tool("timeline_retime_captions",
                 "Snap caption word starts / ends to speech onsets / offsets in the narration audio (10 ms RMS "
                 "envelope, silence below thresholdDb) when one lies within toleranceMs. Words inside connected "
                 "speech have no edge nearby and stay put.",
                 {"type": "object", "required": ["captionsTrackId", "toleranceMs"], "additionalProperties": False,
                  "properties": {
                      "captionsTrackId": {"type": "string"},
                      "toleranceMs": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000},
                      "audioTrackId": {"type": "string", "description": "the voice track (default: the audio "
                                                                        "track whose clips the captions start on)"},
                      "thresholdDb": {"type": "number", "default": DEFAULT_THRESHOLD_DB},
                      "dryRun": DRY_RUN}},
                 self._retime),
            Tool("asset_replace_broll",
                 "Swap a video clip's media for another file, keeping its timing, fit, motion and transition. "
                 "sourceUri: a workspace-relative path, an absolute path or a file:// URI; a file outside "
                 "assets/ is copied into assets/broll/. Remote URLs are refused (use montage scrape, which "
                 "checks licenses).",
                 {"type": "object", "required": ["clipId", "sourceUri"], "additionalProperties": False,
                  "properties": {
                      "clipId": {"type": "string"}, "sourceUri": {"type": "string"},
                      "sourceInFrame": {"type": "integer", "minimum": 0,
                                        "description": "in-point in the new source (default: keep the current one, "
                                                       "or 0 if the new source is too short for it)"},
                      "dryRun": DRY_RUN}},
                 self._replace),
            Tool("inspect_frame_context",
                 "Render one timeline frame to PNG (the renderer's own filter chain, captions burned in) and "
                 "describe it from the plan: active video clips with source time, zoom and crossfade progress, "
                 "active audio, the caption page and current word. No OCR or vision model.",
                 {"type": "object", "required": ["frameNumber"], "additionalProperties": False,
                  "properties": {"frameNumber": {"type": "integer", "minimum": 0},
                                 "maxWidth": {"type": "integer", "minimum": 64, "default": 1280,
                                              "description": "downscale the returned image to at most this "
                                                            "width (the plan's own size if smaller)"},
                                 "captions": {"type": "boolean", "default": True,
                                              "description": "burn in the caption track"}}},
                 self._inspect),
        ]

    def _edit(self, args: dict, fn: Callable[[dict], edit_mod.EditResult]) -> list[dict]:
        dry = bool(args.get("dryRun", False))
        result, plan = self.file.apply(fn, dry_run=dry)
        lines = [("dry run, NOT written: " if dry else "written: ") + f"{self.file.path.name} (validated)"]
        lines += [f"  {c}" for c in result.changes]
        lines += [f"  note: {n}" for n in result.notes]
        if not dry:
            lines.append("  the storyboard approval no longer matches; before rendering run "
                         f"montage contact-sheet \"{plan.workspace}\" and montage approve --workspace "
                         f"\"{plan.workspace}\"")
        return [_text("\n".join(lines))]

    def _split(self, a: dict) -> list[dict]:
        return self._edit(a, lambda raw: edit_mod.split_clip(raw, a["trackId"], a["clipId"], a["splitFrame"],
                                                             a.get("newClipId")))

    def _trim(self, a: dict) -> list[dict]:
        return self._edit(a, lambda raw: edit_mod.trim_boundary(raw, a["clipId"], a["edge"], a["deltaFrames"]))

    def _easing(self, a: dict) -> list[dict]:
        return self._edit(a, lambda raw: edit_mod.apply_easing(raw, a["clipId"], a["property"], a["keyframes"],
                                                               a["curve"]))

    def _retime(self, a: dict) -> list[dict]:
        return self._edit(a, lambda raw: retime_captions(raw, self.file.workspace, a["captionsTrackId"],
                                                         a["toleranceMs"], a.get("audioTrackId"),
                                                         a.get("thresholdDb", DEFAULT_THRESHOLD_DB)))

    def _replace(self, a: dict) -> list[dict]:
        return self._edit(a, lambda raw: edit_mod.replace_broll(raw, self.file.workspace, a["clipId"],
                                                                a["sourceUri"], a.get("sourceInFrame"),
                                                                probe=self.file.probe))

    def _inspect(self, a: dict) -> list[dict]:
        frame = a["frameNumber"]
        plan = self.file.load()
        png = frame_context.grab(plan, frame, captions=a.get("captions", True),
                                 max_width=a.get("maxWidth", 1280))
        data = base64.b64encode(png.read_bytes()).decode("ascii")
        text = frame_context.describe(plan, frame) + f"\nimage: {png}"
        return [{"type": "image", "data": data, "mimeType": "image/png"}, _text(text)]

    def _plan_summary(self, _a: dict) -> list[dict]:
        _, raw = self.file.read()
        try:
            plan = self.file.load()
            status = "valid"
        except PlanError as exc:
            plan = None
            status = "INVALID:\n" + "\n".join(f"  - {e}" for e in exc.errors)
        lines = [f"{self.file.path} -- {status}"]
        meta = raw.get("meta") or {}
        fps = meta.get("fps")
        lines.append(f"meta: '{meta.get('title')}' {meta.get('resolution')} @ {fps} fps, "
                     f"{meta.get('durationFrames')} frames"
                     + (f" ({meta['durationFrames'] / fps:.2f}s)" if isinstance(fps, int) and fps and
                        isinstance(meta.get('durationFrames'), int) else ""))
        for t in raw.get("tracks") or []:
            extra = f", style {t.get('style', 'highlight')}" if t.get("type") == "caption" else ""
            lines.append(f"track '{t.get('id')}' ({t.get('type')}{extra}), {len(t.get('clips') or [])} clips:")
            for c in t.get("clips") or []:
                s, d = c.get("startFrame"), c.get("durationFrames")
                end = s + d if isinstance(s, int) and isinstance(d, int) else "?"
                bits = [f"  - '{c.get('id')}' [{s}-{end})"]
                if "src" in c:
                    bits.append(c["src"])
                if c.get("sourceInFrame"):
                    bits.append(f"in {c['sourceInFrame']}")
                if "volume" in c:
                    bits.append(f"volume {c['volume']}")
                if "fit" in c:
                    bits.append(f"fit {c['fit']}")
                if "transition" in c:
                    bits.append(f"crossfade {c['transition'].get('durationFrames')}")
                if "motion" in c:
                    bits.append(f"zoom {c['motion'].get('scale')} {c['motion'].get('easing')}")
                if "words" in c:
                    bits.append(f"\"{' '.join(w.get('text', '') for w in c['words'])}\"")
                lines.append(" ".join(bits))
        if plan is not None:
            st = approval_mod.check(plan, self.file.path)
            lines.append("approval: " + (f"ok (approved {st.approved_at})" if st.approved
                                         else "NOT current -- " + "; ".join(st.problems)))
        return [_text("\n".join(lines))]

    # ── protocol ──────────────────────────────────────────────────────────────

    def _initialize(self, params: dict) -> dict:
        asked = params.get("protocolVersion")
        if not isinstance(asked, str):
            raise RpcError(INVALID_PARAMS, "initialize: protocolVersion must be a string")
        self.protocol_version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {"protocolVersion": self.protocol_version,
                "capabilities": {"tools": {"listChanged": False},
                                 "resources": {"subscribe": False, "listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS}

    def _tools_list(self, _params: dict) -> dict:
        return {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.schema}
                          for t in self.tools.values()]}

    def _tools_call(self, params: dict) -> dict:
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str):
            raise RpcError(INVALID_PARAMS, "tools/call: 'name' must be a string")
        tool = self.tools.get(name)
        if tool is None:
            raise RpcError(INVALID_PARAMS, f"unknown tool '{name}'; available: {', '.join(self.tools)}")
        if not isinstance(args, dict):
            raise RpcError(INVALID_PARAMS, "tools/call: 'arguments' must be an object")
        problems = check_args(tool.schema, args)
        if problems:
            return {"content": [_text("invalid arguments:\n" + "\n".join(f"  - {p}" for p in problems))],
                    "isError": True}
        try:
            return {"content": tool.run(args), "isError": False}
        except (edit_mod.EditError, PlanError) as exc:
            return {"content": [_text("\n".join(exc.errors))], "isError": True}
        except (frame_context.FrameError, probe_mod.ToolMissing, OSError) as exc:
            return {"content": [_text(str(exc))], "isError": True}

    def _resources_list(self, _params: dict) -> dict:
        return {"resources": [{"uri": PLAN_URI, "name": "scene_plan.json", "mimeType": "application/json",
                               "description": f"the scene plan this server edits ({self.file.path})"}]}

    def _resources_read(self, params: dict) -> dict:
        if params.get("uri") != PLAN_URI:
            raise RpcError(INVALID_PARAMS, f"unknown resource {params.get('uri')!r}; available: {PLAN_URI}")
        data, _ = self.file.read()
        return {"contents": [{"uri": PLAN_URI, "mimeType": "application/json", "text": data.decode("utf-8")}]}

    def handle(self, msg: Any) -> dict | None:
        """One decoded message -> the response to write, or None (notifications)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            mid = msg.get("id") if isinstance(msg, dict) else None
            return _error(mid if _valid_id(mid) else None, INVALID_REQUEST,
                          "not a JSON-RPC 2.0 message" + (" (batches are not supported)" if isinstance(msg, list)
                                                          else ""))
        method = msg.get("method")
        is_request = "id" in msg
        if not isinstance(method, str):
            if is_request and ("result" in msg or "error" in msg):
                return None  # a response to something we never sent; ignore
            return _error(msg.get("id") if _valid_id(msg.get("id")) else None, INVALID_REQUEST,
                          "missing 'method'")
        if not is_request:
            if method == "notifications/initialized":
                self.initialized = True
            return None  # notifications (initialized, cancelled, ...) never get a response
        mid = msg["id"]
        if not _valid_id(mid):
            return _error(None, INVALID_REQUEST, "id must be a string or an integer")
        params = msg.get("params", {})
        if not isinstance(params, dict):
            return _error(mid, INVALID_PARAMS, "params must be an object")
        handlers = {"initialize": self._initialize, "ping": lambda p: {},
                    "tools/list": self._tools_list, "tools/call": self._tools_call,
                    "resources/list": self._resources_list, "resources/read": self._resources_read}
        handler = handlers.get(method)
        if handler is None:
            return _error(mid, METHOD_NOT_FOUND, f"method not found: {method}")
        try:
            return {"jsonrpc": "2.0", "id": mid, "result": handler(params)}
        except RpcError as exc:
            return _error(mid, exc.code, exc.message, exc.data)
        except Exception as exc:  # noqa: BLE001 -- a server bug must not kill the session
            traceback.print_exc(file=sys.stderr)
            return _error(mid, INTERNAL_ERROR, f"internal error: {type(exc).__name__}: {exc}")

    def serve(self, stdin: BinaryIO, stdout: BinaryIO) -> int:
        while True:
            line = stdin.readline()
            if not line:
                return 0
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                reply = _error(None, PARSE_ERROR, f"parse error: {exc}")
            else:
                reply = self.handle(msg)
            if reply is not None:
                stdout.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
                stdout.flush()


def _valid_id(mid: Any) -> bool:
    return isinstance(mid, str) or (isinstance(mid, int) and not isinstance(mid, bool))


def _error(mid: Any, code: int, message: str, data: Any = None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": err}


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def _type_ok(value: Any, typ: str) -> bool:
    if typ in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[typ])


def check_args(schema: dict, args: dict) -> list[str]:
    """The subset of JSON Schema the tool schemas use: required, additionalProperties, top-level
    type / enum / minimum. Nested shapes (keyframes, curve) are checked by the edit itself."""
    props = schema.get("properties", {})
    out = [f"missing '{k}'" for k in schema.get("required", []) if k not in args]
    for k, v in args.items():
        spec = props.get(k)
        if spec is None:
            if schema.get("additionalProperties") is False:
                out.append(f"unknown argument '{k}' (accepted: {', '.join(props)})")
            continue
        typ = spec.get("type")
        if typ and not _type_ok(v, typ):
            out.append(f"'{k}' must be {typ}, got {type(v).__name__} {v!r}")
            continue
        if "enum" in spec and v not in spec["enum"]:
            out.append(f"'{k}' must be one of {spec['enum']}, got {v!r}")
        if "minimum" in spec and _type_ok(v, "number") and v < spec["minimum"]:
            out.append(f"'{k}' must be >= {spec['minimum']}, got {v!r}")
    return out


def run_stdio(plan_path: str | Path) -> int:
    """Serve on the real stdin/stdout. Anything a library prints goes to stderr, not the protocol."""
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    server = MontageServer(plan_path)
    print(f"montage mcp: serving {server.file.path} over stdio", file=sys.stderr)
    try:
        return server.serve(sys.stdin.buffer, out)
    except KeyboardInterrupt:
        return 0
