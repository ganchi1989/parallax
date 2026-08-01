"""Single-frame renderer used by previews, final output, and unit tests."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import ComfortConfig, RenderConfig
from ..models import StereoParameters
from .anaglyph import compose_anaglyph
from .disparity import (
    depth_to_disparity,
    edge_violation_mask,
    resize_depth,
    stereo_reference_width,
)
from .hole_fill import fill_holes
from .splat import synthesize_views


@dataclass(frozen=True)
class RenderedFrame:
    left: np.ndarray
    right: np.ndarray
    anaglyph: np.ndarray
    disparity_norm: np.ndarray
    holes: np.ndarray
    edge_violations: np.ndarray


def render_stereo_frame(
    frame: np.ndarray,
    depth: np.ndarray,
    parameters: StereoParameters,
    *,
    render_config: RenderConfig | None = None,
    comfort_config: ComfortConfig | None = None,
) -> RenderedFrame:
    render = render_config or RenderConfig()
    comfort = comfort_config or ComfortConfig()
    height, width = frame.shape[:2]
    z = resize_depth(depth, height, width)
    disparity_norm, disparity_pixels = depth_to_disparity(
        z,
        parameters,
        image_width=stereo_reference_width(width, height),
        edge_margin_fraction=comfort.edge_margin_fraction,
    )
    views = synthesize_views(frame, z, disparity_pixels)
    left, left_remaining = fill_holes(
        views.left, views.left_valid, inpaint_radius=render.inpaint_radius
    )
    right, right_remaining = fill_holes(
        views.right, views.right_valid, inpaint_radius=render.inpaint_radius
    )
    holes = left_remaining | right_remaining | ~views.left_valid | ~views.right_valid
    violations = edge_violation_mask(
        disparity_norm,
        margin_fraction=comfort.edge_margin_fraction,
        limit_norm=comfort.edge_disparity_limit_norm,
    )
    anaglyph = compose_anaglyph(left, right, config=render)
    return RenderedFrame(left, right, anaglyph, disparity_norm, holes, violations)
