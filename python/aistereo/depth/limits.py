"""One memory budget for depth, shared by every guard that enforces it.

These limits used to be independent constants in three modules. They disagreed,
so a shot could pass the pipeline pre-flight, be computed by the backend, and
then be rejected by the artifact loader on the next stage — reported to the user
as three unrelated failures. Everything here derives from one budget instead.
"""

from __future__ import annotations

from ..config import DepthConfig

# Peak resident memory one shot may occupy while its depth is computed.
MAX_DEPTH_WORKING_SET_BYTES = 3 * 1024 * 1024 * 1024

# Measured peak for the whole depth path at the configured grid: decoded frames
# (3 bytes) plus the shot depth buffer and the in-place normalisation that
# follows it. Measured at 16.0; the margin covers backend intermediates.
ESTIMATED_BYTES_PER_DEPTH_PIXEL = 18

# A stored artifact is float16 and is widened to float32 when read, so the
# loader must admit two dtypes at once for the largest shot the budget allows.
_ARTIFACT_BYTES_PER_PIXEL = 6


def max_depth_pixels() -> int:
    """Total depth pixels one shot may hold, across all of its frames."""

    return MAX_DEPTH_WORKING_SET_BYTES // ESTIMATED_BYTES_PER_DEPTH_PIXEL


def max_frames_for_working_set(config: DepthConfig) -> int:
    """Longest shot the bounded-memory budget allows at this depth grid."""

    per_frame = config.height * config.width * ESTIMATED_BYTES_PER_DEPTH_PIXEL
    return max(1, MAX_DEPTH_WORKING_SET_BYTES // per_frame)


# Whatever the frame limit permits must also be loadable, or a shot computes
# successfully and then fails to read back.
MAX_DEPTH_ARRAY_BYTES = max_depth_pixels() * _ARTIFACT_BYTES_PER_PIXEL
MAX_DEPTH_ARCHIVE_BYTES = MAX_DEPTH_ARRAY_BYTES
