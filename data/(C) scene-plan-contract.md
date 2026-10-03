# scene_plan.json contract

Source: [[00 Specifications/montagestudio_architecture_technical_guide]] §4 Stage 3. The Tier 1 → Tier 2 interface.

## Shape (`extracted` from the guide; the implemented contract is under "Slice 1 fields")
- `meta`: `title`, `resolution [w,h]`, `fps`, `durationFrames`
- `tracks[]`: `id`, `type` (`audio` | `video`), `clips[]`
- clip: `id`, `src` (workspace-relative), `startFrame`, `durationFrames`, optional `volume`, `fit`, `transition {type, durationFrames}`, `motion {scale [from,to], easing}`
- Units are frames, not ms. Example is internally consistent: crossfade 15 at 210 → next clip starts 195.

## Sibling files (`extracted`, schemas not given)
`manifest.json`, `captions.json` (word-level offsets), `contact_sheet.png`, `project.ts`. As built: `captions.json` under "Caption track", `manifest.json` / `contact_sheet.png` / `approval.json` under "Stage 2 siblings" below.

## Defects in the guide's example
- `inferred` — `project.ts` contains JSX; it has to be `project.tsx` (or the compiler emits `h()` calls).
- `inferred` — `<Crossfade/>` as a sibling between clips and `<Motion>` wrapping a clip are two different composition models; the compiler needs one rule for where transitions and motion live.
- `inferred` — no caption/text track type exists, yet the bridge "maps word-level audio alignment to kinetic subtitle track markers" and MCP has `timeline_retime_captions`.
- `ambiguous` — `$schema` points at `montagestudio.dev`; unknown whether that domain is owned.

## Source-of-truth risk (`inferred`)
"Bidirectional sync" means canvas edits rewrite `project.ts` source. Once a human or agent edits `project.ts`, `scene_plan.json` is stale and re-running Tier 1 overwrites their work. Decide which file is canonical after the gate before writing the bridge.

## Slice 1 fields (defined 2026-09-28)

A strict subset of the guide's shape plus one added field (`sourceInFrame`), so the slice 2 compiler reads the same file. The slice 1 renderer is FFmpeg only (see [[architecture/decisions]]). **Anything not listed here is rejected by the validator, not silently ignored**, so a Tier 1 output that relies on an unsupported feature fails loudly instead of rendering wrong.

### `meta`
| Field | Type | Rule |
|---|---|---|
| `title` | string | required; used for the output filename |
| `resolution` | `[w, h]` ints | required; both even (yuv420p needs even dimensions) |
| `fps` | int | required; integer only (no 29.97 in slice 1) |
| `durationFrames` | int > 0 | required; the video track must end exactly here |
| `$schema` | string | optional; accepted and ignored |

### `tracks[]`
| Field | Type | Rule |
|---|---|---|
| `id` | string | required, unique |
| `type` | `"video"` \| `"audio"` \| `"caption"` | exactly **one** video track; **one or more** audio tracks (voice, music); **at most one** caption track (added 2026-10-02, see below) |
| `clips[]` | array | required, non-empty, sorted by `startFrame` |
| `style` | `"highlight"` \| `"pop"` | caption track only; optional, default `"highlight"`. Rejected on video and audio tracks. |

### Clip, both types
| Field | Type | Rule |
|---|---|---|
| `id` | string | required, unique across the whole plan |
| `src` | string | required; workspace-relative, inside `assets/`, file must exist |
| `startFrame` | int ≥ 0 | required; position on the timeline |
| `durationFrames` | int > 0 | required; `startFrame + durationFrames ≤ meta.durationFrames` |
| `sourceInFrame` | int ≥ 0 | optional, default 0; **added, not in the guide.** In-point inside the source file. Without it every clip plays from the source's first frame, which rules out cutting a segment out of archival footage. Video and audio sources must have at least `sourceInFrame + durationFrames` frames (checked with ffprobe). Ignored for stills. |

