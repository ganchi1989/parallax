# Windows Release Checklist

> Status: open release gate. This source tree does not contain reviewed FFmpeg/model binaries, a Python release lock, generated SBOM/inventory, signing configuration, installer validation evidence, or model compatibility evidence. Do not publish an installer until every item below is completed.

## Reproducible inputs

- Pin npm, Cargo, Python, model, FFmpeg, and installer inputs.
- Record hashes for the Small checkpoint and all sidecar resources.
- Build on a clean Windows 11 x64 runner with a documented CUDA/CPU matrix.
- Generate dependency inventories and an SBOM.
- Choose and implement the release provenance policy: either host-authenticated depth receipts that cannot be forged by an imported mutable project, or mandatory VDA regeneration under the trusted signed host before a render can carry a certified-production claim. Local project hashes alone are not authentication.

## Worker packaging

On the controlled Windows build machine, create the packaging environment with
`scripts/bootstrap.ps1 -ReleasePackaging`. This installs the exact PyInstaller
version declared by the `packaging` extra plus the video/depth runtime; normal
developer bootstrap intentionally does not install the freezer or PyTorch.

Release one is x64-only. Run `scripts/package-worker.ps1` on an x64 Windows build
host with explicit, reviewed local paths; independently
approved SHA-256 values; license files; and HTTPS source/provenance URLs for
FFmpeg, ffprobe, and the TorchScript depth adapter. The script must fail on a
missing input or hash mismatch and must never download release inputs.

Create the Python environment from a reviewed, hash-locked dependency set; the
broad development ranges in `pyproject.toml` are not a release lock. Archive the
resolved Python/npm/Cargo inventories and generated SBOM with the build evidence.
The packaging script records FFmpeg's reported build configuration, but the
release reviewer must still confirm its GPL/LGPL implications.

The worker is a PyInstaller one-file executable because Tauri `externalBin`
stages only that target-triple-suffixed file (for example,
`aistereo-worker-x86_64-pc-windows-msvc.exe`). FFmpeg/ffprobe and the model remain
separate, explicitly declared Tauri resources. Measure one-file extraction and
cold-start time on supported Windows baselines. Do not change to onedir unless
the installer is also changed and tested to carry the complete runtime tree.

## Validation

1. Run `scripts/check.ps1` from a clean checkout.
2. Run curated end-to-end fixtures in CPU and certified NVIDIA/CUDA configurations.
3. Verify frame count, color metadata, audio sync, guard limits, blocked-pipe cancellation/watchdogs, resume after corrupt derived state, low-disk failure, Unicode paths, long paths, extreme-aspect/dimension rejection, container-specific MP4/MOV/MKV behavior, and corrupt input handling.
4. Test installer, repair, upgrade, downgrade protection, uninstall, and project preservation on clean VMs.
5. Review representative output frames plus the QC HTML/JSON reports with reference red-cyan glasses; automated contact-sheet generation is not included yet.
6. Attempt to import a deliberately forged project/state pair and verify it cannot obtain an externally meaningful certified-production claim without authenticated provenance or trusted regeneration.

## Signing and updates

- Sign the reviewed FFmpeg/ffprobe inputs before calculating the approved hashes passed to the packaging script; do not modify those binaries after staging.
- Sign the Tauri host and Python worker before the final bundle, then sign the installer with the company certificate.
- Timestamp signatures and verify them on a clean machine.
- Publish only signature-verified update manifests over TLS.
- Keep the prior stable installer and a tested rollback route.
- Do not enable the updater until signing keys, rotation, revocation, and incident handling are documented.

## Commercial launch

- Finalize product name/trademark review, EULA, refund terms, support contact, privacy policy, accessibility statement, pricing, trial behavior, crash-support workflow, and jurisdictional tax handling.
- State hardware, clip-length, codec, glasses, and comfort constraints plainly.
- Do not market the alpha as production-quality automatic movie conversion.
