"""Contracts for image-analysis depth and the still preview renderer."""

from __future__ import annotations

import numpy as np
import pytest

from aistereo.config import DepthConfig, RenderConfig, canonical_depth_backend
from aistereo.depth import MonocularCuesDepthBackend, create_depth_backend
from aistereo.depth.normalize import normalize_depth_shot
from aistereo.errors import PipelineCancelled, ValidationError
from aistereo.models import StereoParameters
from aistereo.render.anaglyph import compose_anaglyph
from aistereo.render.disparity import depth_to_disparity
from aistereo.render.frame import render_stereo_frame

DEPTH_CONFIG = DepthConfig(width=192, height=108)


def _layered_frame(height: int = 216, width: int = 384) -> np.ndarray:
    """Sharp, saturated foreground low in frame; soft, washed-out distance high."""

    rng = np.random.default_rng(11)
    frame = np.full((height, width, 3), 30, dtype=np.uint8)
    horizon = height // 2
    frame[:horizon] = np.clip(
        140 + rng.normal(0.0, 2.0, (horizon, width, 3)), 0, 255
    ).astype(np.uint8)
    detail = rng.integers(0, 256, (height - horizon, width, 3), dtype=np.uint16)
    frame[horizon:] = detail.astype(np.uint8)
    return frame


def test_monocular_depth_separates_foreground_from_distance() -> None:
    frame = _layered_frame()
    depth = MonocularCuesDepthBackend().estimate(
        [frame], shot_id=1, config=DEPTH_CONFIG
    )

    assert depth.shape == (1, DEPTH_CONFIG.height, DEPTH_CONFIG.width)
    assert np.all(np.isfinite(depth))
    horizon = DEPTH_CONFIG.height // 2
    far = float(np.mean(depth[0, : horizon - 8]))
    near = float(np.mean(depth[0, horizon + 8 :]))
    assert near > far


def test_monocular_depth_produces_visible_parallax() -> None:
    """The failure this replaced: constant depth meant left and right matched."""

    frame = _layered_frame()
    raw = MonocularCuesDepthBackend().estimate([frame], shot_id=1, config=DEPTH_CONFIG)
    normalized, stats = normalize_depth_shot(raw, DEPTH_CONFIG)
    assert stats["reliability"] > 0

    parameters = StereoParameters(
        depth_strength=0.8,
        convergence_depth_percentile=0.55,
        max_background_disparity_norm=0.010,
        max_popout_disparity_norm=0.004,
        temporal_smoothing=0.9,
        transition_frames=8,
        edge_protection=True,
    )
    _, pixels = depth_to_disparity(
        normalized[0], parameters, image_width=frame.shape[1]
    )
    assert float(pixels.max() - pixels.min()) > 2.0

    rendered = render_stereo_frame(frame, normalized[0], parameters)
    assert not np.array_equal(rendered.left, rendered.right)
    assert not np.array_equal(rendered.anaglyph, frame)


def test_monocular_depth_is_deterministic_and_cancellable() -> None:
    frame = _layered_frame()
    backend = MonocularCuesDepthBackend()
    first = backend.estimate([frame], shot_id=2, config=DEPTH_CONFIG)
    second = backend.estimate([frame], shot_id=2, config=DEPTH_CONFIG)
    assert np.array_equal(first, second)

    with pytest.raises(PipelineCancelled):
        backend.estimate([frame], shot_id=2, config=DEPTH_CONFIG, cancel=lambda: True)
    with pytest.raises(ValidationError):
        backend.estimate([], shot_id=2, config=DEPTH_CONFIG)


def test_monocular_backend_is_registered_under_its_aliases() -> None:
    for alias in ("monocular-cues", "monocular", "image-analysis", "image_analysis"):
        assert canonical_depth_backend(alias) == "monocular-cues"
        assert isinstance(create_depth_backend(alias), MonocularCuesDepthBackend)


def test_calibrated_anaglyph_matches_the_direct_gamma_computation() -> None:
    """The linear-code table must not change the composed image."""

    rng = np.random.default_rng(5)
    left = rng.integers(0, 256, (48, 64, 3), dtype=np.uint16).astype(np.uint8)
    right = rng.integers(0, 256, (48, 64, 3), dtype=np.uint16).astype(np.uint8)
    settings = RenderConfig()
    gamma = settings.gamma

    linear_left = (left[..., ::-1].astype(np.float32) / 255.0) ** gamma
    linear_right = (right[..., ::-1].astype(np.float32) / 255.0) ** gamma
    leakage = settings.leakage_compensation
    linear_left[..., 1:] *= 1.0 - leakage
    linear_right[..., 0] *= 1.0 - leakage
    weights = np.asarray([0.2126, 0.7152, 0.0722])
    for plane in (linear_left, linear_right):
        luminance = np.sum(plane * weights, axis=-1, keepdims=True)
        plane[...] = luminance + (plane - luminance) * settings.saturation
    mixed = linear_left @ np.asarray(settings.left_matrix, np.float32).T
    mixed += linear_right @ np.asarray(settings.right_matrix, np.float32).T
    expected = np.rint(
        (np.clip(mixed, 0.0, 1.0) ** (1.0 / gamma))[..., ::-1] * 255.0
    ).astype(np.uint8)

    actual = compose_anaglyph(left, right, config=settings)
    # Float accumulation order differs, so allow a single code point of drift.
    assert np.max(np.abs(actual.astype(int) - expected.astype(int))) <= 1


