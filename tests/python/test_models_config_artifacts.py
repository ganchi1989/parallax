from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

import aistereo.artifacts as artifacts_module
from aistereo.artifacts import (
    ProjectLayout,
    fingerprint,
    prepare_output_path,
    read_model,
    write_json_atomic,
    write_text_atomic,
)
from aistereo.config import AppConfig, LLMConfig, load_config
from aistereo.errors import ArtifactError
from aistereo.models import (
    MediaInfo,
    Shot,
    ShotManifest,
    StereoParameters,
)


@pytest.mark.parametrize("writer,value", [(write_text_atomic, "ready\n"), (write_json_atomic, {"ready": True})])
def test_atomic_writes_retry_transient_windows_sharing_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer, value
) -> None:
    destination = tmp_path / "artifact.json"
    real_replace = artifacts_module.os.replace
    attempts = 0

    def flaky_replace(source, target) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = PermissionError("temporarily locked")
            error.winerror = 32  # type: ignore[attr-defined]
            raise error
        real_replace(source, target)

    monkeypatch.setattr(artifacts_module.os, "replace", flaky_replace)
    monkeypatch.setattr(artifacts_module.time, "sleep", lambda _seconds: None)

    assert writer(destination, value) == destination
    assert attempts == 3


def test_stereo_parameters_reject_non_finite_and_out_of_range() -> None:
    with pytest.raises(PydanticValidationError):
        StereoParameters(
            depth_strength=float("nan"),
            convergence_depth_percentile=0.5,
            max_background_disparity_norm=0.01,
            max_popout_disparity_norm=0.004,
            temporal_smoothing=0.8,
            transition_frames=4,
        )
    with pytest.raises(PydanticValidationError):
        StereoParameters(
            depth_strength=1,
            convergence_depth_percentile=0.5,
            max_background_disparity_norm=0.2,
            max_popout_disparity_norm=0.004,
            temporal_smoothing=0.8,
            transition_frames=4,
        )


def test_shot_manifest_requires_contiguous_complete_timeline() -> None:
    with pytest.raises(PydanticValidationError):
        ShotManifest(
            source_path="x.mp4",
            frame_rate=24,
            frame_count=4,
            shots=[
                Shot(
                    shot_id=1,
                    start_frame=1,
                    end_frame=3,
                    start_time=0,
                    end_time=1,
                )
            ],
        )


def test_media_rotation_is_normalized() -> None:
    info = MediaInfo(
        path="x.mp4",
        width=1920,
        height=1080,
        frame_rate=24,
        duration_seconds=1,
        frame_count=24,
        rotation_degrees=-90,
    )
    assert info.rotation_degrees == 270


def test_atomic_artifact_round_trip_and_layout(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    model = MediaInfo(
        path="x.mp4", width=8, height=6, frame_rate=24, duration_seconds=1, frame_count=24
    )
    write_json_atomic(layout.media, model)
    assert read_model(layout.media, MediaInfo) == model
    assert layout.depth_dir.is_dir()
    assert not list(layout.source_dir.glob("*.tmp"))


def test_output_containment_is_checked_before_creating_through_symlink(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = project / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this Windows host")

    with pytest.raises(ArtifactError, match="outside its allowed root"):
        prepare_output_path(link / "must-not-exist" / "output.json", allowed_root=project)

    assert not (outside / "must-not-exist").exists()


def test_output_preparation_creates_contained_nested_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    output = prepare_output_path(project / "nested" / "output.json", allowed_root=project)

    assert output == (project / "nested" / "output.json").resolve()
    assert output.parent.is_dir()


def test_fingerprint_changes_with_file_contents(tmp_path: Path) -> None:
    path = tmp_path / "input.bin"
    path.write_bytes(b"a")
    first = fingerprint([path])
    path.write_bytes(b"different")
    assert fingerprint([path]) != first


def test_fingerprint_detects_same_size_content_change_with_restored_mtime(tmp_path: Path) -> None:
    path = tmp_path / "depth.bin"
    path.write_bytes(b"first")
    original = path.stat()
    first = fingerprint([path])

    path.write_bytes(b"other")
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))

    assert path.stat().st_size == original.st_size
    assert path.stat().st_mtime_ns == original.st_mtime_ns
    assert fingerprint([path]) != first


def test_shipped_configs_are_dependency_free_json_yaml() -> None:
    config = load_config(Path("configs/default.yaml"))
    assert config.media.target_height == 720
    assert config.llm.enabled is False


def test_config_forbids_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(json.dumps({"unknown": True}), encoding="utf-8")
    with pytest.raises(Exception, match="Invalid configuration"):
        load_config(path)


def test_llm_config_requires_https() -> None:
    with pytest.raises(PydanticValidationError):
        LLMConfig(base_url="http://api.openai.com/v1")


def test_default_config_is_within_conservative_model_bounds() -> None:
    value = AppConfig()
    assert value.comfort.max_popout_disparity_norm < value.comfort.max_background_disparity_norm
    assert value.depth.backend == "synthetic"
