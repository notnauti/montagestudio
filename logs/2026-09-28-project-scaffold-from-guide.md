---
date:    2026-09-28
project: MontageStudio
session: db198985-1b79-4812-8704-7470ea46b5a2
---
# MontageStudio scaffolded from the technical guide

## What I did
- Created `03 Projects/MontageStudio/` from `(PROJECT TEMPLATE)` per the new-project skill.
- Read the technical guide (user-supplied, identical to the Downloads copy) and upgraded the structure: process folders renamed to `00 Specifications/`, `01 Build/`, `02 Shipped/`; code folders `pipeline/`, `data/`, `features/` added.
- Moved the guide from `architecture/` to `00 Specifications/` (user-approved); updated all four wikilinks.
- Rewrote project `CLAUDE.md`: three-tier summary, Claude's role (build partner + scope cop), rules (`scene_plan.json` is the contract; upstream claims `ambiguous` until checked). "Done means" and target user left as TODOs.
- Seeded one note per code folder: pipeline stages, scene_plan contract, feature inventory (MVP column empty on purpose).
- Root `CLAUDE.md` "My Current Projects": replaced the "no projects" placeholder with entries for Agent Harness OS (description `inferred` from its ADR and log index — it has no project CLAUDE.md) and MontageStudio, plus an open-question comment.

## Decisions
- none (structure and placement only; no build decisions)

## Flagged, unresolved
- Drift: is MontageStudio the 60-day product (replacing Agent Harness OS) or alongside it? Unanswered.
- Guide scope is months of work vs the 60-day $5k/100-user target; thinnest path `inferred`: brief → script → Piper → local assets → scene_plan → render.
- Bidirectional canvas ⇄ `project.ts` sync makes `scene_plan.json` stale after the first edit — canonical file undecided.
- Guide inconsistencies: MCP tool names (§2 vs §5), CLI names, JSX in `project.ts` (needs `.tsx`), no caption track type, undefined cost-check gate.
- Unchecked (`ambiguous`): upstream licenses vs Apache 2.0 claim, OpenMontage feature claims, "Montage Studio" name collision / GitHub org, "Apollo 1" demo topic is factually wrong.

## Touched
- [[MontageStudio]]
- [[CLAUDE]] (project and root)
- [[montagestudio_architecture_technical_guide]]
- [[(C) pipeline-stages]]
- [[(C) scene-plan-contract]]
- [[(C) feature-inventory]]
- [[Agent Harness OS]]

## Next
- Answer replace-or-alongside for Agent Harness OS.
- Write MontageStudio's "Done means" line and name one target user.
- Mark the MVP column in the feature inventory (3–5 rows max).
