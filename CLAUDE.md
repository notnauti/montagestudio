# MontageStudio

Agentic video pipeline. Tier 1 (macro-production, adapted from OpenMontage) turns a brief into script, narration, sourced/generated assets and a scene plan, gated by a contact-sheet approval. Tier 2 (`@montagestudio/bridge`) compiles `scene_plan.json` into a Diffusion Studio `project.ts`. Tier 3 is the interactive SolidJS timeline plus an MCP server so coding agents can edit the cut. Export engine renders and QCs. Full spec: [[00 Specifications/montagestudio_architecture_technical_guide]].

**User:** n@uti — built as my own production studio. Runs alongside Agent Harness OS, not in place of it; it does not count toward the vault's 60-day $5k / 100-user target.

## Claude's Role

Build partner and scope cop. Turn the spec into the smallest end-to-end slice a real user can run, keep the scene-plan contract honest across tiers, and push back when work drifts into tiers no user has asked for.

**Done means:** Functioning Production Studio — checked by: from one brief, n@uti gets a rendered video they would publish (plan → assets → gate → scene_plan → studio refine → render) without hand-editing generated code.

Because this runs alongside the primary target, it gets the leftover time, not the best time. If a MontageStudio session starts while Agent Harness OS has an open next step toward users, nudge: *"Is the harness moving this week?"*
If a session is polishing a tier no user has touched, nudge: *"Who ran the last thing we shipped?"*

## Process

1. **`00 Specifications/`** — input: specs, briefs, research on OpenMontage / Diffusion Studio
2. **`01 Build/`** — process: active build notes, spikes, open questions
3. **`02 Shipped/`** — output: released slices, verified by someone other than me

<!-- TODO: add a discovery/user stage if outreach becomes its own track. -->

## Folder Structure

`ls` and `pyproject.toml` show the layout. What they don't show:
- `tests/`: render tests need ffmpeg, Piper tests need the voice, live network test opt-in (`MONTAGE_LIVE_NET=1`).
- `.venv/` and `voices/` are gitignored: `.venv` holds piper-tts, anthropic, onnxruntime==1.20.1; `voices/` holds the Piper model (en_US-lessac-medium).

## Rules & Conventions

- **`(C)` prefix** — files created by Claude are prefixed `(C)` so it's clear they're AI-generated. Structural files the skills define by exact name (`CLAUDE.md`, `COMMANDS.md`, `logs/index.md`, `architecture/decisions.md`) are exempt — the names are load-bearing.
- **Editing rule** — before editing any file without the `(C)` prefix, ask permission first. The technical guide is human-supplied: propose diffs, don't edit.
- **Skills** — reusable scripts and automations are saved as markdown in `04 Skills/`, not as Claude Code skills.
- **Label uncertain claims** `extracted` / `inferred` / `ambiguous`, per the vault rule.
- **`scene_plan.json` is the contract** between Tier 1 and Tier 2. Any change to it is a decision in `architecture/decisions.md`, not a silent edit.
- **Upstream claims need a source** — capabilities or licenses attributed to OpenMontage or Diffusion Studio are `ambiguous` until checked against their repos.

## Sessions

See [[logs/index]]

## Current Status

> **Last updated:** 2026-10-08
> **Status:** Every guide stage that runs on this machine is built, plus the six workflow upgrades: scrape candidates sheet + `--pick` (ranker bugs fixed), script gate + `approve-script`, speakable narration + `pronunciations.json`, `preview` animatic with sha256 approval freshness, `publish-pack`. 403 passed / 1 skipped. JWST re-run done 2026-10-04 on the real workspace: re-plan → preview → approve → 1080p render QC 7/7 (81.4 s) → `publish/youtube.md` merged with hand text. Not run live: the Claude script writer, `scrape --vision` (no API credit). Not built: `project.ts` compiler, SolidJS canvas, generative fallback. No published video yet — blocked only on n@uti watching the render and uploading it manually in YouTube Studio (64 MB master > browser tool's 10 MB cap).

<!-- TODO: Update this as the project progresses -->
