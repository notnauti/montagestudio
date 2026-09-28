# Architectural Decisions

What was decided and why. Newest first. `/save` appends here; `/resume` reads it.

## 2026-09-28 — QC loudness target −14 LUFS; captions deferred to slice 2
−14 LUFS integrated is the render's loudness target. Captions stay out of slice 1: Piper emits no word timestamps, so they need a forced aligner first, and adding one now would widen slice 1 past its first published video.

## 2026-09-28 — Slice 1 scene_plan is a strict subset of the guide's shape plus `sourceInFrame`; unsupported fields are rejected
Subset so the slice 2 compiler reads the same file; `sourceInFrame` added because the guide has no in-point and every clip would otherwise play from the source's first frame; reject rather than ignore so an unsupported Tier 1 output fails loudly instead of rendering wrong. One video track, crossfade only, scale-only motion. Full field table in `data/(C) scene-plan-contract.md`.

## 2026-09-28 — Slice 1 renders scene_plan.json through FFmpeg; the compiler waits for slice 2
Slice 1 has no canvas, so compiling to `project.tsx` only to render it in a headless browser would pull the Diffusion Studio integration forward for no user-visible gain. FFmpeg alone gets a publishable video soonest. Cost: slice 1 does not exercise the compiler, so the `scene_plan.json` shape is only proven against FFmpeg until slice 2. The scene_plan stays the Tier 1 → Tier 2 contract either way.
