"""Convert shot-normalized depth into separately bounded signed disparity."""

from __future__ import annotations

import numpy as np

from ..errors import ValidationError
from ..models import StereoParameters


def resize_depth(depth: np.ndarray, height: int, width: int) -> np.ndarray:
    """Dependency-free bilinear resize for a single depth plane."""

    source = np.asarray(depth, dtype=np.float32)
    if source.ndim != 2:
        raise ValidationError("Depth plane must be two-dimensional")
    if height <= 0 or width <= 0:
        raise ValidationError("Output depth dimensions must be positive")
    if source.shape == (height, width):
        return source.copy()
    y = np.linspace(0, source.shape[0] - 1, height, dtype=np.float32)
    x = np.linspace(0, source.shape[1] - 1, width, dtype=np.float32)
    y0 = np.floor(y).astype(np.intp)
    x0 = np.floor(x).astype(np.intp)
    y1 = np.minimum(y0 + 1, source.shape[0] - 1)
    x1 = np.minimum(x0 + 1, source.shape[1] - 1)
    wy = (y - y0)[:, None]
    wx = (x - x0)[None, :]
    top = source[y0[:, None], x0[None, :]] * (1 - wx) + source[y0[:, None], x1[None, :]] * wx
    bottom = source[y1[:, None], x0[None, :]] * (1 - wx) + source[y1[:, None], x1[None, :]] * wx
    return np.asarray(top * (1 - wy) + bottom * wy, dtype=np.float32)


# Comfort budgets are a fraction of the screen width the picture occupies, and a
# viewer shows a clip at full height. Content narrower than the display covers
# less screen width than a 16:9 clip of the same height, so the same fraction of
# its own width yields proportionally less angular parallax. Measuring against
# the landscape-equivalent width restores the parallax the preset asks for
# without exceeding it: 16:9 and wider are unchanged, and the Comfort Guard
# still clamps in normalised space.
REFERENCE_DISPLAY_ASPECT = 16.0 / 9.0


def stereo_reference_width(
    width: int,
    height: int,
    *,
    display_aspect: float = REFERENCE_DISPLAY_ASPECT,
) -> float:
    """Width the comfort budget is measured against for this frame shape."""

    if width <= 0 or height <= 0:
        raise ValidationError(
            "Stereo reference width needs positive frame dimensions",
            details={"width": width, "height": height},
        )
    return max(float(width), float(height) * display_aspect)


def depth_to_disparity(
    depth: np.ndarray,
    parameters: StereoParameters,
    *,
    image_width: int,
    edge_margin_fraction: float = 0.05,
) -> tuple[np.ndarray, np.ndarray]:
    """Return normalized and pixel disparities (positive means pop-out)."""

    value = np.asarray(depth, dtype=np.float32)
    if value.ndim != 2 or value.size == 0:
        raise ValidationError("Depth must be a non-empty plane")
    if image_width <= 0:
        raise ValidationError("Image width must be positive")
    if not np.all(np.isfinite(value)):
        raise ValidationError("Renderer received non-finite normalized depth")
    value = np.clip(value, 0.0, 1.0)
    convergence = float(np.percentile(value, parameters.convergence_depth_percentile * 100.0))
    signed = value - convergence
    foreground = np.clip(signed / max(1.0 - convergence, 1e-6), 0.0, 1.0)
    background = np.clip(signed / max(convergence, 1e-6), -1.0, 0.0)
    normalized = parameters.depth_strength * (
        foreground * parameters.max_popout_disparity_norm
        + background * parameters.max_background_disparity_norm
    )
    normalized = np.clip(
        normalized,
        -parameters.max_background_disparity_norm,
        parameters.max_popout_disparity_norm,
    )
    if parameters.edge_protection and edge_margin_fraction > 0:
        margin = max(1, round(value.shape[1] * min(edge_margin_fraction, 0.25)))
        taper = np.ones(value.shape[1], dtype=np.float32)
        ramp = np.linspace(0.0, 1.0, margin, dtype=np.float32)
        taper[:margin] = ramp
        taper[-margin:] = ramp[::-1]
        positive = normalized > 0
        normalized = np.where(positive, normalized * taper[None, :], normalized)
    return normalized.astype(np.float32), (normalized * image_width).astype(np.float32)


def edge_violation_mask(
    disparity_norm: np.ndarray,
    *,
    margin_fraction: float = 0.05,
    limit_norm: float = 0.002,
) -> np.ndarray:
    value = np.asarray(disparity_norm, dtype=np.float32)
    if value.ndim != 2:
        raise ValidationError("Disparity must be a plane")
    margin = max(1, round(value.shape[1] * min(max(margin_fraction, 0.0), 0.25)))
    edge = np.zeros(value.shape, dtype=bool)
    edge[:, :margin] = True
    edge[:, -margin:] = True
    return edge & (value > limit_norm)
