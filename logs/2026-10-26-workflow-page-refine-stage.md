---
date:    2026-10-26
project: MontageStudio
session: f2e193c7-5409-48e7-bb9e-44cb9d1ca164
---
# Workflow page: refine stage updated to "dry run on a copy"

Continues [[2026-10-26-refine-dry-run-voyager-copy]] in the same session.

## What I did
- Updated **MontageStudio User Workflow** (https://claude.ai/artifact/LsS34ur8WdQ4iM6EW1uREe) to version 4 with 5 edits:
  - strip cell 7 and the stage-7 row changed from "unused live" / "skipped" to "dry run on a copy";
  - the stage-7 "Seen live" lane now has the two swaps, the frame inspects, QC 5/5 and the ad-hoc client;
  - the stage-7 "Next" lane: decide whether refine is required, register the MCP server, route new media through `scrape`;
  - next-steps item 4 now reads: decide whether refine is required, then set up the MCP host;
  - the tag on "Built since 10-04" item 4 changed to match.
- Earlier in the session: a duplicate `/save` after a conversation rewind was undone in `a668823`. See [[2026-10-26-refine-dry-run-voyager-copy]], which is the 578ce41 version.

## Decisions
- none

## Touched
- [[MontageStudio]]
- [[2026-10-26-refine-dry-run-voyager-copy]]

## Next
- Done: [[2026-10-26-refine-dry-run-voyager-copy]] → Next "Workflow artifact: move refine from 'unused live' to 'dry-run on a copy' (offered, not done)". Done in version 4.
- Still open: whether refine is required in the done test; the MCP host config; the `asset_replace_broll` licence check; Voyager stats on 11-02 and 11-23.
