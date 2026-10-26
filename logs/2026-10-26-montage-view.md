---
date:    2026-10-26
project: MontageStudio
session: 483abb08-ac1e-4cb5-aecc-0c6c2e866c04
---
# `montage view`: the read-only refine page (PRD FR-5–FR-9); every PRD code item now built

## What I did
- Flagged once that the PRD puts the web view after video 4; n@uti asked for it, so I built it.
- New `src/montage/view.py`:
  - stdlib server on 127.0.0.1, fixed routes, Host and Origin checks, preview served with byte ranges
  - frames via `frame_context.grab`, cached by plan + asset content and pinned to their snapshot
  - one ffmpeg job at a time; preview re-render in the background
- New `src/montage/view_page.py`: self-contained page with timeline blocks and crossfades, a clip detail panel (frames, motion, caption words), click-a-lane frames, the preview and its Re-render button, gate badges with reasons, and a 1.5 s poll of the plan's sha256.
- `montage view <ws> [--port N]` added to `cli.py`; `view` added to production's untimed commands.
- Tests: `tests/test_view.py` (18: page and CSP, plan snapshot, foreign Host refused, cross-site POST refused, fixed routes only, real-ffmpeg frame grab cached and never re-grabbed, bad frame numbers, an edit changes the key and an invalid plan blocks frames, preview byte ranges, script status, CLI without a plan). Full suite 485 passed / 1 skipped. `COMMANDS.md` updated.
- Live in Chrome on a scratchpad copy of `jwst-unfold`:
  - gates matched the records (storyboard approved 2026-10-05, preview fresh, script has no gate record)
  - shot_06 detail right (frames 1375–1661, scale 1→1.04 easeInOutCubic, 18-frame crossfade, caption words)
  - a title edit to the plan redrew the page; storyboard and preview went stale with the right reasons
  - Re-render made the preview fresh again, while the storyboard stayed unapproved
- Bug found only live: no frame ever loaded, because `queueFrame` ran the queue before the image was attached and the job was dropped. Fixed (next tick). The page JS has no automated test, so this class of bug would not fail the suite.
- Frames take about 3–4 s each through the real renderer, the first ~7 s (it normalizes once).

## Decisions
- Read-only page, frames from the renderer cached by content, only action = re-render preview, approval stays CLI. See [[architecture/decisions]].

## Touched
- [[MontageStudio]]
- [[00 Specifications/(C) 2026-10-26-montagestudio-production-cadence-mvp-prd]]
- [[architecture/decisions]]
- [[COMMANDS]]
- [[2026-10-26-suggested-asset-queries]]
- `src/montage/view.py` (new), `src/montage/view_page.py` (new), `src/montage/cli.py`, `src/montage/production.py`, `tests/test_view.py` (new)

## Next
- Every PRD code item is built. Nothing more on the cadence target depends on code.
- n@uti, Phase 0: fund the API credit, rotate the 2026-10-04 keys, write 4 briefs, answer G9 (hours/week) and G10 (spend ceiling). Then video 2, measured, using the writer, `--suggest-queries`, `--vision`, `view` + `mcp`, `publish-pack --draft`. Five Claude-backed paths have never run live.
- JWST stats backfill: `montage stats workspace/jwst-unfold --url https://youtu.be/uicRn1QgrsU --day 7 --views N` (or `--day 28` after 2026-11-05).
- Optional later: a browser test for the page JS (would have caught today's frame bug).
