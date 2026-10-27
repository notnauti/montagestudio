---
date:    2026-10-26
project: MontageStudio
session: f2e193c7-5409-48e7-bb9e-44cb9d1ca164
---
# Refine dry run: montage mcp + montage view on a Voyager copy; workflow page updated

Continues [[2026-10-26-voyager-published]] in the same session.

## What I did
- **Workflow artifact updated** (https://claude.ai/artifact/LsS34ur8WdQ4iM6EW1uREe, version 3):
  - re-dated to 10-26;
  - the strip now has 11 stages (refine and stats added), coloured by how each stage went on Voyager;
  - a "Found on the Voyager run" table;
  - "Built since 10-04" replaces the old upgrade list;
  - new next steps.
  
  It flags that Voyager skipped refine, so whether video 2 counts as "1 of 4" is `ambiguous` (see Corrections).
- **Refine dry run.** n@uti chose a copy over re-uploading, so the published workspace was untouched. The copy is in the scratchpad (`voyager-refine/`, renders removed).
  - A new licensed still was added through the brief + `scrape`: `File:Voyager golden record gold plating.jpg`, PD, 1920×1470. This keeps the licence gate in the loop.
  - `montage view` was served on 127.0.0.1:8765 and answered 200.
  - Claude acted as the agent over `montage mcp` stdio, using a 30-line JSON-RPC client in the scratchpad (`mcp_client.py`; not kept in the vault):
    - `asset_replace_broll` shot_03 → plating photo (the line is "plated in gold");
    - `asset_replace_broll` shot_12 → Pale Blue Dot (the closing line is "life on this planet"; this ends the 19 s hold on the interstellar illustration).
  - Both writes were validated and atomic, and both voided the approval. `inspect_frame_context` rendered frames 560 and 2650 correctly with captions.
  - Re-preview: QC 5/5 in 12.4 s. The copy was re-approved (plan + 21 files pinned) but not rendered. Compute for the whole refine loop was about 1 min.

## Findings
- **No MCP host is configured.** Refine only ran because Claude wrote a stdio client. For n@uti, a Claude Code session needs the server registered (`03 System/(C) mcp-config-example.md`), and that setup hasn't been done or timed. (`inferred`)
- **`asset_replace_broll` doesn't check licences.** It takes any local file, so an agent can swap in unlicensed media. The scrape detour (brief entry → `scrape`) is the only gated path today.
- The edit result says to run `contact-sheet` + `approve` next, but `preview` + `approve` also works. The message understates the options.

## Corrections
- [[2026-10-26-voyager-published]] and its index lines say "1 of 4 under the cadence done test" without a caveat. The done test lists refine (agent edits via `montage mcp`, seen in `montage view` + preview), and Voyager skipped it. Whether video 2 counts is `ambiguous` until n@uti decides whether refine is mandatory.

## Decisions
- none. Whether refine is mandatory in the done test is still n@uti's call.

## Touched
- [[MontageStudio]]
- [[2026-10-26-voyager-published]]
- [[2026-10-26-voyager-render-publish-pack]]

## Next
- n@uti: decide whether refine stays mandatory in the done test. If it does, register `montage mcp` as an MCP server for video 3 and time the setup.
- Before an agent uses `asset_replace_broll` on a real video, decide whether it should refuse files that have no manifest licence entry.
- Workflow page: update the refine stage from "unused live" to "dry-run on a copy" (offered, not done).
- `montage view` is still running on :8765 from this session; it stops with the session or with Ctrl+C.
