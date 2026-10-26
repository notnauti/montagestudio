# PRD: MontageStudio — Production Cadence MVP

> **Date:** 2026-10-26 · **Author:** Claude (via `/eg-prd`, Comprehensive depth, no external research) · **Owner:** n@uti
> **Status:** approved 2026-10-26 · **Supersedes:** nothing; narrows [[00 Specifications/montagestudio_architecture_technical_guide]] for the next slice
> Claims are labelled `extracted` (from the vault/code), `inferred` (connected by Claude) or `ambiguous` (unresolved). Unlabelled statements are requirements, not claims.

## Executive summary

Slice 1 already works: one brief became a published YouTube video (JWST explainer, 2026-10-08) (`extracted`). The next MVP is **not** the guide's SolidJS canvas. It is **cadence**: n@uti publishes **4 videos in 6 weeks, each in ≤3 hours of hands-on time, without hand-editing generated code.** To get there, the MVP removes the four hand steps that dominated the JWST run (script writing, asset sourcing, publish packaging, fine-tuning). "Studio refine" is redefined as an **agent-first edit loop** (the existing `montage mcp`) plus a **thin, local, read-mostly web view** over `scene_plan.json`. The Tier 2 `project.ts` compiler and the Tier 3 Diffusion Studio canvas stay deferred until measured friction justifies them.

## Problem statement

Producing one video took many manual interventions the pipeline was supposed to remove (`extracted`, 2026-10-04 and 2026-10-08 logs):
- The script was a local 3B/7B draft below the publish bar, rewritten by hand. The Claude writer has never run live (no API credit).
- Assets needed hand-written per-asset search queries and candidate picks. Lexical ranking cannot see pixels.
- Publish packaging was manual: the thumbnail frame the pack suggested fell inside a crossfade, the title was fixed after upload, the description was hand-merged, and the upload was manual.
- Fine-tuning timing and visuals had no visual surface. Edits meant MCP calls or JSON, checked by re-rendering.

There is also no measurement. Nobody knows how long a video takes, how many hand edits it needs, or how JWST performed: the "check views after a week" next step was never recorded (`extracted`: no log after 2026-10-08). Without numbers, the choice between "build the canvas" and "fix Tier 1" is a guess. The spec's answer, a full canvas, is the most expensive option and the least evidenced one (`inferred`).

## Target users

- **Persona:** n@uti, the only user. Technical, runs the CLI and Claude Code, publishes explainer videos on their own YouTube channel. No other creators, no onboarding, no multi-user work (G2).
- **Job to be done:** "When I have a topic I want to explain, turn it into a video I'm willing to publish in one sitting of a few hours, without fighting the tool."
- **Trigger:** a topic idea, captured as a brief (`montage init` / `plan --topic`).
- **Time budget:** leftover time after Agent Harness OS (project CLAUDE.md). The actual hours per week are an open question (G9).

## Current state

(`extracted` from `src/montage/`, `architecture/decisions.md`, `features/(C) feature-inventory.md`; test count from logs, not rerun)

| Stage | What exists | Hand work on JWST |
|---|---|---|
| Brief | `montage init`, `plan --topic`, recipes in `pipelines/*.json` | brief written by hand (expected) |
| Script | `script.py`: Claude writer (structured output, asset enum) with Ollama fallback; `script_gate.py` beat table, coverage, runtime estimate, sha256 approval | Ollama draft below the bar, rewritten by hand; Claude writer never run live |
| Narration | Piper + `speakable.py` + `pronunciations.json`; word timings via `align.py` | none reported |
| Assets | `scrape.py`: Commons/NASA/Archive.org, licensed only, candidates sheet + `--pick`; `vision.py` Claude re-rank (never run live) | per-asset queries written by hand; picks by hand |
| Gate | contact sheet, 480p `preview`, `approve` pinned by sha256 | expected human review |
| Refine | `montage mcp`: 6 validated atomic edit tools over `scene_plan.json` + `plan_summary`; `frame_context.grab` renders any frame through the real filter chain | no visual surface |
| Render | `render.py` profiles, normalization, QC 7/7 | none |
| Publish | `publish.py` → `publish/youtube.md` (credits, chapters, draft tags, disclosure) | thumbnail made by hand, title/description fixed by hand, manual upload |

