from __future__ import annotations

import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import aistereo.pipeline as pipeline_module
from aistereo.artifacts import (
    MAX_JSON_ARTIFACT_BYTES,
    ProjectLayout,
    read_json,
    resolve_project_artifact,
    write_json_atomic,
)
from aistereo.cli import estimate_depth_command
from aistereo.cli import render as render_command
from aistereo.config import (
    CERTIFIED_DEPTH_BACKEND,
    AppConfig,
    DepthConfig,
    MediaConfig,
    sanitize_runtime_paths,
)
from aistereo.depth.base import load_depth_shot, save_depth_shot
from aistereo.depth.video_depth_anything import (
    VideoDepthAnythingBackend,
    _prepare_model_for_device,
    _resize_frame,
    _resolve_device,
    max_frames_for_working_set,
    validate_depth_working_set,
)
from aistereo.director.rules import create_stereo_script
from aistereo.errors import (
    ArtifactError,
    StageError,
    SyntheticDepthFinalError,
    ValidationError,
)
from aistereo.models import (
    DepthManifest,
    DepthShotMetadata,
    FeatureManifest,
    MediaInfo,
    Shot,
    ShotFeatures,
    ShotManifest,
    StereoParameters,
)
from aistereo.pipeline import AIStereoPipeline, _validate_feature_working_set
from aistereo.render.video import build_raw_encode_command, interpolate_stereo_parameters
from aistereo.security import sanitized_subprocess_env
from aistereo.state import CancellationToken, PipelineStateStore
from aistereo.worker import JSONLWorker


def _parameters(strength: float, convergence: float, smoothing: float = 0.8) -> StereoParameters:
    return StereoParameters(
        depth_strength=strength,
        convergence_depth_percentile=convergence,
        max_background_disparity_norm=0.008,
        max_popout_disparity_norm=0.003,
        temporal_smoothing=smoothing,
        transition_frames=4,
        edge_protection=True,
    )


def _one_shot_cached_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProjectLayout, AIStereoPipeline]:
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    layout.normalized_video.write_bytes(b"normalized")
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
            Shot(
                shot_id=1,
                start_frame=0,
                end_frame=0,
                start_time=0,
                end_time=1 / 24,
                transition="start",
            )
        ],
    )
    pipeline = AIStereoPipeline(
        layout.root,
        config=AppConfig(depth=DepthConfig(backend="cached", width=32, height=32)),
    )
    monkeypatch.setattr(pipeline, "normalize", lambda: media)
    monkeypatch.setattr(pipeline, "detect_shots", lambda: shots)
    return layout, pipeline


def test_subprocess_environment_strips_api_credentials(monkeypatch) -> None:
    monkeypatch.setenv("AISTEREO_LLM_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-2")
    monkeypatch.setenv("SOME_ACCESS_TOKEN", "secret-3")
    environment = sanitized_subprocess_env()
    assert "AISTEREO_LLM_API_KEY" not in environment
    assert "OPENAI_API_KEY" not in environment
    assert "SOME_ACCESS_TOKEN" not in environment


def test_security_module_captures_key_once_and_removes_environment(monkeypatch) -> None:
    import aistereo.security as security_module

    monkeypatch.setenv("AISTEREO_LLM_API_KEY", "captured-secret")
    spec = importlib.util.spec_from_file_location(
        "isolated_aistereo_security", security_module.__file__
    )
    assert spec is not None and spec.loader is not None
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    assert isolated.captured_llm_api_key() == "captured-secret"
    assert "AISTEREO_LLM_API_KEY" not in __import__("os").environ


def test_render_encode_command_restores_bt709_metadata(tmp_path: Path) -> None:
    command = build_raw_encode_command(
        tmp_path / "out.mp4",
        width=1280,
        height=720,
        frame_rate=24,
        media_config=MediaConfig(color_primaries="bt709"),
    )
    assert command[command.index("-preset") + 1] == "medium"
    assert command[command.index("-color_primaries") + 1] == "bt709"
    assert command[command.index("-color_trc") + 1] == "bt709"
    assert command[command.index("-colorspace") + 1] == "bt709"


def test_resume_requires_same_recorded_output_destination(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path).ensure()
    store = PipelineStateStore(layout, "job")
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    store.begin("render_final", "hash")
    store.complete("render_final", [first])
    assert store.can_resume("render_final", "hash", [first])
    assert not store.can_resume("render_final", "hash", [second])
    first.write_bytes(b"changed output")
    assert not store.can_resume("render_final", "hash", [first])


def test_pipeline_state_invalidates_all_stages_after_engine_change(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path).ensure()
    store = PipelineStateStore(layout, "first")
    output = tmp_path / "output.bin"
    output.write_bytes(b"complete")
    store.begin("render_final", "fingerprint")
    store.complete("render_final", [output])
    persisted = json.loads(layout.pipeline_state.read_text(encoding="utf-8"))
    persisted["engine_version"] = "older-engine"
    write_json_atomic(layout.pipeline_state, persisted)

    reopened = PipelineStateStore(layout, "second")
    assert reopened.state.stages == {}
    assert reopened.state.engine_version != "older-engine"