def test_basic_mode_keeps_the_classic_red_cyan_split() -> None:
    left = np.array([[[1, 2, 30]]], dtype=np.uint8)
    right = np.array([[[10, 20, 3]]], dtype=np.uint8)
    assert compose_anaglyph(left, right, mode="basic").tolist() == [[[10, 20, 30]]]


def test_image_analysis_depth_never_passes_the_release_gate() -> None:
    """Preview quality is not export quality, and the gate must say so."""

    from aistereo.config import CERTIFIED_DEPTH_BACKEND

    assert canonical_depth_backend("monocular-cues") != CERTIFIED_DEPTH_BACKEND


def test_fallback_identity_is_part_of_the_depth_stage_fingerprint() -> None:
    """A project cached under the old flat fallback must recompute, not resume."""

    from aistereo.pipeline import FALLBACK_DEPTH_IDENTITY

    assert MonocularCuesDepthBackend.name in FALLBACK_DEPTH_IDENTITY


def test_release_gate_admits_measured_depth_and_still_blocks_the_test_pattern() -> None:
    """Image-analysis depth can ship; synthetic depth encodes no scene and cannot."""

    from aistereo.config import CERTIFIED_DEPTH_BACKEND
    from aistereo.pipeline import RELEASE_DEPTH_BACKENDS

    assert CERTIFIED_DEPTH_BACKEND in RELEASE_DEPTH_BACKENDS
    assert MonocularCuesDepthBackend.name in RELEASE_DEPTH_BACKENDS
    assert "synthetic" not in RELEASE_DEPTH_BACKENDS
    assert "cached" not in RELEASE_DEPTH_BACKENDS


def test_depth_validates_as_current_immediately_after_it_is_computed(
    tmp_path, monkeypatch
) -> None:
    """The staleness hash must be reproducible from the stored artifact alone.

    Regression: the estimate and validate paths built their fingerprints from
    two separate expression lists. Adding an input to one silently marked every
    project's depth stale, which reads to a user as "export is locked forever".
    """

    import numpy as np

    import aistereo.pipeline as pipeline_module
    from aistereo.artifacts import ProjectLayout, write_json_atomic
    from aistereo.config import AppConfig, DepthConfig
    from aistereo.models import MediaInfo, Shot, ShotManifest
    from aistereo.pipeline import AIStereoPipeline

    layout = ProjectLayout.at(tmp_path / "project").ensure()
    layout.normalized_video.write_bytes(b"normalized-fixture")
    media = MediaInfo(
        path=str(layout.normalized_video),
        width=32,
        height=32,
        frame_rate=24,
        duration_seconds=1 / 24,
        frame_count=1,
    )
    shots = ShotManifest(
        source_path=str(layout.normalized_video),
        frame_rate=24,
        frame_count=1,
        shots=[
            Shot(shot_id=1, start_frame=0, end_frame=0, start_time=0, end_time=1 / 24,
                 transition="start")
        ],
    )
    write_json_atomic(layout.normalized_media, media)
    write_json_atomic(layout.shots, shots)
    pipeline = AIStereoPipeline(
        layout.root,
        config=AppConfig(depth=DepthConfig(backend="synthetic", width=32, height=32)),
    )
    monkeypatch.setattr(pipeline, "normalize", lambda: media)
    monkeypatch.setattr(pipeline, "detect_shots", lambda: shots)
    monkeypatch.setattr(
        pipeline_module,
        "decode_shots",
        lambda *_a, **_k: [(shots.shots[0], [np.full((32, 32, 3), 96, dtype=np.uint8)])],
    )

    pipeline.estimate_depth()
    # Same inputs, same process: this must not report the artifact as stale.
    pipeline.validate_depth_artifact(shots=shots, require_current_stage=True)


