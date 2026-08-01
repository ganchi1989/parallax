"""Shot-consistent sanitisation and temporal depth filtering."""

from __future__ import annotations

import numpy as np

from ..config import DepthConfig
from ..errors import ValidationError


def _edge_aware_step(frame: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return frame
    padded = np.pad(frame, 1, mode="edge")
    center = padded[1:-1, 1:-1]
    neighbors = (
        padded[:-2, 1:-1],
        padded[2:, 1:-1],
        padded[1:-1, :-2],
        padded[1:-1, 2:],
    )
    # Similar neighbours contribute; strong depth boundaries remain crisp.
    sigma = 0.08
    weight_sum = np.ones_like(center)
    weighted = center.copy()
    for neighbor in neighbors:
        weight = np.exp(-np.abs(neighbor - center) / sigma)
        weighted += neighbor * weight
        weight_sum += weight
    smoothed = weighted / weight_sum
    return center * (1.0 - amount) + smoothed * amount


def normalize_depth_shot(
    raw_depth: np.ndarray,
    config: DepthConfig | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Normalize once per shot, never independently per frame.

    Invalid pixels are replaced by the shot median. Percentiles and scaling are
    calculated over the entire shot, preventing per-frame depth pumping.
    """

    settings = config or DepthConfig()
    source = np.asarray(raw_depth, dtype=np.float32)
    if source.ndim != 3 or any(size <= 0 for size in source.shape):
        raise ValidationError("Raw depth must have non-empty [frames, height, width] shape")
    # One owned buffer, transformed in place. A long shot is hundreds of
    # megabytes per copy, and the old sanitized/normalized/filtered chain held
    # four of them at once, which is what capped shot length.
    working = source.astype(np.float32, copy=True)
    finite = np.isfinite(working)
    invalid_fraction = float(1.0 - np.mean(finite))
    if not np.any(finite):
        raise ValidationError("Depth backend returned no finite values")
    fully_finite = invalid_fraction == 0.0
    # Only materialise the valid subset when something actually has to be
    # filtered out; otherwise the whole array already is the valid subset.
    valid = working if fully_finite else working[finite]
    median = float(np.median(valid))
    lower = float(np.percentile(valid, settings.lower_percentile))
    upper = float(np.percentile(valid, settings.upper_percentile))
    raw_min = float(np.min(valid))
    raw_max = float(np.max(valid))
    if not fully_finite:
        del valid
        np.copyto(working, median, where=~finite)
    del finite

    span = upper - lower
    if span <= max(abs(upper), 1.0) * 1e-7:
        working.fill(0.5)
        reliability = 0.0
    else:
        working -= lower
        working /= span
        np.clip(working, 0.0, 1.0, out=working)
        # Invalid fraction and a collapsed robust range lower confidence.
        reliability = float(np.clip(1.0 - invalid_fraction * 2.0, 0.0, 1.0))

    # Filtering in place is safe because frame `index` only ever reads its own
    # (still unfiltered) values and frame `index - 1` (already filtered).
    working[0] = _edge_aware_step(working[0], settings.spatial_smoothing)
    alpha = settings.temporal_alpha
    for index in range(1, working.shape[0]):
        spatial = _edge_aware_step(working[index], settings.spatial_smoothing)
        working[index] = alpha * working[index - 1] + (1.0 - alpha) * spatial
    np.clip(working, 0.0, 1.0, out=working)
    metadata = {
        "raw_min": raw_min,
        "raw_max": raw_max,
        "clip_lower": lower,
        "clip_upper": upper,
        "invalid_fraction": invalid_fraction,
        "reliability": reliability,
    }
    return working, metadata
