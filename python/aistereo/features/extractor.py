"""Assemble the reliable low-cost features consumed by the rule director."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..models import Shot, ShotFeatures
from .depth_stats import depth_statistics
from .motion import camera_movement, motion_score


def _speech_ratio(shot: Shot, speech_intervals: Sequence[tuple[float, float]] | None) -> float:
    duration = max(shot.end_time - shot.start_time, 1e-9)
    overlap = 0.0
    for start, end in speech_intervals or ():
        overlap += max(0.0, min(shot.end_time, end) - max(shot.start_time, start))
    return float(np.clip(overlap / duration, 0.0, 1.0))


def extract_shot_features(
    shot: Shot,
    frames: Sequence[np.ndarray],
    depth: np.ndarray,
    *,
    frame_indexes: Sequence[int] | None = None,
    speech_intervals: Sequence[tuple[float, float]] | None = None,
    depth_reliability: float = 1.0,
) -> ShotFeatures:
    motion = motion_score(frames, frame_indexes)
    spread, foreground, _ = depth_statistics(depth)
    if frames:
        sample_indexes = np.linspace(0, len(frames) - 1, min(len(frames), 12), dtype=int)
        brightness = float(
            np.mean(
                [
                    np.mean(np.asarray(frames[int(index)], dtype=np.float32))
                    for index in sample_indexes
                ]
            )
            / 255.0
        )
    else:
        brightness = 0.0
    duration = max(0.0, shot.end_time - shot.start_time)
    cut_context = float(np.clip(1.0 - duration / 8.0, 0.0, 1.0))
    return ShotFeatures(
        shot_id=shot.shot_id,
        duration_seconds=duration,
        motion_score=motion,
        speech_ratio=_speech_ratio(shot, speech_intervals),
        depth_spread=spread,
        foreground_ratio=foreground,
        brightness=float(np.clip(brightness, 0.0, 1.0)),
        cut_frequency_context=cut_context,
        camera_movement=camera_movement(frames, motion, frame_indexes),
        depth_reliability=float(np.clip(depth_reliability, 0.0, 1.0)),
    )