### Audio clip only
| Field | Type | Rule |
|---|---|---|
| `volume` | float 0–1 | optional, default 1.0 |

Within one audio track clips may not overlap; gaps are silence. Clips on different audio tracks may overlap (voice over music).

### Video clip only
| Field | Type | Rule |
|---|---|---|
| `fit` | `"cover"` \| `"contain"` | optional, default `"cover"` (scale + crop vs scale + black pad) |
| `transition` | `{ "type": "crossfade", "durationFrames": int }` | optional; crossfade from this clip into the **next** one. Only `crossfade` in slice 1. Not allowed on the last clip. |
| `motion` | `{ "scale": [from, to], "easing": "linear" \| "easeInOutCubic" }` | optional; Ken Burns zoom across the clip's full duration. Scale ≥ 1.0 at both ends (zooming out below 1.0 would show borders). `scale` is the only motion property in slice 1. |

A `src` ending in `.png` / `.jpg` / `.jpeg` / `.webp` is a still: it is held for `durationFrames`. Anything else must be a video ffprobe can read.

### Video-track timing rule
The video track is one continuous single-layer sequence:
- first clip starts at 0; last clip ends at `meta.durationFrames`
- each next clip starts at `prev.startFrame + prev.durationFrames − prev.transition.durationFrames` (0 when there's no transition): no gaps, no other overlap
- `transition.durationFrames` < both neighbours' `durationFrames`

The guide's crossfade example obeys this (210 − 15 = 195). Its full example fails the end rule — the video track ends at 450 while `meta.durationFrames` is 2700 — which is fine for an excerpt but a real plan would be rejected.

### FFmpeg mapping (as built in `src/montage/render.py`, 2026-09-28)
| Field | FFmpeg |
|---|---|
| `sourceInFrame`, `durationFrames` | input `-ss` for the in-point, then `fps`, `tpad` clone, `trim=end_frame` so every clip is exactly `durationFrames`; audio `apad` + `atrim=end_sample`; stills via `-loop 1` |
| `resolution`, `fit` | `scale=...:force_original_aspect_ratio=increase,crop` (cover) or `=decrease,pad` (contain); `setsar=1`, `fps`, `format=yuv420p` on every clip so `xfade` inputs match; `scale=out_range=tv` before `format` since 2026-10-02 (a full-range JPEG otherwise tagged the output `yuvj420p`) |
| `motion` | `zoompan` with `z` as an expression over the clip's frame index; `easeInOutCubic` written out as a piecewise expression. Stills first upscaled ~4× to avoid zoompan jitter. |
| `transition` | `xfade=transition=fade:duration=d/fps:offset=next.startFrame/fps`; hard cuts via `concat` |
| audio placement, `volume` | `adelay=startFrame/fps*1000`, `volume`, `aresample=48000`, then `amix=normalize=0` |
| QC | ffprobe frame count = `durationFrames`; EBU R128 integrated loudness −14 LUFS (`loudnorm` on the final mix, `ebur128` to verify). |

### Caption track (added 2026-10-02)
**Added, not in the guide's shape** — the guide only says the bridge "maps word-level audio alignment to kinetic subtitle track markers" and lists `captions.json` as a sibling. See [[architecture/decisions]]. Caption clips have **no `src`**: the words live in the plan, so the renderer needs nothing else.

Caption clip:
| Field | Type | Rule |
|---|---|---|
| `id` | string | required; unique across the whole plan (shared with video and audio clip ids) |
| `startFrame` | int ≥ 0 | required; timeline position |
| `durationFrames` | int > 0 | required; `startFrame + durationFrames ≤ meta.durationFrames` |
| `words` | array, non-empty | required; see below |

Word: `{ "text": string, "startFrame": int ≥ 0, "endFrame": int }`, all three required, nothing else allowed.
- frames are **timeline** frames (like `startFrame` everywhere else), `endFrame` exclusive, `startFrame < endFrame`
- inside the clip: `clip.startFrame ≤ word.startFrame` and `word.endFrame ≤ clip end`
- in order and non-overlapping: each word starts at or after the previous word's `endFrame`
- `text` non-empty, single line (no control characters)

Caption clips within the track are sorted and may not overlap. The planner writes one caption clip per beat, spanning that beat's voice clip.

```json
{ "id": "captions", "type": "caption", "style": "highlight", "clips": [
  { "id": "cap_01", "startFrame": 12, "durationFrames": 84, "words": [
    { "text": "Every", "startFrame": 14, "endFrame": 20 },
    { "text": "GPS", "startFrame": 22, "endFrame": 39 } ] } ] }
```

**Sibling `captions.json`** (written by `montage plan`, not read by the renderer): `{version, fps, style, beats[{beat, clip, src, method, offsetFrame, offsetMs, words[{text, startMs, endMs, startFrame, endFrame}]}]}`. `startMs` / `endMs` are offsets into that beat's wav; `offsetFrame` / `offsetMs` is where the wav starts on the timeline; `startFrame` / `endFrame` match the caption track. `method` is `piper-alignment` (Piper's phoneme durations) or `estimated` (spread by word length; the planner prints a note).

