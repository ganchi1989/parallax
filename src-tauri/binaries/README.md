# Release sidecar staging

Do not commit the worker executable, model weights, FFmpeg binaries, or a
PyInstaller output directory.

Before a Tauri release build, run `scripts/package-worker.ps1`. It builds the
Python environment as one PyInstaller executable and stages it using Tauri's
required target-triple suffix. For Windows x64 the input file is:

```text
src-tauri/binaries/aistereo-worker-x86_64-pc-windows-msvc.exe
```

`tauri.conf.json` declares the suffix-free path
`binaries/aistereo-worker`; Tauri selects and bundles the target-specific file.
The installed binary is launched by Rust through
`app.shell().sidecar("aistereo-worker")` with no frontend-controlled arguments.

The packaging command deliberately uses `--onefile`; `externalBin` copies one
executable and therefore cannot safely stage an onedir runtime by itself. Measure
the one-time extraction/startup cost on every supported Windows baseline before
release, but do not switch to onedir without adding the complete runtime tree to
the installer contract.

Packaging has no download path. The caller must provide reviewed local FFmpeg,
ffprobe, and TorchScript adapter files; independently approved SHA-256 values;
their license texts; and HTTPS corresponding-source/provenance URLs. A mismatch
or missing input fails packaging. The script stages the exact tools and notices
declared by `tauri.conf.json`, embeds the reviewed model hash through `build.rs`,
and Rust verifies the installed model once before exporting its path to Python.
Keep binaries, weights, and generated notices out of Git. Code-sign the worker,
application, and installer, and retain the source-offer records used to build it.
