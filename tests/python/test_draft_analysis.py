from __future__ import annotations

import io
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

import aistereo.media.frames as frames_module
import aistereo.pipeline as pipeline_module
from aistereo.artifacts import read_model, write_json_atomic
from aistereo.config import CERTIFIED_DEPTH_BACKEND, AppConfig, DepthConfig
from aistereo.director.overrides import stereo_script_revision
from aistereo.errors import ArtifactError, DependencyUnavailableError, ValidationError
from aistereo.features.motion import camera_movement, motion_score
from aistereo.media.frames import decode_sampled_shots, representative_frame_indices
from aistereo.models import (
    DraftAnalysisSnapshot,
    DraftCoverage,
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


def _shot(shot_id: int, start: int, end: int, *, transition: str = "cut") -> Shot:
    return Shot(
        shot_id=shot_id,
        start_frame=start,
        end_frame=end,
        start_time=start / 24,
        end_time=(end + 1) / 24,
        transition=transition,
    )


def test_representative_plan_is_deterministic_bounded_and_pair_aware() -> None:
    long_shot = _shot(1, 100, 199, transition="start")
    first = representative_frame_indices(long_shot)
    assert first == representative_frame_indices(long_shot)
    assert len(first) == 12
    assert first[:2] == (100, 101)
    assert first[-2:] == (198, 199)
    assert all(long_shot.start_frame <= index <= long_shot.end_frame for index in first)
    assert sum(right == left + 1 for left, right in pairwise(first)) >= 6

    short_shot = _shot(2, 200, 204)
    assert representative_frame_indices(short_shot) == (200, 201, 202, 203, 204)
    assert max(first) < min(representative_frame_indices(short_shot))


def test_sampled_decoder_selects_before_scaling_and_reads_only_planned_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "normalized.mp4"
    source.write_bytes(b"video")
    media = MediaInfo(
        path=str(source),
        width=8,
        height=4,
        frame_rate=24,
        duration_seconds=20 / 24,
        frame_count=20,
    )
    shots = ShotManifest(
        source_path=str(source),
        frame_rate=24,
        frame_count=20,
        shots=[_shot(1, 0, 9, transition="start"), _shot(2, 10, 19)],
    )
    plan = {1: (0, 1, 9), 2: (10, 18, 19)}
    frame_size = 3 * 2 * 3
    payload = b"".join(bytes([index]) * frame_size for index in range(1, 7))
    captured_filter = ""
    filter_path: Path | None = None

    class Process:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(payload)
            self.returncode: int | None = 0

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    def popen(command: list[str], **_kwargs: object) -> Process:
        nonlocal captured_filter, filter_path
        option = command.index("-/filter:v:0")
        filter_path = Path(command[option + 1])
        captured_filter = filter_path.read_text(encoding="ascii").strip()
        return Process()

    monkeypatch.setattr(frames_module.subprocess, "Popen", popen)
    progress: list[tuple[int, int]] = []

    groups = list(
        decode_sampled_shots(
            source,
            media,
            shots,
            plan,
            output_size=(2, 3),
            progress=lambda done, total: progress.append((done, total)),
        )
    )

    assert [indexes for _shot_value, indexes, _frames in groups] == [
        (0, 1, 9),
        (10, 18, 19),
    ]
    assert [int(frame[0, 0, 0]) for _, _, frames in groups for frame in frames] == list(range(1, 7))
    assert progress == [(index, 6) for index in range(1, 7)]
    assert captured_filter == (
        "select=between(n\\,0\\,1)+between(n\\,9\\,10)+between(n\\,18\\,19),scale=3:2:flags=area"
    )
    assert captured_filter.index("select=") < captured_filter.index("scale=")
    assert filter_path is not None and not filter_path.exists()


def test_sparse_motion_uses_only_true_adjacent_source_pairs() -> None:
    black = np.zeros((8, 8, 3), dtype=np.uint8)
    white = np.full_like(black, 255)
    frames = [black, black, white, white]
    indexes = [0, 1, 50, 51]

    assert motion_score(frames, indexes) == 0
    assert camera_movement(frames, frame_indexes=indexes) == "static"
    assert motion_score(frames) > 0


def _draft_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    backend: str = CERTIFIED_DEPTH_BACKEND,
) -> tuple[AIStereoPipeline, ShotManifest, list[tuple[int, ...]]]:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    pipeline = AIStereoPipeline.create(
        tmp_path / "project",
        source,
        config=AppConfig(depth=DepthConfig(backend=backend, width=32, height=32)),
    )
    pipeline.layout.normalized_video.write_bytes(b"normalized")
    media = MediaInfo(
        path=str(pipeline.layout.normalized_video),
        width=32,
        height=32,
        frame_rate=24,
        duration_seconds=40 / 24,
        frame_count=40,
    )
    shots = ShotManifest(
        source_path=str(pipeline.layout.normalized_video),
        frame_rate=24,
        frame_count=40,
        shots=[_shot(1, 0, 19, transition="start"), _shot(2, 20, 39)],
    )
    write_json_atomic(pipeline.layout.normalized_media, media)
    write_json_atomic(pipeline.layout.shots, shots)
    monkeypatch.setattr(pipeline, "normalize", lambda: media)
    monkeypatch.setattr(pipeline, "detect_shots", lambda: shots)
    observed_indexes: list[tuple[int, ...]] = []

    def sampled_groups(_source, _media, _shots, plan, **_kwargs):
        report = _kwargs.get("progress")
        total = sum(len(plan[shot.shot_id]) for shot in shots.shots)
        decoded = 0
        for shot in shots.shots:
            indexes = tuple(plan[shot.shot_id])
            observed_indexes.append(indexes)
            frames = [np.full((32, 32, 3), (index * 7) % 255, dtype=np.uint8) for index in indexes]
            for _frame in frames:
                decoded += 1
                if report is not None:
                    report(decoded, total)
            yield shot, indexes, frames

    monkeypatch.setattr(pipeline_module, "decode_sampled_shots", sampled_groups)
    return pipeline, shots, observed_indexes


