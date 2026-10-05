---
date:    2026-10-04
project: MontageStudio
session: cc967385-e5e1-44b7-920d-687a560be679
---
# JWST re-run through the upgraded pipeline: QC 7/7, publish pack ready, upload still manual

## What I did
- **Re-plan** on the real `workspace/jwst-unfold`, at n@uti's request. The script.json had no approval record, so the gate printed its table and continued: 9 beats, an estimated 83 s, 5/5 brief points covered. Beat 1 is now spoken "twenty twenty-one" while the caption still shows "2021." 28 stale wavs (56 files) were pruned. The plan came out at 81.4 s.
- **Preview** at 480p: light QC 5/5.
- **Approve.** Done on n@uti's instruction to run through to publish-pack. The note in approval.json says n@uti had not watched the preview yet. 17 asset files were pinned.
- **Render** with `--profile 1080p30-h264`: QC 7/7, 2443/2443 frames, −14.0 LUFS, −1.5 dBTP. It overwrote the earlier master at the same path.
- **publish-pack** wrote `publish/youtube.md`: 8 credited assets, 6 chapters. I saved the generated original as `cache/youtube.publish-pack.orig.md`. Then, at n@uti's request, I merged in from the hand-written `youtube.md`: the description, the tags, the title suffix "| A Short Documentary", and the category / not-for-kids / thumbnail settings (shot_06 = mirror wing, frame 1375, about 0:46). I fixed chapter 5's truncated title to "344 single points of failure".
- Opened the 1080p render in the local player for n@uti's review.
- **Upload: not done.** The browser file-upload tool caps an upload at 10 MB and only takes files shared with the session; the master is about 64 MB. Upload stays manual.

## Decisions
- none
- Correction to [[2026-10-04-jwst-first-publish-run]]: it said the script was "64.1 s" and QC counted "1923/1923 frames". The old master probes at 81.7 s. That 64.1 s figure was wrong (extracted: ffprobe). The new 81.4 s plan is not a regression.

## Touched
- [[MontageStudio]]
- `workspace/jwst-unfold/` (local only, gitignored): scene_plan.json, captions.json, assets/narration/, contact_sheet.png, cache/preview.mp4, approval.json, renders/…-1080p30-h264.mp4, publish/youtube.md

## Next
- n@uti: watch the render (listen to "Celsius" and "three hundred and forty four"), then upload in YouTube Studio using `publish/youtube.md`. That makes it the first published video and meets the done test.
- publish-pack follow-ups seen live:
  - The description defaults to the raw brief.
  - Chapter titles come from the first clause of a beat and can be cut off.
  - Tags are single words from the brief.
  - The AI-script disclosure ignores script.json's writer.
- Fund the API key and rotate both pasted keys. The Claude writer and `--vision` have still never run live.
- The harness: this whole session went to MontageStudio.