def test_render_preserves_manual_script_when_generated_inputs_are_current(
    tmp_path: Path, monkeypatch
) -> None:
    layout = ProjectLayout.at(tmp_path).ensure()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    config = AppConfig()
    media = MediaInfo(
        path=str(layout.normalized_video),
        width=32,
        height=24,
        frame_rate=24,
        duration_seconds=1 / 24,
        frame_count=1,
    )
    shots = ShotManifest(
        source_path=str(layout.normalized_video),
        frame_rate=24,
        frame_count=1,
        shots=[
            Shot(
                shot_id=1,
                start_frame=0,
                end_frame=0,
                start_time=0,
                end_time=1 / 24,
                transition="start",
            )
        ],
    )
    features = FeatureManifest(
        shots=[
            ShotFeatures(
                shot_id=1,
                duration_seconds=1 / 24,
                motion_score=0.1,
                speech_ratio=0,
                depth_spread=0.2,
                foreground_ratio=0.2,
                brightness=0.5,
                cut_frequency_context=0,
            )
        ]
    )
    depth_path = save_depth_shot(layout.depth_dir / "shot_0001.npz", np.zeros((1, 4, 6)))
    depth = DepthManifest(
        backend="synthetic",
        shots=[
            DepthShotMetadata(
                shot_id=1,
                path=str(depth_path.relative_to(layout.root)),
                frame_count=1,
                width=6,
                height=4,
                raw_min=0,
                raw_max=0,
                invalid_fraction=0,
                reliability=1,
                backend="synthetic",
            )
        ],
    )
    layout.normalized_video.write_bytes(b"normalized")
    write_json_atomic(layout.normalized_media, media)
    write_json_atomic(layout.shots, shots)
    write_json_atomic(layout.features, features)
    write_json_atomic(layout.depth_metadata, depth)
    script = create_stereo_script(features, video_width=media.width)
    write_json_atomic(layout.stereo_script, script)

    pipeline = AIStereoPipeline.create(layout.root, source, config=config)
    dependency_hash = pipeline._direct_dependency_fingerprint()
    pipeline.state.begin("direct", "generated", dependency_fingerprint=dependency_hash)
    pipeline.state.complete("direct", [layout.stereo_script])
    script.shots[0].manual_override = True
    script.shots[0].parameters.depth_strength = 0.42
    write_json_atomic(layout.stereo_script, script)
    assert not pipeline.state.can_resume("direct", "generated", [layout.stereo_script])
    assert pipeline.state.dependencies_are_current("direct", dependency_hash)

    monkeypatch.setattr(pipeline, "normalize", lambda: media)
    monkeypatch.setattr(pipeline, "detect_shots", lambda: shots)
    monkeypatch.setattr(pipeline, "estimate_depth", lambda: depth)
    monkeypatch.setattr(pipeline, "extract_features", lambda: features)

    def must_not_regenerate(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("render regenerated a valid manual stereo script")

    monkeypatch.setattr(pipeline, "direct", must_not_regenerate)
    captured: dict[str, object] = {}

    def fake_render_video(
        _input_path: Path, rendered_path: Path, **kwargs: object
    ) -> tuple[Path, int, object]:
        captured["script"] = kwargs["script"]
        rendered_path.write_bytes(b"preview")
        return rendered_path, 1, object()

    monkeypatch.setattr(pipeline_module, "render_video", fake_render_video)
    output = layout.previews_dir / "manual.mp4"
    assert pipeline.render(output, shot_id=1) == output.resolve()
    rendered_script = captured["script"]
    assert rendered_script.shots[0].manual_override is True  # type: ignore[attr-defined]
    assert rendered_script.shots[0].parameters.depth_strength == 0.42  # type: ignore[attr-defined]


def test_project_artifact_resolution_rejects_absolute_and_parent_paths(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    depth = layout.depth_dir / "shot_0001.npz"
    depth.write_bytes(b"fixture")
    assert (
        resolve_project_artifact(layout.root, "depth/shot_0001.npz", subdirectory="depth")
        == depth.resolve()
    )
    with pytest.raises(ArtifactError, match="relative"):
        resolve_project_artifact(layout.root, depth.resolve(), subdirectory="depth")
    with pytest.raises(ArtifactError, match="traversal"):
        resolve_project_artifact(layout.root, "depth/../depth/shot_0001.npz", subdirectory="depth")


def test_json_artifact_reader_rejects_oversized_file_before_parsing(tmp_path: Path) -> None:
    artifact = tmp_path / "config.json"
    with artifact.open("wb") as handle:
        handle.seek(MAX_JSON_ARTIFACT_BYTES)
        handle.write(b"x")
    with pytest.raises(ArtifactError, match="bounded size"):
        read_json(artifact)


def test_dynamic_depth_output_rejects_preexisting_symlink(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    outside = tmp_path / "outside.npz"
    outside.write_bytes(b"do not replace")
    link = layout.depth_dir / "shot_0001.npz"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlink creation is unavailable on this Windows host")
    with pytest.raises(ArtifactError, match="symbolic link"):
        save_depth_shot(link, np.zeros((1, 4, 6)))
    assert outside.read_bytes() == b"do not replace"


def test_render_output_cannot_overwrite_source_or_project_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    pipeline = AIStereoPipeline.create(tmp_path / "project", source)
    with pytest.raises(ValidationError, match="source video"):
        pipeline._prepare_render_output(source, preview=False)
    with pytest.raises(ValidationError, match="output folder"):
        pipeline._prepare_render_output(pipeline.layout.config, preview=False)
    expected = pipeline.layout.renders_dir / "master.mp4"
    assert pipeline._prepare_render_output(expected, preview=False) == expected.resolve()


def _one_shot_manifest() -> ShotManifest:
    return ShotManifest(
        source_path="fixture.mp4",
        frame_rate=24,
        frame_count=2,
        shots=[
            Shot(
                shot_id=1,
                start_frame=0,
                end_frame=1,
                start_time=0,
                end_time=1 / 12,
                transition="start",
            )
        ],
    )


def _depth_metadata(*, path: str, backend: str) -> DepthShotMetadata:
    return DepthShotMetadata(
        shot_id=1,
        path=path,
        frame_count=2,
        width=6,
        height=4,
        raw_min=0,
        raw_max=1,
        invalid_fraction=0,
        reliability=1,
        backend=backend,
    )


def test_release_depth_requires_certified_per_shot_provenance(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path).ensure()
    shot_manifest = _one_shot_manifest()
    depth_path = save_depth_shot(layout.depth_dir / "shot_0001.npz", np.zeros((2, 4, 6)))
    write_json_atomic(layout.shots, shot_manifest)
    write_json_atomic(
        layout.depth_metadata,
        DepthManifest(
            backend="video-depth-anything-small",
            shots=[
                _depth_metadata(
                    path=str(depth_path.relative_to(layout.root)), backend="unreviewed-backend"
                )
            ],
        ),
    )

    with pytest.raises(ValidationError, match="provenance"):
        AIStereoPipeline(layout.root, config=AppConfig()).validate_depth_artifact(
            shots=shot_manifest, for_release=True
        )


def test_depth_validation_rejects_manifest_path_escape_and_nonfinite_data(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    shot_manifest = _one_shot_manifest()
    write_json_atomic(layout.shots, shot_manifest)
    outside = save_depth_shot(tmp_path / "outside.npz", np.zeros((2, 4, 6)))
    write_json_atomic(
        layout.depth_metadata,
        DepthManifest(
            backend="synthetic",
            shots=[_depth_metadata(path=str(outside), backend="synthetic")],
        ),
    )
    pipeline = AIStereoPipeline(layout.root, config=AppConfig())
    with pytest.raises(ArtifactError, match="relative"):
        pipeline.validate_depth_artifact(shots=shot_manifest)

    contained = save_depth_shot(
        layout.depth_dir / "shot_0001.npz", np.full((2, 4, 6), np.nan, dtype=np.float32)
    )
    write_json_atomic(
        layout.depth_metadata,
        DepthManifest(
            backend="synthetic",
            shots=[
                _depth_metadata(path=str(contained.relative_to(layout.root)), backend="synthetic")
            ],
        ),
    )
    with pytest.raises(ArtifactError, match="non-finite"):
        pipeline.validate_depth_artifact(shots=shot_manifest)


def test_cached_depth_directory_identity_invalidates_resume(tmp_path: Path, monkeypatch) -> None:
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    layout.normalized_video.write_bytes(b"normalized")
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
            Shot(
                shot_id=1,
                start_frame=0,
                end_frame=0,
                start_time=0,
                end_time=1 / 24,
                transition="start",
            )
        ],
    )
    write_json_atomic(layout.shots, shots)
    first_cache = tmp_path / "cache-one"
    second_cache = tmp_path / "cache-two"
    grid = np.linspace(0, 1, 32 * 32, dtype=np.float32).reshape(1, 32, 32)
    save_depth_shot(first_cache / "shot_0001.npz", grid)
    save_depth_shot(second_cache / "shot_0001.npz", grid[:, :, ::-1])
    pipeline = AIStereoPipeline(
        layout.root,
        config=AppConfig(depth=DepthConfig(backend="cached", width=32, height=32)),
    )
    monkeypatch.setattr(pipeline, "normalize", lambda: media)
    monkeypatch.setattr(pipeline, "detect_shots", lambda: shots)

    first = pipeline.estimate_depth(cache_dir=first_cache)
    first_stage_fingerprint = pipeline.state.state.stages["estimate_depth"].fingerprint
    second = pipeline.estimate_depth(cache_dir=second_cache)
    second_stage_fingerprint = pipeline.state.state.stages["estimate_depth"].fingerprint
    assert first.backend_source_fingerprint != second.backend_source_fingerprint
    assert first_stage_fingerprint != second_stage_fingerprint


def test_cached_depth_rejects_project_depth_output_and_descendants(
    tmp_path: Path, monkeypatch
) -> None:
    layout, pipeline = _one_shot_cached_pipeline(tmp_path, monkeypatch)
    save_depth_shot(layout.depth_dir / "shot_0001.npz", np.zeros((1, 32, 32)))

    with pytest.raises(ValidationError, match="outside the project's depth output"):
        pipeline.estimate_depth(cache_dir=layout.depth_dir)

    nested = layout.depth_dir / "nested"
    save_depth_shot(nested / "shot_0001.npz", np.zeros((1, 32, 32)))
    with pytest.raises(ValidationError, match="outside the project's depth output"):
        pipeline.estimate_depth(cache_dir=nested)


def test_cached_depth_rejects_symlinked_source_inside_project_depth(
    tmp_path: Path, monkeypatch
) -> None:
    layout, pipeline = _one_shot_cached_pipeline(tmp_path, monkeypatch)
    output_source = save_depth_shot(
        layout.depth_dir / "existing.npz",
        np.zeros((1, 32, 32)),
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    link = cache / "shot_0001.npz"
    try:
        link.symlink_to(output_source)
    except OSError:
        pytest.skip("file symlink creation is unavailable on this Windows host")

    with pytest.raises(ValidationError, match="outside the project's depth output"):
        pipeline.estimate_depth(cache_dir=cache)


def test_cached_depth_rejects_symlinked_cache_root_to_project_depth(
    tmp_path: Path, monkeypatch
) -> None:
    layout, pipeline = _one_shot_cached_pipeline(tmp_path, monkeypatch)
    save_depth_shot(layout.depth_dir / "shot_0001.npz", np.zeros((1, 32, 32)))
    cache_alias = tmp_path / "cache-alias"
    try:
        cache_alias.symlink_to(layout.depth_dir, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this Windows host")

    with pytest.raises(ValidationError, match="outside the project's depth output"):
        pipeline.estimate_depth(cache_dir=cache_alias)


def test_cached_depth_rejects_change_after_precomputed_identity(
    tmp_path: Path, monkeypatch
) -> None:
    layout, pipeline = _one_shot_cached_pipeline(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    source = save_depth_shot(cache / "shot_0001.npz", np.zeros((1, 32, 32)))
    original_execute_stage = pipeline._execute_stage

    def mutate_before_operation(*args: object, **kwargs: object) -> object:
        save_depth_shot(source, np.ones((1, 32, 32)))
        return original_execute_stage(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "_execute_stage", mutate_before_operation)
    with pytest.raises(ArtifactError, match="changed while it was being imported"):
        pipeline.estimate_depth(cache_dir=cache, allow_fallback=True)
    assert not (layout.depth_dir / "shot_0001.npz").exists()


def test_cached_depth_rejects_change_during_pinned_load(tmp_path: Path, monkeypatch) -> None:
    layout, pipeline = _one_shot_cached_pipeline(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    save_depth_shot(cache / "shot_0001.npz", np.zeros((1, 32, 32)))

    def mutate_after_load(path: str | Path, **kwargs: object) -> np.ndarray:
        loaded = load_depth_shot(path, **kwargs)  # type: ignore[arg-type]
        save_depth_shot(path, np.ones((1, 32, 32)))
        return loaded

    monkeypatch.setattr(pipeline_module, "load_depth_shot", mutate_after_load)
    with pytest.raises(ArtifactError, match="changed while it was being imported"):
        pipeline.estimate_depth(cache_dir=cache, allow_fallback=True)
    assert not (layout.depth_dir / "shot_0001.npz").exists()


def test_gradual_transition_consumes_smoothing_but_hard_cut_uses_target() -> None:
    previous = _parameters(0.2, 0.2)
    target = _parameters(0.8, 0.8, smoothing=0.9)
    gradual = interpolate_stereo_parameters(previous, target, 0)
    assert previous.depth_strength < gradual.depth_strength < target.depth_strength
    assert interpolate_stereo_parameters(previous, target, 4) is target


def test_vda_chunks_and_resizes_model_inputs() -> None:
    calls: list[tuple[int, int, int]] = []

    class Model:
        def predict(self, frames: np.ndarray) -> np.ndarray:
            calls.append(frames.shape[:3])
            return np.mean(frames, axis=3, dtype=np.float32)

    frames = [np.full((80, 120, 3), index, dtype=np.uint8) for index in range(9)]
    progress: list[tuple[int, int]] = []
    config = DepthConfig(
        width=32,
        height=32,
        chunk_frames=4,
        chunk_overlap=1,
    )
    depth = VideoDepthAnythingBackend(model=Model()).estimate(
        frames,
        shot_id=1,
        config=config,
        progress=lambda done, total: progress.append((done, total)),
    )
    assert depth.shape == (9, 32, 32)
    assert len(calls) == 3
    assert all(shape[0] <= 4 and shape[1:] == (32, 32) for shape in calls)
    assert progress[-1] == (9, 9)


def test_vda_prefers_opencv_area_resize_when_available(monkeypatch) -> None:
    calls: list[tuple[tuple[int, int], int]] = []

    def resize(frame, size, interpolation):
        calls.append((size, interpolation))
        return np.zeros((size[1], size[0], frame.shape[2]), dtype=frame.dtype)

    fake_cv2 = SimpleNamespace(INTER_AREA=3, INTER_LINEAR=1, resize=resize)
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    result = _resize_frame(np.zeros((64, 96, 3), dtype=np.uint8), 32, 40)
    assert result.shape == (32, 40, 3)
    assert calls == [((40, 32), 3)]


def test_vda_aligns_chunk_relative_depth_scale_and_offset() -> None:
    class Model:
        calls = 0

        def predict(self, frames: np.ndarray) -> np.ndarray:
            self.calls += 1
            base = frames[..., 0].astype(np.float32)
            return base * self.calls + self.calls * 10

    frames = [np.full((32, 32, 3), index, dtype=np.uint8) for index in range(8)]
    depth = VideoDepthAnythingBackend(model=Model()).estimate(
        frames,
        shot_id=1,
        config=DepthConfig(
            width=32,
            height=32,
            chunk_frames=4,
            chunk_overlap=2,
        ),
    )
    medians = np.median(depth, axis=(1, 2))
    assert np.allclose(np.diff(medians), 1.0, atol=0.15)


def test_vda_rejects_shot_above_bounded_memory_limit() -> None:
    frames = [np.zeros((32, 32, 3), dtype=np.uint8)] * 33
    with pytest.raises(ValidationError, match="bounded-memory"):
        VideoDepthAnythingBackend(model=object()).estimate(
            frames,
            shot_id=7,
            config=DepthConfig(width=32, height=32, max_shot_frames=32),
        )


def test_vda_rejects_oversized_aggregate_working_set() -> None:
    # Derived from the budget rather than hard-coded, so raising the ceiling
    # cannot silently turn this into a test that asserts nothing.
    config = DepthConfig(width=2048, height=2048, max_shot_frames=10000)
    allowed = max_frames_for_working_set(config)
    validate_depth_working_set(allowed, config, shot_id=9)
    with pytest.raises(ValidationError, match="working-set"):
        validate_depth_working_set(allowed + 1, config, shot_id=9)


def test_feature_extraction_rejects_oversized_aggregate_working_set() -> None:
    with pytest.raises(ValidationError, match="feature working-set"):
        _validate_feature_working_set(720, 720, 1280, shot_id=11)


def test_depth_loader_rejects_zip_bomb_shape_before_allocation(tmp_path: Path) -> None:
    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        header,
        {"descr": "<f2", "fortran_order": False, "shape": (10_000, 2048, 2048)},
    )
    artifact = tmp_path / "oversized.npz"
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("normalized.npy", header.getvalue())
    with pytest.raises(ArtifactError, match="allocation limit"):
        load_depth_shot(artifact)


def test_regeneration_carries_only_revisioned_manual_overrides() -> None:
    features = FeatureManifest(
        shots=[
            ShotFeatures(
                shot_id=shot_id,
                duration_seconds=1,
                motion_score=0.1,
                speech_ratio=0,
                depth_spread=0.2,
                foreground_ratio=0.2,
                brightness=0.5,
                cut_frequency_context=0,
            )
            for shot_id in (1, 2)
        ]
    )
    existing = create_stereo_script(features, video_width=1280)
    existing.shots[0].manual_override = True
    existing.shots[0].parameters.depth_strength = 0.42
    existing.shots[1].parameters.depth_strength = 0.99
    generated = create_stereo_script(features, video_width=1280)
    generated_second = generated.shots[1].parameters.depth_strength

    merged = AIStereoPipeline._carry_manual_overrides(existing, generated)
    assert merged.shots[0].manual_override is True
    assert merged.shots[0].parameters.depth_strength == 0.42
    assert merged.shots[1].manual_override is False
    assert merged.shots[1].parameters.depth_strength == generated_second

    changed_width = create_stereo_script(features, video_width=1920)
    with pytest.raises(ValidationError, match="cannot be migrated"):
        AIStereoPipeline._carry_manual_overrides(existing, changed_width)


def test_vda_prepares_direct_torch_model_for_selected_device(monkeypatch) -> None:
    class Cuda:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def device_count() -> int:
            return 1

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=Cuda()))

    class Model:
        moved_to: str | None = None
        evaluated = False

        def to(self, device: str):
            self.moved_to = device
            return self

        def eval(self):
            self.evaluated = True
            return self

    model = Model()
    assert _resolve_device("auto") == "cuda"
    assert _prepare_model_for_device(model, "cuda") is model
    assert model.moved_to == "cuda"
    assert model.evaluated is True


def test_explicit_unavailable_cuda_fails_clearly(monkeypatch) -> None:
    class Cuda:
        @staticmethod
        def is_available() -> bool:
            return False

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=Cuda()))
    with pytest.raises(Exception, match="CUDA was requested"):
        _resolve_device("cuda")


@pytest.mark.parametrize("failure", ["zero_devices", "runtime_error"])
def test_auto_device_falls_back_to_cpu_when_cuda_runtime_is_not_usable(
    monkeypatch, failure: str
) -> None:
    class Cuda:
        @staticmethod
        def is_available() -> bool:
            if failure == "runtime_error":
                raise RuntimeError("driver mismatch")
            return True

        @staticmethod
        def device_count() -> int:
            return 0

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=Cuda()))
    assert _resolve_device("auto") == "cpu"


