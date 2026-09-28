# Commands & Skills

Quick reference for all available skills and commands in this project.

## Skills (in 04 Skills/)

_No project-specific skills yet._

## Commands

Run from the project folder with the project venv: `.venv/Scripts/python.exe -m montage …` (the package is installed there editable, with piper-tts, anthropic and pytest; onnxruntime is pinned to 1.20.1 because 1.30 segfaults on import on this machine).

| Command | What it does | Exit |
|---|---|---|
| `montage plan <workspace>/brief.json [--writer auto\|claude\|ollama] [--rewrite]` | Brief → `script.json` (Claude `claude-opus-5`, or local Ollama `qwen2.5:7b` when no Claude credentials) → Piper narration in `assets/narration/` → `scene_plan.json`, validated. An existing `script.json` is reused — edit it by hand and re-run; `--rewrite` asks the model again | 0 ok, 1 invalid brief/script/plan, 2 model or Piper failed |
| `montage validate <workspace>/scene_plan.json` | Check a plan against the slice 1 contract; lists every error at once | 0 ok, 1 invalid |
| `montage render <plan> [-o out.mp4] [--skip-qc]` | Validate → two-pass render (−14 LUFS) → QC. Default output `<workspace>/renders/<title>.mp4` | 0 ok, 1 invalid, 2 render failed, 3 QC failed |
| `.venv/Scripts/python.exe -m pytest -q` | 62 tests: validator, brief, script writers (Claude and Ollama mocked — no spend), composition, real-ffmpeg renders, and one real Piper → render → QC run | |

A workspace is a folder holding `brief.json` (format in `src/montage/brief.py`), `assets/`, and what `plan` writes: `script.json`, `assets/narration/`, `scene_plan.json`. Every `src` must sit inside `assets/`.

Setup on a new machine: `python -m venv .venv`, `.venv/Scripts/python.exe -m pip install -e . piper-tts anthropic pytest "onnxruntime==1.20.1"`, then `.venv/Scripts/python.exe -m piper.download_voices --download-dir voices en_US-lessac-medium`. Claude uses `ANTHROPIC_API_KEY` or an `ant auth login` profile.
