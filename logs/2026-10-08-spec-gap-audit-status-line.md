---
date:    2026-10-08
project: MontageStudio
session: dde94c89-fab3-4ff5-9d1b-62226680dfac
---
# Spec gap audit against the logs; CLAUDE.md status line updated

## What I did
- Read `logs/index.md`, `architecture/decisions.md`, the feature inventory, the 10-03 and 10-04 logs, and checked the technical guide for what is still not built. No code was touched.
  - **Tier 2 (bridge):** the `scene_plan.json` → `project.ts` compiler, the bridge validator, and mapping captions to subtitle markers in `project.ts`.
  - **Tier 3 (studio):** the SolidJS canvas, timeline, property panels and bezier editor; two-way canvas ⇄ code sync; `montage open`; live MCP sync with a canvas.
  - **MCP:** `remove_silence` and `balance_audio_lufs` are not built. Easing handles `scale` only. `inspect_frame_context` has no OCR.
  - **Tier 1:** generative fallback, CLIP ranking, ElevenLabs, the research engine (web / Perplexity / YouTube transcripts), a web preview for the gate, and the cost check (§7).
  - **Export:** the WebCodecs / VideoToolbox canvas exporter. All hardware encoders failed their probe here (ambiguous: the sandbox may hide the GPU).
  - **Built but never run live:** the Claude writer and `scrape --vision` (no API credit).
- Flagged two issues (inferred):
  - The done test includes "studio refine", so it cannot pass until the canvas (Tier 3) exists.
  - The JWST video has sat ready since 10-04, waiting only on n@uti's manual upload.
- Updated the `## Current Status` block in the project `CLAUDE.md` (with permission): Last updated is now 2026-10-08, the stale "JWST needs re-plan → … → publish-pack" text is replaced with the 10-04 results, and the blocker is now the manual YouTube upload. The test count "403 passed / 1 skipped" was kept from the logs, not rerun.

## Decisions
- none

## Touched
- [[MontageStudio]]
- [[00 Specifications/montagestudio_architecture_technical_guide]]
- [[features/(C) feature-inventory]]
- [[architecture/decisions]]
- [[2026-10-04-jwst-replan-publish-pack]]
- [[2026-10-03-guide-gaps-built]]
- `03 Projects/MontageStudio/CLAUDE.md` (status block; the commit also carries n@uti's earlier uncommitted cut to the Folder Structure section)

## Next
- n@uti: watch the 1080p JWST render, then upload it in YouTube Studio with `workspace/jwst-unfold/publish/youtube.md`. That is the first published video.
- Decide whether "studio refine" stays in the done test before any Tier 2/3 work starts.
- Still open from 10-04: fund the API key and rotate both pasted keys; publish-pack follow-ups (description, chapter titles, tags, disclosure).
