# Model and Dependency License Policy

This is an engineering policy, not legal advice. Have counsel review the exact binaries, weights, notices, EULA, and distribution channels before sale.

## Approved initial depth model

The commercial build may integrate **Video Depth Anything Small** only after recording the upstream commit, checkpoint URL, SHA-256 hash, license text, attribution, and a reproducible compatibility test. The upstream project states that the Small model is Apache-2.0 licensed.

Do not bundle Video Depth Anything Base or Large in the commercial SKU under their published CC-BY-NC-4.0 terms. Do not silently substitute a differently licensed checkpoint.

Official upstream: <https://github.com/DepthAnything/Video-Depth-Anything>

### Development checkpoints are not release inputs

Developers may place any checkpoint in the git-ignored `.dev-tools/depth/` cache
for local evaluation, including the CC-BY-NC-4.0 Base and Large encoders. That
cache is never a packaging input: release staging reads only the reviewed
`src-tauri/resources/models/` path. Record every locally cached checkpoint with
its licence, source URL, and SHA-256 so a non-commercial weight can never be
mistaken for an approved one.

## Image-analysis depth backend

The `monocular-cues` backend carries no model and no weights. It derives depth
from detail falloff, framing, and atmospheric perspective using NumPy only, so
it introduces no new licence obligations and no download during conversion. It
is the fallback when the neural model is unavailable, and is also selectable
deliberately. It is permitted for final export because it measures the actual
picture; synthetic depth is not, because it does not.

## FFmpeg

Record the exact FFmpeg build configuration. FFmpeg is primarily LGPL, but enabling GPL components changes distribution obligations. Prefer an LGPL-compatible build unless the commercial release process explicitly accepts and fulfills GPL obligations. Ship notices and the required corresponding-source mechanism for the actual build.

Official legal page: <https://ffmpeg.org/legal.html>

## Dependency rules

- Every runtime dependency must have a reviewed SPDX identifier and pinned version.
- PyInstaller is pinned in the release-only `packaging` extra. Its GPL-2.0-or-later
  license includes the bootloader exception used for distributing frozen apps;
  retain its license/exception text in the generated release inventory.
- Every bundled native binary and model requires provenance and SHA-256.
- Avoid AGPL components in the closed-source desktop product unless a deliberate licensing decision is made.
- Ultralytics/YOLO is not a core dependency.
- No dependency may download executable code during conversion.
- Generate an SBOM for each installer and archive it with release evidence.

## Pre-release inventory

Create a generated inventory containing: component, version/commit, purpose, source URL, license, bundled/not bundled, modification status, notice path, source-offer path where applicable, and reviewer/date. Block the release if any runtime component is `UNKNOWN`.
