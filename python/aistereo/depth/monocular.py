"""Dependency-free monocular depth from classical image cues.

This backend exists because a project without the optional neural checkpoint
still has to produce *usable* stereo. It is not a learned depth model and never
claims to be one: it combines three cues that hold for ordinary photographic
footage in both daylight and night scenes.

Defocus and detail
    Surfaces close to the lens keep high-frequency detail. Distance removes it,
    through optical falloff, atmospheric scattering, and the simple fact that a
    far object covers fewer pixels. Multi-scale local contrast measures this.

Ground plane
    In hand-held and tripod footage the camera looks roughly at the horizon, so
    image rows below the centre are usually nearer than rows above it.

Atmospheric perspective
    Distance desaturates. Colourful regions read as near, washed-out ones far.

Every cue is scaled by a fixed absolute transfer function rather than a
per-frame rank, so a shot keeps a consistent depth scale from frame to frame and
:func:`normalize_depth_shot` can do the shot-level normalisation it expects.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..config import DepthConfig
from ..errors import PipelineCancelled, ValidationError
from .base import CancelCheck, DepthBackend, ProgressCallback

# Cue weights. Detail dominates because it is the only cue that survives a
# camera that is not level, and the only one that responds to real geometry.
_DETAIL_WEIGHT = 0.58
_GROUND_WEIGHT = 0.27
_SATURATION_WEIGHT = 0.15

# Contrast is measured at two radii so both fine texture and object-sized
# structure contribute; the fine scale is weighted higher.
_FINE_RADIUS = 2
_COARSE_RADIUS = 6
_FINE_SCALE = 900.0
_COARSE_SCALE = 260.0

_OUTPUT_SMOOTHING_RADIUS = 3
_BGR_LUMA = np.asarray([0.114, 0.587, 0.299], dtype=np.float32)


def _resize_bgr(frame: np.ndarray, height: int, width: int) -> np.ndarray:
    """Area-average downscale, nearest upscale, without an image library."""

    value = np.asarray(frame)
    if value.ndim != 3 or value.shape[2] != 3:
        raise ValidationError("Monocular depth needs [height, width, 3] BGR frames")
    if value.shape[:2] == (height, width):
        return value.astype(np.float32)
    source_height, source_width = value.shape[:2]
    if source_height >= height and source_width >= width:
        # Averaging the pixels that fall in each output cell keeps distant,
        # high-frequency detail from aliasing into the contrast cue.
        row_edges = np.linspace(0, source_height, height + 1).astype(np.intp)
        column_edges = np.linspace(0, source_width, width + 1).astype(np.intp)
        integral = np.zeros((source_height + 1, source_width + 1, 3), dtype=np.float64)
        np.cumsum(np.cumsum(value.astype(np.float64), axis=0), axis=1, out=integral[1:, 1:])
        top = row_edges[:-1, None]
        bottom = row_edges[1:, None]
        left = column_edges[None, :-1]
        right = column_edges[None, 1:]
        totals = (
            integral[bottom, right]
            - integral[top, right]
            - integral[bottom, left]
            + integral[top, left]
        )
        counts = np.maximum((bottom - top) * (right - left), 1)[..., None]
        return np.asarray(totals / counts, dtype=np.float32)
    rows = np.rint(np.linspace(0, source_height - 1, height)).astype(np.intp)
    columns = np.rint(np.linspace(0, source_width - 1, width)).astype(np.intp)
    return value[rows[:, None], columns[None, :]].astype(np.float32)


def _box_blur(plane: np.ndarray, radius: int) -> np.ndarray:
    """Constant-time mean filter with edge padding, via a summed-area table."""

    if radius <= 0:
        return plane
    height, width = plane.shape
    padded = np.pad(plane, radius, mode="edge").astype(np.float64)
    integral = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1), dtype=np.float64)
    np.cumsum(np.cumsum(padded, axis=0), axis=1, out=integral[1:, 1:])
    size = 2 * radius + 1
    total = (
        integral[size : size + height, size : size + width]
        - integral[0:height, size : size + width]
        - integral[size : size + height, 0:width]
        + integral[0:height, 0:width]
    )
    return np.asarray(total / float(size * size), dtype=np.float32)


def _local_contrast(luma: np.ndarray, radius: int) -> np.ndarray:
    """Local standard deviation: how much detail survives at this scale."""

    mean = _box_blur(luma, radius)
    mean_of_squares = _box_blur(luma * luma, radius)
    variance = np.maximum(mean_of_squares - mean * mean, 0.0)
    return np.sqrt(variance, dtype=np.float32)


def _ground_plane_prior(height: int, width: int) -> np.ndarray:
    """Rows below the horizon read nearer; the top third stays flat and far."""

    rows = np.linspace(0.0, 1.0, height, dtype=np.float32)
    # Flat above the horizon, then a smooth ramp toward the bottom of frame, so
    # a sky region does not acquire a spurious depth gradient.
    prior = np.clip((rows - 0.32) / 0.68, 0.0, 1.0) ** 1.35
    return np.repeat(prior[:, None], width, axis=1)


def _saturation(bgr: np.ndarray) -> np.ndarray:
    largest = np.max(bgr, axis=2)
    smallest = np.min(bgr, axis=2)
    return np.asarray((largest - smallest) / np.maximum(largest, 1e-4), dtype=np.float32)


def estimate_frame_depth(frame: np.ndarray, height: int, width: int) -> np.ndarray:
    """Return relative depth for one frame, higher meaning nearer."""

    bgr = _resize_bgr(frame, height, width) / 255.0
    luma = np.asarray(bgr @ _BGR_LUMA, dtype=np.float32)

    fine = _local_contrast(luma, _FINE_RADIUS)
    coarse = _local_contrast(luma, _COARSE_RADIUS)
    # log1p keeps a single bright specular edge from saturating the cue while
    # still separating textured foreground from smooth distance.
    detail = 0.68 * np.log1p(fine * _FINE_SCALE) + 0.32 * np.log1p(coarse * _COARSE_SCALE)
    detail /= np.log1p(_FINE_SCALE)
    detail = _box_blur(detail, _COARSE_RADIUS)

    ground = _ground_plane_prior(height, width)
    saturation = _box_blur(_saturation(bgr), _COARSE_RADIUS)

    near = (
        _DETAIL_WEIGHT * detail + _GROUND_WEIGHT * ground + _SATURATION_WEIGHT * saturation
    )
    return _box_blur(near, _OUTPUT_SMOOTHING_RADIUS)


class MonocularCuesDepthBackend(DepthBackend):
    """Deterministic image-analysis depth. No model weights, no network."""

    name = "monocular-cues"

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
            raise ValidationError("Monocular depth needs at least one frame")
        height, width = config.height, config.width
        output = np.empty((len(frames), height, width), dtype=np.float32)
        for index, frame in enumerate(frames):
            if cancel and cancel():
                raise PipelineCancelled("Depth estimation was cancelled")
            output[index] = estimate_frame_depth(frame, height, width)
            if progress:
                progress(index + 1, len(frames))
        return output
