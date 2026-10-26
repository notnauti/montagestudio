# Commands & Skills

Quick reference for all available skills and commands in this project.

## Skills (in 04 Skills/)

_No project-specific skills yet._

## Commands

Run from the project folder with the project venv: `.venv/Scripts/python.exe -m montage …` (the package is installed there editable, with piper-tts, anthropic and pytest; onnxruntime is pinned to 1.20.1 because 1.30 segfaults on import on this machine).

Order of a run: `init` → `scrape` (look at `cache/candidates.png`, `--pick` where #1 is wrong) → `plan` (stops at the script gate on a model draft) → `plan --approve-script` → watch `preview` or look at `contact_sheet.png` → `approve` → `render` → `publish-pack` (asks for hands-on minutes) → upload → `stats` at 7 and 28 days; `mcp` for agent edits (re-run `preview` or `contact-sheet` + `approve` after any edit).

Every command run on a workspace appends a line (command, argv, start, seconds, exit) to `<ws>/production_log.jsonl`; a logging failure never fails the command. `publish-pack` and `stats` turn it into `<ws>/publish/production_summary.json` and mirror that into `logs/production-ledger.jsonl` (one row per video, versioned). Tests write no ledger unless `MONTAGE_LEDGER` names one.

Exit codes everywhere: 0 ok, 1 invalid input (brief, script, plan, pipeline, arguments, an unapproved plan, an asset no source could supply), 2 a tool, model or network failed, 3 QC failed. `plan` also exits 0 when it stops at the script gate, so `plan && render` stops quietly on an unapproved draft.

| Command | What it does |
|---|---|
| `montage init <dir> [--pipeline explainer\|documentary\|promo]` | Scaffold a workspace: `assets/` + a template `brief.json` for that pipeline. Never overwrites a brief |
| `montage scrape <workspace> [--sources …] [--max-mb 50] [--min-score …] [--dry-run] [--no-candidates] [--vision]` | Fill the brief's wanted visuals from Wikimedia Commons, NASA, Archive.org. Keyword ranking (distinctive-term tie-breaks, scene-type penalty); downloads PD / CC0 / CC BY / CC BY-SA only; writes `manifest.json` with source, license, attribution. Keeps the top 3 previews per asset in `cache/candidates/` and draws `cache/candidates.png`; `--dry-run` downloads previews only. `--vision` re-ranks with Claude vision (exit 2 without credentials; never run live) |
| `montage scrape <workspace> --pick <asset>=<n> [--pick …]` | Download candidate n (1-3) from the last scrape's `candidates.json` for that asset (id, file name or src); license re-checked, manifest updated; a failed pick leaves the asset untouched |
| `montage plan <workspace>/brief.json [--writer auto\|claude\|ollama] [--rewrite] [--approve-script] [--no-script-gate]` | Brief → `script.json` (Claude `claude-opus-5-5`, the one model constant for writer, vision and drafts, or local Ollama `qwen2.5:7b` when no Claude credentials) → **script gate** (beat table, estimated runtime at 3.1 words/s, brief-point coverage; a model draft stops here until approved, and an unedited draft >15% over target or over the recipe's beat limit is rejected, exit 1) → speakable text (numbers, units, years; optional `pronunciations.json`) → Piper narration + word timings (`captions.json`, written words kept) → `scene_plan.json` with caption track, validated → `contact_sheet.png`; stale narration wavs pruned. An existing `script.json` is reused — edit it by hand and re-run (once approved, an edit needs re-approval); `--rewrite` asks the model again |
| `montage approve-script <workspace \| brief.json> [--note …]` | Print the gate table and record the script's sha256 in `script_approval.json`, without narrating |
| `montage plan --topic "…" [--pipeline …] [--target-duration S] [--workspace DIR]` | Same, but writes `brief.json` from a pipeline recipe first (default dir `workspace/<topic-slug>`). Needs media already in `assets/` |
| `montage contact-sheet <workspace> [-o out.png]` | Redraw the storyboard grid (one frame per visual clip, beat, clip id, narration excerpt) |
| `montage preview <workspace \| plan> [-o out.mp4] [--no-captions] [--skip-normalize] [--skip-qc]` | Fast 480p animatic of the plan, approved or not, with captions and narration → `cache/preview.mp4` + `.source.json` hash record; light QC (no loudness check) |
| `montage approve --workspace <dir> [--note …]` | Pin `scene_plan.json` + every asset by sha256 in `approval.json`. Needs a fresh `contact_sheet.png` or `cache/preview.mp4`, judged by its `.source.json` sha256 record of plan + assets (sheets without a record are refused) |
| `montage validate <workspace>/scene_plan.json` | Check a plan against the contract; lists every error at once |
| `montage normalize <plan> [--fix]` | Report asset mismatches (sample rate, VFR, pixel format, aspect); `--fix` writes copies to `cache/normalized/` |
| `montage render <plan> [-o out] [--profile 1080p30-h264\|720p30-h264\|4k60-prores] [--encoder software\|auto\|h264_nvenc\|h264_qsv\|h264_amf] [--no-captions] [--skip-normalize] [--skip-qc] [--force]` | Approval check → normalize → two-pass render (−14 LUFS) with burned-in captions → QC against the profile. Refuses an unapproved or changed plan unless `--force`. Default output `<workspace>/renders/<title>[-<profile>].<ext>`; no `--profile` = the plan's own grid in H.264 |
| `montage publish-pack <workspace> [--force] [--draft] [--hands-on-min N]` | Write `publish/youtube.md`: title, chapters from beat timings, a credit block per asset copied from `manifest.json`, disclosures, draft tags, and a `## Thumbnail` section: up to 3 candidates in `publish/thumb_candidates/` (one frame per source file, at least 6 frames clear of any cut or crossfade, captions off, 1280 px JPEG under 2 MB; a failed grab is listed, not fatal). `--draft`: Claude writes the title, description intro, chapter titles and tags from the approved script (credits and disclosure stay copied from the records; exit 2 without credentials, or when the draft fails, which leaves the brief's text in the pack). Refuses an unapproved plan, a stale render, missing attribution, a disallowed license or a sha256 mismatch. Never overwrites without `--force`; never touches `<ws>/youtube.md`. Then writes the production summary (wall time per command, hand edits, hands-on minutes; asked on a terminal when not given) and its ledger row |
| `montage stats <workspace> [--hands-on-min N] [--url URL] [--day 7\|28 --views N [--avg-view-seconds S]]` | No flags: show the production summary (derived now if none is saved). With flags: record them, re-derive the rest, write the summary and its ledger row. Never writes the publish pack, so it backfills a video made before logging (e.g. JWST) |
| `montage mcp <workspace>` | MCP server over stdio editing `scene_plan.json`: `timeline_split_clip`, `timeline_trim_boundary`, `timeline_apply_easing` (scale only), `timeline_retime_captions`, `asset_replace_broll`, `inspect_frame_context`, `plan_summary`. Every write validated and atomic. Host config: `03 System/(C) mcp-config-example.md` |
| `.venv/Scripts/python.exe -m pytest -q` | 450 passed, 1 skipped (2026-10-26). Model calls and network mocked; real ffmpeg renders and real Piper runs included. The skip is the live network test — run it with `MONTAGE_LIVE_NET=1` |

Pipeline recipes live in `pipelines/*.json` (beat pacing, tone, timing, `captionStyle`); a brief's `recipe` field names one.

A workspace is a folder holding `brief.json` (format in `src/montage/brief.py`), `assets/`, and what the commands write: `manifest.json`, `script.json`, `script_approval.json`, `assets/narration/`, `captions.json`, `scene_plan.json`, `contact_sheet.png` (+ `.source.json`), `approval.json`, `cache/` (candidates, preview), `renders/`, `publish/`. Optional input: `pronunciations.json` (`{"Webb": "web"}`, one word per key). Every `src` must sit inside `assets/`.

Setup on a new machine: `python -m venv .venv`, `.venv/Scripts/python.exe -m pip install -e . piper-tts anthropic pytest "onnxruntime==1.20.1"`, then `.venv/Scripts/python.exe -m piper.download_voices --download-dir voices en_US-lessac-medium`. Claude uses `ANTHROPIC_API_KEY` or an `ant auth login` profile. The `montage` command reads `ANTHROPIC_API_KEY` from the project's `.env` (gitignored by the vault-root `.gitignore`); a variable already set in the shell wins, and tests never load it.
