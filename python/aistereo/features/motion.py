"""Dependency-free, low-resolution motion and camera movement proxies."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

import numpy as np

from ..models import CameraMovement


def _gray_small(frame: np.ndarray, max_width: int = 160) -> np.ndarray:
    value = np.asarray(frame)
    if value.ndim == 3:
        # Frames from FFmpeg/OpenCV are BGR.
        value = 0.114 * value[..., 0] + 0.587 * value[..., 1] + 0.299 * value[..., 2]
    if value.ndim != 2:
        raise ValueError("frame must be grayscale or BGR")
    if value.shape[1] > max_width:
        step = max(1, int(np.ceil(value.shape[1] / max_width)))
        value = value[::step, ::step]
    return np.asarray(value, dtype=np.float32) / 255.0


def _adjacent_frame_pairs(
    frames: Sequence[np.ndarray], frame_indexes: Sequence[int] | None
) -> list[tuple[np.ndarray, np.ndarray]]:
    if frame_indexes is None:
        return list(pairwise(frames))
    if len(frame_indexes) != len(frames):
        raise ValueError("frame_indexes must match frames")
    if any(right <= left for left, right in pairwise(frame_indexes)):
        raise ValueError("frame_indexes must be strictly increasing")
    return [
        (frames[index], frames[index + 1])
        for index in range(len(frames) - 1)
        if frame_indexes[index + 1] == frame_indexes[index] + 1
    ]


def motion_score(frames: Sequence[np.ndarray], frame_indexes: Sequence[int] | None = None) -> float:
    if len(frames) < 2:
        return 0.0
    scores: list[float] = []
    for previous_frame, current_frame in _adjacent_frame_pairs(frames, frame_indexes):
        previous = _gray_small(previous_frame)
        current = _gray_small(current_frame)
        if current.shape != previous.shape:
            height = min(current.shape[0], previous.shape[0])
            width = min(current.shape[1], previous.shape[1])
            current = current[:height, :width]
            previous = previous[:height, :width]
        scores.append(float(np.mean(np.abs(current - previous))))
    if not scores:
        return 0.0
    # Typical frame differences are small; 0.18 mean luma change maps to 1.
    return float(np.clip(np.percentile(scores, 75) / 0.18, 0.0, 1.0))


def _best_translation(
    previous: np.ndarray, current: np.ndarray, radius: int = 3
) -> tuple[int, int]:
    best = (0, 0)
    best_error = float("inf")
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            y0a, y1a = max(0, dy), min(previous.shape[0], previous.shape[0] + dy)
            x0a, x1a = max(0, dx), min(previous.shape[1], previous.shape[1] + dx)
            y0b, y1b = max(0, -dy), min(current.shape[0], current.shape[0] - dy)
            x0b, x1b = max(0, -dx), min(current.shape[1], current.shape[1] - dx)
            if y1a <= y0a or x1a <= x0a:
                continue
            error = float(np.mean(np.abs(previous[y0a:y1a, x0a:x1a] - current[y0b:y1b, x0b:x1b])))
            if error < best_error:
                best_error = error
                best = (dx, dy)
    return best


def camera_movement(
    frames: Sequence[np.ndarray],
    score: float | None = None,
    frame_indexes: Sequence[int] | None = None,
) -> CameraMovement:
    overall = motion_score(frames, frame_indexes) if score is None else score
    if len(frames) < 2 or overall < 0.08:
        return CameraMovement.STATIC
    frame_pairs = _adjacent_frame_pairs(frames, frame_indexes)
    if not frame_pairs:
        return CameraMovement.STATIC
    pair_indexes = np.linspace(0, len(frame_pairs) - 1, min(len(frame_pairs), 8), dtype=int)
    translations: list[tuple[int, int]] = []
    for raw_index in pair_indexes:
        previous_frame, current_frame = frame_pairs[int(raw_index)]
        previous = _gray_small(previous_frame, 96)
        current = _gray_small(current_frame, 96)
        height = min(current.shape[0], previous.shape[0])
        width = min(current.shape[1], previous.shape[1])
        translations.append(_best_translation(previous[:height, :width], current[:height, :width]))
    if not translations:
        return CameraMovement.STATIC
    values = np.asarray(translations, dtype=np.float32)
    mean = np.mean(values, axis=0)
    deviation = float(np.mean(np.linalg.norm(values - mean, axis=1)))
    magnitude = float(np.linalg.norm(mean))
    if deviation > max(1.5, magnitude * 1.2) or overall > 0.82:
        return CameraMovement.UNSTABLE
    if magnitude < 0.6:
        # Brightness/subject movement without coherent translation is treated
        # conservatively as unstable, not falsely labelled a camera pan.
        return CameraMovement.UNSTABLE
    if abs(mean[0]) > abs(mean[1]) * 1.4:
        return CameraMovement.LATERAL
    if abs(mean[1]) > abs(mean[0]) * 1.4:
        return CameraMovement.VERTICAL
    return CameraMovement.UNSTABLE
