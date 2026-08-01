# AI Stereo Director Engineering Rules

## Product constraints

- This is a Windows-first, offline video-processing desktop application.
- Keep the Tauri/Rust host thin; product logic belongs in the Python package.
- The UI exchanges paths, metadata, commands, and progress events—not video frames.
- Prioritize deterministic, reproducible, and resumable output.
- Do not add an LLM dependency to the rendering core.
- The optional LLM Assistant may select only a named preset and explanation; it must never emit numerical stereo parameters or bypass the Comfort Guard.
- Never persist, log, return to JavaScript, or place user API keys in JSONL messages.
- Validate and clamp every numerical stereo parameter.
- Never smooth depth or stereo parameters across a hard cut.
- Preserve audio, frame order, and source timing.
- Keep large-model integrations optional and behind interfaces.
- Use synthetic or cached depth in automated tests.

## Architecture contracts

- Frontend: Svelte 5, Vite, TypeScript, under `src/`.
- Native host: Tauri v2 under `src-tauri/`.
- Engine: typed Python package under `python/aistereo/`.
- Python worker IPC: newline-delimited JSON on stdin/stdout.
- Requests use `{ "id": string, "method": string, "params": object }`.
- Events use `progress`, `result`, `error`, or `log` in the `type` field.
- On browsers without Tauri, the frontend must use a realistic demo adapter.
- Do not expose arbitrary shell execution from the desktop UI.

## Engineering rules

- Add tests for behavioral changes.
- Document production dependencies and their licenses.
- Keep modules small and use explicit error handling at process boundaries.
- Never commit model weights, rendered media, caches, or secrets.
- Use `apply_patch` for hand-authored file changes.

## Required completion checks

- Run the Python test suite and linter.
- Run frontend type checking, tests, and production build.
- Run Rust formatting/checks when a Rust toolchain is available.
- Report skipped integration checks and unresolved release limitations honestly.
