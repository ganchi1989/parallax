from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import aistereo.pipeline as pipeline_module
from aistereo.artifacts import ProjectLayout, write_json_atomic
from aistereo.config import CERTIFIED_DEPTH_BACKEND, AppConfig, DepthConfig
from aistereo.errors import ValidationError
from aistereo.models import (
    DepthManifest,
    FeatureManifest,
    MediaInfo,
    Shot,
    ShotManifest,
    StereoScript,
    WorkerRequest,
)
from aistereo.pipeline import FALLBACK_DEPTH_RELIABILITY, AIStereoPipeline
from aistereo.state import CancellationToken
from aistereo.worker import JSONLWorker, _validate_parameter_contract


def _one_frame_project(tmp_path: Path) -> tuple[AIStereoPipeline, MediaInfo, ShotManifest]:
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
    write_json_atomic(layout.normalized_media, media)
    write_json_atomic(layout.shots, shots)
    pipeline = AIStereoPipeline(
        layout.root,
        config=AppConfig(
            depth=DepthConfig(
                backend=CERTIFIED_DEPTH_BACKEND,
                width=32,
                height=32,
            )
        ),
    )
    return pipeline, media, shots


def test_fallback_depth_is_reused_by_features_and_direction(
    tmp_path: Path, monkeypatch
) -> None:
    pipeline, media, shots = _one_frame_project(tmp_path)
    depth_progress: list[tuple[int, int, str | None]] = []
    pipeline.progress = lambda stage, done, total, message: (
        depth_progress.append((done, total, message)) if stage == "estimate_depth" else None
    )
    frame = np.full((32, 32, 3), 96, dtype=np.uint8)
    attempts = 0

    class FailingProductionBackend:
        name = CERTIFIED_DEPTH_BACKEND

        def estimate(self, *_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("model unavailable in test")

    monkeypatch.setattr(pipeline, "normalize", lambda: media)
    monkeypatch.setattr(pipeline, "detect_shots", lambda: shots)
    monkeypatch.setattr(
        pipeline_module,
        "create_depth_backend",
        lambda *_args, **_kwargs: FailingProductionBackend(),
    )
    monkeypatch.setattr(
        pipeline_module,
        "decode_shots",
        lambda *_args, **_kwargs: [(shots.shots[0], [frame])],
    )

    depth = pipeline.estimate_depth(allow_fallback=True)
    assert depth.shots[0].fallback_used is True
    # Fallback depth comes from image analysis, so a draft still shows parallax.
    # fallback_used stays true, which is what keeps a release render locked.
    assert depth.shots[0].backend == "monocular-cues"
    assert 0 < depth.shots[0].reliability <= FALLBACK_DEPTH_RELIABILITY
    assert attempts == 1
    assert any(
        done == total == 1 and message == "Shot 1 · depth 1/1"
        for done, total, message in depth_progress
    )

    features = pipeline.extract_features(allow_fallback=True)
    assert len(features.shots) == 1
    assert attempts == 1

    script = pipeline.direct(allow_fallback=True)
    assert len(script.shots) == 1
    assert attempts == 1


def test_worker_analysis_methods_accept_and_forward_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    captured: list[tuple[str, bool]] = []

    class PipelineProbe:
        layout = SimpleNamespace(stereo_script=tmp_path / "stereo_script.json")

        def extract_features(
            self, *, speech_intervals=None, allow_fallback: bool = False
        ) -> FeatureManifest:
            del speech_intervals
            captured.append(("extract_features", allow_fallback))
            return FeatureManifest(shots=[])

        def direct(
            self, *, director_name: str = "rules", allow_fallback: bool = False
        ) -> StereoScript:
            del director_name
            captured.append(("direct", allow_fallback))
            return StereoScript(video_width=32, shots=[])

    worker = JSONLWorker()
    probe = PipelineProbe()
    monkeypatch.setattr(worker, "_pipeline", lambda *_args, **_kwargs: probe)
    try:
        for method in ("extract_features", "direct"):
            request = WorkerRequest(
                id=f"{method}-test",
                method=method,
                params={"project_dir": str(tmp_path), "allow_fallback": True},
            )
            _validate_parameter_contract(request)
            worker.dispatch(request, CancellationToken())
    finally:
        worker._executor.shutdown(wait=True)

    assert captured == [("extract_features", True), ("direct", True)]


def test_worker_analysis_defaults_keep_preview_fallback_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    captured: list[bool] = []

    class PipelineProbe:
        def extract_features(
            self, *, speech_intervals=None, allow_fallback: bool = False
        ) -> FeatureManifest:
            del speech_intervals
            captured.append(allow_fallback)
            return FeatureManifest(shots=[])

    worker = JSONLWorker()
    monkeypatch.setattr(worker, "_pipeline", lambda *_args, **_kwargs: PipelineProbe())
    try:
        worker.dispatch(
            WorkerRequest(
                id="extract-features-test",
                method="extract_features",
                params={"project_dir": str(tmp_path)},
            ),
            CancellationToken(),
        )
    finally:
        worker._executor.shutdown(wait=True)

    assert captured == [True]


def test_worker_preview_reuses_fallback_but_final_contract_stays_strict(
    tmp_path: Path, monkeypatch
) -> None:
    captured: list[bool] = []

    class PipelineProbe:
        layout = SimpleNamespace(previews_dir=tmp_path)

        def render(
            self,
            output_path,
            *,
            shot_id=None,
            output_mode="anaglyph",
            allow_fallback: bool = False,
        ) -> Path:
            del shot_id, output_mode
            captured.append(allow_fallback)
            return Path(output_path)

    worker = JSONLWorker()
    monkeypatch.setattr(worker, "_pipeline", lambda *_args, **_kwargs: PipelineProbe())
    monkeypatch.setattr(
        worker,
        "_depth_manifest",
        lambda _pipeline: DepthManifest(backend=CERTIFIED_DEPTH_BACKEND, shots=[]),
    )
    monkeypatch.setattr(
        worker,
        "_depth_status",
        lambda _pipeline: {"production_ready": False},
    )
    try:
        result = worker.dispatch(
            WorkerRequest(
                id="preview-test",
                method="render_preview",
                params={"project_dir": str(tmp_path), "shot_id": 1},
            ),
            CancellationToken(),
        )
    finally:
        worker._executor.shutdown(wait=True)

    assert captured == [True]
    assert result["synthetic_depth"] is True

    final_request = WorkerRequest(
        id="export-test",
        method="render_final",
        params={"project_dir": str(tmp_path), "allow_fallback": True},
    )
    with pytest.raises(ValidationError, match="unsupported parameters"):
        _validate_parameter_contract(final_request)
