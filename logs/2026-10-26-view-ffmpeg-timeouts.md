---
date:    2026-10-26
project: MontageStudio
session: 1c7e4405-7d14-45e5-a158-ac0c21cd1e00
---
# montage view: ffmpeg timeouts on frame grabs and the preview render

Continues [[2026-10-26-view-frame-bugs]] in the same session.

## What I did
- `grab` timeout: `frame_context.GRAB_TIMEOUT = 120` passed to ffmpeg; on expiry `run()` kills it, the `.part.png` is removed, `FrameError("frame grab timed out …")` → 500 on the page, `view.job` released. Also covers `inspect_frame_context` and publish-pack thumbnails. Test `test_view.py::test_a_hung_grab_times_out_and_frees_the_next_frame` (sleep stub for ffmpeg, timeout 1 s): hung 32 s before, passes after. Review 2 rounds — fixed: dropped `raising=False` on the `GRAB_TIMEOUT` monkeypatch; rebutted then: preview path out of scope. Commit `b44e157`, pushed.
- Preview timeout (that rebuttal, fixed next): `render._run(cmd, what, timeout=None)` → kill + `RenderError` on expiry; only `preview` passes `preview_timeout(plan) = max(120, 10 × duration s)` (10× assumes a 480p animatic renders ~real time — inferred, not timed). Tests: `test_a_hung_preview_times_out_and_frees_the_frames`, `test_the_preview_timeout_scales_with_the_video`; `test_preview.py`'s `_run` stub forwards the new arg (reviewer confirmed it still checks the same thing). Review 1 round, no findings. Commit `1303227`, pushed.
- Gate after each: 492/1, then 494/1. Gate run only after the reviewer finished (last log's process note applied).

## Decisions
- none (timeouts are implementation detail; scene_plan contract untouched)

## Touched
- [[MontageStudio]]
- [[COMMANDS]]
- [[2026-10-26-view-frame-bugs]]

## Next
- Correction to [[2026-10-26-view-frame-bugs]] → Next: "`grab`'s `subprocess.run` has no timeout … unfixed" — fixed in `b44e157`.
- `montage render` (final 1080p render + loudness pass) still has no timeout, by choice: it doesn't hold `view.job` and its normal running time hasn't been measured. Time the next real render before picking a limit.
- Phase 0 is still the next real step (fund credit, rotate keys, JWST stats backfill, 4 briefs). Three robustness fixes on a page no user has run yet — no more polish here until video 2 is underway.