**Render mapping:** words are mapped onto the output grid (`round(frame × out_fps / meta.fps)`), split into pages of ≤ 6 words / ~32 characters (sentence ends always break a page), written as ASS (`<workspace>/cache/captions/<title>-<profile>.ass`, ASS times `floor(frame × 100 / fps)` centiseconds) and burned in with `ass=filename=…` as the last video filter. `highlight` = page visible, current word gold; `pop` = words appear as spoken, current word gold and scaling 125 % → 100 % over 120 ms, unspoken words drawn transparent so the line does not reflow. `montage render --no-captions` skips it.

### Render-time overrides (2026-10-02, no shape change)
The fields above are unchanged; these act on a validated plan without rewriting `scene_plan.json`. See [[architecture/decisions]].
| Input | Effect |
|---|---|
| `--profile 1080p30-h264` / `720p30-h264` / `4k60-prores` | Output short edge 1080 / 720 / 2160 (long edge from the plan's aspect), fps 30 / 30 / 60, H.264 + AAC `.mp4` or ProRes 422 HQ (`yuv422p10le`) + 24-bit PCM `.mov`. Every plan frame `f` lands on output frame `round(f × out_fps / meta.fps)`; crossfades are re-derived from the mapped boundaries. QC expects the profile's frame count, size, fps, codec and pixel format. No `--profile` = `meta.resolution` and `meta.fps` in H.264, as before. |
| `--encoder auto\|h264_nvenc\|h264_qsv\|h264_amf` | H.264 only. Used when `ffmpeg -encoders` lists it and a 1-frame test encode succeeds; otherwise libx264, with the reason printed. Default `software` (libx264). |
| normalization (`src/montage/normalize.py`) | Before rendering, every `src` is probed per role; audio ≠ 48 kHz, variable-frame-rate video, video not 8-bit 4:2:0, stills above 8 bits, and non-square pixels are fixed into `<workspace>/cache/normalized/`, and the render reads those copies. Aspect ratio ≠ `meta.resolution` is reported only (`fit` handles it). `montage normalize <plan>` reports; `--fix` fills the cache; `render --skip-normalize` bypasses it. |

### Stage 2 siblings: `manifest.json`, `contact_sheet.png`, `approval.json` (2026-10-02, no shape change)
The scene_plan fields above are unchanged: sourced files are ordinary `src` paths in `assets/`, and the validator still rejects anything not listed. See [[architecture/decisions]].

**brief.json** (input, not the contract): an asset may carry an optional `query` (search words for `montage scrape`; default: its `description`). An asset whose `src` file does not exist yet is *wanted*; its extension picks image (`.png .jpg .jpeg .webp`) or video (`.mp4 .mov .mkv .webm .m4v`), and the file lands at exactly that `src`, converted with ffmpeg when the source's container differs.

**`manifest.json`** (written by `montage scrape`, not read by the renderer): `{version: 1, assets: {<src>: entry}}`, merged across runs. Entry: `source` (`commons` | `nasa` | `archive`), `sourceId`, `title`, `pageUrl`, `downloadUrl`, `license`, `licenseUrl`, `licenseBasis` (`item-field` = the item's own license field; `source-policy` = NASA, whose API has no per-item license), `attribution`, `query`, `score`, `matchedTerms`, `ranking` (the scoring rule in words), `bytes`, `downloadedBytes`, `sha256`, `convertedFrom` (original extension or null), `retrievedAt`. Only PD / CC0 / CC BY / CC BY-SA are accepted; NC, ND, missing and unrecognised licenses are refused before ranking.

**`contact_sheet.png`**: one tile per video clip (representative frame = middle of the used stretch, or the still; fitted with the clip's `fit`), labelled `#index clip_id @start length [still|in <source time>] [zoom]` plus the first two lines of narration (caption words starting inside the clip before its outgoing crossfade; else script.json when it has one beat per clip). Header: title, clip count, length.

**`approval.json`** (written by `montage approve --workspace`): `{version: 1, approvedAt, plan: {path, sha256}, assets: {<workspace-relative path>: sha256}, contactSheet: {path, sha256}, note?}`. `assets` covers every video and audio `src` the plan uses. `montage render` recomputes plan + asset digests and refuses (exit 1) on any difference — plan edited, asset bytes changed, asset added or dropped — unless `--force`. Approving needs a `contact_sheet.png` at least as new as the plan and its assets.

### MCP edits (2026-10-02, no shape change)
`montage mcp <ws>` edits this file in place; see [[architecture/decisions]]. Every tool writes only plans that pass the validator above, so the MCP surface cannot widen the contract: `timeline_apply_easing` sets `motion.scale` + `easing` (two keyframes, first and last frame of the clip) and refuses `position` / `opacity`; a split adds a clip with a new id (default `<id>_b`) and sets `sourceInFrame`; a trim changes `startFrame` / `durationFrames` / `sourceInFrame`; a b-roll swap changes `src` (a file outside `assets/` is copied to `assets/broll/`); caption retiming changes word frames only. Previous versions go to `<workspace>/cache/plan-history/`. Any edit invalidates `approval.json`.

### Explicitly out of slice 1
Free text / title tracks (captions are in, since 2026-10-02), more than one video track (no overlays or picture-in-picture), transitions other than crossfade, motion `position` / `opacity`, fractional fps, per-clip speed changes, audio fades.

### Minimal valid plan
```json
{
  "meta": { "title": "slice1-smoke", "resolution": [1920, 1080], "fps": 30, "durationFrames": 300 },
  "tracks": [
    { "id": "narration", "type": "audio", "clips": [
      { "id": "vo_01", "src": "./assets/vo_01.wav", "startFrame": 0, "durationFrames": 290 }
    ]},
    { "id": "visuals", "type": "video", "clips": [
      { "id": "shot_a", "src": "./assets/archive.mp4", "startFrame": 0, "durationFrames": 165, "sourceInFrame": 120,
        "transition": { "type": "crossfade", "durationFrames": 15 } },
      { "id": "shot_b", "src": "./assets/diagram.png", "startFrame": 150, "durationFrames": 150,
        "motion": { "scale": [1.0, 1.08], "easing": "easeInOutCubic" } }
    ]}
  ]
}
```

### Settled 2026-09-28
- Loudness target: −14 LUFS integrated.
- Captions: slice 2. Correction (2026-09-28, after installing it): piper-tts 1.8 can return phoneme alignments (`PiperVoice.load(..., include_alignments=True)`, `synthesize_wav(..., include_alignments=True)`), so slice 2 may not need a separate forced aligner — check whether lessac-medium supports them before choosing.
- Captions, settled 2026-10-02: lessac-medium does expose them (one `Ceil` tensor). Built without the `onnx` package and without an aligner; see "Caption track" above.
