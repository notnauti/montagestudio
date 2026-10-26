---
date:    2026-10-26
project: MontageStudio
session: 483abb08-ac1e-4cb5-aecc-0c6c2e866c04
---
# publish-pack: thumbnail candidates, `--draft`, one Claude model (PRD FR-10, FR-11, FR-4)

## What I did
- Flagged first: the PRD puts publish-pack fixes in Phase 2, after video 2 ships; zero measured videos exist and Phase 0 has not moved. n@uti asked to continue, so I built it.
- Correction to [[2026-10-08-jwst-first-video-published]] "Next": "Fix publish-pack: suggest a thumbnail frame after the crossfade". The pack never suggested a thumbnail in code ([[2026-10-08-thumbnail-bug-correction]] already said the 10-04 frame was a hand pick), so FR-10 is new code, not a fix.
- **FR-10:** `publish.thumbnail_frames` picks up to 3 frames, one per source file, ≥6 frames clear of any cut or crossfade. `render_thumbnails` grabs them through `frame_context.grab` (captions off) as 1280 px JPEGs under 2 MB in `publish/thumb_candidates/`. `with_thumbnails` adds a `## Thumbnail` section; a failed grab is listed, never fatal.
  - Live on a scratchpad copy of `jwst-unfold`: picks at frames 459 / 1247 / 1764 (shots 02 / 05 / 07), all inside clear spans of the 18-frame fades; 3 JPEGs (84–381 KB) in 16.3 s. I looked at two: clean single frames, no double exposure, no captions. Not the hand-chosen gold mirror (spread by time, not judged on looks).
- **FR-11:** new `src/montage/drafts.py` (`ClaudeDrafter`, structured JSON, effort low, server-side fallback), behind `publish-pack --draft` (opt-in, exit 2 without credentials). It drafts title / description intro / chapter titles (fed the narration of every merged beat) / tags. Limits are validated and the whole draft is rejected on any breach. On failure the pack keeps the brief's text and exits 2.
- The existing-pack check moved into the CLI ahead of the model call and the grabs (a refused overwrite costs nothing and keeps the old candidates).
- **FR-4:** `script.CLAUDE_MODEL = "claude-opus-5-5"`, used by vision and drafts too. The writer was `claude-opus-5`.
- Tests: `tests/test_thumbnails.py` (7, one with real ffmpeg media), `tests/test_drafts.py` (19, fake client / drafter, no billed calls), one model assertion in `test_planning.py`. Full suite 450 passed / 1 skipped. `COMMANDS.md` updated.

## Decisions
- `--draft` opt-in (not automatic as the PRD said), thumbnails never fatal, one model constant. See [[architecture/decisions]].

## Touched
- [[MontageStudio]]
- [[00 Specifications/(C) 2026-10-26-montagestudio-production-cadence-mvp-prd]]
- [[architecture/decisions]]
- [[COMMANDS]]
- [[2026-10-26-production-measurement]]
- [[2026-10-08-jwst-first-video-published]]
- `src/montage/publish.py`, `src/montage/drafts.py` (new), `src/montage/cli.py`, `src/montage/script.py`, `src/montage/vision.py`, `tests/test_thumbnails.py` (new), `tests/test_drafts.py` (new), `tests/test_planning.py`

## Next
- `--draft`, the Claude writer and `--vision` are all mocked only. All three need the API credit funded (Phase 0).
- PRD order: ship video 2 now; FR-2 (writer-proposed asset queries) is the last Phase 2 item, and it waits until video 2 shows sourcing still costs time.
- JWST stats: `montage stats workspace/jwst-unfold --url https://youtu.be/uicRn1QgrsU --day 7 --views N` (or `--day 28` after 2026-11-05).
