# Security Policy

## Supported versions

No signed distributable is provided by this source tree. Once private-alpha builds are published, only the latest signed build will be supported.

## Reporting a vulnerability

Do not file public issues containing exploit details, customer media, project files, or local paths. Before launch, replace this paragraph with a monitored private security address and a response-time commitment.

## Security design

- Conversion is local and telemetry is disabled by default.
- The webview has no general shell permission.
- Native commands expose fixed operations and validate media extensions and filesystem paths.
- The Rust host owns the Python worker lifecycle and parses typed JSON Lines.
- Worker output is treated as untrusted input at the IPC boundary.
- Projects never execute embedded code or commands. Editable project/config files cannot select FFmpeg, FFprobe, or model code; those runtime paths come only from the supervised release host or explicit trusted development environment variables.
- Python JSONL requests are capped at 256 KiB, use exact per-method fields and strict boolean types, and drain oversized lines without parsing them. Rust independently validates desktop requests before forwarding.
- Project JSON/NPZ inputs, decoded working sets, raw frames, media dimensions, and worker result events are bounded before expensive allocation. NPZ inspection and loading stay pinned to one open handle, release-relevant artifacts are content-fingerprinted, and dynamic outputs reject symlink targets, containment escapes, and source/project collisions.
- FFmpeg pipe I/O remains cancellation-aware on Windows, has watchdogs and bounded diagnostic tails, and terminates/reaps stalled children before returning an error.
- User API keys are accepted only by a typed native command, stored in the OS credential store on supported platforms, never returned to the webview, and injected only into the supervised worker environment.
- LLM requests have a fixed provider policy, bounded size, strict timeouts, schema validation, and no tool execution.
- Operator-acquired model inputs must be hash-verified and staged during a controlled release build; the application performs no runtime model download. Any future application updates must use pinned, signed release manifests.

Project content hashes and stage receipts provide cache integrity and change detection; they are not a third-party signature because the project folder is user-writable. An imported project's claim that depth is "certified" must not be treated as commercial provenance by itself. Before public release, either authenticate provenance receipts with a host-managed secret/signature or force certified depth regeneration in the trusted release process.

## Operator responsibilities

Production releases require code signing, signature-verified updates, authenticated depth provenance or trusted regeneration, dependency and SBOM review, malware scanning of bundled binaries, and an incident-response contact. Never distribute an unsigned binary as a production release.
