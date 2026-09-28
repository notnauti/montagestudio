# MontageStudio

> **The Agentic Video Engineering Pipeline**: Autonomous macro-production meets interactive bidirectional timeline composition.

MontageStudio synthesizes the autonomous asset gathering, generative scripting, and pipeline orchestration of **OpenMontage** with the real-time, bidirectional SolidJS visual canvas and MCP server tooling of **Diffusion Studio**.

---

## 1. Executive Overview & Problem Statement

Building programmatic, AI-directed video traditionally runs into two opposing friction points:
1. **Headless Generation Bottleneck (OpenMontage model):** Scripts and assets are orchestrated autonomously into code (Remotion/GSAP/FFmpeg), but micro-adjustments (nudging a transition by 150ms, tuning bezier curves, reframing text overlays) require blind code adjustments and tedious full-render test cycles.
2. **Manual IDE Friction (Diffusion Studio model):** While providing a responsive, bidirectional SolidJS canvas and MCP agent hooks, manual setup is still required for script research, multi-modal asset retrieval, semantic scraping (CLIP), and storyboard gating.

**MontageStudio solves this by establishing a clear two-tier separation of concerns:**
* **Tier 1 (Macro-Production):** Autonomous agent workflows handle scriptwriting, audio synthesis, CLIP-based archival scraping, generative AI video creation, and pre-render storyboard sign-offs.
* **Tier 2 (Micro-Composition):** An AST adapter translates the scene plan into a declarative Diffusion Studio workspace. Developers and creative directors can scrub, nudge, and co-edit on a real-time canvas while external coding agents manipulate the timeline via Model Context Protocol (MCP).

---

## 2. System Architecture