**Reuse:** all of it. **Replace:** nothing. **Not built and staying unbuilt in this MVP:** `project.ts` compiler, SolidJS canvas, bidirectional sync, `montage open` as specced, CLIP, WebCodecs exporter.

## Proposed solution

Four tracks, sequenced so **a video ships before each new piece of tooling**:

1. **Script & sourcing from the brief:** turn on the already-built Claude writer and `--vision` with a funded key. Extend the writer so it also proposes each asset's search `query`, which removes the hand-written queries.
2. **Publish pack that needs no hand fixes:** a thumbnail frame outside any transition (or three candidates to choose from), plus model-drafted title, description, chapter titles and tags built from the approved script, with the provenance/credit block untouched.
3. **Refine = agent + thin web view:** a local page served by `montage` that shows the timeline (tracks and clips as blocks), real rendered frames at clip boundaries, captions and the 480p preview. It refreshes when `scene_plan.json` changes. Edits happen through Claude Code over `montage mcp`, which is already validated and atomic. The page is for seeing; the agent is for changing.
4. **Measurement:** every `montage` command logs its timing; hand edits are counted from records that already exist; publish-pack asks for hands-on minutes; YouTube stats are recorded at 7 and 28 days.

Generative fallback and a research engine are **in scope but gated** (see Scope): they start only if measurement shows they are needed.

## Scope

**In:**
- Live Claude writer + `scrape --vision` runs (funding + key rotation are prerequisites)
- Writer-proposed per-asset search queries
- Publish-pack fixes: transition-safe thumbnail candidates, drafted title/description/chapter titles/tags
- Thin local web view (stdlib `http.server` + vanilla HTML/JS, 127.0.0.1 only) over `scene_plan.json`
- Per-workspace production log, hand-edit count, hands-on minutes, YouTube 7/28-day stats, and a cross-video ledger
- Redefinition of the done test (proposed diff to project `CLAUDE.md`, needs permission)
- **Gated:** generative image fallback and a research step for scripts. Each needs its trigger condition (FR-G1/FR-G2) met before work starts.

**Out (explicitly):**
- `project.ts` compiler (Tier 2) and any Diffusion Studio / SolidJS / Node / pnpm code
- Drag-to-edit timeline, bezier editor, bidirectional canvas ⇄ code sync
- YouTube upload API (OAuth, quota, app verification). Upload stays manual.
- Multi-user, hosted or remote access to the web view
- CLIP / torch, ElevenLabs, the WebCodecs exporter
- `remove_silence` / `balance_audio_lufs` MCP tools (no observed need)
- Any change to the `scene_plan.json` shape. If one becomes necessary, it is a decision in `decisions.md` first.

## User stories / Jobs-to-be-done

1. As n@uti, I write a brief with points and wanted visuals, run `montage plan`, and get a script I'd approve with light edits, without rewriting it.
2. As n@uti, I run `montage scrape` and the right shots come back without my writing search queries. I only override the wrong ones from the candidates sheet.
3. As n@uti, I open the web view, see where the cut feels off, tell Claude Code "start shot_04 eight frames later and hold the zoom", and see the page and the preview update.
4. As n@uti, I run `montage publish-pack` and paste title, description, tags and a thumbnail into YouTube Studio without rewriting any of them.
5. As n@uti, after 4 videos I can see time per stage and hand edits per video, and decide from data whether the canvas, generation or research is worth building.

## Functional requirements

**Script & sourcing**
- **FR-1** The Claude writer runs live end to end on a real brief. The script gate's existing checks apply unchanged. A live run is recorded in a session log with its token usage and cost.
- **FR-2** The script writer's schema gains an optional per-asset `query` (search phrase) for each *wanted* asset. `scrape` uses a writer-proposed query only when the brief has none (hand queries win). This touches `brief.json`/`script.json` handling, **not** `scene_plan.json`.
- **FR-3** `scrape --vision` runs live. On any vision failure the existing behaviour holds: fall back to lexical order, exit 2.
- **FR-4** The Claude model ID used by the writer and vision is one current constant, checked against the current model list at implementation time.

