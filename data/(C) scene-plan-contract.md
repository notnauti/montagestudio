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
| `type` | `"video"` \| `"audio"` | exactly **one** video track; **one or more** audio tracks (voice, music) |
| `clips[]` | array | required, non-empty, sorted by `startFrame` |

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
| `resolution`, `fit` | `scale=...:force_original_aspect_ratio=increase,crop` (cover) or `=decrease,pad` (contain); `setsar=1`, `fps`, `format=yuv420p` on every clip so `xfade` inputs match |
| `motion` | `zoompan` with `z` as an expression over the clip's frame index; `easeInOutCubic` written out as a piecewise expression. Stills first upscaled ~4× to avoid zoompan jitter. |
| `transition` | `xfade=transition=fade:duration=d/fps:offset=next.startFrame/fps`; hard cuts via `concat` |
| audio placement, `volume` | `adelay=startFrame/fps*1000`, `volume`, `aresample=48000`, then `amix=normalize=0` |
| QC | ffprobe frame count = `durationFrames`; EBU R128 integrated loudness −14 LUFS (`loudnorm` on the final mix, `ebur128` to verify). |

### Explicitly out of slice 1
Caption / text tracks, more than one video track (no overlays or picture-in-picture), transitions other than crossfade, motion `position` / `opacity`, fractional fps, per-clip speed changes, audio fades.

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
- Captions: slice 2. Piper gives no word timestamps, so slice 2 needs a forced aligner (or a TTS with timestamps) before a caption track exists.
