# Native host dependency licenses

Direct production crates are intentionally limited:

| Dependency | Purpose | License |
| --- | --- | --- |
| Tauri / `tauri-build` | Desktop runtime and build integration | Apache-2.0 OR MIT |
| `tauri-plugin-dialog` | Native file/folder/save dialogs | Apache-2.0 OR MIT |
| `tauri-plugin-opener` | Reveal a validated output in the OS file manager | Apache-2.0 OR MIT |
| `tauri-plugin-shell` | Fixed Python development command and named release sidecar | Apache-2.0 OR MIT |
| Serde / `serde_json` | Typed JSONL protocol | Apache-2.0 OR MIT |
| `sha2` | Verify the reviewed packaged depth-model digest | Apache-2.0 OR MIT |
| `thiserror` | Internal error definitions | Apache-2.0 OR MIT |
| `uuid` | Native UUID v4 generation for cancellation requests | Apache-2.0 OR MIT |
| `keyring` (Windows target) | Windows Credential Manager integration for the optional LLM key | Apache-2.0 OR MIT |
| `zeroize` | Clear temporary Rust-owned key buffers on drop | Apache-2.0 OR MIT |

`tempfile` is used by tests only and is Apache-2.0 OR MIT. A release pipeline
must generate and review a complete transitive Software Bill of Materials and
license report from the locked dependency graph before distribution.

FFmpeg/ffprobe and the depth adapter are release inputs rather than repository
dependencies. `scripts/package-worker.ps1` requires their reviewed license text,
source/provenance URL, and independently supplied hash, then includes those
notices in the bundle. Release approval must verify the chosen FFmpeg build's
LGPL/GPL configuration and corresponding-source obligations and separately
confirm that the model and adapter permit the intended commercial distribution.
