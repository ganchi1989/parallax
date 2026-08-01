# Parallax Forge Product Specification

## Promise

Parallax Forge is an offline, AI-assisted stereo conversion tool for short video clips. It creates conservative red-cyan anaglyph renders, varies stereo treatment shot by shot, and leaves every automatic decision inspectable and editable.

It does not promise unattended, production-quality conversion of arbitrary movies. Comfort warnings reduce risk but are not a medical or universal viewing guarantee.

## Release-one workflow

1. Create a project and import one local video.
2. Inspect and normalize it to a constant-frame-rate intermediate.
3. Detect shots and populate the shot bin with timing and transition metadata; release one uses clearly labelled numbered placeholders until a rendered preview exists.
4. Select a bounded, deterministic set of representative frames inside every shot and create an explicitly sampled Quick Director draft. Sparse motion measurements use only true adjacent frame pairs and never cross a hard cut.
5. Extract sampled motion, brightness, foreground, depth-spread, cut-context, and camera-motion proxies, then select one bounded preset: `dialogue_subtle`, `action_controlled`, `vista_deep`, `closeup_flat`, or `neutral`.
6. Run the Comfort Guard, write the versioned draft script, and unlock shot editing. Speech ratio is populated only when the caller supplies reviewed speech intervals; otherwise it remains zero.
7. When the editor requests preview preparation or production analysis, estimate temporally coherent full-frame depth per shot (or load compatible cached depth), extract complete features, and refine the Director script while preserving compatible manual overrides.
8. Let the editor adjust a shot, render a short preview, compare it with the source, and save the stereo script. The sampled tier alone cannot unlock preview or export.
9. Render left/right views with depth-aware splatting, fill limited disocclusion holes, compose the anaglyph, and remux source audio.
10. Generate JSON and HTML QC metric reports. Certified final export requires current release-approved depth covering every frame.

## Functional requirements

### Project safety

- Project mutations are atomic where practical.
- Stage completion is recorded only after output validation.
- Every artifact includes a schema version and relevant source/config fingerprint.
- Cancellation is cooperative and leaves prior completed stages reusable.
- Paths crossing IPC are validated; the UI cannot execute arbitrary commands.

### Media

- Probe resolution, display orientation, normalized frame rate, duration, frame count, codec, pixel format, variable-frame-rate status, and audio stream metadata.
- Normalize to standard orientation, CFR, yuv420p, and predictable color metadata.
- Remux every compatible audio stream from the normalized source-audio intermediate; fail explicitly if the selected output container cannot copy it.
- Fail with an actionable diagnostic when FFmpeg, FFprobe, a codec, or output validation is unavailable or fails. Low-disk behavior remains a manual launch gate.

### Shot and depth behavior

- Shot frame ranges are contiguous, non-overlapping, and cover all decoded frames.
- Quick analysis covers every shot exactly once with a named, engine-owned representative-frame profile; the desktop protocol exposes no arbitrary sampling numerics.
- Sampled depth is stored as draft provenance only and can never satisfy the full-frame depth artifact or release preflight.
- Sparse samples are not temporally smoothed across distant frame gaps, and sampled motion compares only adjacent frames from the same shot.
- Temporal depth filtering resets at every hard cut.
- Percentile normalization is shot-level, never independently frame-level.
- Depth backends are interchangeable and validate frame count, shape, range, and finite values.
- The production model is optional; synthetic and cached backends make the renderer testable offline.

### Direction and comfort

- The Director emits only schema-validated presets and bounded parameters.
- Unknown or low-confidence input falls back to `neutral`.
- Manual overrides remain subject to the Comfort Guard.
- LLM output contains a preset, explanation, confidence, narrative importance, and qualitative emphasis only—never numerical render parameters.
- A valid but low-confidence LLM recommendation falls back to `neutral`; provider failures, refusals, timeouts, invalid schemas, missing credentials, and unknown presets fall back to deterministic rules.
- The guard clamps foreground/background disparity, reduces depth under high motion or low confidence, limits convergence jumps, disables unsafe pop-out, and records its actions.

### Rendering

- Disparity is normalized by output width.
- Zero disparity yields identical views.
- Foreground collision ordering favors the nearer sample.
- Newly revealed holes are measured and filled directionally before limited inpainting.
- Output adapters include left, right, side-by-side preview, basic anaglyph, and calibrated anaglyph.
- Eye swapping is explicit and testable.

### Quality control

- Report frame count and timing agreement, observed disparity limits, holes, edge/window violations, temporal depth change, fallback stages, model failures, and Comfort Guard actions.
- Write actionable warnings to machine-readable JSON and a human-readable HTML report. The current editor returns the report path but does not yet ingest individual QC warnings.
- A playable output is not alone considered a passing deliverable under release policy; human review of the reports and rendered output remains required.

## Non-functional requirements

- Windows 11 x64 is the intended first certified target.
- Processing is local by default. Only explicit LLM Assistant actions make a network request, and they send compact shot statistics rather than media.
- The editor stays responsive while work runs in a supervised sidecar.
- Logs exclude raw media content and avoid full user paths in telemetry; telemetry is off in release one.
- Unit and synthetic integration tests do not require a GPU or model download.
- UI controls are designed to be keyboard reachable, labelled, and usable at 1280×720 and above; keyboard, scaling, and assistive-technology verification are launch gates.
- Public release artifacts must be code-signed and any future updates signature-verified; no signed distributable is included in this source tree.

## Deferred

Feature-film automation, real-time conversion, identity tracking, emotion/genre classification, object-specific effects, burned-in subtitle removal, an unrestricted or learned LLM Director, public upload services, and broad GPU certification are intentionally outside release one.

## Launch gates

- A curated clip suite covers dialogue, action, vista, close-up, edge crossing, low light, reflection, camera motion, and external subtitles.
- Frame/audio drift stays inside the documented tolerance for every certified fixture.
- Renderer property tests pass and no preset can exceed guard limits.
- Installer/uninstaller, crash recovery, low-disk behavior, cancellation, and update rollback are manually verified on clean Windows machines.
- FFmpeg notices, model attribution, third-party source offers, privacy copy, EULA, support channel, and code-signing identity are finalized.
- A human stereo reviewer approves each release preset on the supported glasses reference set.
