# Commands & Skills

Quick reference for all available skills and commands in this project.

## Skills (in 04 Skills/)

_No project-specific skills yet._

## Commands

Run from the project folder with the project venv: `.venv/Scripts/python.exe -m montage …` (the package is installed there editable, with piper-tts, anthropic and pytest; onnxruntime is pinned to 1.20.1 because 1.30 segfaults on import on this machine).

Order of a run: `init` → `scrape` → `plan` → look at `contact_sheet.png` → `approve` → `render`; `mcp` for agent edits (re-run `contact-sheet` + `approve` after any edit).

Exit codes everywhere: 0 ok, 1 invalid input (brief, script, plan, pipeline, arguments, an unapproved plan, an asset no source could supply), 2 a tool, model or network failed, 3 QC failed.

| Command | What it does |
|---|---|
| `montage init <dir> [--pipeline explainer\|documentary\|promo]` | Scaffold a workspace: `assets/` + a template `brief.json` for that pipeline. Never overwrites a brief |
| `montage scrape <workspace> [--sources …] [--max-mb 50] [--min-score …] [--dry-run]` | Fill the brief's wanted visuals from Wikimedia Commons, NASA, Archive.org. Keyword ranking (no CLIP); downloads PD / CC0 / CC BY / CC BY-SA only; writes `manifest.json` with source, license, attribution |
| `montage plan <workspace>/brief.json [--writer auto\|claude\|ollama] [--rewrite]` | Brief → `script.json` (Claude `claude-opus-5`, or local Ollama `qwen2.5:7b` when no Claude credentials) → Piper narration + word timings (`captions.json`) → `scene_plan.json` with caption track, validated → `contact_sheet.png`. An existing `script.json` is reused — edit it by hand and re-run; `--rewrite` asks the model again |
| `montage plan --topic "…" [--pipeline …] [--target-duration S] [--workspace DIR]` | Same, but writes `brief.json` from a pipeline recipe first (default dir `workspace/<topic-slug>`). Needs media already in `assets/` |
| `montage contact-sheet <workspace> [-o out.png]` | Redraw the storyboard grid (one frame per visual clip, beat, clip id, narration excerpt) |
| `montage approve --workspace <dir> [--note …]` | Pin `scene_plan.json` + every asset by sha256 in `approval.json`. Needs a contact sheet at least as new as the plan |
| `montage validate <workspace>/scene_plan.json` | Check a plan against the contract; lists every error at once |
| `montage normalize <plan> [--fix]` | Report asset mismatches (sample rate, VFR, pixel format, aspect); `--fix` writes copies to `cache/normalized/` |
| `montage render <plan> [-o out] [--profile 1080p30-h264\|720p30-h264\|4k60-prores] [--encoder software\|auto\|h264_nvenc\|h264_qsv\|h264_amf] [--no-captions] [--skip-normalize] [--skip-qc] [--force]` | Approval check → normalize → two-pass render (−14 LUFS) with burned-in captions → QC against the profile. Refuses an unapproved or changed plan unless `--force`. Default output `<workspace>/renders/<title>[-<profile>].<ext>`; no `--profile` = the plan's own grid in H.264 |
| `montage mcp <workspace>` | MCP server over stdio editing `scene_plan.json`: `timeline_split_clip`, `timeline_trim_boundary`, `timeline_apply_easing` (scale only), `timeline_retime_captions`, `asset_replace_broll`, `inspect_frame_context`, `plan_summary`. Every write validated and atomic. Host config: `03 System/(C) mcp-config-example.md` |
| `.venv/Scripts/python.exe -m pytest -q` | 247 passed, 1 skipped (2026-10-04). Model calls and network mocked; real ffmpeg renders and real Piper runs included. The skip is the live network test — run it with `MONTAGE_LIVE_NET=1` |

Pipeline recipes live in `pipelines/*.json` (beat pacing, tone, timing, `captionStyle`); a brief's `recipe` field names one.

A workspace is a folder holding `brief.json` (format in `src/montage/brief.py`), `assets/`, and what the commands write: `manifest.json`, `script.json`, `assets/narration/`, `captions.json`, `scene_plan.json`, `contact_sheet.png`, `approval.json`, `cache/`, `renders/`. Every `src` must sit inside `assets/`.

Setup on a new machine: `python -m venv .venv`, `.venv/Scripts/python.exe -m pip install -e . piper-tts anthropic pytest "onnxruntime==1.20.1"`, then `.venv/Scripts/python.exe -m piper.download_voices --download-dir voices en_US-lessac-medium`. Claude uses `ANTHROPIC_API_KEY` or an `ant auth login` profile. The `montage` command reads `ANTHROPIC_API_KEY` from the project's `.env` (gitignored by the vault-root `.gitignore`); a variable already set in the shell wins, and tests never load it.
