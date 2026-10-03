---
date:    2026-10-03
project: MontageStudio
session: f5113093-d624-453c-b443-89f3cefa5cf6
---
# Guide gaps built: recipes, profiles, normalization, captions, sourcing + gate, MCP server

## What I did
- Gap audit of the technical guide against slice 1. Everything that runs on this machine got built. The `project.ts` compiler, SolidJS canvas, Electron and two-way sync stay deferred: there is no canvas to run them in. CLIP, generative fallback and ElevenLabs need torch or paid keys.
- Ultracode workflow `wf_caca4ef8-7b8`. The vault's one-agent-at-a-time rule held: four build agents ran one after another, then one adversarial reviewer.
  - **Recipes and CLI:** `pipelines/{explainer,documentary,promo}.json`, `montage plan --pipeline --topic --target-duration`, `montage init`.
  - **Profiles:** `--profile 1080p30-h264|720p30-h264|4k60-prores`, plus `--encoder` (hardware H.264 only after a probe encode passes). QC now checks the profile's codec and pixel format.
  - **Normalization:** 48 kHz audio, constant frame rate, yuv420p, square pixels, written into `cache/normalized/`. `montage normalize` reports; `--fix` fills the cache.
  - **Captions:** word timings come from Piper's own phoneme durations (stdlib ONNX graph edit, `align.py`). `captions.json`, a `caption` track in scene_plan, and ASS burn-in in highlight or pop style. Measured against detected silences: median 6 ms off, max 77 ms.
  - **Sourcing and gate:** `montage scrape` searches Commons, NASA and Archive.org with keyword ranking and downloads licensed assets only, recorded in `manifest.json`. Then `contact-sheet` → `approve` (sha256 pin of the plan and assets). Render refuses an unapproved or changed plan unless `--force`.
  - **MCP server:** `montage mcp <ws>`, stdlib JSON-RPC over stdio, the six §5 tools plus `plan_summary`. Validated, atomic writes; a rejected edit leaves the file byte-identical.
- The reviewer fixed 2 confirmed bugs, each with a regression test:
  - A `%` in the workspace path broke the contact sheet, and with it approval.
  - A badly shaped `approval.json` crashed with a traceback.
- Live end-to-end (no model call, hand-written script.json, real Piper). Steps: plan → contact sheet → approve → render → MCP trim → render refused → re-approve → re-render. QC passed 7/7 both times: 543/543 frames, 1920x1080@30, h264 yuv420p, aac, −14.0 LUFS, −1.5 dBTP.
- Suite: **244 passed, 1 skipped** (opt-in live network test, `MONTAGE_LIVE_NET=1`), confirmed by my own rerun. It was 62 at the start.

## Decisions
- Four entries dated 2026-10-02 in [[architecture/decisions]], written by the build agents during the run: render profiles, normalization and recipes (no shape change); captions (new `caption` track); sourcing and storyboard gate; MCP server before the canvas.
- **Correction to [[2026-09-28-slice-one-scene-plan-fields]] and the 2026-09-28 captions decision:** the claim "Piper emits no word timestamps" was wrong for piper-tts 1.8. Per-phoneme durations are available once the duration tensor is exposed as a graph output. No forced aligner was needed.

## Touched
- [[MontageStudio]]
- [[00 Specifications/montagestudio_architecture_technical_guide]]
- [[architecture/decisions]]
- [[data/(C) scene-plan-contract]]
- [[features/(C) feature-inventory]]
- [[pipeline/(C) pipeline-stages]]
- [[03 System/(C) mcp-config-example]]
- `pipelines/`, `src/montage/` (new modules: align, approval, captions, contact_sheet, edit, frame_context, mcp_server, net, normalize, pipeline, profiles, retime, scaffold, scrape, sources, subtitles), `tests/` (6 new test files)

## Next
- One real brief → published video with the **Claude** script writer. The writer has never run live; every test reused a hand-written script.json. "Who ran the last thing we shipped?" is still only n@uti.
- Needs permission: update `COMMANDS.md` (still says 62 tests, lacks init/scrape/contact-sheet/approve/normalize/mcp/--profile/--encoder/--no-captions/--force) and the project `CLAUDE.md` status line.
- Open, low-impact bugs:
  - A still image whose path contains `%d` is read as an image sequence.
  - `scrape` may crash with a traceback on IncompleteRead or ValueError.
  - A 1-frame clip can round to 0 frames when the profile's fps is below the plan's.
  - `--target-duration 0` with a brief is silently ignored.
- Known weaknesses:
  - Approval freshness uses the contact sheet's file time, not a hash.
  - Hardware encoders all failed their probe here; possibly the sandbox hides the GPU (ambiguous).
  - Keyword ranking picked weak shots live; the contact sheet catches this.
