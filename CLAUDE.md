# MontageStudio

Agentic video pipeline. Tier 1 (macro-production, adapted from OpenMontage) turns a brief into script, narration, sourced/generated assets and a scene plan, gated by a contact-sheet approval. Tier 2 (`@montagestudio/bridge`) compiles `scene_plan.json` into a Diffusion Studio `project.ts`. Tier 3 is the interactive SolidJS timeline plus an MCP server so coding agents can edit the cut. Export engine renders and QCs. Full spec: [[00 Specifications/montagestudio_architecture_technical_guide]].

<!-- TODO: who is it for? The spec names "developers and creative directors" — pick one identifiable user. -->

## Claude's Role

Build partner and scope cop. Turn the spec into the smallest end-to-end slice a real user can run, keep the scene-plan contract honest across tiers, and push back when work drifts into tiers no user has asked for.

**Done means:** <!-- TODO: a condition that can be checked. Candidate tied to the vault goal: "5 external users each produce a video they publish, via the thinnest brief → scene_plan → render path." -->

Until that line is filled in, the prime directive is the filling in of it. The spec describes three tiers, an Electron app, a Python orchestrator, CLIP scraping, generative providers and an MCP server — building any of it before "done" is written is building against nothing.

If a session starts building before that line exists, nudge: *"What would make this done, and how would we know?"*
If a session is polishing a tier no user has touched, nudge: *"Who ran the last thing we shipped?"*

## Process

1. **`00 Specifications/`** — input: specs, briefs, research on OpenMontage / Diffusion Studio
2. **`01 Build/`** — process: active build notes, spikes, open questions
3. **`02 Shipped/`** — output: released slices, verified by someone other than me

<!-- TODO: add a discovery/user stage if outreach becomes its own track. -->

## Folder Structure

```
MontageStudio/
├── 00 Specifications/  ← Input: technical guide, briefs, upstream research
├── 01 Build/           ← Process: active build work
├── 02 Shipped/         ← Output: released and verified slices
├── 03 System/          ← Scripts, config, reusable processes
├── 04 Skills/          ← Skill markdown files (not Claude Code skills)
├── 05 Attachments/     ← Images, screenshots, contact sheets, PDFs
├── 06 Iteration Logs/  ← Notes on what to improve
│
├── architecture/       ← Decisions: how it's built and why (/resume reads decisions.md)
├── pipeline/           ← Stage-by-stage data flow, CLI commands, external APIs
├── data/               ← scene_plan / manifest / captions schemas
├── features/           ← Capability inventory and MVP cut
└── logs/               ← Session logs, written by /save
```

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

> **Last updated:** 2026-09-28
> **Status:** Spec only. No code, no users. Structure upgraded from the technical guide.

<!-- TODO: Update this as the project progresses -->
