"""Directional small-hole propagation with optional OpenCV cleanup."""

from __future__ import annotations

import numpy as np

from ..errors import ValidationError


def fill_holes(
    image: np.ndarray,
    valid_mask: np.ndarray,
    *,
    max_directional_width: int = 16,
    inpaint_radius: int = 3,
) -> tuple[np.ndarray, np.ndarray]:
    output = np.asarray(image).copy()
    valid = np.asarray(valid_mask, dtype=bool).copy()
    if output.ndim != 3 or output.shape[:2] != valid.shape:
        raise ValidationError("Hole mask must match the image dimensions")
    height, width = valid.shape
    # Fill only bounded, small horizontal disocclusions. The interpolation uses
    # the two exposed boundaries and is deterministic across platforms.
    for row in range(height):
        x = 0
        while x < width:
            if valid[row, x]:
                x += 1
                continue
            start = x
            while x < width and not valid[row, x]:
                x += 1
            end = x
            run = end - start
            if run > max_directional_width:
                continue
            left = start - 1 if start > 0 and valid[row, start - 1] else None
            right = end if end < width and valid[row, end] else None
            if left is None and right is None:
                continue
            if left is None:
                output[row, start:end] = output[row, right]
            elif right is None:
                output[row, start:end] = output[row, left]
            else:
                left_color = output[row, left].astype(np.float32)
                right_color = output[row, right].astype(np.float32)
                for index, column in enumerate(range(start, end), start=1):
                    weight = index / (run + 1)
                    output[row, column] = np.clip(
                        left_color * (1.0 - weight) + right_color * weight, 0, 255
                    ).astype(output.dtype)
            valid[row, start:end] = True
    remaining = ~valid
    if np.any(remaining) and inpaint_radius > 0:
        try:
            import cv2  # type: ignore[import-not-found]
        except ImportError:
            pass
        else:
            mask = remaining.astype(np.uint8) * 255
            output = cv2.inpaint(output, mask, inpaint_radius, cv2.INPAINT_TELEA)
            valid[:] = True
            remaining[:] = False
    return output, remaining