**Refine (web view)**
- **FR-5** `montage view <ws>` (name open; `open` is reserved by the guide for the canvas) serves a local page on 127.0.0.1 with a random free port, prints the URL, and stops on Ctrl+C. It needs no dependencies beyond stdlib and ffmpeg.
- **FR-6** The page shows: tracks as horizontal rows of clip blocks scaled to frames (ids, start/duration, transition overlaps marked); the caption words for the selected clip; real frames at each clip's first, middle and last frame from `frame_context.grab` (cached by plan+asset hash); and `cache/preview.mp4` with a "regenerate preview" action that runs the existing `preview`.
- **FR-7** The page polls the sha256 of `scene_plan.json` and re-renders when it changes, so agent edits through `montage mcp` show up without a manual reload. Frame thumbnails are invalidated by hash, not mtime (same rule as `review.py`).
- **FR-8** The page performs **no plan writes** in this MVP. Any future write action must go through the `edit.py` functions and the validator, never through a separate code path.
- **FR-9** The page displays approval status (script approval, storyboard approval fresh/stale with the reasons `approve` would give).

**Publish**
- **FR-10** Thumbnail: publish-pack proposes 3 candidate frames, each at least `transition + 6` frames from any clip boundary, saved under `publish/thumb_candidates/`. It never suggests a frame inside a crossfade.
- **FR-11** When a model writer is available, publish-pack drafts title (≤70 chars), description intro, chapter titles and tags from the approved script and brief. The credit block, NASA line and disclosure stay deterministic and verbatim. Drafted text is marked as a draft in the file.
- **FR-12** Existing publish-pack refusals (unapproved plan, stale render, asset lacking usable attribution) are unchanged.

**Measurement**
- **FR-13** Every `montage` subcommand appends `{cmd, args summary, start, end, exit}` to `<ws>/production_log.jsonl`. Logging failure never fails the command.
- **FR-14** Hand-edit count per video is derived from existing records: script changed after the model draft (`script_approval.json` draft vs approved hash), `--pick` overrides (`pickedRank`), MCP edits (`cache/plan-history/` entries), hand-written manifest entries, `--force` uses.
- **FR-15** `publish-pack` accepts `--hands-on-min N` (prompts if omitted on a TTY; skippable) and writes it plus a per-video summary (wall time per stage, hand-edit counts, cost if known) to `<ws>/publish/production_summary.json`.
- **FR-16** The summary also goes into a cross-video ledger **outside `workspace/`** (because workspaces are gitignored, see decision 2026-10-04): `data/(C) production-ledger.md`, one row per video. Location to be confirmed (open question).
- **FR-17** `montage stats <ws> --day 7|28 --views N --avd SECONDS` records YouTube stats into the same ledger row. The numbers are entered by hand; there is no API.

**Gated**
- **FR-G1 Generative image fallback** starts only if, across ≥2 videos, ≥1 wanted asset per video has no acceptable licensed candidate (recorded as such in the ledger). First scope: still images only, one provider, the output recorded in `manifest.json` as `licenseBasis: generated` with prompt and model, and the disclosure line extended.
- **FR-G2 Research step** starts only if ≥2 approved scripts needed factual corrections that a source lookup would have caught (recorded in the ledger). First scope: the writer gets a cited-source list from a single search provider; citations go into `script.json`.

## Non-functional requirements