def test_worker_bounds_oversized_result_event() -> None:
    worker = JSONLWorker()
    output = io.StringIO()
    worker._output = output
    worker.emit({"type": "result", "id": "large", "result": "x" * 1_000_000})
    worker._executor.shutdown(wait=True)
    event = json.loads(output.getvalue())
    assert event["type"] == "error"
    assert event["id"] == "large"
    assert event["error"]["code"] == "result_too_large"


def test_worker_emits_unicode_messages_as_ascii_safe_json() -> None:
    worker = JSONLWorker()
    output = io.StringIO()
    worker._output = output
    worker.emit(
        {
            "type": "progress",
            "id": "analyze-draft-test",
            "job_id": "analyze-draft-test",
            "stage": "analyze_draft",
            "completed": 12,
            "total": 12,
            "message": "Shot 1 · 12 representative frames · 界",
        }
    )
    worker._executor.shutdown(wait=True)

    encoded = output.getvalue()
    assert encoded.isascii()
    assert "\\u00b7" in encoded
    assert "\\u754c" in encoded
    event = json.loads(encoded)
    assert event["message"] == "Shot 1 · 12 representative frames · 界"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_worker_replaces_non_finite_output_with_correlated_protocol_error(value: float) -> None:
    worker = JSONLWorker()
    output = io.StringIO()
    worker._output = output
    worker.emit(
        {
            "type": "result",
            "id": "analyze-draft-test",
            "result": {"unsafe_number": value},
        }
    )
    worker._executor.shutdown(wait=True)

    encoded = output.getvalue()
    assert encoded.isascii()
    event = json.loads(encoded)
    assert event == {
        "type": "error",
        "id": "analyze-draft-test",
        "error": {
            "code": "invalid_worker_output",
            "message": "Worker output could not be serialized safely",
            "retryable": False,
        },
    }


