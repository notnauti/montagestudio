---
date:    2026-10-08
project: MontageStudio
session: dde94c89-fab3-4ff5-9d1b-62226680dfac
---
# First video published: JWST explainer on YouTube

## What I did
- Checked the 1080p render before upload: ffprobe gave 1920x1080 at 30 fps, h264 + aac, 81.5 s, 2443 frames (matches the 10-04 QC). I looked at 21 frames taken every 4 s: no black or broken shots, each image matches its beat, and the captions show the written words ("2021."). I could not listen to the audio.
- **Found a publish-pack thumbnail bug.** The pack suggested the first frame of shot_06 (frame 1375, about 0:46). shot_06 starts at frame 1375 and the 12-frame crossfade from shot_05 runs on until 1387, so that frame is a double exposure of the sunshield and the mirror (extracted: frame grab). I made a clean thumbnail instead, a 1280x720 cover crop of `assets/jwst-mirror-wing.jpg`, saved as `workspace/jwst-unfold/publish/thumbnail.jpg`.
- Opened YouTube Studio upload and Explorer with the file selected. **n@uti uploaded and published it.** n@uti listened during processing and said the audio sounded fine, "Celsius" and "three hundred forty-four" included.
- Updated the project `CLAUDE.md` status line: first video published.

## Decisions
- none
- The video URL was not given, so it is not recorded here. n@uti confirmed it was published; I did not check it myself.

## Touched
- [[MontageStudio]]
- [[2026-10-04-jwst-replan-publish-pack]]
- [[2026-10-08-spec-gap-audit-status-line]]
- `03 Projects/MontageStudio/CLAUDE.md` (status line)
- `workspace/jwst-unfold/publish/thumbnail.jpg` (local only, gitignored)

## Next
- Done test: met for slice 1, except "studio refine". Decide whether that step stays before starting any Tier 2/3 work. The real next signal is viewers, not code.
- Record the YouTube URL and check views and retention after about a week. That is the first external data this project has.
- Fix publish-pack: suggest a thumbnail frame after the crossfade (clip start + transition frames, or the clip's midpoint), not the clip's start frame.
- Still open: fund the API key and rotate both pasted keys (the Claude writer and `--vision` have never run live); the other publish-pack follow-ups from 10-04.
