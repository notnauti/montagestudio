# MCP config example: `montage mcp`

Attach a coding agent to one workspace's `scene_plan.json`. The server is `montage mcp <workspace>` (stdio). See [[architecture/decisions]] (2026-10-02, MCP server) and [[data/(C) scene-plan-contract]] ("MCP edits").

## `.mcp.json` (Claude Code, project scope)

Put this in the root of the folder you run Claude Code from. Paths are absolute because the client may start the server from any directory. Replace `<workspace>` with the workspace folder that holds `scene_plan.json`.

```json
{
  "mcpServers": {
    "montage": {
      "type": "stdio",
      "command": "C:/Personal/Nauti11/Obsidian/Company_Brain_Package2.0/03 Projects/MontageStudio/.venv/Scripts/montage.exe",
      "args": ["mcp", "C:/Personal/Nauti11/Obsidian/Company_Brain_Package2.0/03 Projects/MontageStudio/workspace/<workspace>"]
    }
  }
}
```

The `montage.exe` entry point comes from the editable install in `.venv` (checked 2026-10-02: it resolves to `src/montage`). If that is ever missing, use the venv Python. Never use bare `python`, which is Anaconda.

```json
"command": "C:/Personal/Nauti11/Obsidian/Company_Brain_Package2.0/03 Projects/MontageStudio/.venv/Scripts/python.exe",
"args": ["-m", "montage", "mcp", "<workspace>"],
"env": { "PYTHONPATH": "C:/Personal/Nauti11/Obsidian/Company_Brain_Package2.0/03 Projects/MontageStudio/src" }
```

`inferred`: the `mcpServers` / `type` / `command` / `args` / `env` keys are Claude Code's `.mcp.json` format as I know it. They were not checked against current docs this session. `ffmpeg` and `ffprobe` must be on the PATH the client passes to the server.

## Tools

| Tool | What it does |
|---|---|
| `plan_summary` | Validation status, every track and clip, and approval status |
| `timeline_split_clip` | `trackId, clipId, splitFrame[, newClipId]`. The second part continues the source |
| `timeline_trim_boundary` | `clipId, edge, deltaFrames`. A roll edit on video; checks collisions on audio and captions |
| `timeline_apply_easing` | `clipId, property: scale, keyframes [from, to], curve linear\|easeInOutCubic`. `position` and `opacity` are refused |
| `timeline_retime_captions` | `captionsTrackId, toleranceMs[, audioTrackId, thresholdDb]`. Snaps word edges to speech on/offsets |
| `asset_replace_broll` | `clipId, sourceUri[, sourceInFrame]`. Takes a local path or file:// URI and keeps timing, fit, motion and transition |
| `inspect_frame_context` | `frameNumber[, captions, maxWidth=1280]`. Returns a PNG plus what is active at that frame (no OCR) |

Every edit tool takes `dryRun: true`. The resource `montage://scene_plan.json` is the plan itself.

## After editing

An edit changes `scene_plan.json`, so the storyboard approval no longer matches:

```
montage contact-sheet "<workspace>"
montage approve --workspace "<workspace>"
montage render "<workspace>/scene_plan.json"
```

Earlier versions of the plan are kept in `<workspace>/cache/plan-history/`.
