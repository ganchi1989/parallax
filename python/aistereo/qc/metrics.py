"""Streaming render QC metrics; no full-resolution frame history is retained."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..models import FrameQCMetrics, ShotQCSummary

MAX_COMPONENT_ANALYSIS_CELLS = 256 * 256


def _largest_component_size_exact(value: np.ndarray) -> int:
    height, width = value.shape
    seen = np.zeros_like(value)
    largest = 0
    for start_y, start_x in zip(*np.nonzero(value), strict=True):
        if seen[start_y, start_x]:
            continue
        stack = [(int(start_y), int(start_x))]
        seen[start_y, start_x] = True
        size = 0
        while stack:
            y, x = stack.pop()
            size += 1
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and value[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        largest = max(largest, size)
    return largest


def _component_analysis_grid(value: np.ndarray) -> tuple[np.ndarray, int]:
    """OR-reduce a mask until its pure-Python analysis grid is predictably bounded."""

    height, width = value.shape
    block_size = 2
    while ((height + block_size - 1) // block_size) * (
        (width + block_size - 1) // block_size
    ) > MAX_COMPONENT_ANALYSIS_CELLS:
        block_size += 1
    row_starts = np.arange(0, height, block_size, dtype=np.intp)
    column_starts = np.arange(0, width, block_size, dtype=np.intp)
    reduced_rows = np.logical_or.reduceat(value, row_starts, axis=0)
    reduced = np.logical_or.reduceat(reduced_rows, column_starts, axis=1)
    return reduced, block_size


def largest_component_size(mask: np.ndarray) -> int:
    """Return the largest four-connected hole area, exactly for small masks.

    Masks above :data:`MAX_COMPONENT_ANALYSIS_CELLS` are reduced with an
    any-occupied rule before connected-component analysis. Scaling each
    occupied analysis cell by its maximum source block area and clipping to the
    total hole count yields a conservative upper bound: nearby components may
    merge and be over-reported, but a source component is not under-reported.
    """

    value = np.asarray(mask, dtype=bool)
    if value.ndim != 2:
        return 0
    hole_count = int(np.count_nonzero(value))
    if hole_count == 0:
        return 0
    if value.size <= MAX_COMPONENT_ANALYSIS_CELLS:
        return _largest_component_size_exact(value)
    analysis_grid, block_size = _component_analysis_grid(value)
    largest_blocks = _largest_component_size_exact(analysis_grid)
    return min(hole_count, largest_blocks * block_size * block_size)


@dataclass
class _ShotMetrics:
    frame_count: int = 0
    max_popout: float = 0.0
    max_background: float = 0.0
    edge_violations: int = 0
    hole_pixels: int = 0
    largest_hole: int = 0
    temporal_change_sum: float = 0.0
    temporal_change_count: int = 0


@dataclass
class QCAccumulator:
    histogram_edges: np.ndarray = field(
        default_factory=lambda: np.linspace(-0.05, 0.05, 41, dtype=np.float64)
    )
    _histogram: np.ndarray = field(init=False)
    _shots: dict[int, _ShotMetrics] = field(default_factory=dict, init=False)
    _previous_depth: dict[int, np.ndarray] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._histogram = np.zeros(len(self.histogram_edges) - 1, dtype=np.int64)

    def add_frame(
        self,
        shot_id: int,
        frame_index: int,
        disparity_norm: np.ndarray,
        holes: np.ndarray,
        edge_violations: np.ndarray,
        depth: np.ndarray,
    ) -> FrameQCMetrics:
        disparity = np.asarray(disparity_norm, dtype=np.float32)
        hole_mask = np.asarray(holes, dtype=bool)
        edge_mask = np.asarray(edge_violations, dtype=bool)
        current_depth = np.asarray(depth, dtype=np.float32)
        popout = max(0.0, float(np.max(disparity).item()) if disparity.size else 0.0)
        background = max(0.0, -float(np.min(disparity).item()) if disparity.size else 0.0)
        hole_count = int(np.count_nonzero(hole_mask))
        largest_hole = largest_component_size(hole_mask)
        previous = self._previous_depth.get(shot_id)
        temporal = (
            float(np.mean(np.abs(current_depth - previous)))
            if previous is not None and previous.shape == current_depth.shape
            else 0.0
        )
        self._previous_depth[shot_id] = current_depth.copy()
        counts, _ = np.histogram(disparity, bins=self.histogram_edges)
        self._histogram += counts
        shot = self._shots.setdefault(shot_id, _ShotMetrics())
        shot.frame_count += 1
        shot.max_popout = max(shot.max_popout, popout)
        shot.max_background = max(shot.max_background, background)
        shot.edge_violations += int(np.count_nonzero(edge_mask))
        shot.hole_pixels += hole_count
        shot.largest_hole = max(shot.largest_hole, largest_hole)
        if previous is not None:
            shot.temporal_change_sum += temporal
            shot.temporal_change_count += 1
        return FrameQCMetrics(
            frame_index=frame_index,
            max_popout_disparity_norm=popout,
            max_background_disparity_norm=background,
            edge_violation_pixels=int(np.count_nonzero(edge_mask)),
            hole_pixels=hole_count,
            largest_hole_pixels=largest_hole,
            depth_temporal_change=temporal,
        )

    def shot_summaries(
        self,
        *,
        guard_actions: dict[int, list[str]] | None = None,
        fallback_shots: set[int] | None = None,
    ) -> list[ShotQCSummary]:
        actions = guard_actions or {}
        fallbacks = fallback_shots or set()
        return [
            ShotQCSummary(
                shot_id=shot_id,
                frame_count=value.frame_count,
                max_popout_disparity_norm=value.max_popout,
                max_background_disparity_norm=value.max_background,
                edge_violations=value.edge_violations,
                hole_pixels=value.hole_pixels,
                largest_hole_pixels=value.largest_hole,
                depth_temporal_change=(
                    value.temporal_change_sum / value.temporal_change_count
                    if value.temporal_change_count
                    else 0.0
                ),
                comfort_overrides=actions.get(shot_id, []),
                fallback_used=shot_id in fallbacks,
            )
            for shot_id, value in sorted(self._shots.items())
        ]

    @property
    def histogram(self) -> list[int]:
        return [int(value) for value in self._histogram]
