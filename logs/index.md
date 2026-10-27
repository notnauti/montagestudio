# Session Log

One line per session, newest first. `/save` appends here; `/resume` reads this file rather than the individual logs, which is what keeps session startup cheap as history grows.

<!-- Format: - YYYY-MM-DD — what happened → [[YYYY-MM-DD-slug]] -->
- 2026-10-26 — video 2 (Voyager): Claude rewrote script to 87 s → approved → preview → 1080p render QC 7/7 (1m54s for 92 s) → publish-pack, 20 min hands-on; disclosure/description/tags hand-fixed; awaiting n@uti watch + upload → [[2026-10-26-voyager-render-publish-pack]]
- 2026-10-26 — video 2 (Voyager Golden Record) first live run: scrape 8/8 + 2 picks, Claude 400 (no credit), Ollama draft stopped at script gate (54 s vs 90, 6 beats on one still); Commons TIF downscale + upload.wikimedia 429 bugs logged → [[2026-10-26-voyager-run-to-script-gate]]
- 2026-10-26 — `montage view`: ffmpeg timeouts on frame grabs (120 s) and the preview render (max(120 s, 10× length)), so a hung ffmpeg can't hold the frame lock; 494/1 → [[2026-10-26-view-ffmpeg-timeouts]]
- 2026-10-26 — `montage view`: frame-load browser regression test committed; half-written/failed frame PNG fix (atomic `grab`); 491/1 → [[2026-10-26-view-frame-bugs]]
- 2026-10-26 — `montage view`: read-only refine page, frames by the renderer, live-follows mcp edits (checked in Chrome on JWST copy; page frame bug fixed); all PRD code built; 485/1 → [[2026-10-26-montage-view]]
- 2026-10-26 — `scrape --suggest-queries`: Claude archive queries pre-scrape (FR-2 rebuilt: no script exists at sourcing time); brief query wins; 467/1 → [[2026-10-26-suggested-asset-queries]]
- 2026-10-26 — publish-pack: thumbnail candidates clear of every transition (live on JWST copy), opt-in `--draft` Claude text, one model constant; 450/1 → [[2026-10-26-publish-pack-thumbnails-draft]]
- 2026-10-26 — production measurement built (command log, hand-edit counts, summary, `montage stats`, ledger in logs/); stray-log bug fixed; 424/1 → [[2026-10-26-production-measurement]]
- 2026-10-26 — production cadence PRD approved (4 videos/6 wks, ≤3 h each); done test redefined, canvas deferred; Phase 0 next → [[2026-10-26-production-cadence-prd-done-test]]
- 2026-10-08 — JWST custom thumbnail live after re-upload (verified); video metadata complete → [[2026-10-08-jwst-thumbnail-live]]
- 2026-10-08 — JWST re-check: title fixed, thumbnail still YouTube auto frame; inbox-file deletion left uncommitted; pushed → [[2026-10-08-jwst-title-thumbnail-recheck]]
- 2026-10-08 — JWST URL recorded (youtu.be/uicRn1QgrsU); live title is the filename and thumbnail is YouTube's auto frame — fix in Studio → [[2026-10-08-jwst-url-recorded]]
- 2026-10-08 — correction: "publish-pack thumbnail bug" was a 10-04 hand pick, not code; no fix, auto-thumbnail deferred → [[2026-10-08-thumbnail-bug-correction]]
- 2026-10-08 — FIRST VIDEO PUBLISHED: JWST explainer uploaded by n@uti, audio OK; clean thumbnail made (pack's frame fell in a crossfade) → [[2026-10-08-jwst-first-video-published]]
- 2026-10-08 — spec gap audit (Tier 2 compiler + Tier 3 canvas unbuilt; done test needs studio refine); CLAUDE.md status line updated; JWST waits on manual upload → [[2026-10-08-spec-gap-audit-status-line]]
- 2026-10-04 — JWST re-plan → preview → approve → 1080p render QC 7/7 → publish-pack merged with hand text; upload still manual (64 MB > browser tool cap) → [[2026-10-04-jwst-replan-publish-pack]]
- 2026-10-04 — user workflow page + all six upgrades built (gate, candidates/pick, speakable, preview, publish-pack); 403/1; JWST needs re-plan before upload → [[2026-10-04-user-workflow-six-upgrades]]
- 2026-10-04 — keys moved to project .env, registry vars deleted, montage CLI .env loader (247/1); transcript redaction blocked, revoke both keys → [[2026-10-04-key-hygiene-dotenv-loader]]
- 2026-10-04 — first real publish run: JWST explainer, scrape steered by queries, 3b draft hand-rewritten, QC 7/7; awaiting n@uti's watch + upload; Claude writer blocked on credits → [[2026-10-04-jwst-first-publish-run]]
- 2026-10-03 — guide gaps built: recipes, profiles, normalize, Piper word captions, scrape + approve gate, MCP server; 244 passed/1 skipped; Claude writer still not run live → [[2026-10-03-guide-gaps-built]]
- 2026-09-28 — first live run: Ollama script → hand edit → 1080p render QC 5/5; review page published; Claude creds absent → [[2026-09-28-first-live-run-review]]
- 2026-09-28 — script-from-brief built: brief → Claude/Ollama script → Piper → scene_plan; 62 tests; live model run blocked by machine memory → [[2026-09-28-script-from-brief]]
- 2026-09-28 — slice 1 validator + FFmpeg renderer + QC built; 39 tests, sabotage 3/3; 1080p minimal plan QC 5/5 → [[2026-09-28-slice-one-validator-renderer]]
- 2026-09-28 — slice 1 scene_plan fields defined (+`sourceInFrame`, reject unsupported); −14 LUFS; captions → slice 2 → [[2026-09-28-slice-one-scene-plan-fields]]
- 2026-09-28 — done test adopted, slice 1 cut, slice 1 renders scene_plan via FFmpeg (compiler → slice 2) → [[2026-09-28-done-test-slice-one]]
- 2026-09-28 — project scaffolded from the technical guide; code folders seeded; drift + done-condition open → [[2026-09-28-project-scaffold-from-guide]]

