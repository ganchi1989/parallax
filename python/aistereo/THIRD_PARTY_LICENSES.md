# Python engine dependency licenses

This inventory covers dependencies declared by `pyproject.toml`; release builds
must retain the corresponding upstream notices.

| Dependency | Use | License |
| --- | --- | --- |
| NumPy | Required numerical core | BSD-3-Clause |
| Pydantic | Required configuration/artifact validation | MIT |
| Typer | Required CLI | MIT |
| OpenCV | Optional video/CV fallback | Apache-2.0 |
| PySceneDetect | Optional shot detection | BSD-3-Clause |
| PyTorch | Optional depth inference runtime | BSD-style |
| Video Depth Anything **Small** | Optional model adapter/checkpoint | Apache-2.0 |
| pytest | Development/test only | MIT |
| Ruff | Development/lint only | MIT |
| mypy | Development/type-check only | MIT |
| PyInstaller | Release freezer; its bootloader becomes part of the packaged worker | GPL-2.0-or-later with the PyInstaller bootloader exception |

FFmpeg and FFprobe are external executables rather than Python dependencies.
FFmpeg is primarily LGPL, but a particular binary can become GPL-covered based
on its enabled components. Every installer must audit the exact bundled build,
ship its license/notices, and provide the source offer required by that build.

Video Depth Anything Base/Large checkpoints are not selected or downloaded by
this engine; their CC-BY-NC-4.0 terms are not suitable for the default commercial
path. Model weights are never committed or downloaded automatically.