```
                                 ┌─────────────────────────┐
                                 │   Creative Director     │
                                 │   (Prompt & Approval)   │
                                 └────────────┬────────────┘
                                              │ Brief / Topic Prompt
                                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ TIER 1: MACRO-PRODUCTION ORCHESTRATION (Adapted from OpenMontage)                       │
│                                                                                         │
│  ┌───────────────────────────────┐     ┌─────────────────────────────────────────────┐  │
│  │     Agent Director & Skills   │◄────┤ Pipeline Definitions (YAML Workflows)       │  │
│  └──────────────┬────────────────┘     └─────────────────────────────────────────────┘  │
│                 │                                                                       │
│                 ├─► Scripting & Research Engine (Web, Perplexity, YouTube transcripts)  │
│                 ├─► Narration Synthesis (ElevenLabs / Piper TTS + Word Timestamps)      │
│                 ├─► Multi-Modal Asset Ingestion                                         │
│                 │     ├── Semantic Scraping (Archive.org, NASA, Commons via CLIP)       │
│                 │     └── Generative Synthesis (Runway, LTX-Video, Recraft, SDXL)      │
│                 │                                                                       │
│                 ▼                                                                       │
│  ┌───────────────────────────────┐                                                      │
│  │   Storyboard Quality Gate     ├────► Contact Sheet Approval (Terminal / Web Preview) │
│  └──────────────┬────────────────┘                                                      │
└─────────────────┼───────────────────────────────────────────────────────────────────────┘
                  │
                  │ Validated `manifest.json` + `scene_plan.json` + Media Assets
                  ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ TIER 2: ADAPTER & AST COMPILATION LAYER (`@montagestudio/bridge`)                       │
│                                                                                         │
│  • Normalizes sample rates, aspect ratios, and pixel formats via FFmpeg probes          │
│  • Maps word-level audio alignment to kinetic subtitle track markers                   │
│  • Transpiles intermediate Scene Plan AST into SolidJS Timeline Elements (`project.ts`) │
└─────────────────┼───────────────────────────────────────────────────────────────────────┘
                  │
                  ▼ Emits project workspace
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ TIER 3: INTERACTIVE TIMELINE & FINISHING IDE (Adapted from Diffusion Studio)            │
│                                                                                         │
│  ┌──────────────────────────────────────────┐   Bidirectional Sync   ┌───────────────┐  │
│  │      Diffusion Studio Desktop / Web      │◄──────────────────────►│  `project.ts` │  │
│  │   (SolidJS Canvas, Bezier Curve Editor)  │                        │   (SolidJS)   │  │
│  └──────────────────▲───────────────────────┘                        └───────▲───────┘  │
│                     │                                                        │          │
│                     │ Direct Visual Scrubbing & Trimming                     │          │
│                     │                                                        │          │
│  ┌──────────────────┴────────────────────────────────────────────────────────┴───────┐  │
│  │ Diffusion Studio MCP Server (`dapi` / stdio / WebSocket)                          │  │
│  │ Coding Agent Actions: (Claude Code / Cursor / Codex / Windsurf)                   │  │
│  │   • `trim_clip`, `shift_track`, `replace_broll`, `apply_easing_preset`           │  │
│  │   • `remove_silence`, `balance_audio_lufs`, `retime_captions`                    │  │
│  └──────────────────┬────────────────────────────────────────────────────────────────┘  │
└─────────────────────┼───────────────────────────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ EXPORT & ARTIFACT DELIVERY ENGINE                                                       │
│                                                                                         │
│  • Headless CLI Render (`montage render --profile 4k60-prores`)                         │
│  • Local Hardware-Accelerated Canvas Exporter (WebCodecs / NVENC / VideoToolbox)        │
│  • Automated QC Validation (`ffprobe` frame drops, EBU R128 audio loudness checks)     │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Repository Topology

```
montagestudio/
├── apps/
│   ├── studio/                    # Diffusion Studio Electron/Web application (SolidJS)
│   │   ├── src/
│   │   │   ├── canvas/            # Real-time WebGL/WebCodecs rendering engine
│   │   │   ├── timeline/          # Visual multi-track timeline component
│   │   │   ├── inspector/         # Property panels (Transform, Filters, Typography)
│   │   │   └── mcp-client/        # Bridge to local agent session
│   │   └── package.json
│   └── cli/                       # Terminal interface for orchestration & rendering
│       ├── src/
│       │   ├── commands/          # `init`, `plan`, `scrape`, `synthesize`, `open`
│       │   └── index.ts
│       └── package.json
├── packages/
│   ├── orchestrator/              # OpenMontage Python core engine
│   │   ├── pipelines/             # Declarative pipeline recipes (Explainer, Promo, etc.)
│   │   ├── skills/                # Agent prompt definitions and tool bindings
│   │   ├── scrapers/              # CLIP-guided search (Archive.org, NASA, Commons)
│   │   └── providers/             # Generative model adapters (Video, Image, Audio)
│   ├── bridge/                    # Scene AST to SolidJS Transpiler
│   │   ├── src/
│   │   │   ├── ast.ts             # Intermediate Scene Plan schema definitions
│   │   │   ├── compiler.ts        # Transpilation logic generating SolidJS timeline code
│   │   │   └── validator.ts       # Audio synchronization and asset integrity checks
│   │   └── package.json
│   └── mcp-server/                # Diffusion Studio Model Context Protocol server
│       ├── src/
│       │   ├── tools/             # MCP tools: cut, ripple, transition, caption, grade
│       │   └── server.ts          # JSON-RPC server transport (stdio / sse)
│       └── package.json
├── pipelines/                     # Custom YAML pipeline definitions
├── workspace/                     # Runtime directory for active video projects
│   └── demo-explainer/
│       ├── assets/                # Downloaded & generated raw media
│       ├── contact_sheet.png      # Pre-render approval artifact
│       ├── scene_plan.json        # OpenMontage intermediate compilation target
│       └── project.ts             # Diffusion Studio bidirectional SolidJS source
├── docker-compose.yml
└── package.json
```

---

## 4. End-to-End Workflow Pipeline

### Stage 1: Macro-Planning & Script Synthesis
The user invokes the CLI with a high-level creative brief:
```bash
montage plan --pipeline documentary --topic "The Apollo 1 Guidance Computer" --target-duration 90
```
1. **Research & Structure:** The agent creates a scene outline, defines narration beats, and builds an asset manifest.
2. **Audio Generation:** Piper TTS or ElevenLabs produces speech tracks with word-level boundary offsets (`captions.json`).

### Stage 2: Multi-Modal Asset Sourcing
1. **Semantic Search:** OpenMontage computes CLIP embeddings of script segments to retrieve archival footage from Archive.org and NASA.
2. **Generative Fallback:** Where archival coverage drops below threshold confidence, the pipeline queries image/video generation models (e.g., LTX-Video, Flux, Recraft).
3. **Contact Sheet Gate:** The system generates a visual contact sheet grid (`contact_sheet.png`) and halts execution until the director issues approval:
   ```bash
   montage approve --workspace ./workspace/demo-explainer
   ```

### Stage 3: Intermediate AST Translation (`@montagestudio/bridge`)
Once approved, the pipeline translates the abstract scene plan into a concrete Diffusion Studio project definition.

#### Intermediate Scene AST (`scene_plan.json` excerpt):
```json
{
  "$schema": "https://montagestudio.dev/schemas/v1/scene_plan.json",
  "meta": {
    "title": "Apollo Guidance Computer",
    "resolution": [1920, 1080],
    "fps": 30,
    "durationFrames": 2700
  },
  "tracks": [
    {
      "id": "narration_audio",
      "type": "audio",
      "clips": [
        {
          "id": "vocal_01",
          "src": "./assets/voiceover_01.mp3",
          "startFrame": 0,
          "durationFrames": 450,
          "volume": 1.0
        }
      ]
    },
    {
      "id": "broll_visuals",
      "type": "video",
      "clips": [
        {
          "id": "nasa_agc_rope_memory",
          "src": "./assets/nasa_archive_rope_core.mp4",
          "startFrame": 0,
          "durationFrames": 210,
          "fit": "cover",
          "transition": { "type": "crossfade", "durationFrames": 15 }
        },
        {
          "id": "gen_schematic_render",
          "src": "./assets/flux_wireframe_circuit.png",
          "startFrame": 195,
          "durationFrames": 255,
          "motion": { "scale": [1.0, 1.08], "easing": "easeInOutCubic" }
        }
      ]
    }
  ]
}
```

#### Compiled SolidJS Output (`project.ts`):
```typescript
import { Composition, Track, Video, Audio, Motion, Crossfade } from '@montagestudio/studio';

