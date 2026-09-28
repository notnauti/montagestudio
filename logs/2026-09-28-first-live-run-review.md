---
date:    2026-09-28
project: MontageStudio
session: db198985-1b79-4812-8704-7470ea46b5a2
---
# First live run: Ollama script, hand edit, 1080p render, review page

Sixth log from this session, after [[2026-09-28-script-from-brief]]. No vault files changed; everything ran in session scratch space.

## What I did
- `--writer claude` twice: `no Claude credentials` both times (no `ANTHROPIC_API_KEY` in the shell, no `ant` CLI installed). Nothing sent, nothing charged.
- After the user's reboot (06:54; commit 20.5 / 30.4 GB): `montage plan --writer ollama` on the GPS demo brief — qwen2.5:7b, 5 beats in 67 s, 36.5 s plan, validated.
- 1080p render at default threads: 36 s, QC 5/5 (1096 frames, −14.1 LUFS, −1.5 dBTP). Confirms the earlier `x264 malloc failed` was the machine, not the renderer.
- Script review: the 7B draft skipped the brief's payoff (clock correction) and the 38 µs figure, had a confusing beat 4, filler, and a static still. Rewrote `script.json` by hand (original kept as `script.ollama.json`); re-plan reused it (no model call), re-voiced all 5 beats, 40.9 s vs 40 s target.
- Re-render: 51 s, QC 5/5 (1227/1227 frames, −14.0 LUFS, −1.5 dBTP). Plan check: crossfades end exactly where the next line starts; orbit.mp4 in-point advanced to frame 228 on reuse; 1.2 s end hold.
- Review artifact published (private): https://claude.ai/artifact/N2kn1bdknG4z1G7LnmqfHG — 720p review copy (7.9 MB; master 73.6 MB is over the 15 MB file limit), shot/voice timeline, per-beat cards, composer-only comments. Player and comment button not exercised by me.

## Findings
- Script quality from a 7B local model is below the "would publish" bar for this brief; the edit point (`script.json`) worked as designed.
- Narration cache keeps stale wavs when lines change (10 files for 5 beats after one edit). Harmless; a prune step is small if wanted.
- "Around ten kilometres a day" in beat 4 is the commonly cited figure, not checked against a primary source.

## Decisions
- none

## Touched
- [[MontageStudio]]

## Next
- Listen to the render on the review page; leave per-beat comments.
- Switch to Agent Harness OS (`/resume`) — MontageStudio took the whole day while the primary target sat.
- MontageStudio later: one real brief with own assets in `workspace/`; Claude credentials or accept hand-editing scripts; then scope slice 2 (canvas refine + captions via Piper alignments).
