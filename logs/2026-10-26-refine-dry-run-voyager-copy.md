---
date:    2026-10-26
project: MontageStudio
session: f2e193c7-5409-48e7-bb9e-44cb9d1ca164
---
# Refine dry run: `montage mcp` + `montage view` on a Voyager copy

Continues [[2026-10-26-voyager-published]] in the same session. Also updated the **MontageStudio User Workflow** artifact (https://claude.ai/artifact/LsS34ur8WdQ4iM6EW1uREe, version 3) to the 10-26 state: 11 stages, the Voyager findings table, builds since 10-04, and next steps.

## What I did
- n@uti chose a dry run on a copy over refining the published workspace, since YouTube can't swap the file on a published video. The copy is in Claude's session scratchpad, without `renders/`. The real `workspace/voyager-golden-record` was untouched.
- Added one wanted visual to the **copy's** brief (`voyager-record-plating.jpg`, query "voyager golden record gold plating") and ran `scrape`. It saved `File:Voyager golden record gold plating.jpg`: public domain, 1920×1470; 2 NC candidates were refused.
- Started `montage view <copy> --port 8765` and opened it in n@uti's browser (HTTP 200).
- Drove `montage mcp` over stdio with a hand-written client (`mcp_client.py`, in the scratchpad, not versioned):
  - `asset_replace_broll` shot_03: disc → plating photo, to match "…plated in gold";
  - `asset_replace_broll` shot_12: interstellar → Pale Blue Dot, to match "…life on this planet" and break the 19 s hold;
  - `inspect_frame_context` at frames 560 and 2650: frames render with captions and read correctly.
  
  Both writes were validated and atomic, kept their timing and motion, and voided the approval.
- `preview`: 12.4 s, QC 5/5. `approve`: plan + 21 files pinned. Not rendered.

## Findings
1. **No MCP host is configured for refine.** Claude reached the server only through an ad-hoc stdio client. For n@uti, a session needs the server registered (`03 System/(C) mcp-config-example.md`), and that setup is outside every hands-on count so far.
2. **`asset_replace_broll` bypasses the licence gate.** It accepts any local or file:// path. Keeping the gate meant a detour through the brief and `scrape`. (`inferred`) An agent swap with an unlicensed file would reach publish-pack's credit check only if the manifest lacked it, and a file copied into `assets/broll/` has no manifest entry. Publish-pack's "missing attribution" refusal should catch that, but it wasn't tested.
3. The edit result says to run `contact-sheet` + `approve` before render. `preview` + `approve` also works, so the message understates the options.
4. Cost: about 1 min of compute (scrape + 2 edits + preview). Choosing the edits from the contact sheet took about 2 min.

## Corrections
- [[2026-10-26-voyager-published]] and [[2026-10-26-voyager-render-publish-pack]] call Voyager "1 of 4" under the cadence done test. The done test lists refine (`montage mcp` + `montage view`) as a step, and Voyager skipped it, so whether it counts is `ambiguous`. That's n@uti's call: either keep refine in the test and use it on video 3, or mark it optional.

## Decisions
- none (whether refine stays in the done test is open)

## Touched
- [[MontageStudio]]
- [[2026-10-26-voyager-published]]
- [[(C) mcp-config-example]]

## Next
- n@uti: decide whether refine is required or optional in the done test.
- If required: register `montage mcp` in a Claude Code MCP config before video 3.
- `asset_replace_broll` licence check: route swaps through scrape candidates or require a manifest entry. It's a code change; do it only if refine stays in the loop.
- Workflow artifact: move refine from "unused live" to "dry-run on a copy" (offered, not done).
- `montage view` may still be running on port 8765 in the background; Ctrl+C or end the session.
