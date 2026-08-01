# Parallax Forge

**AI-assisted stereo direction for short-form video.** Parallax Forge is a Windows-first offline desktop application that turns conventional 2D clips into comfort-bounded red-cyan anaglyph video. It analyzes each shot, proposes bounded stereo treatment, lets an editor make shot-level adjustments, renders through an occlusion-aware pipeline, remuxes compatible source audio streams, and produces a quality-control report.

> Product status: production-minded alpha. The deterministic engine, desktop workflow, resumable artifacts, and synthetic test path are included. Shipping the real Video Depth Anything model, FFmpeg binaries, code signing, and hardware certification are release operations that cannot be truthfully completed from source code alone.

## What is different

Most 2D-to-3D demos stop at monocular depth. Parallax Forge adds a **Stereo Director** between depth estimation and rendering. The Director selects a tested treatment per shot; a non-bypassable Comfort Guard clamps it before any pixels move. Every decision is written to an editable `stereo_script.json`, so creative changes do not require another model run.

The desktop workflow uses two deliberately separate analysis tiers. A **Quick Director draft** samples a bounded set of representative frames inside every detected shot, generates compact real shot features, runs the deterministic Director and Comfort Guard, and unlocks editing without waiting for full-frame depth. Preview and export never treat that sampled draft as render depth: the editor must explicitly run full-frame analysis before previews become available, and certified final export still requires complete release-approved depth for every frame.

Previews come in two forms so they answer different questions: a still of the frame under the playhead renders in about a second for adjusting depth, and a full shot clip renders every frame for judging motion and temporal comfort. Frames render across CPU cores and are encoded in strict order, so output is identical regardless of core count.

An optional **LLM Assistant** can recommend one of those tested presets and explain its choice from compact shot statistics. It is opt-in, uses the user's own API key, sends no frames or audio, and cannot author numerical disparity or bypass the Comfort Guard. The deterministic rules engine remains the offline default. See [LLM Assistant design](docs/LLM_ASSISTANT.md).

## Product boundaries

The first supported product is for short clips and selected scenes, not unattended conversion of a whole feature film. It supports CFR normalization, a conservative 720p desktop default (with 1080p available through the validated engine configuration or CLI), red-cyan anaglyph, compatible source-audio remux, hard-cut detection, rule-based direction, revisioned shot-level overrides, preview rendering, and QC diagnostics. Compatible cached depth is available for preview and development workflows, where provenance review remains the operator's responsibility.

Synthetic depth is a test/demo backend and is refused for final export: it is a fixed pattern containing no scene geometry, so a render made from it would be 2D in a stereo container. Export accepts either the certified Video Depth Anything backend or the built-in image-analysis backend, which measures the actual picture. The app never labels a non-certified render as a production AI conversion: the depth tier is reported in the export dialog, badged in the title bar, and recorded in the QC report.

The initial commercial model policy permits the Apache-2.0 Video Depth Anything Small checkpoint. Base and Large checkpoints are not bundled because their upstream terms are non-commercial. A dependency-free **image-analysis** backend is always available as a fallback and as an explicit choice, deriving depth from detail falloff, framing, and atmospheric cues with no model or download. See [model and dependency policy](docs/MODEL_AND_LICENSE_POLICY.md).

## Architecture

```text
Svelte editor ──typed Tauri commands/events── Rust host
                                                   │
                                      JSON Lines over stdio
                                                   │
                                            Python worker
                                                   │
     inspect → normalize → shots → sampled draft → editable Director
                                  └→ full depth → features → direct
                                      → comfort guard → stereo render → audio → QC
```

Large artifacts stay in the project directory; only paths, metadata, progress, and small JSON payloads cross the desktop bridge.

The interactive path is `inspect -> normalize -> shots -> sampled draft -> edit`. The explicit render-refinement path remains `depth -> features -> direct -> render -> QC`. Representative frames are selected within each shot rather than from codec I-frames, so every shot is covered and no motion or depth state crosses a hard cut.

## Quick start

### Browser demo (no media tools required)

The UI contains a deterministic demo adapter for evaluating the complete editing experience without Tauri, Python, CUDA, or model weights.

```powershell
npm.cmd install
npm.cmd run dev
```

Open the printed local URL. Use the demo project in the welcome screen.

### Python engine

