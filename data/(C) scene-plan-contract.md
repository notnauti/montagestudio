# scene_plan.json contract

Source: [[00 Specifications/montagestudio_architecture_technical_guide]] §4 Stage 3. The Tier 1 → Tier 2 interface.

## Shape (`extracted`)
- `meta`: `title`, `resolution [w,h]`, `fps`, `durationFrames`
- `tracks[]`: `id`, `type` (`audio` | `video`), `clips[]`
- clip: `id`, `src` (workspace-relative), `startFrame`, `durationFrames`, optional `volume`, `fit`, `transition {type, durationFrames}`, `motion {scale [from,to], easing}`
- Units are frames, not ms. Example is internally consistent: crossfade 15 at 210 → next clip starts 195.

## Sibling files (`extracted`, schemas not given)
`manifest.json`, `captions.json` (word-level offsets), `contact_sheet.png`, `project.ts`.

## Defects in the guide's example
- `inferred` — `project.ts` contains JSX; it has to be `project.tsx` (or the compiler emits `h()` calls).
- `inferred` — `<Crossfade/>` as a sibling between clips and `<Motion>` wrapping a clip are two different composition models; the compiler needs one rule for where transitions and motion live.
- `inferred` — no caption/text track type exists, yet the bridge "maps word-level audio alignment to kinetic subtitle track markers" and MCP has `timeline_retime_captions`.
- `ambiguous` — `$schema` points at `montagestudio.dev`; unknown whether that domain is owned.

## Source-of-truth risk (`inferred`)
"Bidirectional sync" means canvas edits rewrite `project.ts` source. Once a human or agent edits `project.ts`, `scene_plan.json` is stale and re-running Tier 1 overwrites their work. Decide which file is canonical after the gate before writing the bridge.
