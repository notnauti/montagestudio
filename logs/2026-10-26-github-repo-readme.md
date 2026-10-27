---
date:    2026-10-26
project: MontageStudio
session: f2e193c7-5409-48e7-bb9e-44cb9d1ca164
---
# MontageStudio pushed to a private GitHub repo, with a README

Continues [[2026-10-26-workflow-page-refine-stage]] in the same session.

## What I did
- **Pre-push checks** (`extracted`):
  - tracked files and the whole project history were scanned for key patterns (`sk-ant-`, `ghp_`, AWS) and for personal paths and names; nothing was found;
  - `.env`, `.venv/`, `voices/` and `workspace/` are untracked;
  - the vault repo `notnauti/fuzzy-parakeet` is private;
  - `notnauti/montagestudio` was free.
- n@uti chose a **private** repo **with history**.
- `git subtree split --prefix="03 Projects/MontageStudio" -b montagestudio-split`: 39 commits, 110 files, none of them media, models or env files. `gh repo create notnauti/montagestudio --private`, then the branch was pushed to `main` by URL. No remote was added to the vault.
- `.env` and `.env.local` were added to the project `.gitignore` at n@uti's request (`fd4d1f9`, confirmed with `git check-ignore`).
- `README.md` was written: what MontageStudio is, status, run order and gates, setup, quick start, exit codes, asset licensing (including the `asset_replace_broll` gap), layout, tests, and a note about the dead wikilinks. Committed in `2a74003`, re-split, and pushed: GitHub `main` went from `e3ed985` to `ab17d9d`, and the README shows on the repo page.
- `montage view` (:8765, serving the refine copy) was stopped by Claude Code because the machine was low on memory. It wasn't restarted.

## Decisions
- The GitHub repo is a manually synced mirror made by subtree split, and the vault remains the source of truth. Recorded in `architecture/decisions.md`.

## Touched
- [[MontageStudio]]
- [[README]]
- [[decisions]]
- [[2026-10-26-workflow-page-refine-stage]]

## Next
- To sync GitHub after future sessions: re-run the subtree split and push it, from the vault root. Nothing pushes automatically.
- Open: whether to add `README.md` to the `(C)`-exempt list in `AGENTS.md`. That file has no prefix, so it needs n@uti's OK.
- Before ever making the repo public: clean up the vault wikilinks and review `logs/`.
- Unchanged from earlier: decide whether refine is required, set up the MCP host, add the `asset_replace_broll` licence check, Voyager stats on 11-02 and 11-23, fund the API credit.
