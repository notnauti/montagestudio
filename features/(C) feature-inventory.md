# Feature inventory

Source: [[00 Specifications/montagestudio_architecture_technical_guide]]. Status for all: **spec'd, not built, not validated with a user.** MVP column marked 2026-09-28: **Slice 1** = brief → script → Piper voiceover → local assets → `scene_plan.json` → headless render + QC. Everything else is `later` until a video from slice 1 is published.

| Area | Feature | Tier | MVP? |
|---|---|---|---|
| Plan | Research + script from brief, pipeline recipes (explainer, promo, documentary) | 1 | **Slice 1** — script from brief, one recipe, no research. Built 2026-09-28 (`montage plan`) |
| Audio | TTS with word timestamps (ElevenLabs / Piper) | 1 | **Slice 1** — Piper only. Built 2026-09-28 |
| Assets | CLIP semantic scraping (Archive.org, NASA, Commons) | 1 | later |
| Assets | Generative fallback (Runway, LTX-Video, Flux, Recraft, SDXL) | 1 | later |
| Gate | Contact-sheet approval (terminal / web) | 1 | later |
| Bridge | FFmpeg normalization (sample rate, aspect, pixel format) | 2 | later |
| Bridge | Captions → subtitle markers | 2 | **Slice 2** — needs word timestamps (forced aligner) |
| Bridge | scene_plan → `project.ts` compiler + validator | 2 | **Slice 2** — with the canvas |
| Studio | SolidJS canvas, timeline, inspector, bezier editor (Electron/web) | 3 | later |
| Studio | Bidirectional canvas ⇄ code sync | 3 | later |
| MCP | `timeline_split_clip`, `timeline_trim_boundary`, `timeline_apply_easing`, `timeline_retime_captions`, `asset_replace_broll`, `inspect_frame_context` | 3 | later |
| Export | Headless CLI render, HW-accelerated export, ffprobe + EBU R128 QC | Export | **Slice 1** — FFmpeg render straight from `scene_plan.json` + QC; no HW accel. Built 2026-09-28 |

## Notes
- `ambiguous` — MCP tool names disagree: §2 lists `trim_clip, shift_track, replace_broll, apply_easing_preset, remove_silence, balance_audio_lufs, retime_captions`; §5 lists the six above. `remove_silence` and `balance_audio_lufs` appear only in §2.
- `ambiguous` — Diffusion Studio and OpenMontage licenses and their actual feature sets are unchecked. The guide claims Apache 2.0 for MontageStudio; that only works if the upstream licenses allow it.
- Decided 2026-09-28: slice 1 renders `scene_plan.json` straight through FFmpeg; the compiler moves to slice 2 with the canvas. See [[architecture/decisions]].
- `inferred` — slice 1 passes the done test except for "studio refine"; the canvas is slice 2.