def test_worker_refuses_synthetic_or_fallback_depth_for_final(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AISTEREO_ALLOW_SYNTHETIC_FINAL", "1")
    layout = ProjectLayout.at(tmp_path).ensure()
    manifest = DepthManifest(
        backend="video-depth-anything-small",
        shots=[
            DepthShotMetadata(
                shot_id=1,
                path="depth/shot_0001.npz",
                frame_count=1,
                width=32,
                height=32,
                raw_min=0,
                raw_max=1,
                invalid_fraction=0,
                reliability=0,
                backend="synthetic",
                fallback_used=True,
            )
        ],
    )
    write_json_atomic(layout.depth_metadata, manifest)
    worker = JSONLWorker()

    class Pipeline:
        def __init__(self, project_layout: ProjectLayout) -> None:
            self.layout = project_layout

        def estimate_depth(self) -> DepthManifest:
            return manifest

        def validate_depth_artifact(self, **_kwargs) -> DepthManifest:
            raise AssertionError("synthetic depth must be rejected before release validation")

    with pytest.raises(SyntheticDepthFinalError):
        worker._require_release_depth(Pipeline(layout))  # type: ignore[arg-type]
    worker._executor.shutdown(wait=True)


def test_worker_ignores_untrusted_tool_and_model_paths(tmp_path: Path, monkeypatch) -> None:
    project = ProjectLayout.at(tmp_path).ensure()
    untrusted = AppConfig()
    untrusted.media.ffmpeg_path = "malicious.exe"
    untrusted.media.ffprobe_path = "malicious-probe.exe"
    untrusted.depth.model_path = str(tmp_path / "untrusted.pt")
    write_json_atomic(project.config, untrusted)
    monkeypatch.delenv("AISTEREO_FFMPEG_PATH", raising=False)
    monkeypatch.delenv("AISTEREO_FFPROBE_PATH", raising=False)
    monkeypatch.delenv("AISTEREO_DEPTH_MODEL_PATH", raising=False)
    worker = JSONLWorker()
    settings = worker._config({}, project_dir=str(project.root))
    worker._executor.shutdown(wait=True)
    assert settings.media.ffmpeg_path == "ffmpeg"
    assert settings.media.ffprobe_path == "ffprobe"
    assert settings.depth.model_path is None


def test_worker_rejects_missing_trusted_model_path(monkeypatch) -> None:
    monkeypatch.setenv("AISTEREO_DEPTH_MODEL_PATH", "definitely-missing-model.pt")
    worker = JSONLWorker()
    with pytest.raises(ValidationError):
        worker._config({})
    worker._executor.shutdown(wait=True)


def test_trusted_startup_model_selects_production_backend(tmp_path: Path, monkeypatch) -> None:
    model = tmp_path / "vda-small.pt"
    model.write_bytes(b"reviewed-fixture")
    monkeypatch.setenv("AISTEREO_DEPTH_MODEL_PATH", str(model))
    worker = JSONLWorker()
    settings = worker._config({})
    worker._executor.shutdown(wait=True)
    assert settings.depth.model_path == str(model.resolve())
    assert settings.depth.backend == "video-depth-anything-small"


def test_worker_persists_backend_and_device_between_requests(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path).ensure()
    worker = JSONLWorker()
    worker._pipeline(
        "configure",
        {"project_dir": str(layout.root), "backend": "cached", "device": "cpu"},
        CancellationToken(),
    )
    subsequent = worker._config({}, project_dir=str(layout.root))
    worker._executor.shutdown(wait=True)
    assert subsequent.depth.backend == "cached"
    assert subsequent.depth.device == "cpu"


def test_rejected_create_does_not_overwrite_existing_project_config(tmp_path: Path) -> None:
    first_source = tmp_path / "first.mp4"
    second_source = tmp_path / "second.mp4"
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")
    project_dir = tmp_path / "project"
    original = AppConfig(depth=DepthConfig(backend="cached", device="cpu"))
    AIStereoPipeline.create(project_dir, first_source, config=original)

    worker = JSONLWorker()
    with pytest.raises(ValidationError, match="different source"):
        worker._pipeline(
            "create-project",
            {
                "project_dir": str(project_dir),
                "input_path": str(second_source),
                "backend": "synthetic",
                "device": "auto",
            },
            CancellationToken(),
            create=True,
        )
    persisted = AppConfig.model_validate_json(ProjectLayout.at(project_dir).config.read_text())
    worker._executor.shutdown(wait=True)
    assert persisted.depth.backend == "cached"
    assert persisted.depth.device == "cpu"


def test_depth_status_reports_provenance(tmp_path: Path) -> None:
    manifest = DepthManifest(
        backend="video-depth-anything-small",
        shots=[
            DepthShotMetadata(
                shot_id=1,
                path="depth/shot_0001.npz",
                frame_count=1,
                width=32,
                height=32,
                raw_min=0,
                raw_max=1,
                invalid_fraction=0,
                reliability=0,
                backend="synthetic",
                fallback_used=True,
                error_code="dependency_unavailable",
            )
        ],
    )
    layout = ProjectLayout.at(tmp_path).ensure()
    write_json_atomic(layout.depth_metadata, manifest)

    class Pipeline:
        def __init__(self) -> None:
            self.layout = layout

        def validate_depth_artifact(self, **_kwargs) -> DepthManifest:
            raise ArtifactError("untrusted fixture")

    status = JSONLWorker._depth_status(Pipeline())  # type: ignore[arg-type]
    assert status == {
        "backend": "video-depth-anything-small",
        "production_ready": False,
        # A synthetic shot pins the tier regardless of the configured name.
        "tier": "synthetic",
        "synthetic_shot_ids": [1],
        "fallback_shot_ids": [1],
        "model_failure_shot_ids": [1],
    }


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("PRECOMPUTED", "cached"),
        ("vda-small", CERTIFIED_DEPTH_BACKEND),
        ("video_depth_anything", CERTIFIED_DEPTH_BACKEND),
    ],
)
def test_depth_backend_aliases_are_canonicalized(alias: str, expected: str) -> None:
    assert DepthConfig(backend=alias).backend == expected


