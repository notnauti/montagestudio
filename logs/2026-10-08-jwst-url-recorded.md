---
date:    2026-10-08
project: MontageStudio
session: dde94c89-fab3-4ff5-9d1b-62226680dfac
---
# JWST video URL recorded; live title and thumbnail are not the pack's

## What I did
- Recorded the URL n@uti gave: https://www.youtube.com/watch?v=uicRn1QgrsU, on channel @notnauti. Checked it through YouTube oEmbed and the public watch page:
  - **Live, and the description is from the pack:** it opens "Christmas morning, 2021: a rocket on the coast of French Guiana…" (extracted).
  - **The title is the filename:** "how the james webb space telescope unfolded 1080p30 h264". It should be "How the James Webb Space Telescope Unfolded | A Short Documentary" (extracted: oEmbed).
  - **The thumbnail is YouTube's auto frame,** the Cosmic Cliffs with the burned-in caption "newborn stars, glowing", not `publish/thumbnail.jpg` (extracted: `hqdefault.jpg`). Two possible causes (ambiguous): the custom thumbnail was never applied (custom thumbnails need a phone-verified channel), or YouTube's image cache had not refreshed yet.
- Added the URL to the status line in the project `CLAUDE.md`.

## Decisions
- none

## Touched
- [[MontageStudio]]
- [[2026-10-08-jwst-first-video-published]]
- `03 Projects/MontageStudio/CLAUDE.md` (status line)

## Next
- n@uti, in YouTube Studio → Details: set the title to "How the James Webb Space Telescope Unfolded | A Short Documentary" and upload `workspace/jwst-unfold/publish/thumbnail.jpg`. If the thumbnail option is greyed out, verify the channel first (youtube.com/verify).
- Check views and retention around 2026-10-15.
- Still open: fund the API key and rotate both pasted keys; the other publish-pack follow-ups from 10-04.
