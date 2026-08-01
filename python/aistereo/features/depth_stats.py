from __future__ import annotations

import numpy as np


def depth_statistics(depth: np.ndarray) -> tuple[float, float, float]:
    value = np.asarray(depth, dtype=np.float32)
    finite = value[np.isfinite(value)]
    if finite.size == 0:
        return 0.0, 0.0, 0.0
    lower, upper = np.percentile(finite, [10, 90])
    spread = float(np.clip(upper - lower, 0.0, 1.0))
    foreground = float(np.mean(finite >= 0.75))
    temporal_change = 0.0 if value.shape[0] < 2 else float(np.mean(np.abs(np.diff(value, axis=0))))
    return spread, foreground, temporal_change