def test_runtime_paths_ignore_editable_config_and_require_trusted_vda_model(
    tmp_path: Path,
) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    model = tmp_path / "vda-small.pt"
    for artifact in (ffmpeg, ffprobe, model):
        artifact.write_bytes(b"reviewed")
    editable = AppConfig(depth=DepthConfig(backend="vda-small", model_path="untrusted.pt"))
    editable.media.ffmpeg_path = "untrusted-ffmpeg.exe"
    editable.media.ffprobe_path = "untrusted-ffprobe.exe"

    trusted = sanitize_runtime_paths(
        editable,
        environment={
            "AISTEREO_FFMPEG_PATH": str(ffmpeg),
            "AISTEREO_FFPROBE_PATH": str(ffprobe),
            "AISTEREO_DEPTH_MODEL_PATH": str(model),
        },
    )
    assert trusted.media.ffmpeg_path == str(ffmpeg.resolve())
    assert trusted.media.ffprobe_path == str(ffprobe.resolve())
    assert trusted.depth.model_path == str(model.resolve())
    assert trusted.depth.backend == CERTIFIED_DEPTH_BACKEND
    assert editable.depth.model_path == "untrusted.pt"

    with pytest.raises(ValidationError, match="trusted local model"):
        sanitize_runtime_paths(editable, environment={})
    assert editable.depth.backend == CERTIFIED_DEPTH_BACKEND