export default function ApolloProject() {
  return (
    <Composition width={1920} height={1080} fps={30} duration={2700}>
      {/* Audio Track: Voiceover */}
      <Track id="narration" type="audio">
        <Audio 
          src="./assets/voiceover_01.mp3" 
          start={0} 
          duration={450} 
          volume={1.0} 
        />
      </Track>

      {/* Visual B-Roll Sequence */}
      <Track id="broll" type="video">
        <Video 
          src="./assets/nasa_archive_rope_core.mp4" 
          start={0} 
          duration={210} 
          fit="cover" 
        />
        <Crossfade duration={15} />
        <Motion scale={[1.0, 1.08]} easing="easeInOutCubic">
          <Video 
            src="./assets/flux_wireframe_circuit.png" 
            start={195} 
            duration={255} 
            fit="cover" 
          />
        </Motion>
      </Track>
    </Composition>
  );
}
```

### Stage 4: Interactive Micro-Refinement & Agent Co-Editing
The user launches the GUI with live MCP bindings:
```bash
montage open ./workspace/demo-explainer
```
* **Visual Editing:** Scrub the timeline, drag boundary trims, and visually tweak motion trajectories on the canvas. Changes immediately patch `project.ts`.
* **Agentic Assistance (MCP Server):** A coding agent attached via Model Context Protocol can be instructed in natural language:
  * *"Add a subtle zoom-in on clip `gen_schematic_render` over the last 60 frames."*
  * *"Trim all audio gaps longer than 300ms in track `narration`."*
  * *"Ensure video cut boundaries snap cleanly to the start of speech sentences."*

---

## 5. Model Context Protocol (MCP) Tools Reference

MontageStudio ships with an integrated MCP server exposing deterministic video editing primitives to external coding agents:

| Tool Identifier | Input Parameters | Description |
| :--- | :--- | :--- |
| `timeline_split_clip` | `trackId`, `clipId`, `splitFrame` | Splits a video or audio clip at a target frame without altering ripple sync. |
| `timeline_trim_boundary` | `clipId`, `edge: 'start' \| 'end'`, `deltaFrames` | Shifts in/out cut points while validating boundary collisions. |
| `timeline_apply_easing` | `clipId`, `property: 'scale' \| 'position' \| 'opacity'`, `keyframes`, `curve` | Injects cubic-bezier keyframes into the target SolidJS component. |
| `timeline_retime_captions`| `captionsTrackId`, `toleranceMs` | Re-aligns subtitle layout timestamps to audio waveform peaks. |
| `asset_replace_broll` | `clipId`, `sourceUri` | Swaps out media references while preserving transform and timing metadata. |
| `inspect_frame_context` | `frameNumber` | Captures an uncompressed frame buffer and returns OCR/visual descriptions to the agent. |

---

## 6. Installation & Quickstart

### Prerequisites
* **Node.js**: `v20.x` or later
* **Python**: `3.10+`
* **FFmpeg**: `v6.0+` with `libx264`, `libvpx`, and `libmp3lame`
* **pnpm**: `v9.x`

### Setup Instructions

1. **Clone the repository and install dependencies:**
   ```bash
   git clone https://github.com/montagestudio/montagestudio.git
   cd montagestudio
   pnpm install
   ```

2. **Initialize Python orchestration virtualenv:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r packages/orchestrator/requirements.txt
   ```

