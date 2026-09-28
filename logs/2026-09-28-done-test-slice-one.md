---
date:    2026-09-28
project: MontageStudio
session: db198985-1b79-4812-8704-7470ea46b5a2
---
# Done test set, slice 1 cut, render path decided

Continues [[2026-09-28-project-scaffold-from-guide]] in the same session. That log's open drift question is now answered: MontageStudio runs alongside Agent Harness OS.

## What I did
- Recorded the answers: MontageStudio runs alongside Agent Harness OS; user is n@uti (own production studio); does not count toward the 60-day $5k / 100-user target. Project and root `CLAUDE.md` updated; root open-question comment removed.
- "Done means: Functioning Production Studio" made checkable with the adopted test: from one brief, n@uti gets a rendered video they would publish (plan → assets → gate → scene_plan → studio refine → render) without hand-editing generated code.
- Replaced the fill-in-done nudge with a harness-first nudge ("Is the harness moving this week?").
- Marked the MVP column in the feature inventory. Slice 1 = script from brief (one recipe), Piper TTS, FFmpeg render from `scene_plan.json` + QC. Compiler → slice 2 with the canvas. Other eight rows `later`.

## Decisions
- Slice 1 renders `scene_plan.json` straight through FFmpeg; the scene_plan → `project.ts` compiler moves to slice 2 (appended to `architecture/decisions.md`).

## Touched
- [[MontageStudio]]
- [[CLAUDE]] (project and root)
- [[(C) feature-inventory]]
- [[decisions]]

## Next
- Define the minimal `scene_plan.json` fields slice 1 needs (audio track + video/still track, frames, crossfade) and write the FFmpeg render for it.
- Pick the one recipe and the one brief for the first video.
- Keep the harness's next step moving; MontageStudio gets the leftover time.
