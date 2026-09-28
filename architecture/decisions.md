# Architectural Decisions

What was decided and why. Newest first. `/save` appends here; `/resume` reads it.

## 2026-09-28 — Slice 1 renders scene_plan.json through FFmpeg; the compiler waits for slice 2
Slice 1 has no canvas, so compiling to `project.tsx` only to render it in a headless browser would pull the Diffusion Studio integration forward for no user-visible gain. FFmpeg alone gets a publishable video soonest. Cost: slice 1 does not exercise the compiler, so the `scene_plan.json` shape is only proven against FFmpeg until slice 2. The scene_plan stays the Tier 1 → Tier 2 contract either way.
