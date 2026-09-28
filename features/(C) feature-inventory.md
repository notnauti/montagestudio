# Feature inventory

Source: [[00 Specifications/montagestudio_architecture_technical_guide]]. Status for all: **spec'd, not built, not validated with a user.** MVP column is empty on purpose — that cut is the first decision.

| Area | Feature | Tier | MVP? |
|---|---|---|---|
| Plan | Research + script from brief, pipeline recipes (explainer, promo, documentary) | 1 | |
| Audio | TTS with word timestamps (ElevenLabs / Piper) | 1 | |
| Assets | CLIP semantic scraping (Archive.org, NASA, Commons) | 1 | |
| Assets | Generative fallback (Runway, LTX-Video, Flux, Recraft, SDXL) | 1 | |
| Gate | Contact-sheet approval (terminal / web) | 1 | |
| Bridge | FFmpeg normalization (sample rate, aspect, pixel format) | 2 | |
| Bridge | Captions → subtitle markers | 2 | |
| Bridge | scene_plan → `project.ts` compiler + validator | 2 | |
| Studio | SolidJS canvas, timeline, inspector, bezier editor (Electron/web) | 3 | |
| Studio | Bidirectional canvas ⇄ code sync | 3 | |
| MCP | `timeline_split_clip`, `timeline_trim_boundary`, `timeline_apply_easing`, `timeline_retime_captions`, `asset_replace_broll`, `inspect_frame_context` | 3 | |
| Export | Headless CLI render, HW-accelerated export, ffprobe + EBU R128 QC | Export | |

## Notes
- `ambiguous` — MCP tool names disagree: §2 lists `trim_clip, shift_track, replace_broll, apply_easing_preset, remove_silence, balance_audio_lufs, retime_captions`; §5 lists the six above. `remove_silence` and `balance_audio_lufs` appear only in §2.
- `ambiguous` — Diffusion Studio and OpenMontage licenses and their actual feature sets are unchecked. The guide claims Apache 2.0 for MontageStudio; that only works if the upstream licenses allow it.
