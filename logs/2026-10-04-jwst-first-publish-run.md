---
date:    2026-10-04
project: MontageStudio
session: ab843154-e0ce-4039-bd56-f6c4b30a51f3
---
# First real publish run: JWST explainer rendered, QC 7/7, ready for YouTube

## What I did
- **Brief.** "How the James Webb Space Telescope Unfolded", explainer recipe, 70 s target, 8 wanted stills, in `workspace/jwst-unfold/`. The brief, the media and the upload text stay local (see Decisions).
- **Scrape.** All 8 assets ended up licensed and correct, but only after manual steering:
  - Every asset needed an explicit `query`. Searches built from the descriptions were too generic.
  - I looked at a grid of the downloads, since nothing checks image content. Two were wrong: the ops room for "mirror wing", and a robot arm for "full mirror". On re-scrape, the first replacement for the wing was an artist sketching.
  - The final wing shot is "NASA's Webb Telescope Extends a Wing". Getting it meant rewording the asset description, because the ranker kept choosing the ops-room photo.
  - Several of the best Commons shots ("Golden Mirror Revealed", "Wings Open") are CC BY-NC-ND and were correctly refused.
- **Claude writer: blocked.** The key authenticates but the org has zero credit (`req_011CfgYmSz4EZ6miKMDf29pX`). The user set it as `ANTHROPIC_KEY`, then `ANTROPHIC_KEY`; the code reads `ANTHROPIC_API_KEY`. Both keys were pasted into the transcript, so both should be rotated.
- **Ollama.** qwen2.5:7b and then 3b failed to allocate memory (2.4 GB RAM free; the commit limit was nearly exhausted). 3b ran once memory freed up (`MONTAGE_OLLAMA_MODEL=qwen2.5:3b`). Its draft wasn't publishable: 18 beats and 89 s, invented filler ("hide and seek with the sun"), the launch shot last, the infrared/cold reason missing.
- **Script.** At n@uti's call I rewrote `script.json` by hand: 9 beats, 64.1 s, written by `ollama:qwen2.5:3b, hand-edited`. The 3b draft is kept in `cache/script.qwen3b-draft.json`. Facts in the narration (6.5 m mirror, 344 single points of failure, about two weeks of deployment, below −200 °C) come from my knowledge of NASA's mission facts, not sources checked this session (inferred).
- **Gate and render.** Contact sheet reviewed, n@uti approved; render with `--profile 1080p30-h264`. QC 7/7: 1923/1923 frames, 1920x1080 at 30 fps, h264 yuv420p, aac, −14.0 LUFS, −1.5 dBTP. Captions checked on three frames.
- **Upload text.** `workspace/jwst-unfold/youtube.md` has the title, the description with all 8 attributions from manifest.json, tags, and the synthetic-content flag.
- Added `workspace/` to the project `.gitignore`, with permission.
- Created the reminder `06 Inbox/(C) 2026-10-04-claude-writer-credits.md` at the user's request.

## Decisions
- Workspaces are local and unversioned: the `workspace/` gitignore entry is in [[architecture/decisions]]. Provenance lives in each workspace's manifest.json and approval.json.

## Touched
- [[MontageStudio]]
- [[architecture/decisions]]
- [[06 Inbox/(C) 2026-10-04-claude-writer-credits]]
- `.gitignore`
- `workspace/jwst-unfold/` (local only)

## Next
- n@uti watches the full render, listening especially to how Piper says "Celsius" and "three hundred and forty four", then uploads it to YouTube using `youtube.md`. Nothing is published until that happens.
- Claude writer: buy credits, set `ANTHROPIC_API_KEY`, rotate both pasted keys, re-run with `--writer claude --rewrite` (inbox note). Still never run live.
- Scrape bugs found on the live run (not fixed):
  - `QUERY_TERMS = 5` truncation drops the words that tell assets apart.
  - The stemmer maps "james" to "jame" and sends that to the search APIs.
  - The lexical ranker can't tell a control-room photo titled "Mirror Deployment" from the mirror. Pressure toward CLIP or a vision check.
  - Ties are broken by source order, not by distinctive matches.
- Machine memory is a recurring blocker for local Ollama. 7b needs about 5 GB free.
