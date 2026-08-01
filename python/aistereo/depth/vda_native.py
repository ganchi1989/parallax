"""Adapter for a native upstream Video Depth Anything checkpoint.

The packaged application consumes a reviewed TorchScript archive. Development
and evaluation need the upstream ``.pth`` checkpoints as published, which cannot
be TorchScripted without upstream cooperation, so this builds the upstream model
from its own source tree and presents the ``infer_video_depth`` interface that
:class:`VideoDepthAnythingBackend` already knows how to drive.

Neither the source tree nor the checkpoint is downloaded, bundled, or committed
by this package. Both are supplied by the operator, and the checkpoint's licence
is the operator's to honour: the Small encoder is Apache-2.0, while Base and
Large are CC-BY-NC-4.0 and must not be shipped in a commercial build.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

from ..errors import DependencyUnavailableError, ValidationError

# Upstream's own configuration table, keyed by the encoder in the file name.
ENCODER_CONFIGS: dict[str, dict[str, Any]] = {
    "vits": {"encoder": "vits", "features": 64, "out_channels": [48, 96, 192, 384]},
    "vitb": {"encoder": "vitb", "features": 128, "out_channels": [96, 192, 384, 768]},
    "vitl": {"encoder": "vitl", "features": 256, "out_channels": [256, 512, 1024, 1024]},
}
# Upstream treats a negative target frame rate as "do not resample".
_KEEP_SOURCE_FPS = -1


def encoder_for_checkpoint(path: str | Path) -> str:
    """Infer the encoder size from an upstream checkpoint file name."""

    stem = Path(path).stem.lower()
    for encoder in ENCODER_CONFIGS:
        if stem.endswith(encoder) or f"_{encoder}_" in stem:
            return encoder
    raise ValidationError(
        "Could not tell which encoder this checkpoint holds",
        details={"path": str(path), "expected_suffix": sorted(ENCODER_CONFIGS)},
    )


class NativeVideoDepthAnything:
    """Presents the upstream model through the interface the backend expects."""

    def __init__(self, model: Any, encoder: str, *, fp32: bool = False) -> None:
        self._model = model
        self._device: str | None = None
        self.encoder = encoder
        # Half precision is roughly 1.7x faster here and the depth is normalised
        # per shot afterwards, so the reduced mantissa costs nothing visible.
        self.fp32 = fp32

    def infer_video_depth(
        self,
        frames: np.ndarray,
        *,
        input_size: int = 518,
        device: str = "cuda",
    ) -> np.ndarray:
        # Upstream expects the caller to place the module; its inference path
        # assumes weights already live on the device it is handed.
        if self._device != device:
            self._model = self._model.to(device)
            self._model.eval()
            self._device = device
        # The pipeline works in BGR; upstream expects RGB.
        rgb = np.ascontiguousarray(np.asarray(frames)[..., ::-1])
        depth, _fps = self._model.infer_video_depth(
            rgb,
            _KEEP_SOURCE_FPS,
            input_size=input_size,
            device=device,
            fp32=self.fp32,
        )
        return np.asarray(depth, dtype=np.float32)


def load_native_model(checkpoint: str | Path, source_dir: str | Path) -> NativeVideoDepthAnything:
    """Build the upstream model from its source tree and a native checkpoint."""

    checkpoint_path = Path(checkpoint).expanduser().resolve()
    source_path = Path(source_dir).expanduser().resolve()
    if not checkpoint_path.is_file():
        raise DependencyUnavailableError(
            "The configured depth checkpoint does not exist",
            details={"path": str(checkpoint_path)},
        )
    if not (source_path / "video_depth_anything").is_dir():
        raise DependencyUnavailableError(
            "The configured depth model source tree is not an upstream checkout",
            details={"path": str(source_path), "expected": "video_depth_anything/"},
        )
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DependencyUnavailableError(
            "PyTorch is not installed", details={"install_extra": "ai-stereo-director[depth]"}
        ) from exc

    encoder = encoder_for_checkpoint(checkpoint_path)
    if str(source_path) not in sys.path:
        sys.path.insert(0, str(source_path))
    try:
        from video_depth_anything.video_depth import (  # type: ignore[import-not-found]
            VideoDepthAnything,
        )
    except ImportError as exc:
        raise DependencyUnavailableError(
            "The upstream Video Depth Anything package could not be imported",
            details={"source": str(source_path), "reason": str(exc)},
        ) from exc

    model = VideoDepthAnything(**ENCODER_CONFIGS[encoder])
    state = torch.load(str(checkpoint_path), map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    model.eval()
    return NativeVideoDepthAnything(model, encoder)
