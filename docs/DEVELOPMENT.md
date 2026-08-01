# Windows Development Setup

Parallax Forge keeps video processing local, so desktop development needs a
working, matched FFmpeg/FFprobe pair before the workstation can inspect media.
The supported development version is **8.1.2**.

## First-time workstation setup

Install Node.js 20+, Rust 1.88+, Windows WebView2, and 64-bit Python 3.11 or
newer. Then run this from the repository root in PowerShell:

```powershell
.\scripts\bootstrap.ps1 -ProvisionMediaTools
.\scripts\dev.ps1 -Target desktop
```

`-ProvisionMediaTools` is deliberately explicit because it performs a network
download. It downloads the pinned Windows x64 essentials archive, checks the
SHA-256 digest before extraction, rejects unsafe archive paths, validates both
executables and their reported versions, and checks for the `libx264` encoder.
The complete archive payload (including its license material) is retained under:

```text
.dev-tools/ffmpeg/8.1.2/bin/ffmpeg.exe
.dev-tools/ffmpeg/8.1.2/bin/ffprobe.exe
```

`.dev-tools/` is ignored by Git. Once the cache exists, ordinary bootstrap and
desktop startup validate it without downloading anything:

```powershell
.\scripts\bootstrap.ps1
.\scripts\dev.ps1 -Target desktop
```

You can also repair or provision the cache directly during startup:

```powershell
.\scripts\dev.ps1 -Target desktop -ProvisionMediaTools
```

The browser demo does not use local media tools; start it with
`npm.cmd run dev` or `.\scripts\dev.ps1 -Target web`.

## Resolver policy

The development resolver chooses the first complete pair in this order:

1. `AISTEREO_FFMPEG_PATH` and `AISTEREO_FFPROBE_PATH`, when both are set.
2. The exact project-local cache paths shown above.
3. `ffmpeg` and `ffprobe` found together on `PATH`.

Every source must identify itself as a matched FFmpeg/FFprobe 8.1.2 pair. The
two executables must be colocated in the same directory, and FFmpeg must expose
`libx264`. A partial environment pair, split-directory pair, partial cache,
wrong version, or incompatible build fails immediately with a repair command.
This prevents the workstation from opening successfully and failing much later
in a render.

To use an already-reviewed local installation instead of downloading the
development build, set absolute paths for both tools in the same PowerShell
session:

```powershell
$env:AISTEREO_FFMPEG_PATH = "C:\Tools\ffmpeg-8.1.2\bin\ffmpeg.exe"
$env:AISTEREO_FFPROBE_PATH = "C:\Tools\ffmpeg-8.1.2\bin\ffprobe.exe"
.\scripts\dev.ps1 -Target desktop
```

Do not configure only one variable. The scripts do not read these executable
paths from project JSON or `.env` files.

## Pinned development artifact and license

The provisioner is pinned to:

- Version: FFmpeg 8.1.2 essentials build for Windows x64
- Source: `https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip`
- SHA-256: `db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec`
- Provider license designation: GPLv3

This artifact is for local development only. It is not copied into release
resources and cannot be provisioned together with `-ReleasePackaging`.
Commercial distribution has separate provenance, codec/patent, license,
hash-review, signing, and SBOM gates documented in [RELEASING.md](RELEASING.md).

## Offline and troubleshooting notes

- Provisioning needs network access once. A validated cache works offline.
- Re-run bootstrap with `-ProvisionMediaTools` to replace an incomplete or
  invalid cache. A checksum mismatch is never installed.
- If PowerShell reports that scripts are disabled, use a signed-script policy
  approved for the workstation; do not weaken the machine policy globally.
- If Python setup reports an unsupported version, `py -0p` lists installed
  interpreters. Bootstrap accepts any 64-bit Python 3.11 or newer runtime.
- Use the wrapper script for desktop development because it validates and
  exports the selected media-tool paths before starting Tauri.

## Script-level verification

The resolver tests use local command stubs and never download FFmpeg:

```powershell
.\tests\scripts\media-tools.tests.ps1
```

They also run as part of `.\scripts\check.ps1`.
