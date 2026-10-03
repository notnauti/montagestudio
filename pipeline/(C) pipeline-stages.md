# Pipeline stages

Source: [[00 Specifications/montagestudio_architecture_technical_guide]] §2, §4. All rows `extracted` unless labelled.

| # | Stage | CLI | In → Out | External deps |
|---|---|---|---|---|
| 1 | Macro-plan + script | `montage plan --pipeline <p> --topic <t> --target-duration <s>` | brief → scene outline, narration beats, asset manifest | web / Perplexity / YouTube transcripts |
| 1b | Narration | (inside plan) | script → voiceover + `captions.json` (word offsets) | ElevenLabs or Piper TTS |
| 2 | Asset sourcing | `scrape`, `synthesize` | manifest → `assets/` | CLIP; Archive.org, NASA, Commons; Runway / LTX-Video / Flux / Recraft / SDXL |
| 2g | Contact-sheet gate | `montage approve --workspace <dir>` | assets → `contact_sheet.png`, blocks until approved | — |
| 3 | Bridge compile | (post-approve) | `scene_plan.json` → `project.ts` | FFmpeg probes |
| 4 | Refine | `montage open <dir>` | `project.ts` ⇄ canvas ⇄ MCP agent | Diffusion Studio, MCP |
| 4 (as built) | Refine, agent only | `montage mcp <dir>` | scene_plan.json ⇄ MCP agent (no canvas; edits re-validated, then contact-sheet + approve before render) | MCP stdio |
| 5 | Export + QC | `montage render --profile 4k60-prores` | `project.ts` → video; ffprobe drops, EBU R128 loudness | WebCodecs / NVENC / VideoToolbox |

## As built (2026-10-02, stage 2 / 2g)
Order is `init` → list wanted visuals in brief.json → `scrape` → `plan` (ends with `contact_sheet.png`) → `approve --workspace` → `render`. The guide runs scraping after planning; here it runs before, because the script writer picks visuals from files that exist (`inferred` simplification: sourcing is keyed on the brief's asset descriptions, plus any script.json beat narration already pointing at the asset). No `synthesize`, no CLIP, no confidence threshold beyond `--min-score` on the lexical score. See [[architecture/decisions]].

## Open questions
- `ambiguous` — CLI names drift across the guide: `montage plan` / `montage new` (§6) / `pnpm montage new`; §3 lists `init, plan, scrape, synthesize, open` but not `approve`, `render`, `new`.
- `inferred` — stage 2's "below threshold confidence" fallback has no threshold, cost cap, or per-provider budget. The comparison matrix claims a "cost check" gate the pipeline never defines.
- `inferred` — thinnest runnable path is 1 → 1b → 3 → 5 with local assets only (Piper, no scraping, no gen, no canvas). Everything else is additive.