Use Python 3.11+ for supported development. The lightweight test suite does not download model weights.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests/python
.\.venv\Scripts\python.exe -m aistereo.cli --help
```

For actual video work, use the pinned FFmpeg/FFprobe 8.1.2 development setup below and install the `video` optional dependencies described by the CLI. GPU depth inference remains an explicit optional install.

Project configuration is editable data, not authority to select executables or model code. The CLI ignores FFmpeg, FFprobe, and model paths stored in a project/config file. It uses the development `PATH` fallback for FFmpeg, or resolved existing files selected through `AISTEREO_FFMPEG_PATH`, `AISTEREO_FFPROBE_PATH`, and `AISTEREO_DEPTH_MODEL_PATH`. A final VDA render always requires the trusted model variable; a missing model fails closed and never downgrades to synthetic depth.

### Desktop development

Install Node.js 20+, Rust 1.88+, Windows WebView2, and 64-bit Python 3.11+. The first setup can explicitly provision the verified project-local FFmpeg/FFprobe 8.1.2 pair:

```powershell
.\scripts\bootstrap.ps1 -ProvisionMediaTools
.\scripts\dev.ps1 -Target desktop
```

The opt-in provision step verifies the pinned archive SHA-256 before extracting it to the ignored `.dev-tools/ffmpeg/8.1.2/` cache. Later runs of bootstrap or the desktop wrapper validate that cache without downloading again. A matched, colocated 8.1.2 pair can instead be supplied with both `AISTEREO_FFMPEG_PATH` and `AISTEREO_FFPROBE_PATH`; a partial pair, split-directory pair, incompatible version, or build without `libx264` fails with a repair command.

Bootstrap also installs the development and video extras into `.venv`; the development host uses that interpreter automatically when present. Set `AISTEREO_PYTHON` only to override it. The provisioned GPLv3 Gyan build is development-only and never becomes a release input. See the full [development setup](docs/DEVELOPMENT.md) and separate [release process](docs/RELEASING.md).

When you choose a source video, the desktop app shows the source and planned project destination together before creating anything. New work defaults to a collision-safe subfolder under `D:\Parallax Projects`; use **Change** on that setup screen only when you want a different parent folder. The source video is not moved or copied by this step.

## Commands

```text
aistereo inspect INPUT
aistereo detect-shots INPUT --work-dir PROJECT
aistereo estimate-depth --work-dir PROJECT --backend synthetic
aistereo estimate-depth --work-dir PROJECT --backend monocular-cues
aistereo estimate-depth --work-dir PROJECT --backend cached --cache-dir DEPTH_CACHE
aistereo estimate-depth --work-dir PROJECT --backend video-depth-anything-small --device cuda
aistereo direct --work-dir PROJECT
aistereo preview-shot --work-dir PROJECT --shot 1 --output preview.mp4
aistereo render --work-dir PROJECT --output output.mp4
aistereo qc --work-dir PROJECT
aistereo run INPUT --work-dir PROJECT --output output.mp4 --backend video-depth-anything-small --resume
aistereo worker
```

Set `AISTEREO_DEPTH_MODEL_PATH` to the reviewed local TorchScript adapter before either certified VDA command. Backend/device/render overrides are written to the project so staged commands resume with one coherent configuration; runtime executable/model paths are re-established from the trusted process environment on every invocation.

Run `aistereo COMMAND --help` for the authoritative options implemented by the engine.

## Project data

```text
MyStereoProject/
├── project.json
├── config.json
├── pipeline_state.json
├── source/
│   ├── media.json
│   ├── normalized_media.json
│   ├── normalized.mp4
│   └── audio.mka
├── shots/shots.json
├── depth/metadata.json
├── features/features.json
├── director/stereo_script.json
├── director/draft_analysis.json
├── previews/
├── renders/
├── logs/
└── qc/
    ├── report.json
    └── report.html
```

Files are created only when the corresponding stage needs them; for example, `audio.mka` is absent for a silent source.

Completed stages include an input/config fingerprint. Resume skips only artifacts whose fingerprints, pipeline schema, engine version, and algorithm namespace still match. JSON, NPZ, and model artifacts use content fingerprints; very large ordinary media also include filesystem identity and change-time metadata. A corrupt derived state index is reset rather than making the project unopenable. Revisioned manual shot overrides are carried across safe regeneration and are never silently discarded when the shot structure changes.

## Verification

```powershell
.\scripts\check.ps1
```

That script runs frontend checks/tests/build, Python lint/tests/type checks, and Rust formatting/checks when the required toolchains are installed. Media integration tests require local FFmpeg and fixtures; real model inference additionally requires checkpoint weights and a supported PyTorch runtime.

## Documentation

- [User guide](docs/USER_GUIDE.md) — end-to-end workflow and troubleshooting
- [Product specification](SPEC.md)
- [Windows development setup](docs/DEVELOPMENT.md)
- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Model and dependency license policy](docs/MODEL_AND_LICENSE_POLICY.md)
- [Optional LLM Assistant and key handling](docs/LLM_ASSISTANT.md)
- [Product design system](docs/DESIGN_SYSTEM.md)
- [Release checklist](docs/RELEASING.md)
- [Security policy](SECURITY.md)
- [Privacy statement](PRIVACY.md)

No license is granted for this repository by default. Add an explicit commercial or open-source license before distributing source.