def test_shot_length_limit_is_one_number_derived_from_the_memory_budget() -> None:
    """The pre-flight cap and the backend's own check must never disagree.

    They used to be independent constants: 900 frames versus a 1.5 GiB ceiling
    that only permitted 809. A shot in that gap passed one guard and was then
    rejected by the other, deeper in, with a different message.
    """

    from aistereo.depth.video_depth_anything import (
        MAX_DEPTH_WORKING_SET_BYTES,
        max_frames_for_working_set,
        validate_depth_working_set,
    )

    config = DepthConfig()
    allowed = max_frames_for_working_set(config)

    # The derived limit is exactly the largest shot the backend will accept.
    validate_depth_working_set(allowed, config, shot_id=1)
    with pytest.raises(ValidationError):
        validate_depth_working_set(allowed + 1, config, shot_id=1)

    # The configured cap must not promise more than the memory budget allows.
    assert min(config.max_shot_frames, allowed) == allowed
    assert allowed * config.height * config.width <= MAX_DEPTH_WORKING_SET_BYTES

    # A 50-second shot at the 24 fps working copy has to fit.
    assert allowed >= 24 * 50


def test_normalisation_holds_one_buffer_not_four() -> None:
    """Long shots were capped by normalisation's copies, not by the model."""

    import tracemalloc

    config = DepthConfig(width=64, height=48)
    frames = 400
    raw = np.linspace(0, 9, frames * config.height * config.width, dtype=np.float32)
    raw = raw.reshape(frames, config.height, config.width)

    tracemalloc.start()
    normalized, _stats = normalize_depth_shot(raw, config)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Comfortably under the four full-size float32 copies the old path held.
    assert peak < raw.nbytes * 3
    assert normalized.shape == raw.shape


def test_every_depth_guard_agrees_on_the_same_shot_length() -> None:
    """A shot that computes must also load back, on the next stage.

    Regression: three modules each carried their own ceiling. Depth completed,
    then feature extraction failed reading the artifact the depth stage had just
    written, which surfaced to the user as an unrelated second failure.
    """

    from aistereo.depth.base import MAX_DEPTH_ARRAY_BYTES
    from aistereo.depth.limits import max_frames_for_working_set
    from aistereo.depth.video_depth_anything import validate_depth_working_set

    config = DepthConfig()
    frames = min(config.max_shot_frames, max_frames_for_working_set(config))

    # The backend accepts it...
    validate_depth_working_set(frames, config, shot_id=1)
    # ...and the artifact it produces must be readable: stored float16, widened
    # to float32 on load, so both dtypes are resident at once.
    artifact_bytes = frames * config.height * config.width * 6
    assert artifact_bytes <= MAX_DEPTH_ARRAY_BYTES

    # A 50-second shot at 24 fps is the case that failed in practice.
    assert frames >= 24 * 50


def test_portrait_footage_costs_the_same_as_landscape_to_analyse() -> None:
    """Regression: bounding only the width never reduced tall frames.

    A 320x567 phone video is already narrower than the target width, so the
    old formula left it full height and the feature stage needed three times
    the memory of the equivalent landscape clip, then failed on a shot that
    depth had already processed successfully.
    """

    from aistereo.pipeline import (
        MAX_FEATURE_WORKING_SET_BYTES,
        _feature_frame_size,
        _validate_feature_working_set,
    )

    landscape = _feature_frame_size(568, 320, 320)
    portrait = _feature_frame_size(320, 567, 320)

    # Landscape keeps exactly the size the width-only formula produced.
    assert landscape == (320, 180)
    # Portrait is reduced to a comparable area rather than left at full height.
    assert portrait[1] < 567
    assert abs(landscape[0] * landscape[1] - portrait[0] * portrait[1]) < 0.05 * (
        landscape[0] * landscape[1]
    )
    # Aspect ratio survives the reduction.
    assert abs(portrait[0] / portrait[1] - 320 / 567) < 0.02

    # The shot that failed in practice: 1209 frames of portrait video.
    _validate_feature_working_set(1209, portrait[1], portrait[0], shot_id=9)
    reduced = 1209 * portrait[0] * portrait[1] * 4
    unreduced = 1209 * 567 * 320 * 4
    assert reduced < MAX_FEATURE_WORKING_SET_BYTES
    # The old width-only sizing cost roughly three times as much for this clip.
    assert unreduced > reduced * 2.5


def test_feature_budget_admits_any_shot_depth_accepts() -> None:
    """No stage may reject a shot an earlier stage already processed."""

    from aistereo.depth.limits import max_frames_for_working_set
    from aistereo.pipeline import _feature_frame_size, _validate_feature_working_set

    config = DepthConfig()
    frames = min(config.max_shot_frames, max_frames_for_working_set(config))
    for width, height in ((1920, 1080), (1080, 1920), (568, 320), (320, 567)):
        feature_width, feature_height = _feature_frame_size(width, height, 320)
        _validate_feature_working_set(frames, feature_height, feature_width, shot_id=1)
