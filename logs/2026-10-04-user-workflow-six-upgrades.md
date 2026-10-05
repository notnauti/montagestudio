---
date:    2026-10-04
project: MontageStudio
session: cc967385-e5e1-44b7-920d-687a560be679
---
# User workflow page, then all six workflow upgrades built (403/1)

## What I did
- **Workflow page.** Mapped a `montage` run as 10 stages: what the user does, what the tool does, what broke live, and the upgrade for each. Built from COMMANDS.md, the feature inventory and the 2026-09-28 / 10-03 / 10-04 logs. Published privately at https://claude.ai/artifact/LsS34ur8WdQ4iM6EW1uREe. v1 was the diagnosis: the front half (brief, scrape, script, narration) is where all live hand-work and failures came from; the back half (plan, approve, render, QC) is solid. v2 shows the state after the build.
- **Build (ultracode workflow `wf_6e27f040-ad1`).** Four build agents ran one after another, per the vault rule, then one adversarial reviewer:
  - **#2 ranker fixes.** Explicit queries are sent in full; description queries drop the words every asset shares; search APIs get unstemmed words; ties break on distinctive terms, then pixels; there is a scene-type penalty.
  - **#3 candidates.** Top 3 previews per asset, `cache/candidates.png`, `--pick <asset>=<n>`, `--vision` (Claude, never called live).
  - **#1 script gate.** A model draft stops before narration with a beat table and brief-point coverage. `--approve-script` / `approve-script` record its sha256 in `script_approval.json`. An unedited draft more than 15% over the target or over the beat limit is rejected. The writer prompt is stricter. Speaking rate recalibrated 2.5 → 3.1 words/s (measured: the JWST estimate is 83 s, actual 81 s).
  - **#5 speakable.** Numbers, units, years and abbreviations go to Piper in spoken form; captions keep the written words; optional `pronunciations.json`; stale wavs pruned.
  - **#4 `publish-pack`.** Writes `publish/youtube.md` with credits from the manifest. It refuses an unapproved plan, a stale render, a missing attribution, a bad license or a hash mismatch.
  - **#6 `preview`.** 480p animatic. Approval freshness is now a sha256 sidecar, not file time. Decoding each still once made the preview about 4× faster (54.2 s → 14.3 s).
- **Reviewer.** Fixed one confirmed bug: `--approve-script` could approve a draft nobody had read. It also ran the full flow on a JWST copy: plan → preview → contact-sheet → approve → 720p render QC 7/7 → publish-pack exit 0.
- **Suite.** 247 → 403 passed, 1 skipped; I re-ran it myself (extracted).
- **Docs.** With permission, updated COMMANDS.md (new commands, flags, run order, workspace files, 403/1) and CLAUDE.md (src line, status).

## Decisions
- Five dated 2026-10-04 entries in [[architecture/decisions]], written by the build agents and the reviewer: scrape ranking + candidates, script gate, speakable + publish-pack, preview + sha256 freshness, and `--approve-script` requiring an existing script.json. None changes the shape of `scene_plan.json`.
- Correction to [[2026-10-04-jwst-first-publish-run]]: "every asset needed an explicit `query`" was true of the old ranker. With the new ranker, queries are optional (inferred; not re-measured without them).

## Touched
- [[MontageStudio]]
- [[COMMANDS]]
- [[architecture/decisions]]
- [[features/(C) feature-inventory]]
- `src/montage/`: new candidates, vision, script_gate, coverage, speakable, publish, review; changed approval, cli, contact_sheet, mcp_server, narrate, planner, profiles, qc, render, scrape, script, sources
- `tests/`: 6 new test files (candidates, script_gate, speakable, publish, preview), plus updates to gate, pipelines, plan_e2e, planning, sourcing

## Next
- JWST: re-plan (beat 1 is re-narrated as "twenty twenty-one"; 54 stale files pruned) → `preview` (the old contact sheet has no hash record) → `approve` → `render --profile 1080p30-h264` → `publish-pack` → edit the description and chapters → upload. Still 0 published videos.
- Fund the API key and rotate both keys pasted into the transcript. Then run `--writer claude --rewrite` and `scrape --vision` live for the first time.
- Open follow-ups (inferred, low): the publish-pack AI disclosure should fall back to script.json's writer; pick the render by plan hash, not mtime; dedupe the same image across sources; bring the decode-once speedup to the final render.
- Scratchpad copies (gate-demo, preview-live, e2e: about 115 MB+) are disposable.
- "Is the harness moving this week?" This session was all MontageStudio.
