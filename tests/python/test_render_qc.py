from __future__ import annotations

import numpy as np
import pytest

import aistereo.qc.metrics as metrics_module
from aistereo.config import RenderConfig
from aistereo.director.presets import preset_parameters
from aistereo.models import StereoPreset
from aistereo.qc.metrics import (
    MAX_COMPONENT_ANALYSIS_CELLS,
    QCAccumulator,
    largest_component_size,
)
from aistereo.qc.report import build_qc_report
from aistereo.render.anaglyph import compose_anaglyph
from aistereo.render.disparity import depth_to_disparity, resize_depth
from aistereo.render.frame import render_stereo_frame
from aistereo.render.hole_fill import fill_holes
from aistereo.render.splat import synthesize_views


def test_basic_anaglyph_uses_left_red_and_right_cyan_bgr() -> None:
    left = np.array([[[1, 2, 30]]], dtype=np.uint8)
    right = np.array([[[10, 20, 3]]], dtype=np.uint8)
    result = compose_anaglyph(left, right, mode="basic")
    assert result.tolist() == [[[10, 20, 30]]]
    swapped = compose_anaglyph(left, right, mode="basic", swap_eyes=True)
    assert swapped.tolist() == [[[1, 2, 3]]]


def test_calibrated_anaglyph_is_bounded_and_gamma_aware() -> None:
    left = np.full((3, 4, 3), 250, dtype=np.uint8)
    right = np.full((3, 4, 3), 100, dtype=np.uint8)
    result = compose_anaglyph(left, right, config=RenderConfig(anaglyph_mode="calibrated"))
    assert result.dtype == np.uint8
    assert result.shape == left.shape


def test_disparity_clamps_foreground_background_separately_and_tapers_edges() -> None:
    depth = np.tile(np.linspace(0, 1, 100, dtype=np.float32), (10, 1))
    params = preset_parameters(StereoPreset.VISTA_DEEP)
    norm, pixels = depth_to_disparity(depth, params, image_width=100)
    assert np.max(norm) <= params.max_popout_disparity_norm
    assert -np.min(norm) <= params.max_background_disparity_norm
    assert np.max(norm[:, -1]) == 0
    assert pixels.shape == depth.shape


def test_depth_resize_preserves_endpoints() -> None:
    source = np.array([[0, 1], [1, 0]], dtype=np.float32)
    result = resize_depth(source, 5, 7)
    assert result.shape == (5, 7)
    assert result[0, 0] == 0 and result[0, -1] == 1


def test_splat_nearer_pixel_wins_collision() -> None:
    frame = np.zeros((1, 3, 3), dtype=np.uint8)
    frame[0, 0] = [10, 0, 0]
    frame[0, 1] = [200, 0, 0]
    depth = np.array([[0.1, 0.9, 0.1]], dtype=np.float32)
    disparity = np.array([[2.0, 0.0, 0.0]], dtype=np.float32)
    result = synthesize_views(frame, depth, disparity)
    assert result.left.shape == frame.shape
    assert result.left_valid.dtype == bool


def test_directional_hole_fill_fills_small_runs_without_opencv() -> None:
    image = np.zeros((1, 5, 3), dtype=np.uint8)
    image[0, 0] = 10
    image[0, 4] = 50
    valid = np.array([[True, False, False, False, True]])
    output, remaining = fill_holes(image, valid, max_directional_width=3, inpaint_radius=0)
    assert not np.any(remaining)
    assert 10 < output[0, 2, 0] < 50


def test_frame_renderer_outputs_pair_anaglyph_and_metrics() -> None:
    frame = np.zeros((16, 24, 3), dtype=np.uint8)
    frame[..., 1] = 120
    depth = np.tile(np.linspace(0, 1, 12), (8, 1))
    result = render_stereo_frame(frame, depth, preset_parameters(StereoPreset.NEUTRAL))
    assert result.left.shape == frame.shape
    assert result.anaglyph.shape == frame.shape
    assert result.disparity_norm.shape == frame.shape[:2]


def test_qc_accumulator_resets_temporal_comparison_per_shot() -> None:
    qc = QCAccumulator()
    disparity = np.array([[-0.01, 0.004]], dtype=np.float32)
    mask = np.array([[True, False]])
    first = qc.add_frame(1, 0, disparity, mask, mask, np.zeros((1, 2)))
    second_shot = qc.add_frame(2, 1, disparity, mask, mask, np.ones((1, 2)))
    assert first.depth_temporal_change == 0
    assert second_shot.depth_temporal_change == 0
    report = build_qc_report(qc, expected_frame_count=3, rendered_frame_count=2)
    assert report.dropped_frames == 1
    assert report.max_background_disparity_norm == pytest.approx(0.01)


