# Commands & Skills

Quick reference for all available skills and commands in this project.

## Skills (in 04 Skills/)

_No project-specific skills yet._

## Commands

Run from the project folder. Without `pip install -e .`, prefix with `PYTHONPATH=src python -m` (e.g. `PYTHONPATH=src python -m montage validate …`).

| Command | What it does | Exit |
|---|---|---|
| `montage validate <workspace>/scene_plan.json` | Check a plan against the slice 1 contract; lists every error at once | 0 ok, 1 invalid |
| `montage render <plan> [-o out.mp4] [--skip-qc]` | Validate → two-pass render (−14 LUFS) → QC. Default output `<workspace>/renders/<title>.mp4` | 0 ok, 1 invalid, 2 render failed, 3 QC failed |
| `python -m pytest -q` | 39 tests: validator rules (fake probe) + real-ffmpeg renders at 320×180 | |

A workspace is a folder holding `scene_plan.json` and `assets/`; every `src` must sit inside `assets/`.
