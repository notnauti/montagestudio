---
date:    2026-10-26
project: MontageStudio
session: f2e193c7-5409-48e7-bb9e-44cb9d1ca164
---
# Video 2 (Voyager Golden Record): first live run, stopped at the script gate

n@uti chose the topic over a JWST smoke run. Workspace `workspace/voyager-golden-record` (gitignored), documentary recipe, 90 s target, 8 wanted visuals with brief queries. The brief's facts (1977, Sagan committee, 55 languages, 116 images) came from Claude's own knowledge (`inferred`); check them at the gate.

## What ran (from `production_log.jsonl`)
| step | s | exit |
|---|---|---|
| `init --pipeline documentary` | 0.02 | 0 |
| `scrape` (8/8 saved) | 53.5 | 0 |
| `scrape --pick` pale-blue-dot=2, jupiter=1, interstellar=3 | 6.7 | 2 (jupiter failed) |
| `scrape --pick` jupiter=1 (retry) | 0.5 | 2 |
| `plan` (auto → Claude) | 6.2 | 2 |
| `plan --writer ollama` | 111.1 | 0, stopped at the script gate |

## Findings
1. **Claude credit still unfunded** (`extracted`): `plan` got a 400 "credit balance is too low" (req_011CgRr8ERjDcAHtqwAApiYD) and stopped rather than falling back, as designed. Phase 0 blocker confirmed live. `--suggest-queries`, `--vision` and `publish-pack --draft` are blocked for the same reason.
2. **Bug: Commons TIF originals narrower than 1920 px get downscaled.** For a TIF, Commons always returns a `lossy-page1-<N>px` JPEG thumbnail at a standard step *below* the original width. Pale Blue Dot 453×614 was saved at 330×447 (the preview width); Jupiter 800×920 was saved at 500×575. `sources.py` Commons search takes `thumburl` when it is present (line ~309). Not fixed. A fix would request a step at least as wide as the original, or the original via a converter. That is a decision for n@uti; nothing was changed this session.
3. **Bug: `--pick` of a Commons original under 1920 px fails with HTTP 429.** These are served unscaled from `upload.wikimedia.org`, the same host that 429'd on 2026-10-04. That fix moved only *previews* to `thumb.wikimedia.org`. Jupiter #1 (1024×576 JPG) failed twice, so the 500 px TIF thumbnail stays. Same host, so the same fix area as finding 2.
4. **Ranker picks needing a human:** interstellar #1 was a heliosphere model blob, re-picked to #3 (PIA26353, 1920×1080). Pale Blue Dot was re-picked to #2 (PIA23645, now 1920×1900). `record-mounted` has no usable candidate: the top 3 were line drawings of the spacecraft, and the saved file is `File:Voyager.jpg` (a spacecraft photo ranked below the top 3). As a result the candidates sheet shows **no CHOSEN tile** for that row, so a reviewer can't see what was saved (`inferred`: top-3 downloads failed and it fell through; the scrape output head was not kept).
5. **Ollama script is weak at the gate** (`extracted` from the gate table): 11 beats, 126 words, about 54 s against the 90 s target (40% under). Beats 3–8 are **six beats in a row on `voyager-record-disc.jpg`** (a 981×552 still for about 25 s). Jupiter, Pale Blue Dot and record-mounted go unused. Brief point 4 is out of order. "Over 14 billion miles" is true but stale (`ambiguous`; Voyager 1 is roughly 15+ billion miles out in 2026, not checked). The closing line is generic. The gate did its job: no narration was spent on this draft.
6. Hands-on so far is about 10 min of n@uti's time (two choices). Wall time to the gate is about 3 min of commands plus about 2 min for the Ollama draft.

## Decisions
- none

## Touched
- [[MontageStudio]]
- [[2026-10-26-view-ffmpeg-timeouts]]

## Next
- **n@uti at the script gate:** rewrite `script.json` by hand toward about 90 s, spread visuals across beats (Jupiter at "outer planets", Pale Blue Dot before the closing line), fix the mileage line. Then run `montage plan workspace/voyager-golden-record/brief.json --approve-script` → `preview` → `approve` → `render` (time it; the last log wants a render timing before picking a timeout) → `publish-pack`.
- Or fund credit first and re-run with `--writer claude --rewrite`. Either way the credit is the Phase 0 blocker.
- Findings 2 and 3 (Commons resolution and the 429 host) are one fix in `sources.py`. Make it only if a second video hits it; the hand-picks worked around it here.
- Record-mounted: find a real photo or drop the asset from the brief.
