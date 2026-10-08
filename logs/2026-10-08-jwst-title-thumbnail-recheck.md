---
date:    2026-10-08
project: MontageStudio
session: dde94c89-fab3-4ff5-9d1b-62226680dfac
---
# JWST re-check: title fixed, thumbnail still auto; vault pushed

## What I did
- Checked the live video again (https://www.youtube.com/watch?v=uicRn1QgrsU):
  - **Title fixed:** it now reads "How the James Webb space telescope unfolded" (extracted: oEmbed). This is n@uti's wording, not the pack's "… | A Short Documentary".
  - **Thumbnail still YouTube's auto frame:** the Cosmic Cliffs with the caption "newborn stars, glowing". `hqdefault` is byte-identical to the earlier check, and `maxresdefault` shows the same frame (extracted).
  - Two possible causes (ambiguous): the custom thumbnail never saved (the channel may not be phone-verified), or i.ytimg is still serving a cached image.
- Updated the status line in the project `CLAUDE.md` with this result.
- Found the inbox task `06 Inbox/(C) 2026-10-08-jwst-youtube-title-thumbnail.md` (added in commit dc38ab6) deleted on disk, and not by me. **The deletion was not committed.** Its thumbnail item is still open, and the vault rules leave inbox deletions to n@uti or the weekly review.
- Pushed `master` to origin at n@uti's explicit request (the save skill's never-push rule was overridden).

## Decisions
- none

## Touched
- [[MontageStudio]]
- [[2026-10-08-jwst-url-recorded]]
- `03 Projects/MontageStudio/CLAUDE.md` (status line)

## Next
- n@uti: Studio → the video → Details → Thumbnail → upload `workspace/jwst-unfold/publish/thumbnail.jpg`. If the option is greyed out, go to youtube.com/verify. Then re-check in a private window.
- n@uti: decide on the inbox file deletion. Either commit it or restore it with `git restore "06 Inbox/(C) 2026-10-08-jwst-youtube-title-thumbnail.md"`.
- Check views and retention around 2026-10-15.
