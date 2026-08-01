"""Depth-aware forward splatting with deterministic near-surface collision wins."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..errors import ValidationError


@dataclass(frozen=True)
class SynthesisResult:
    left: np.ndarray
    right: np.ndarray
    left_valid: np.ndarray
    right_valid: np.ndarray


def _forward_warp(
    frame: np.ndarray,
    depth: np.ndarray,
    horizontal_offset: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    height, width = depth.shape
    yy, xx = np.mgrid[0:height, 0:width]
    target_x = np.rint(xx + horizontal_offset).astype(np.int64)
    valid = (target_x >= 0) & (target_x < width)
    source_flat = np.flatnonzero(valid)
    target_flat = (yy[valid] * width + target_x[valid]).astype(np.int64)
    z = depth.ravel()[source_flat]
    # Primary key target, then nearer depth first, then lower source index for a
    # reproducible tie. Selecting the first row per target implements a z-buffer.
    order = np.lexsort((source_flat, -z, target_flat))
    sorted_targets = target_flat[order]
    first = np.empty(sorted_targets.shape, dtype=bool)
    if first.size:
        first[0] = True
        first[1:] = sorted_targets[1:] != sorted_targets[:-1]
    chosen = order[first]
    destination_indexes = target_flat[chosen]
    source_indexes = source_flat[chosen]
    output = np.zeros_like(frame)
    output.reshape(-1, frame.shape[2])[destination_indexes] = frame.reshape(-1, frame.shape[2])[
        source_indexes
    ]
    mask = np.zeros(height * width, dtype=bool)
    mask[destination_indexes] = True
    return output, mask.reshape(height, width)


def synthesize_views(
    frame: np.ndarray,
    depth: np.ndarray,
    disparity_pixels: np.ndarray,
) -> SynthesisResult:
    image = np.asarray(frame)
    z = np.asarray(depth, dtype=np.float32)
    disparity = np.asarray(disparity_pixels, dtype=np.float32)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValidationError("Frame must have shape [height, width, 3]")
    if z.shape != image.shape[:2] or disparity.shape != image.shape[:2]:
        raise ValidationError("Frame, depth, and disparity dimensions must match")
    if not np.all(np.isfinite(z)) or not np.all(np.isfinite(disparity)):
        raise ValidationError("Depth and disparity must be finite")
    half = disparity * 0.5
    left, left_valid = _forward_warp(image, z, half)
    right, right_valid = _forward_warp(image, z, -half)
    return SynthesisResult(left, right, left_valid, right_valid)
