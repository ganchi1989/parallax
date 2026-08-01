"""Deterministic depth patterns for tests and UI/demo projects."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..config import DepthConfig
from ..errors import PipelineCancelled, ValidationError
from .base import CancelCheck, DepthBackend, ProgressCallback


class SyntheticDepthBackend(DepthBackend):
    name = "synthetic"

    def __init__(self, pattern: str = "moving-ramp") -> None:
        self.pattern = pattern

    def estimate(
        self,
        frames: Sequence[np.ndarray],
        *,
        shot_id: int,
        config: DepthConfig,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
    ) -> np.ndarray:
        if not frames:
            raise ValidationError("Synthetic depth needs at least one frame")
        height, width = config.height, config.width
        y, x = np.mgrid[0:height, 0:width].astype(np.float32)
        x /= max(width - 1, 1)
        y /= max(height - 1, 1)
        output = np.empty((len(frames), height, width), dtype=np.float32)
        for index in range(len(frames)):
            if cancel and cancel():
                raise PipelineCancelled("Depth estimation was cancelled")
            if self.pattern == "flat":
                frame_depth = np.full((height, width), 0.5, dtype=np.float32)
            elif self.pattern == "radial":
                center_x = 0.5 + 0.15 * np.sin((index + shot_id) * 0.15)
                distance = np.sqrt((x - center_x) ** 2 + (y - 0.5) ** 2)
                frame_depth = np.clip(1.0 - distance * 1.6, 0.0, 1.0)
            else:
                offset = 0.05 * np.sin((index + shot_id * 7) * 0.12)
                frame_depth = np.clip(0.65 * (1.0 - y) + 0.35 * x + offset, 0.0, 1.0)
            output[index] = frame_depth
            if progress:
                progress(index + 1, len(frames))
        return output