3. **Configure Environment Keys:**
   ```bash
   cp .env.example .env
   # Add keys for ElevenLabs, OpenAI/Anthropic, and preferred Generative Video providers
   ```

4. **Launch the Development Studio & MCP Server:**
   ```bash
   pnpm dev
   ```

5. **Generate a First Video (CLI):**
   ```bash
   pnpm montage new --pipeline quick-explainer --prompt "How GPS works using relativistic time corrections"
   ```

---

## 7. Comparison Matrix: Standalone vs. Hybrid

| Capability | OpenMontage | Diffusion Studio | MontageStudio (Hybrid) |
| :--- | :---: | :---: | :---: |
| **Autonomous Asset Sourcing** | Yes | No | **Yes** (Automated retrieval + gen routing) |
| **CLIP Semantic Scraping** | Yes | No | **Yes** (Archive.org, NASA, Commons) |
| **Pre-Render Quality Gates** | Yes | No | **Yes** (Contact sheets & cost check) |
| **Real-Time Interactive Canvas** | No (Code/Render only) | Yes | **Yes** (Sub-100ms SolidJS canvas) |
| **Bidirectional Code-UI Sync** | No | Yes | **Yes** (Canvas edits rewrite `project.ts`) |
| **Native MCP Agent Server** | Partial | Yes | **Yes** (Fine-grained timeline editing tools) |
| **Zero-Recompile Micro-Edits** | No | Yes | **Yes** (Real-time scrubbing & layout) |

---

## 8. License & Acknowledgments

This project is licensed under the Apache 2.0 License.

**Special Thanks:**
* The **OpenMontage** project for pioneering agent-driven production workflows and generative scraping pipelines.
* The **Diffusion Studio** team for creating the high-performance SolidJS bidirectional video canvas and MCP editor foundation.