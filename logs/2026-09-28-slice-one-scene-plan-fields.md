---
date:    2026-09-28
project: MontageStudio
session: db198985-1b79-4812-8704-7470ea46b5a2
---
# Slice 1 scene_plan fields defined

Third log from this session, after [[2026-09-28-done-test-slice-one]].

## What I did
- Defined the slice 1 `scene_plan.json` fields in the contract note: meta (even resolution, integer fps, video track ends at `durationFrames`), one video track + 1..n audio tracks, per-clip `id`/`src`/`startFrame`/`durationFrames`/`sourceInFrame`, audio `volume`, video `fit`/`transition` (crossfade only)/`motion` (scale only, linear or easeInOutCubic), still detection by extension.
- Video-track timing rule: continuous single-layer sequence; next start = prev end − crossfade.
- Field → FFmpeg mapping (`inferred`), explicit out-of-slice list, and a minimal valid plan (JSON parses; checked against the timing rules by hand — not yet run through FFmpeg).
- Noted the guide's full example fails the end rule (video ends at 450, meta says 2700).
- Settled: QC target −14 LUFS; captions marked slice 2 in the feature inventory.

## Decisions
- scene_plan slice 1 = strict subset of the guide's shape + added `sourceInFrame`; unsupported fields rejected, not ignored.
- QC loudness −14 LUFS; captions deferred to slice 2 (need word timestamps).

## Touched
- [[MontageStudio]]
- [[(C) scene-plan-contract]]
- [[(C) feature-inventory]]
- [[decisions]]

## Next
- Write the validator for the slice 1 fields and the FFmpeg renderer; smoke-test with the minimal plan.
- Pick the one recipe and the one brief for the first video.
- Harness first; MontageStudio gets the leftover time.