- **Performance:** web view first paint ≤2 s on a cached plan. Thumbnail strip for an 80 s, ~12-clip plan ≤20 s cold, then cached. Preview regenerate stays at the measured ~15 s for 80 s of video (`extracted`: 14.3 s). Agent edit → page refresh ≤3 s, excluding frame grabs.
- **Accessibility:** single-user local tool; keyboard-navigable clip list, text labels on every block, no colour-only state. No formal WCAG target.
- **Security & privacy:** web server binds 127.0.0.1 only, serves only files under the workspace (path traversal refused), no write endpoints (FR-8). API keys stay in the project `.env`, loaded only by the CLI entry (decision 2026-10-04). Pending key rotation from 2026-10-04 is a prerequisite (`extracted`: "revoke both keys" still open in logs).
- **Multi-tenant / scoping:** n/a (single user).
- **i18n:** English only. The Piper voice is en_US.
- **Offline / degraded:** without a key or credit, the pipeline still runs end to end (Ollama writer, lexical ranking, deterministic publish text), and the summary records that the degraded path was used.
- **Dependencies:** no new pip or npm dependencies. Stdlib + ffmpeg + existing `.venv` (G11/G12).
- **Authority:** the ffmpeg render is authoritative. Every frame the web view shows comes from the renderer's own filter chains (`frame_context.grab`). Anything approximated in the browser (block layout, playhead) is labelled as approximate (G13).

## Success metrics

| Metric | Target | Measured by |
|---|---|---|
| Videos published | **4 in 6 weeks** from PRD approval (JWST excluded) | ledger rows with a YouTube URL |
| Hands-on time per video | **median ≤3 h**, none >5 h | `--hands-on-min` (self-report) cross-checked against the production log |
| Hand-edited generated code | **0** per video. Editing `script.json` text counts as a script edit, not code. | review of session logs |
| Hand edits per video | trend down from video 1 to video 4; script rewritten wholesale in **0 of the last 2** | FR-14 counts |
| Publish text used as drafted | title + description pasted with ≤1 minor edit on **≥3 of 4** | self-report field in summary |
| YouTube signal | recorded at 7 and 28 days for every video, including JWST (backfill) | FR-17. No target until there is a baseline. |

The 3 h and 4-video targets are `inferred` (proposed in the gap Q&A, not measured). Recalibrate after video 2 if the measured baseline says otherwise. Weekly hours (G9) are unknown, so the cadence target may be unreachable as stated.

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Yak-shaving into tooling instead of shipping videos** (your named stress default) | High | High | Hard sequencing: video 2 ships on today's pipeline before any new code; each phase ends with a published video, not a merged feature. |
| MontageStudio starves Agent Harness OS (the primary target) | Medium | High | Project rule: leftover time only. Each session opens with "is the harness moving this week?" G9 must be answered before Phase 1. |
| No topics queued, so cadence fails for non-tool reasons | Medium | High | Phase 0 deliverable: 4 briefs written before any build work. |
| API credit never funded, so Claude writer/vision stay dead and script friction remains | Medium | High | Funding + key rotation are Phase 0 blockers. Spend ceiling (G10) decided before Phase 1. |
| Gated items (generation, research) start without evidence | Medium | Medium | FR-G1/G2 trigger conditions recorded in the ledger. The PRD verdict should confirm the gates. |
| Web view grows into a canvas (drag edits, sync) | Medium | Medium | FR-8: no writes. Any write action needs a new decision entry and must go through `edit.py`. |
| Self-reported hands-on time is optimistic | High | Low | Cross-check against production-log wall time and report both. |
| YouTube synthetic-voice / AI-content disclosure rules change or are stricter than the current disclosure line | Unknown (`ambiguous`, not researched) | Medium | Check YouTube's current altered/synthetic content policy before video 2. Publish-pack's disclosure already names model writers. |
| Writer-proposed queries drift toward assets with unusable licences | Low | Medium | Scrape's licence gate is unchanged. Queries only change the search, never the acceptance rule. |

## Implementation hints

(Loose; `/eg-new-feature` refines.)

**Phases (each ends with a published video):**
- **Phase 0 (prerequisites, no code):** fund API credit, rotate both keys pasted on 2026-10-04, backfill JWST 7/28-day stats, write 4 briefs, answer G9/G10.
- **Phase 1:** FR-13–FR-17 (measurement first, so video 2 produces a baseline) + FR-1/FR-3/FR-4 (live runs). Ship **video 2**.
- **Phase 2:** FR-2 (writer queries) + FR-10–FR-12 (publish pack). Ship **video 3**.
- **Phase 3:** FR-5–FR-9 (web view). Ship **video 4**. Then review the ledger and decide on FR-G1/FR-G2 and on whether a canvas is ever justified.

