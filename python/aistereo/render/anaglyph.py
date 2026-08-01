"""Basic and gamma-aware calibrated red/cyan composition."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

import numpy as np

from ..config import RenderConfig
from ..errors import ValidationError


def _validate_pair(left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lhs = np.asarray(left)
    rhs = np.asarray(right)
    if lhs.shape != rhs.shape or lhs.ndim != 3 or lhs.shape[2] != 3:
        raise ValidationError("Left and right images must have identical BGR dimensions")
    return lhs, rhs


@lru_cache(maxsize=8)
def _to_linear_table(gamma: float) -> np.ndarray:
    """Every 8-bit code point raised to ``gamma``, so no frame ever calls pow."""

    return np.asarray((np.arange(256, dtype=np.float32) / 255.0) ** gamma, dtype=np.float32)


def _basic(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    result = np.empty_like(left)
    result[..., 2] = left[..., 2]
    result[..., 1] = right[..., 1]
    result[..., 0] = right[..., 0]
    return result


def compose_anaglyph(
    left: np.ndarray,
    right: np.ndarray,
    *,
    mode: Literal["basic", "calibrated"] | None = None,
    swap_eyes: bool | None = None,
    config: RenderConfig | None = None,
) -> np.ndarray:
    settings = config or RenderConfig()
    lhs, rhs = _validate_pair(left, right)
    selected_mode = mode or settings.anaglyph_mode
    should_swap = settings.swap_eyes if swap_eyes is None else swap_eyes
    if should_swap:
        lhs, rhs = rhs, lhs
    if selected_mode == "basic":
        return _basic(lhs, rhs)
    if selected_mode != "calibrated":
        raise ValidationError("Unknown anaglyph mode", details={"mode": selected_mode})
    gamma = settings.gamma
    # Matrix coefficients are RGB; frames are BGR at the package boundary.
    if lhs.dtype == np.uint8 and rhs.dtype == np.uint8:
        table = _to_linear_table(gamma)
        left_rgb = table[lhs[..., ::-1]]
        right_rgb = table[rhs[..., ::-1]]
    else:
        left_rgb = (lhs[..., ::-1].astype(np.float32) / 255.0) ** gamma
        right_rgb = (rhs[..., ::-1].astype(np.float32) / 255.0) ** gamma
    leakage = settings.leakage_compensation
    left_rgb[..., 1:] *= 1.0 - leakage
    right_rgb[..., 0] *= 1.0 - leakage
    weights = np.asarray([0.2126, 0.7152, 0.0722], dtype=np.float32)
    saturation = settings.saturation
    # Desaturate toward luminance in place; each full-frame temporary avoided
    # here is several megabytes that never has to cross the memory bus.
    for plane in (left_rgb, right_rgb):
        luminance = np.sum(plane * weights, axis=-1, keepdims=True)
        plane -= luminance
        plane *= saturation
        plane += luminance
    left_matrix = np.asarray(settings.left_matrix, dtype=np.float32)
    right_matrix = np.asarray(settings.right_matrix, dtype=np.float32)
    mixed = left_rgb @ left_matrix.T + right_rgb @ right_matrix.T
    np.clip(mixed, 0.0, 1.0, out=mixed)
    mixed **= 1.0 / gamma
    mixed *= 255.0
    return np.rint(mixed[..., ::-1]).astype(np.uint8)
