# MontageStudio

An agentic video pipeline. You write a brief. MontageStudio turns it into a script, synthetic narration with word-timed captions, and licensed archive visuals. It builds a validated `scene_plan.json` from those, stops at review gates, and renders a QC-checked video with a YouTube publish pack.

It uses stdlib Python and drives the `ffmpeg` / `ffprobe` command-line tools. There are no pip dependencies in the core package.

**Status:** two videos published with it so far: a JWST explainer (2026-10-08) and The Voyager Golden Record (2026-10-26). 494 tests pass, 1 is skipped. The `project.ts` compiler and the interactive canvas from the architecture guide are not built (deferred).

## How a run works

```
brief.json
  → scrape          licensed visuals from Wikimedia Commons, NASA, Archive.org (top 3 per asset; --pick to override)
  → plan            script (Claude, or local Ollama) → script gate → speakable text → Piper narration
                    + word timings → scene_plan.json → contact sheet
  → preview         fast 480p animatic with captions
  → mcp + view      optional agent edits to scene_plan.json, watched live on a local page
  → approve         pins scene_plan.json and every asset by sha256
  → render          normalize → two-pass −14 LUFS render, captions burned in → QC
  → publish-pack    YouTube title, chapters, credits from the manifest, disclosures, thumbnail candidates
  → stats           production time per video, and views at 7 and 28 days
```

There are two human gates. **Script:** a model draft stops before narration until you approve it. **Storyboard:** `render` refuses a plan or asset that changed after `approve`.

## Setup

Requirements: Python ≥ 3.10, with `ffmpeg` and `ffprobe` on `PATH`.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e . piper-tts anthropic pytest "onnxruntime==1.20.1"
.venv/Scripts/python.exe -m piper.download_voices --download-dir voices en_US-lessac-medium
```

On Linux or macOS, use `.venv/bin/python`. onnxruntime is pinned because 1.30 segfaults on import on the development machine.

Claude features (script writer, `--suggest-queries`, `--vision`, `publish-pack --draft`) need `ANTHROPIC_API_KEY`, either in the shell or in a `.env` file at the project root (gitignored). Without it, `plan --writer ollama` uses a local Ollama `qwen2.5:7b`.

## Quick start

```bash
montage init workspace/my-video --pipeline documentary   # explainer | documentary | promo
# edit workspace/my-video/brief.json: title, premise, target seconds, wanted visuals (+ optional queries)
montage scrape workspace/my-video                         # then look at cache/candidates.png
montage plan workspace/my-video/brief.json                # stops at the script gate
montage plan workspace/my-video/brief.json --approve-script
montage preview workspace/my-video                        # watch cache/preview.mp4
montage approve --workspace workspace/my-video
montage render workspace/my-video/scene_plan.json --profile 1080p30-h264
montage publish-pack workspace/my-video --hands-on-min 30
```

Run `montage` as `.venv/Scripts/python.exe -m montage` if the script isn't on `PATH`. `COMMANDS.md` documents every command and flag.

Exit codes: `0` ok · `1` invalid input or an unapproved plan · `2` a tool, model or network failed · `3` QC failed.

## Asset licensing

`scrape` downloads only public domain, CC0, CC BY and CC BY-SA files. NonCommercial and NoDerivatives files are refused. NASA items are accepted under NASA's media guidelines unless their metadata credits a third party. Each asset's source, license and attribution are recorded in the workspace's `manifest.json`, and `publish-pack` copies the credits from there.

`asset_replace_broll` (MCP) does **not** check licences: it accepts any local file. Bring new media in through `scrape`.

## Layout

| Path | What |
|---|---|
| `src/montage/` | the package: CLI, sourcing, script + gate, narration/alignment, planner, renderer, QC, MCP server, view page, publish pack |
| `tests/` | pytest suite. Uses real ffmpeg and Piper, with model and network calls mocked; `MONTAGE_LIVE_NET=1` runs the live network test |
| `pipelines/` | recipes (`explainer`, `documentary`, `promo`): beat pacing, tone, timing, caption style |
| `data/` | the `scene_plan.json` contract (field reference) |
| `architecture/decisions.md` | why things are built the way they are |
| `00 Specifications/` | architecture guide and the production-cadence PRD |
| `logs/` | session logs and `production-ledger.jsonl` (one row per video) |
| `workspace/` | per-video working folders. Gitignored, because media, narration and renders are large and can be regenerated |

`scene_plan.json` is the contract between planning and rendering. Any change to its shape is recorded in `architecture/decisions.md`.

## Tests

```bash
.venv/Scripts/python.exe -m pytest -q
```

The `montage view` browser test needs Chrome or Edge (`MONTAGE_TEST_CHROME` can point at one) and skips without it.

## Note

This repo is split out of a private Obsidian vault. `CLAUDE.md`, `logs/` and the specs contain `[[wikilinks]]` to vault notes that don't exist here.