def test_reopening_existing_project_without_config_preserves_creative_settings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project_dir = tmp_path / "project"
    original = AppConfig(depth=DepthConfig(backend="cached", device="cpu"))
    original.comfort.max_popout_disparity_norm = 0.003
    AIStereoPipeline.create(project_dir, source, config=original)

    reopened = AIStereoPipeline.create(project_dir, source)
    persisted = AppConfig.model_validate_json(reopened.layout.config.read_text(encoding="utf-8"))
    assert reopened.config.depth.backend == "cached"
    assert reopened.config.depth.device == "cpu"
    assert persisted == original


def test_existing_project_rejects_a_different_requested_name(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project_dir = tmp_path / "project"
    AIStereoPipeline.create(project_dir, source, name="Original title")

    with pytest.raises(ValidationError, match="different name"):
        AIStereoPipeline.create(project_dir, source, name="Replacement title")

    assert AIStereoPipeline(project_dir).project.name == "Original title"


def test_existing_project_accepts_a_matching_requested_name(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project_dir = tmp_path / "project"
    AIStereoPipeline.create(project_dir, source, name="Feature title")

    reopened = AIStereoPipeline.create(project_dir, source, name="Feature title")

    assert reopened.project.name == "Feature title"


def test_existing_project_allows_legacy_retry_without_a_requested_name(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project_dir = tmp_path / "project"
    AIStereoPipeline.create(project_dir, source, name="Persisted title")

    reopened = AIStereoPipeline.create(project_dir, source)

    assert reopened.project.name == "Persisted title"


def test_cached_working_set_uses_inspected_shape_and_canonical_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    frame_count = 810
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    media = MediaInfo(
        path=str(layout.normalized_video),
        width=1280,
        height=720,
        frame_rate=24,
        duration_seconds=frame_count / 24,
        frame_count=frame_count,
    )
    shots = ShotManifest(
        source_path=str(layout.normalized_video),
        frame_rate=24,
        frame_count=frame_count,
        shots=[
            Shot(
                shot_id=1,
                start_frame=0,
                end_frame=frame_count - 1,
                start_time=0,
                end_time=frame_count / 24,
                transition="start",
            )
        ],
    )
    cache = tmp_path / "cache"
    save_depth_shot(
        cache / "shot_0001.npz",
        np.zeros((frame_count, 32, 32), dtype=np.float32),
    )
    pipeline = AIStereoPipeline(
        layout.root,
        config=AppConfig(depth=DepthConfig(width=384, height=216, max_shot_frames=900)),
    )
    monkeypatch.setattr(pipeline, "normalize", lambda: media)
    monkeypatch.setattr(pipeline, "detect_shots", lambda: shots)

    manifest = pipeline.estimate_depth(backend_name="PRECOMPUTED", cache_dir=cache)
    assert manifest.backend == "cached"
    assert manifest.shots[0].frame_count == frame_count
    assert manifest.shots[0].width == 32


def test_cli_cached_import_persists_effective_backend_device_and_creative_config(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    settings = AppConfig()
    settings.comfort.max_popout_disparity_norm = 0.003
    write_json_atomic(layout.config, settings)
    cache = tmp_path / "cache"
    cache.mkdir()
    captured: dict[str, object] = {}

    def fake_estimate_depth(
        self: AIStereoPipeline,
        *,
        backend_name: str | None = None,
        cache_dir: str | Path | None = None,
        allow_fallback: bool = False,
    ) -> DepthManifest:
        captured.update(
            backend=self.config.depth.backend,
            backend_name=backend_name,
            cache_dir=cache_dir,
            allow_fallback=allow_fallback,
        )
        return DepthManifest(backend=self.config.depth.backend, shots=[])

    monkeypatch.setattr(AIStereoPipeline, "estimate_depth", fake_estimate_depth)
    estimate_depth_command(
        work_dir=layout.root,
        backend="precomputed",
        cache_dir=cache,
        device="cpu",
        config=None,
        allow_fallback=False,
    )
    capsys.readouterr()

    persisted = AppConfig.model_validate_json(layout.config.read_text(encoding="utf-8"))
    assert persisted.depth.backend == "cached"
    assert persisted.depth.device == "cpu"
    assert persisted.comfort.max_popout_disparity_norm == 0.003
    assert captured == {
        "backend": "cached",
        "backend_name": None,
        "cache_dir": cache,
        "allow_fallback": False,
    }


def test_cli_render_persists_effective_render_overrides(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    settings = AppConfig(depth=DepthConfig(backend="cached"))
    settings.comfort.max_popout_disparity_norm = 0.003
    write_json_atomic(layout.config, settings)
    output = layout.renders_dir / "master.mp4"

    def fake_render(
        self: AIStereoPipeline,
        output_path: str | Path,
        *,
        shot_id: int | None = None,
        output_mode: str = "anaglyph",
    ) -> Path:
        del self, shot_id, output_mode
        return Path(output_path)

    monkeypatch.setattr(AIStereoPipeline, "render", fake_render)
    render_command(
        work_dir=layout.root,
        output=output,
        anaglyph_mode="basic",
        swap_eyes=True,
        config=None,
    )
    capsys.readouterr()

    persisted = AppConfig.model_validate_json(layout.config.read_text(encoding="utf-8"))
    assert persisted.render.anaglyph_mode == "basic"
    assert persisted.render.swap_eyes is True
    assert persisted.depth.backend == "cached"
    assert persisted.comfort.max_popout_disparity_norm == 0.003


@pytest.mark.parametrize(
    ("backend", "error_type"),
    [("synthetic", SyntheticDepthFinalError), ("cached", ValidationError)],
)
def test_final_render_rejects_noncertified_depth_before_processing(
    tmp_path: Path,
    monkeypatch,
    backend: str,
    error_type: type[Exception],
) -> None:
    pipeline = AIStereoPipeline(
        tmp_path / backend,
        config=AppConfig(depth=DepthConfig(backend=backend)),
    )
    monkeypatch.setattr(
        pipeline,
        "normalize",
        lambda: pytest.fail("final preflight must run before media processing"),
    )
    with pytest.raises(error_type):
        pipeline.render(pipeline.layout.renders_dir / "master.mp4")


def test_final_render_rejects_stale_or_untrusted_depth_before_features(
    tmp_path: Path, monkeypatch
) -> None:
    model = tmp_path / "vda-small.pt"
    model.write_bytes(b"reviewed")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    pipeline = AIStereoPipeline.create(
        tmp_path / "project",
        source,
        config=AppConfig(depth=DepthConfig(backend=CERTIFIED_DEPTH_BACKEND, model_path=str(model))),
    )
    media = MediaInfo(
        path=str(pipeline.layout.normalized_video),
        width=32,
        height=24,
        frame_rate=24,
        duration_seconds=1 / 24,
        frame_count=1,
    )
    shots = ShotManifest(
        source_path=str(pipeline.layout.normalized_video),
        frame_rate=24,
        frame_count=1,
        shots=[
            Shot(
                shot_id=1,
                start_frame=0,
                end_frame=0,
                start_time=0,
                end_time=1 / 24,
                transition="start",
            )
        ],
    )
    depth = DepthManifest(backend=CERTIFIED_DEPTH_BACKEND, shots=[])
    monkeypatch.setattr(pipeline, "normalize", lambda: media)
    monkeypatch.setattr(pipeline, "detect_shots", lambda: shots)
    monkeypatch.setattr(pipeline, "estimate_depth", lambda: depth)

    def reject_stale(**_kwargs: object) -> DepthManifest:
        raise StageError("Depth stage is stale or its outputs changed")

    monkeypatch.setattr(pipeline, "validate_depth_artifact", reject_stale)
    monkeypatch.setattr(
        pipeline,
        "extract_features",
        lambda: pytest.fail("untrusted depth must fail before feature extraction"),
    )
    with pytest.raises(StageError, match="stale"):
        pipeline.render(pipeline.layout.renders_dir / "master.mp4")


def test_full_pipeline_run_preflights_depth_before_inspection(tmp_path: Path, monkeypatch) -> None:
    pipeline = AIStereoPipeline(tmp_path / "project", config=AppConfig())
    monkeypatch.setattr(
        pipeline,
        "inspect",
        lambda: pytest.fail("run must reject synthetic depth before source inspection"),
    )
    with pytest.raises(SyntheticDepthFinalError):
        pipeline.run(pipeline.layout.renders_dir / "master.mp4")
