from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from aistereo.artifacts import ProjectLayout, read_model, write_json_atomic
from aistereo.config import ComfortConfig
from aistereo.director.comfort_guard import guard_script_for_render
from aistereo.director.overrides import apply_shot_overrides, stereo_script_revision
from aistereo.director.rules import create_stereo_script
from aistereo.errors import RevisionConflictError
from aistereo.models import (
    ApplyShotOverridesRequest,
    FeatureManifest,
    ShotFeatures,
    StereoParameters,
    StereoScript,
    WorkerRequest,
)
from aistereo.state import CancellationToken
from aistereo.worker import JSONLWorker


def _features() -> FeatureManifest:
    return FeatureManifest(
        shots=[
            ShotFeatures(
                shot_id=1,
                duration_seconds=3,
                motion_score=0.2,
                speech_ratio=0.1,
                depth_spread=0.35,
                foreground_ratio=0.2,
                brightness=0.5,
                cut_frequency_context=0.3,
            )
        ]
    )


def _project(tmp_path: Path) -> tuple[ProjectLayout, StereoScript, str]:
    layout = ProjectLayout.at(tmp_path).ensure()
    script = create_stereo_script(_features(), video_width=1280)
    write_json_atomic(layout.stereo_script, script)
    return layout, script, stereo_script_revision(script)


def _parameters(depth_strength: float = 0.7) -> StereoParameters:
    return StereoParameters(
        depth_strength=depth_strength,
        convergence_depth_percentile=0.6,
        max_background_disparity_norm=0.008,
        max_popout_disparity_norm=0.003,
        temporal_smoothing=0.85,
        transition_frames=6,
        edge_protection=True,
    )


def test_override_is_atomic_revisioned_and_reaches_render_guard(tmp_path: Path) -> None:
    layout, _, revision = _project(tmp_path)
    request = ApplyShotOverridesRequest(
        project_dir=str(layout.root),
        expected_revision=revision,
        overrides=[
            {
                "shot_id": 1,
                "preset": "dialogue_subtle",
                "parameters": _parameters().model_dump(mode="json"),
            }
        ],
    )
    result = apply_shot_overrides(request)
    stored = read_model(layout.stereo_script, StereoScript)
    assert result.revision != revision
    assert stored.shots[0].manual_override is True
    assert stored.shots[0].parameters.depth_strength == 0.7
    applied = guard_script_for_render(stored, _features(), ComfortConfig())
    assert applied.shots[0].parameters.depth_strength == 0.7
    assert applied.shots[0].parameters.max_popout_disparity_norm == 0.003


def test_stale_revision_rejects_without_changing_artifact(tmp_path: Path) -> None:
    layout, script, revision = _project(tmp_path)
    stale = "0" * 64 if revision != "0" * 64 else "1" * 64
    request = ApplyShotOverridesRequest(
        project_dir=str(layout.root),
        expected_revision=stale,
        overrides=[{"shot_id": 1, "preset": "neutral", "parameters": _parameters(1.5)}],
    )
    with pytest.raises(RevisionConflictError):
        apply_shot_overrides(request)
    assert stereo_script_revision(read_model(layout.stereo_script, StereoScript)) == revision
    assert read_model(layout.stereo_script, StereoScript) == script


def test_override_request_is_exact_and_rejects_duplicate_shots(tmp_path: Path) -> None:
    _, _, revision = _project(tmp_path)
    item = {"shot_id": 1, "preset": "neutral", "parameters": _parameters()}
    with pytest.raises(PydanticValidationError):
        ApplyShotOverridesRequest.model_validate(
            {
                "project_dir": str(tmp_path),
                "expected_revision": revision,
                "overrides": [item, item],
                "unexpected": True,
            }
        )


def test_worker_apply_override_returns_typed_revision(tmp_path: Path) -> None:
    layout, _, revision = _project(tmp_path)
    worker = JSONLWorker()
    result = worker.dispatch(
        WorkerRequest(
            id="edit-1",
            method="apply_shot_overrides",
            params={
                "project_dir": str(layout.root),
                "expected_revision": revision,
                "overrides": [
                    {
                        "shot_id": 1,
                        "preset": "vista_deep",
                        "parameters": _parameters(0.8).model_dump(mode="json"),
                    }
                ],
            },
        ),
        CancellationToken(),
    )
    worker._executor.shutdown(wait=True)
    assert result.updated_shot_ids == [1]
    assert result.script.shots[0].manual_override is True
