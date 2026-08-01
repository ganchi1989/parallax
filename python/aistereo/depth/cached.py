"""Read-only backend for user-provided or previously computed depth."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ..config import DepthConfig
from ..errors import PipelineCancelled, ValidationError
from .base import (
    MAX_DEPTH_ARRAY_BYTES,
    CancelCheck,
    DepthBackend,
    ProgressCallback,
    inspect_depth_shot,
    load_depth_shot,
)


class CachedDepthBackend(DepthBackend):
    name = "cached"

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir).expanduser().resolve()

    def estimate(
        self,
        frames: Sequence[np.ndarray],
        *,
        shot_id: int,
        config: DepthConfig,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
    ) -> np.ndarray:
        if cancel and cancel():
            raise PipelineCancelled("Depth estimation was cancelled")
        path = self.cache_dir / f"shot_{shot_id:04d}.npz"
        max_bytes = min(
            MAX_DEPTH_ARRAY_BYTES,
            max(1, len(frames)) * config.height * config.width * 8,
        )
        shape = inspect_depth_shot(path, max_array_bytes=max_bytes)
        if frames and shape[0] != len(frames):
            raise ValidationError(
                "Cached depth frame count does not match the shot",
                details={"expected": len(frames), "actual": shape[0], "path": str(path)},
            )
        if shape[1] > config.height or shape[2] > config.width:
            raise ValidationError(
                "Cached depth dimensions exceed the configured depth resolution",
                details={
                    "height": shape[1],
                    "width": shape[2],
                    "max_height": config.height,
                    "max_width": config.width,
                },
            )
        depth = load_depth_shot(path, expected_shape=shape, max_array_bytes=max_bytes)
        if progress:
            progress(depth.shape[0], depth.shape[0])
        return depth
