---
date:    2026-10-26
project: MontageStudio
session: 483abb08-ac1e-4cb5-aecc-0c6c2e866c04
---
# Production measurement built (cadence PRD Phase 1, FR-13–FR-17)

## What I did
- Read logs/index and decisions: nothing since the cadence PRD. Started the code half of Phase 1. The live Claude writer and `--vision` runs (FR-1/FR-3) still wait on n@uti funding the API credit.
- New `src/montage/production.py`, wired into `cli.py`:
  - every command run on a workspace appends to `<ws>/production_log.jsonl` (cmd, argv, start, seconds, exit, incl. `exception` / `interrupted`); a logging failure never fails the command
  - hand-edit counts are derived from existing records: script draft vs approved hash, `pickedRank`, plan-history snapshots, manifest entries scrape did not write, forced renders / `--rewrite` / approvals from the log
  - `publish-pack --hands-on-min N` (asked on a TTY) writes `publish/production_summary.json`; `mcp` and `stats` time is kept out of the total
  - new `montage stats <ws> [--hands-on-min] [--url] [--day 7|28 --views N [--avg-view-seconds S]]`; it never writes the publish pack, so JWST can be backfilled safely
  - ledger: `logs/production-ledger.jsonl`, one row per video, versioned; written from a real command line only, or a test that sets `MONTAGE_LEDGER`
- Found during `/save`: two existing tests (`validate does-not-exist.json`, `plan b.json`) wrote a `production_log.jsonl` into the project root, because a bare relative path resolves to the cwd. Fixed: the log is written only into a directory with `brief.json` or `scene_plan.json`, plus a regression test. Stray file deleted.
- `tests/test_production.py`: 21 tests. Full suite 424 passed / 1 skipped (was 403/1). `COMMANDS.md` updated (run order, publish-pack row, stats row, count).

## Decisions
- Production measurement design and ledger location (`logs/`, settling the PRD open question). See [[architecture/decisions]].

## Touched
- [[MontageStudio]]
- [[00 Specifications/(C) 2026-10-26-montagestudio-production-cadence-mvp-prd]]
- [[architecture/decisions]]
- [[COMMANDS]]
- [[2026-10-26-production-cadence-prd-done-test]]
- `src/montage/production.py` (new), `src/montage/cli.py`, `tests/test_production.py` (new)

## Next
- n@uti: fund the API credit, then run brief #1 through the live Claude writer + `--vision` (video 2, the first measured one).
- n@uti: read JWST views in YouTube Studio and run `montage stats workspace/jwst-unfold --url https://youtu.be/uicRn1QgrsU --day 7 --views N` (or `--day 28` after 2026-11-05). The first ledger row.
- Still Phase 0: rotate the 2026-10-04 keys, write 4 briefs, answer G9 (hours/week) and G10 (spend ceiling).
- Not run live: `publish-pack`'s TTY prompt and a real ledger write. Both are only exercised by tests so far.
