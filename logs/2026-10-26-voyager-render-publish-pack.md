---
date:    2026-10-26
project: MontageStudio
session: f2e193c7-5409-48e7-bb9e-44cb9d1ca164
---
# Video 2 (Voyager Golden Record): script rewrite → render QC 7/7 → publish pack

Continues [[2026-10-26-voyager-run-to-script-gate]] in the same session.

## What I did
- **Script:** at n@uti's request, Claude rewrote the Ollama draft by hand in `script.json`. The first pass came to 326 words, about 120 s, which the gate rejected as 33% over. Trimmed to 12 beats and 225 words, about 87 s, 8/8 brief points covered, no warnings. The disc still has 3 beats, but they now alternate with the cover; Jupiter, Pale Blue Dot and the record-mounted asset are now used. n@uti approved it (`plan --writer ollama --approve-script`, 14 s): the scene plan runs 92.2 s and numbers are spoken as words.
- **Preview:** 480p, 92.2 s, rendered in 12.9 s, light QC 5/5. A frame grab at 70 s shows the Pale Blue Dot survives the 16:9 crop, just right of centre (about 2 px at 1080p).
- **Approve + render:** n@uti approved (scene plan + 20 asset files pinned). `render --profile 1080p30-h264`: QC 7/7, −14.0 LUFS, −1.4 dBTP, 2765 frames, 39.6 MB. **Render took 1 min 54 s for 92 s of video, about 1.25× real time** on software libx264.
- **Publish pack:** `--hands-on-min 20` → 8 credits, 6 chapters, 3 thumbnail candidates. Production summary: 20 min hands-on, 5.4 min montage wall time over 12 runs; the ledger row is written. Fixed by hand in `publish/youtube.md` at n@uti's request:
  - the disclosure (see Corrections);
  - the description (the brief's prompt text, replaced with two plain paragraphs);
  - the tags (words pulled from the brief, such as "chaired" and "chose", replaced with 14 search terms).
- Brief copied to `01 Build/(C) 2026-10-26-voyager-golden-record-brief.json` (commit `8f19567`), because `workspace/` is gitignored.

## Findings
- **Publish-pack attribution follows `script.json`'s `writer` field, not who actually wrote the text.** A hand rewrite kept `writer: ollama:qwen2.5:7b`, so the pack's disclosure credited Ollama for narration Claude wrote. The fix in `youtube.md` reads "drafted by ollama:qwen2.5:7b, rewritten by an AI model (Claude, Anthropic) at a person's direction". The production summary only says "script edited", so a heavily AI-rewritten script looks like a lightly hand-edited one. (`inferred`) An approved script can't take a corrected `writer` without a new hash, which voids both approvals. Decide how a rewrite should be attributed before video 3.
- **Without credit, the publish pack's description and tags are unusable.** The description is the brief's prompt text and the tags are brief words. The only fix today is hand editing, about 5 min; `--draft` needs credit.
- Chapter titles are cut from the narration, and chapter 2 is truncated: "A committee chaired by Carl Sagan chose what …".
- (`inferred` from this one run) A render timeout of `max(300 s, 5× length)` would leave plenty of headroom; not built.

## Corrections
- The ledger row for video 2 (`logs/production-ledger.jsonl`) records the writer as `ollama:qwen2.5:7b`. The narration was rewritten by Claude. The row is left as written; this log is the correction.
- [[2026-10-26-voyager-run-to-script-gate]] → Next said "n@uti at the script gate: rewrite `script.json` by hand". Claude did the rewrite, at n@uti's request.

## Decisions
- none

## Touched
- [[MontageStudio]]
- [[2026-10-26-voyager-run-to-script-gate]]
- [[(C) 2026-10-26-voyager-golden-record-brief.json]]

## Next
- **n@uti:** watch the 1080p render, fix chapter 2's title in Studio, and upload with a thumbnail (`thumb_2.jpg`, the gold cover, suggested but not viewed). Record the URL with `montage stats workspace/voyager-golden-record --url …`. If it goes up, that's video 2: 1 of 4 under the cadence done test.
- Before video 3: fund the API credit (it blocks the writer, `--suggest-queries`, `--vision` and `--draft`), and decide how a rewrite is attributed (finding 1).
- Still open from [[2026-10-26-voyager-run-to-script-gate]]: Commons TIF downscale + upload.wikimedia 429 (fix only if video 3 hits them); record-mounted has no real photo of the record mounted.
- Stats at 7 and 28 days after upload.