def test_largest_hole_component_uses_four_connectivity() -> None:
    mask = np.array([[True, True, False], [False, True, False], [False, False, True]])
    assert largest_component_size(mask) == 3


def test_large_hole_analysis_caps_pure_python_grid(monkeypatch) -> None:
    mask = np.zeros((2160, 3840), dtype=bool)
    mask[100:200, 300:400] = True
    analyzed_shapes: list[tuple[int, int]] = []
    exact = metrics_module._largest_component_size_exact

    def record_shape(value: np.ndarray) -> int:
        analyzed_shapes.append(value.shape)
        return exact(value)

    monkeypatch.setattr(metrics_module, "_largest_component_size_exact", record_shape)
    result = largest_component_size(mask)
    assert result >= 10_000
    assert analyzed_shapes
    assert analyzed_shapes[0][0] * analyzed_shapes[0][1] <= MAX_COMPONENT_ANALYSIS_CELLS


def test_large_hole_approximation_is_conservative_and_clipped() -> None:
    # This is just above the exact-analysis threshold, forcing 2x2 blocks. The
    # two isolated holes occupy adjacent blocks and may conservatively merge.
    side = 257
    mask = np.zeros((side, side), dtype=bool)
    mask[0, 0] = True
    mask[0, 2] = True
    assert mask.size > MAX_COMPONENT_ANALYSIS_CELLS
    assert largest_component_size(mask) == 2


def test_small_hole_analysis_remains_exact_at_threshold() -> None:
    mask = np.zeros((256, 256), dtype=bool)
    mask[4:12, 7:16] = True
    mask[100:104, 100:104] = True
    assert mask.size == MAX_COMPONENT_ANALYSIS_CELLS
    assert largest_component_size(mask) == 8 * 9


def test_qc_explicitly_marks_synthetic_and_probe_fallback_warning() -> None:
    report = build_qc_report(
        QCAccumulator(),
        expected_frame_count=0,
        rendered_frame_count=0,
        depth_backend="synthetic",
        synthetic_depth=True,
        additional_warnings=["Final output probe was unavailable"],
    )
    assert report.synthetic_depth is True
    assert report.depth_backend == "synthetic"
    assert any("not a production deliverable" in item for item in report.warnings)
    assert "Final output probe was unavailable" in report.warnings


def test_comfort_budget_is_measured_against_the_displayed_width() -> None:
    """Portrait clips were getting a third of the parallax the preset asked for.

    Disparity is a fraction of width, but a viewer shows a clip at full height,
    so a 406x720 phone video covers far less screen width than a 16:9 clip and
    the same fraction produced far less angular parallax. 16:9 and wider are
    measured against their own width and must not move.
    """

    from aistereo.errors import ValidationError
    from aistereo.render.disparity import stereo_reference_width

    for width, height in ((1920, 1080), (1280, 720), (2048, 858), (4096, 1716)):
        assert stereo_reference_width(width, height) == float(width)

    # Narrower than the display: measured against the landscape equivalent.
    assert stereo_reference_width(406, 720) == pytest.approx(1280.0, abs=1.0)
    assert stereo_reference_width(1080, 1920) == pytest.approx(3413.0, abs=1.0)
    assert stereo_reference_width(1440, 1080) == pytest.approx(1920.0, abs=1.0)

    with pytest.raises(ValidationError):
        stereo_reference_width(0, 720)
    with pytest.raises(ValidationError):
        stereo_reference_width(406, 0)


def test_portrait_and_landscape_receive_equivalent_parallax() -> None:
    """The same preset on the same depth must feel the same at either aspect."""

    from aistereo.render.disparity import stereo_reference_width

    parameters = preset_parameters(StereoPreset.VISTA_DEEP)
    depth = np.tile(np.linspace(0, 1, 64, dtype=np.float32), (64, 1))

    landscape = depth_to_disparity(
        depth, parameters, image_width=stereo_reference_width(1280, 720)
    )[1]
    portrait = depth_to_disparity(
        depth, parameters, image_width=stereo_reference_width(406, 720)
    )[1]

    landscape_span = float(landscape.max() - landscape.min())
    portrait_span = float(portrait.max() - portrait.min())
    assert portrait_span == pytest.approx(landscape_span, rel=0.01)
    # And it is a visible amount, not the ~3 px the old sizing produced.
    assert portrait_span > 8.0
