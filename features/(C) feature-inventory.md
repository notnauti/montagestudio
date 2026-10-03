# Feature inventory

Source: [[00 Specifications/montagestudio_architecture_technical_guide]]. Status for all: **spec'd, not built, not validated with a user.** MVP column marked 2026-09-28: **Slice 1** = brief → script → Piper voiceover → local assets → `scene_plan.json` → headless render + QC. Everything else is `later` until a video from slice 1 is published.

| Area | Feature | Tier | MVP? |
|---|---|---|---|
| Plan | Research + script from brief, pipeline recipes (explainer, promo, documentary) | 1 | **Slice 1** — script from brief, no research. Built 2026-09-28 (`montage plan`); recipes explainer / documentary / promo as `pipelines/*.json`, `montage plan --pipeline --topic --target-duration` and `montage init` built 2026-10-02 |
| Audio | TTS with word timestamps (ElevenLabs / Piper) | 1 | **Slice 1** — Piper only. Built 2026-09-28; word timestamps from Piper's own phoneme durations + `captions.json` built 2026-10-02 |
| Assets | CLIP semantic scraping (Archive.org, NASA, Commons) | 1 | Built 2026-10-02 **without CLIP**: `montage scrape <ws>` fetches the brief's missing visuals from Commons / NASA / Archive.org, keyword search + transparent lexical ranking, licensed only (PD / CC0 / CC BY / CC BY-SA), size-capped, provenance in `manifest.json`. CLIP ranking still later (torch) |
| Assets | Generative fallback (Runway, LTX-Video, Flux, Recraft, SDXL) | 1 | later |
| Gate | Contact-sheet approval (terminal / web) | 1 | Built 2026-10-02 (terminal): `contact_sheet.png` (auto at end of `plan`, or `montage contact-sheet`), `montage approve --workspace` pins plan + assets by sha256 in `approval.json`, `render` refuses unapproved / changed plans (exit 1) unless `--force`. Web preview later |
| Bridge | FFmpeg normalization (sample rate, aspect, pixel format) | 2 | Built 2026-10-02 ahead of the compiler: `montage normalize`, wired into render (48 kHz, CFR, yuv420p, square pixels; aspect reported only) |
| Bridge | Captions → subtitle markers | 2 | Built 2026-10-02 for the FFmpeg path: `caption` track in scene_plan, burned in as ASS (highlight / pop per recipe), `render --no-captions`. No aligner model needed. Compiler mapping still slice 2 |
| Bridge | scene_plan → `project.ts` compiler + validator | 2 | **Slice 2** — with the canvas |
| Studio | SolidJS canvas, timeline, inspector, bezier editor (Electron/web) | 3 | later |
| Studio | Bidirectional canvas ⇄ code sync | 3 | later |
| MCP | `timeline_split_clip`, `timeline_trim_boundary`, `timeline_apply_easing`, `timeline_retime_captions`, `asset_replace_broll`, `inspect_frame_context` | 3 | Built 2026-10-02 without the canvas: `montage mcp <ws>` (stdlib stdio JSON-RPC) edits scene_plan.json, every write re-validated + atomic; plus `plan_summary` and a `montage://scene_plan.json` resource. Easing is `scale` only (contract); `remove_silence` / `balance_audio_lufs` (§2 only) not built. Config: [[03 System/(C) mcp-config-example]] |
| Export | Headless CLI render, HW-accelerated export, ffprobe + EBU R128 QC | Export | **Slice 1** — FFmpeg render straight from `scene_plan.json` + QC; Built 2026-09-28. Profiles (1080p30-h264, 720p30-h264, 4k60-prores) and probe-gated NVENC / QSV / AMF with libx264 fallback built 2026-10-02 |

## Notes
- `ambiguous` — MCP tool names disagree: §2 lists `trim_clip, shift_track, replace_broll, apply_easing_preset, remove_silence, balance_audio_lufs, retime_captions`; §5 lists the six above. `remove_silence` and `balance_audio_lufs` appear only in §2.
- `ambiguous` — Diffusion Studio and OpenMontage licenses and their actual feature sets are unchecked. The guide claims Apache 2.0 for MontageStudio; that only works if the upstream licenses allow it.
- Decided 2026-09-28: slice 1 renders `scene_plan.json` straight through FFmpeg; the compiler moves to slice 2 with the canvas. See [[architecture/decisions]].
- `inferred` — slice 1 passes the done test except for "studio refine"; the canvas is slice 2.