**Code anchors (`extracted`):**
- Writer: `script.py` (`build_prompt`, `script_schema`, `write_with_claude`); schema change for FR-2 lives here and in `brief.py`. Query consumption is in `scrape.py` / `sources.py`, where an explicit `query` is already sent whole (decision 2026-10-04).
- Frames for the web view: `frame_context.grab` / `grab_command`. Timeline data: `plan.py` Plan model + `mcp_server` `plan_summary`.
- Hash freshness: `review.py` sidecar pattern; reuse it for thumbnail caching.
- Publish: `publish.py` (`chapters`, `chapter_title`, `tags`, `build`, `write`). The thumbnail picker needs the clip boundaries + `_xfade` logic from `edit.py`.
- CLI: new `view` and `stats` subparsers in `cli.py` beside `preview` / `publish-pack`. The production-log hook wraps `main()` dispatch once.
- `decisions.md` entries needed: FR-2 schema addition (brief/script, not scene_plan), ledger location (FR-16), web view no-write rule (FR-8), done-test redefinition.

**Proposed done-test diff (project `CLAUDE.md`; the file has no `(C)` prefix, so this needs your permission):**
```diff
- **Done means:** Functioning Production Studio — checked by: from one brief, n@uti gets a rendered video they would publish (plan → assets → gate → scene_plan → studio refine → render) without hand-editing generated code.
+ **Done means:** Functioning Production Studio — checked by: from one brief, n@uti gets a rendered video they would publish (plan → assets → gate → scene_plan → refine (agent edits via `montage mcp`, seen in `montage view` + preview) → render) without hand-editing generated code — 4 times in 6 weeks at ≤3 h hands-on each.
```

## Open questions

- **G9 (deferred):** realistic MontageStudio hours per week after the harness. The 4-in-6-weeks target depends on it.
- **G10 (deferred):** monthly spend ceiling for Claude writer/vision, and later generation. FR-G1 needs it.
- Ledger location: `data/(C) production-ledger.md` (versioned, human-readable) vs `logs/` (agent layer). It's machine-written, so `logs/` fits AGENTS.md's two-layer rule better (`inferred`).
- Command name for the web view (`view` vs reusing the guide's `open`).
- Have both keys from 2026-10-04 been rotated? Status unknown since then (`ambiguous`).
- YouTube altered/synthetic content disclosure requirements: not researched (`ambiguous`).
- JWST performance: no stats recorded. Is there a baseline at all?
- Gated items: confirm the FR-G1/FR-G2 trigger thresholds or adjust them.
- If the canvas is ever revisited: OpenMontage / Diffusion Studio licences and feature claims are unchecked (`ambiguous`, project rule).

## Sources & references

No external research was run (research scope: None). Internal sources:
- `03 Projects/MontageStudio/CLAUDE.md`: done test, scope-cop rules, status as of 2026-10-08
- `03 Projects/MontageStudio/architecture/decisions.md`: entries 2026-09-28 → 2026-10-04
- `03 Projects/MontageStudio/features/(C) feature-inventory.md`
- `03 Projects/MontageStudio/logs/2026-10-08-spec-gap-audit-status-line.md`, `2026-10-08-jwst-first-video-published.md`, `logs/index.md`
- `03 Projects/MontageStudio/00 Specifications/montagestudio_architecture_technical_guide.md` §2, §4 Stage 4, §5, §7
- `src/montage/` (`cli.py`, `mcp_server.py`, `edit.py`, `frame_context.py`, `publish.py`, `script.py`)
- Gap answers G1–G14 from this `/eg-prd` session (2026-10-26)

## Out-of-scope follow-ups

- Tier 2 `project.ts` compiler + Tier 3 canvas: separate PRD, only if the ledger shows that refine friction dominates after video 4
- YouTube Data API upload with scheduled publishing
- Generative video (Runway / LTX), beyond the gated still-image fallback
- ElevenLabs or a second Piper voice; CLIP ranking
- `remove_silence` / `balance_audio_lufs` MCP tools
- Opening the studio to other creators (would compete with the 60-day target)
