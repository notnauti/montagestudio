---
date:    2026-09-28
project: MontageStudio
session: db198985-1b79-4812-8704-7470ea46b5a2
---
# Slice 1 validator, FFmpeg renderer and QC built

Fourth log from this session, after [[2026-09-28-slice-one-scene-plan-fields]].

## What I did
- `src/montage/`: `plan.py` (validator: every contract rule, unsupported fields rejected, all errors reported with paths, ffprobe source-length checks), `render.py` (two-pass: measure mix, then picture + linear loudnorm to −14 LUFS; clips normalised and cut to exact frame counts; xfade / concat; zoompan at 4× supersample), `qc.py` (frame count, resolution, fps, −14 ±1 LUFS, true peak ≤ −1 dBTP), `cli.py` (`montage validate|render`, exit 0/1/2/3). `pyproject.toml`, COMMANDS.md table.
- Tests: 39 passed — 35 validator (fake probe; the contract's minimal plan used verbatim) + 4 real-ffmpeg renders at 320×180 with mismatched sources (25 fps, square still, 22 kHz mono). Sabotage 3/3 caught (xfade offset +1 frame → frame count; loudnorm dropped → loudness; exact-length trim dropped → all renders).
- 1080p render of the contract's minimal plan: 17.9 s for 10 s, maxrss 785 MiB, QC 5/5 (300/300 frames, −13.9 LUFS). Contact sheet checked by eye: in-point (timecode 6.000 at frame 60 = 4 s in-point + 2 s), crossfade, 16:9 cover crop, zoom.
- Two test fixtures of mine were wrong (gap case pushed the clip past meta end; 150-frame crossfade was actually legal); fixed the tests, not the validator.
- Root `.gitignore`: `03 Projects/MontageStudio/workspace/` and `**/renders/` (verified with `git check-ignore`).
- Contract note's FFmpeg mapping updated to as-built; project CLAUDE.md folder tree and status updated.

## Decisions
- Slice 1 code is stdlib Python driving the ffmpeg/ffprobe CLIs; loudness is two-pass; every clip forced to an exact frame count.

## Environment finding
- First 1080p render died with `x264 malloc failed`: Windows commit charge was 60.0 / 61.2 GB, no single process above 1.6 GB (not investigated further, nothing killed). Render passed with `-threads 2 -filter_complex_threads 1`. The renderer itself does not limit threads; a reboot should clear it.

## Touched
- [[MontageStudio]]
- [[CLAUDE]] (project)
- [[COMMANDS]]
- [[(C) scene-plan-contract]]
- [[decisions]]
- [[.gitignore]]

## Next
- Slice 1 remaining: script from brief (one recipe) + Piper narration writing `scene_plan.json`.
- Reboot, then re-run a default-thread 1080p render to confirm the malloc failure was the machine.
- Pick the one brief for the first video. Harness first.