def test_draft_analysis_falls_back_without_writing_production_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline, shots, observed_indexes = _draft_pipeline(tmp_path, monkeypatch)
    attempts = 0

    class MissingModelBackend:
        name = CERTIFIED_DEPTH_BACKEND

        def estimate(self, *_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise DependencyUnavailableError("model unavailable")

    monkeypatch.setattr(
        pipeline_module,
        "create_depth_backend",
        lambda *_args, **_kwargs: MissingModelBackend(),
    )

    result = pipeline.analyze_draft()

    assert result.analysis_tier == "sampled"
    assert result.profile == "representative_frames"
    assert result.coverage.shot_ids == [1, 2]
    assert result.coverage.sampled_frames == 24
    assert result.coverage.total_frames == 40
    assert [item.model_dump() for item in result.coverage.per_shot] == [
        {"shot_id": 1, "sampled_frames": 12, "total_frames": 20},
        {"shot_id": 2, "sampled_frames": 12, "total_frames": 20},
    ]
    assert [item.shot_id for item in result.features.shots] == [1, 2]
    assert [item.shot_id for item in result.script.shots] == [1, 2]
    # Image-analysis fallback depth is usable enough to direct a draft against,
    # but it is capped below the certified tier and writes no depth artifact.
    assert all(
        0 <= item.depth_reliability <= FALLBACK_DEPTH_RELIABILITY
        for item in result.features.shots
    )
    assert attempts == 1  # dependency failure is reused for later shots
    assert len(observed_indexes) == 2
    assert max(observed_indexes[0]) < min(observed_indexes[1])
    assert pipeline.layout.draft_analysis.is_file()
    assert pipeline.layout.stereo_script.is_file()
    assert not pipeline.layout.depth_metadata.exists()
    assert not list(pipeline.layout.depth_dir.glob("shot_*.npz"))
    with pytest.raises(ArtifactError):
        pipeline.validate_depth_artifact(shots=shots, for_release=True)


def test_draft_progress_is_live_monotonic_and_finishes_after_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline, _shots, _observed_indexes = _draft_pipeline(
        tmp_path, monkeypatch, backend="synthetic"
    )
    events: list[tuple[int, int, str | None]] = []
    pipeline.progress = lambda stage, done, total, message: (
        events.append((done, total, message)) if stage == "analyze_draft" else None
    )

    result = pipeline.analyze_draft()

    assert result.coverage.sampled_frames == 24
    assert events[0] == (0, 73, "Preparing 24 representative frames")
    assert events[-1] == (72, 73, "Finalizing the validated Director draft")
    assert [done for done, _total, _message in events] == sorted(
        done for done, _total, _message in events
    )
    assert all(total == 73 for _done, total, _message in events)
    assert any(
        message and message.startswith("Reading representative frames")
        for _done, _total, message in events
    )
    assert any(message == "Finalizing features for shot 1" for _done, _total, message in events)


def test_draft_depth_disables_sparse_temporal_carry_and_resumes_manual_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline, _shots, observed_indexes = _draft_pipeline(tmp_path, monkeypatch, backend="synthetic")
    temporal_alpha: list[float] = []
    calls = 0

    class RecordingBackend:
        name = "synthetic"

        def estimate(self, frames, *, config, **_kwargs):
            nonlocal calls
            calls += 1
            temporal_alpha.append(config.temporal_alpha)
            grid = np.linspace(0, 1, 32 * 32, dtype=np.float32).reshape(32, 32)
            return np.stack([grid for _ in frames])

    monkeypatch.setattr(
        pipeline_module,
        "create_depth_backend",
        lambda *_args, **_kwargs: RecordingBackend(),
    )
    first = pipeline.analyze_draft()
    assert temporal_alpha == [0.0, 0.0]
    assert calls == 2
    assert len(observed_indexes) == 2

    edited = read_model(pipeline.layout.stereo_script, StereoScript)
    edited.shots[0].manual_override = True
    edited.shots[0].parameters.depth_strength = 0.37
    write_json_atomic(pipeline.layout.stereo_script, edited)

    resumed = pipeline.analyze_draft()
    assert calls == 2
    assert len(observed_indexes) == 2
    assert resumed.script.shots[0].manual_override is True
    assert resumed.script.shots[0].parameters.depth_strength == pytest.approx(0.37)
    assert resumed.revision == stereo_script_revision(edited)
    assert resumed.revision != first.revision
    summary = pipeline.summary()
    assert summary.draft_analysis is not None
    assert summary.draft_analysis.revision == resumed.revision


def test_draft_analysis_profile_and_worker_contract_are_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline, _shots, _indexes = _draft_pipeline(tmp_path, monkeypatch, backend="synthetic")
    with pytest.raises(ValidationError, match="Unknown draft analysis profile"):
        pipeline.analyze_draft(profile="codec_keyframes")

    valid = WorkerRequest(
        id="draft-test",
        method="analyze_draft",
        params={
            "project_dir": str(tmp_path),
            "profile": "representative_frames",
            "allow_fallback": True,
        },
    )
    _validate_parameter_contract(valid)
    invalid = valid.model_copy(update={"params": {**valid.params, "profile": "codec_keyframes"}})
    with pytest.raises(ValidationError, match="unsupported draft analysis profile"):
        _validate_parameter_contract(invalid)


def test_worker_forwards_draft_profile_and_returns_typed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[tuple[str, bool]] = []
    snapshot = DraftAnalysisSnapshot(
        features=FeatureManifest(shots=[]),
        script=StereoScript(video_width=32, shots=[]),
        revision="0" * 64,
        coverage=DraftCoverage(shot_ids=[], sampled_frames=0, total_frames=0, per_shot=[]),
    )

    class PipelineProbe:
        def analyze_draft(self, *, profile: str, allow_fallback: bool):
            captured.append((profile, allow_fallback))
            return snapshot

    worker = JSONLWorker()
    monkeypatch.setattr(worker, "_pipeline", lambda *_args, **_kwargs: PipelineProbe())
    try:
        result = worker.dispatch(
            WorkerRequest(
                id="draft-test",
                method="analyze_draft",
                params={"project_dir": str(tmp_path)},
            ),
            CancellationToken(),
        )
    finally:
        worker._executor.shutdown(wait=True)

    assert result == snapshot
    assert captured == [("representative_frames", True)]
    assert set(snapshot.model_dump()) == {
        "analysis_tier",
        "profile",
        "features",
        "script",
        "revision",
        "coverage",
    }


def test_source_probe_can_skip_decoded_frame_count() -> None:
    from aistereo.media.probe import build_ffprobe_command

    fast = build_ffprobe_command("source.mp4", count_frames=False)
    exact = build_ffprobe_command("normalized.mp4")
    assert "-count_frames" not in fast
    assert "-count_frames" in exact


def test_pipeline_initial_source_probe_is_header_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    pipeline = AIStereoPipeline.create(tmp_path / "project", source)
    observed: list[bool] = []

    def fake_inspect(path, *, ffprobe_path, count_frames=True):
        del ffprobe_path
        observed.append(count_frames)
        return MediaInfo(
            path=str(path),
            width=32,
            height=24,
            frame_rate=24,
            duration_seconds=1,
            frame_count=24,
        )

    monkeypatch.setattr(pipeline_module, "inspect_media", fake_inspect)
    assert pipeline.inspect().frame_count == 24
    assert observed == [False]
