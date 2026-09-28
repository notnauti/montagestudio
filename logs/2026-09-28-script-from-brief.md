---
date:    2026-09-28
project: MontageStudio
session: db198985-1b79-4812-8704-7470ea46b5a2
---
# Script-from-brief step built: brief → script → Piper → scene_plan

Fifth log from this session, after [[2026-09-28-slice-one-validator-renderer]].

## What I did
- User choices (asked): Claude with Ollama fallback; Piper in a project `.venv`; LLM picks each beat's asset from a described list.
- `src/montage/`: `brief.py` (brief.json validator; unknown fields rejected), `script.py` (Claude + Ollama writers, shared prompt/schema/validation, auto fallback only on unavailability), `narrate.py` (Piper per beat, hash-keyed cache), `compose.py` (explainer timeline → scene_plan), `planner.py` + `montage plan` CLI (reuses `script.json`, `--rewrite`, `--writer`).
- Project `.venv`: piper-tts 1.8.0, anthropic 1.8.0, pytest 9.1.1, package editable. onnxruntime 1.30.0 segfaulted on import (faulthandler: access violation in `_pybind_state`); pinned 1.20.1 (the harness venv's version) — imports clean, `pip check` clean. Voice en_US-lessac-medium (63 MB) in `voices/`. Project `.gitignore`: `.venv/`, `voices/`, `*.egg-info/`.
- Checked the SDK's no-credential failure offline (isolated HOME, dead base_url): `TypeError: Could not resolve authentication method…` — the case auto-fallback matches.
- Tests: 62 passed (was 39). New: brief rules, Claude request shape (model, `fallbacks="default"`, beta header, adaptive thinking, json_schema) + refusal / max_tokens / no-credentials with a fake client, Ollama request with fake urlopen, auto-fallback rules, composition timing, in-point reuse, too-long-line error; e2e with real Piper → plan → render → QC passing, and the wav cache verified by unchanged mtimes.
- One code bug found by a test: reused video restarted at frame 0 while fresh footage remained; now takes the source's tail first.
- Live model run failed both ways on machine memory: Ollama qwen2.5:7b and 3b could not allocate (~1.3–1.6 GB; commit 59.9 / 61.2 GB). Claude not called — no approval to spend. `montage plan` reported both cleanly (exit 2).
- Docs: COMMANDS.md (plan command, venv setup), project CLAUDE.md tree + status, feature inventory (Plan, Audio, Export built), contract note.

## Corrections
- [[2026-09-28-slice-one-scene-plan-fields]] and its decision said "Piper gives no word timestamps" (my claim, unchecked). piper-tts 1.8 exposes phoneme alignments (`include_alignments=True`); captions in slice 2 may not need a separate aligner. Contract note updated; the old log is left as written.
- The auth-permission note: reading the harness `.env` to check for keys was denied by the auto-mode classifier; not pursued. Whether Claude credentials exist on this machine is unknown.

## Decisions
- Claude-first script writer, Ollama only when unavailable; `script.json` is the hand-edit point.
- Explainer timing: crossfade inside the next beat's lead-in; reused video advances, then tail, then restart; too-long line is an error.
- Project `.venv` with onnxruntime pinned 1.20.1.

## Touched
- [[MontageStudio]]
- [[CLAUDE]] (project)
- [[COMMANDS]]
- [[(C) feature-inventory]]
- [[(C) scene-plan-contract]]
- [[decisions]]

## Next
- Reboot to clear the commit charge; then one live `montage plan` (Claude if credentials resolve, else Ollama) and read the script.
- Render that plan at 1080p with default threads — also confirms the earlier malloc failure was the machine.
- Watch the video against the done test. Harness first.
