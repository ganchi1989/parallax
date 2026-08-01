# Architecture and Trust Boundaries

## Components

### Svelte editor

Owns navigation, project presentation, timeline selection, bounded input controls, compare previews, export configuration, queue state, notifications, and accessibility. In a normal browser it uses a deterministic demo adapter; in Tauri it invokes named commands and subscribes to one typed event stream.

### Rust host

Owns native dialogs, path validation, application directories, worker supervision, process cancellation/shutdown, event relay, and packaged-resource discovery. It is intentionally not a second implementation of the video pipeline. There is no command accepting a shell string.

### Python worker

Owns media inspection/normalization, shots, depth, features, direction, guard, rendering, encoding/remux, caching, QC, and project artifacts. It reads one request per stdin line and writes only JSON objects to stdout. Human-readable logging goes through structured `log` events or stderr.

The optional LLM adapter is a leaf dependency of direction only. It receives a compact `ShotFeatures` record, can return only a schema-validated preset recommendation, and cannot call render code or produce numerical parameters. The Rust host retrieves a user credential from the OS store when launching the worker; neither the Svelte webview nor JSONL messages can read it. Editable project configuration is never trusted to select an executable or model path; the supervised host supplies reviewed resources, while developer CLI runs use dedicated validated environment selections.

Interactive analysis has two trust tiers. `analyze_draft` uses the named `representative_frames` profile to select a bounded set of frames within every shot, infer compact draft-only depth/features, run the rule Director and Comfort Guard, and write a resumable sampled snapshot plus the editable versioned script. It never writes `depth/metadata.json`. Full `estimate_depth`, `extract_features`, and `direct` remain the render-refinement path. The UI may unlock Director editing from exact sampled feature/script coverage, but preview requires exact full-frame analysis coverage; certified export additionally requires the approved depth backend with no fallback shots.

Full refinement is user-triggered rather than immediately occupying the single serialized worker after a draft. This keeps versioned edit/autosave requests responsive. Editing remains available while depth and feature work runs; immediately before the final production Director pass, the UI flushes queued overrides and briefly freezes controls so regeneration can carry those overrides without a race.

## IPC protocol

Request:

```json
{"id":"01J...","method":"render_preview","params":{"project_dir":"D:\\Projects\\Demo","shot_id":12}}
```

Progress:

```json
{"type":"progress","id":"01J...","job_id":"01J...","stage":"render_preview","completed":48,"total":120,"message":"Warping frames"}
```

Terminal success and failure:

```json
{"type":"result","id":"01J...","result":{"preview_path":"..."}}
{"type":"error","id":"01J...","error":{"code":"FFMPEG_NOT_FOUND","message":"...","retryable":false}}
```

Every request ID receives at most one terminal event. Progress is monotonic within a stage. The Rust/Tauri bridge rejects unknown methods and fields before forwarding them; the Python endpoint independently caps each line at 256 KiB, rejects unknown fields and non-boolean boolean parameters, and drains oversized lines. Cancellation is idempotent.

The versioned machine-readable contract is [worker-protocol.schema.json](../contracts/worker-protocol.schema.json). Python, Rust, and TypeScript fixtures must all remain compatible with it.

## Artifact consistency

Pipeline stages write through same-directory, unpredictable temporary files and validate before atomic replacement. A state entry records schema, engine/algorithm namespace, source fingerprint, normalized configuration fingerprint, status, timestamps, output paths, and a bounded error message. Engine or algorithm changes invalidate generated caches. A `running` stage discovered after restart is treated as interrupted, not complete.

Editing `stereo_script.json` invalidates previews, final render, and QC only. Revisioned `manual_override` shots survive compatible regeneration; if shot IDs or normalized width change, regeneration fails closed instead of overwriting those edits. Changing depth settings invalidates depth and every downstream stage. Changing only anaglyph matrix settings invalidates composition/render and QC.

`director/draft_analysis.json` is compact provenance and resume state, not a depth cache or release receipt. It records exact per-shot sample counts, aggregate sampled/full frame counts, the sampled features, script, revision, and profile. Resume accepts it only when its stage fingerprint and exact shot coverage are current. Manual script revisions are composed into the runtime draft summary so reopening never restores stale controls.

JSON and NPZ reads are size/allocation bounded before parsing or decompression. NPZ validation and array loading use the same pinned file/archive handle, preventing a path swap between inspection and allocation. JSON, NPZ, and model files are content-fingerprinted; very large ordinary media use strengthened filesystem identity metadata. Corrupt derived pipeline state resets safely. Depth and feature stages enforce backend-appropriate aggregate per-shot working-set budgets before whole-shot frame materialization. Source, normalized, rendered, and side-by-side frame dimensions/bytes are bounded before pipe reads or allocation. Project-internal render targets are confined to `previews/` or `renders/`, and neither source media nor project artifacts may be selected as render outputs.

FFprobe capture is file-backed, live-size-capped, and timeout-supervised. Large-frame QC component analysis uses a bounded block grid and reports a conservative upper bound for the largest hole; small masks retain exact four-connected analysis.

Those local fingerprints are an integrity/resume mechanism, not an authenticity certificate: a party that can rewrite the entire project can also rewrite its state receipts. A commercial release must authenticate provenance outside the mutable project or regenerate certified depth under the trusted host before making an externally meaningful certification claim.

## Render safety sequence

```text
validated depth
  → shot-level normalization
  → requested preset/manual override
  → mandatory Comfort Guard
  → signed disparity with separate near/far clamps
  → depth-priority forward splat
  → directional hole fill / limited inpaint
  → edge and window checks
  → anaglyph adapter
  → encoded video validation and source-audio remux
```

## Failure model

- Missing tools/models: fail before mutating a stage and show setup instructions.
- Invalid or stale cache: ignore it and recompute; never silently consume it as current.
- Non-certified final depth: reject before feature extraction or rendering; synthetic and cached depth remain available only to preview/development workflows.
- Worker crash: the host emits a structured lifecycle failure; cached project stages remain available for an explicit reopen/restart and resume.
- User cancellation or stalled pipe: poll blocked FFmpeg reads/writes from bounded worker threads, terminate cooperatively, escalate after a timeout, reap the child, and remove partial output.
- Write or low-disk failure: surface the underlying stage error and do not promote a partial output; clean-machine low-disk behavior remains a release validation gate.
- Corrupt output: validation fails and the prior completed artifact remains authoritative.
