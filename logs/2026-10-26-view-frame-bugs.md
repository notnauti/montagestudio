---
date:    2026-10-26
project: MontageStudio
session: 1c7e4405-7d14-45e5-a158-ac0c21cd1e00
---
# montage view: frame-load regression test committed, half-written frame PNG fixed

## What I did
- `/eg-fix-bug` on "frames never load" in `montage view`: already fixed in `588cd2b` (`queueFrame` defers `pump` a tick so the `<img>` is attached). The regression test from that session was left uncommitted. Verified it bites: with `setTimeout(pump, 0)` → `pump()` patched into `view_page.PAGE` in memory, `test_overview_frames_actually_load` times out after 90 s; on HEAD 5/5 pass.
- `/eg-precommit-review` on `tests/cdp.py` + `tests/test_view_browser.py` + `COMMANDS.md`, 2 rounds. Fixed: `cdp.Chrome` polls `/json/list` for a page target (one-shot `next()` could raise StopIteration → opaque fixture RuntimeError); `Runtime.enable`/`Page.enable` inside the try so a failed setup closes Chrome; dropped a vacuous `#pvstatus` wait in the Re-render test. Committed `3a1beab`.
- Round 1 also found a real bug outside that diff: `grab` let ffmpeg write the PNG straight to the cached path, and `view.frame` checks `out.is_file()` outside the job lock, so a second tab / reload could get a half-written PNG, and a failed ffmpeg left a truncated file cached for good. Fixed in `frame_context.grab`: render to `<stem>.part.png`, `os.replace` onto `out` only on exit 0, delete the part on failure (covers `inspect_frame_context` too). Test `test_view.py::test_a_failed_grab_leaves_no_half_written_frame` fakes an ffmpeg that dies halfway; failed before, passes after. Review: 1 round, no findings. Committed `9e9d2dc`.
- Gate: 491 passed, 1 skipped (live net). Ruff n/a — not in MontageStudio's `.venv`, no config; the harness venv's ruff flags I001 only because it doesn't know `montage` is first-party (inferred).

## Decisions
- none

## Touched
- [[MontageStudio]]
- [[COMMANDS]]
- [[2026-10-26-montage-view]]

## Next
- Process note: the reviewer subagent swapped `frame_context.py` to HEAD to prove the test bites while the full suite ran → that run discarded and re-run. Don't run the gate concurrently with a reviewer.
- `grab`'s `subprocess.run` has no timeout — a hung ffmpeg holds `view.job` and stalls every frame. Not seen live; unfixed.
- Phase 0 is still the next real step (fund credit, rotate keys, JWST stats backfill, 4 briefs). This session was polish on a page no user has run.
