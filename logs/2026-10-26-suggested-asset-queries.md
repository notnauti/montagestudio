---
date:    2026-10-26
project: MontageStudio
session: 483abb08-ac1e-4cb5-aecc-0c6c2e866c04
---
# Suggested asset queries before scrape (PRD FR-2, rebuilt)

## What I did
- Found that FR-2 as the PRD wrote it ("the script writer proposes each asset's query") cannot work: scrape runs before `plan`, so there is no script yet. Correction to [[00 Specifications/(C) 2026-10-26-montagestudio-production-cadence-mvp-prd]] FR-2 and its "code anchors" line (`script.py` / `brief.py` schema change): the query step is a separate pre-scrape call from the brief alone, in `scrape`, not in the writer.
- New `src/montage/queries.py`: `ClaudeQueries` (one request, srcs as an enum), `parse` (2-6 words, one per asset, whole draft rejected on any breach), `save` / `load` (`cache/queries.json`), `apply` (precedence: brief query > saved suggestion > description), `suggest`.
- `montage scrape --suggest-queries`: credentials gate (exit 2), prints the suggestions, then scrapes with them; a failure falls back to the description search and exits 2. `--pick` refuses the flag.
- `Wanted.query_from` is printed per asset and recorded as `queryFrom` in manifest.json and candidates.json. `production.py` counts `handQueries`.
- Tests: `tests/test_queries.py` (17, fake client, no billed calls; one checks the words actually sent to Commons), plus a `handQueries` case in `test_production.py`. Full suite 467 passed / 1 skipped. `COMMANDS.md` updated.

## Decisions
- Queries suggested pre-scrape from the brief; brief query wins; brief.json never written. See [[architecture/decisions]].

## Touched
- [[MontageStudio]]
- [[00 Specifications/(C) 2026-10-26-montagestudio-production-cadence-mvp-prd]]
- [[architecture/decisions]]
- [[COMMANDS]]
- [[2026-10-26-publish-pack-thumbnails-draft]]
- `src/montage/queries.py` (new), `src/montage/scrape.py`, `src/montage/candidates.py`, `src/montage/cli.py`, `src/montage/production.py`, `tests/test_queries.py` (new), `tests/test_production.py`

## Next
- Every PRD code item before the web view is built. Four Claude features have run against mocks only: writer, `--vision`, `--draft`, `--suggest-queries`. All wait on the API credit.
- Video 2 is the next real step (Phase 0: fund credit, rotate keys, 4 briefs, G9/G10). Further code without a measured video is the PRD's top risk.
- JWST stats backfill: `montage stats workspace/jwst-unfold --url https://youtu.be/uicRn1QgrsU --day 7 --views N` (or `--day 28` after 2026-11-05).
