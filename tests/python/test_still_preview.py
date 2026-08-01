"""Contracts for the single-frame preview decode and still writer."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError as PydanticValidationError

import aistereo.pipeline as pipeline_module
import aistereo.render.still as still_module
from aistereo.artifacts import ProjectLayout, write_json_atomic
from aistereo.config import MediaConfig, RenderConfig
from aistereo.depth import save_depth_shot
from aistereo.errors import ExternalToolError, ValidationError
from aistereo.models import (
    DepthManifest,
    DepthShotMetadata,
    MediaInfo,
    Shot,
    ShotManifest,
    StereoParameters,
    StereoPreset,
    StereoScript,
    StereoShot,
    WorkerRequest,
)
from aistereo.pipeline import AIStereoPipeline
from aistereo.render.video import build_raw_decode_command, scaled_preview_size
from aistereo.render.still import (
    build_still_decode_command,
    build_still_encode_command,
    decode_frame,
    write_still,
)
from aistereo.state import CancellationToken
from aistereo.worker import (
    _METHOD_PARAM_CONTRACTS,
    JSONLWorker,
    _validate_parameter_contract,
)

MEDIA = MediaInfo(
    path="working.mp4",
    width=4,
    height=3,
    frame_rate=24.0,
    duration_seconds=10.0,
    frame_count=240,
    video_codec="h264",
    audio_streams=[],
)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "working.mp4"
    source.write_bytes(b"working copy")
    return source


def test_decode_command_seeks_before_input_and_takes_one_frame() -> None:
    command = build_still_decode_command("clip.mp4", timestamp_seconds=1.5)
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-ss") + 1] == "1.500000"
    assert command[command.index("-frames:v") + 1] == "1"
    assert command[command.index("-pix_fmt") + 1] == "bgr24"


def test_encode_command_writes_a_png_at_the_given_size() -> None:
    command = build_still_encode_command("out.png", width=64, height=36)
    assert command[command.index("-s:v") + 1] == "64x36"
    assert command[command.index("-c:v") + 1] == "png"
    assert command[-1].endswith("out.png")


def test_decode_frame_targets_the_middle_of_the_frame_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []
    payload = bytes(range(MEDIA.width * MEDIA.height * 3))

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        seen.append(command)
        return SimpleNamespace(returncode=0, stdout=payload, stderr=b"")

    monkeypatch.setattr(still_module.subprocess, "run", fake_run)
    frame = decode_frame(_source(tmp_path), media=MEDIA, frame_index=48)

    assert frame.shape == (MEDIA.height, MEDIA.width, 3)
    assert frame.dtype == np.uint8
    # Frame 48 at 24 fps spans 2.000-2.042s, so the request lands mid-interval
    # and cannot round back onto frame 47.
    assert seen[0][seen[0].index("-ss") + 1] == "2.020833"


def test_decode_frame_rejects_a_short_or_failed_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        still_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b"\x00", stderr=b""),
    )
    with pytest.raises(ExternalToolError):
        decode_frame(_source(tmp_path), media=MEDIA, frame_index=0)

    monkeypatch.setattr(
        still_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom"),
    )
    with pytest.raises(ExternalToolError):
        decode_frame(_source(tmp_path), media=MEDIA, frame_index=0)


def test_decode_frame_validates_its_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        decode_frame(tmp_path / "missing.mp4", media=MEDIA, frame_index=0)
    with pytest.raises(ValidationError):
        decode_frame(_source(tmp_path), media=MEDIA, frame_index=-1)


def test_decode_frame_surfaces_a_stalled_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1.0)

    monkeypatch.setattr(still_module.subprocess, "run", timeout)
    with pytest.raises(ExternalToolError):
        decode_frame(_source(tmp_path), media=MEDIA, frame_index=0)


def test_write_still_publishes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[bytes] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        written.append(kwargs["input"])  # type: ignore[index]
        Path(command[-1]).write_bytes(b"\x89PNG\r\n\x1a\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(still_module.subprocess, "run", fake_run)
    image = np.arange(4 * 3 * 3, dtype=np.uint8).reshape(3, 4, 3)
    destination = write_still(image, tmp_path / "previews" / "shot.png")

    assert destination.is_file()
    assert written[0] == image.tobytes()
    assert not list(destination.parent.glob(".*partial*"))


def test_write_still_leaves_no_partial_file_when_ffmpeg_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        still_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=3, stdout=b"", stderr=b"no"),
    )
    target = tmp_path / "shot.png"
    with pytest.raises(ExternalToolError):
        write_still(np.zeros((3, 4, 3), dtype=np.uint8), target)
    assert not target.exists()
    assert not list(tmp_path.glob(".*partial*"))


def test_write_still_rejects_a_frame_that_is_not_uint8_bgr(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        write_still(np.zeros((3, 4), dtype=np.uint8), tmp_path / "flat.png")
    with pytest.raises(ValidationError):
        write_still(
            np.zeros((3, 4, 3), dtype=np.float32),
            tmp_path / "float.png",
            media_config=MediaConfig(),
        )


def _preview_pipeline(tmp_path: Path) -> tuple[AIStereoPipeline, MediaInfo, ShotManifest]:
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    layout.normalized_video.write_bytes(b"normalized-fixture")
    media = MediaInfo(
        path=str(layout.normalized_video),
        width=16,
        height=8,
        frame_rate=24,
        duration_seconds=10 / 24,
        frame_count=10,
    )
    shots = ShotManifest(
        source_path=str(layout.normalized_video),
        frame_rate=24,
        frame_count=10,
        shots=[
            Shot(
                shot_id=1,
                start_frame=0,
                end_frame=3,
                start_time=0,
                end_time=4 / 24,
                transition="start",
            ),
            Shot(
                shot_id=2,
                start_frame=4,
                end_frame=9,
                start_time=4 / 24,
                end_time=10 / 24,
                transition="cut",
            ),
        ],
    )
    write_json_atomic(layout.normalized_media, media)
    write_json_atomic(layout.shots, shots)
    return AIStereoPipeline(layout.root), media, shots


def _stub_render_inputs(
    pipeline: AIStereoPipeline,
    monkeypatch: pytest.MonkeyPatch,
    media: MediaInfo,
    shots: ShotManifest,
) -> Path:
    """Give render_frame validated inputs without running the whole pipeline."""

    depth_path = pipeline.layout.depth_dir / "shot_0002.npz"
    planes = np.stack(
        [np.full((8, 16), value / 10.0, dtype=np.float32) for value in range(1, 7)]
    )
    save_depth_shot(depth_path, planes)
    depth = DepthManifest(
        backend="monocular-cues",
        shots=[
            DepthShotMetadata(
                shot_id=2,
                path=str(depth_path.relative_to(pipeline.layout.root)),
                frame_count=6,
                width=16,
                height=8,
                raw_min=0.0,
                raw_max=1.0,
                invalid_fraction=0.0,
                reliability=0.62,
                backend="monocular-cues",
                fallback_used=True,
            )
        ],
    )
    parameters = StereoParameters(
        depth_strength=0.9,
        convergence_depth_percentile=0.5,
        max_background_disparity_norm=0.010,
        max_popout_disparity_norm=0.004,
        temporal_smoothing=0.9,
        transition_frames=0,
        edge_protection=False,
    )
    script = StereoScript(
        video_width=16,
        shots=[
            StereoShot(
                shot_id=item.shot_id,
                preset=StereoPreset.NEUTRAL,
                confidence=1.0,
                parameters=parameters,
            )
            for item in shots.shots
        ],
    )
    output = pipeline.layout.previews_dir / "shot_0002_f000002.png"
    monkeypatch.setattr(
        pipeline,
        "_prepare_render_inputs",
        lambda *_args, **_kwargs: (output, media, shots, depth, script),
    )
    return output


def test_render_frame_decodes_the_playhead_frame_of_the_requested_shot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline, media, shots = _preview_pipeline(tmp_path)
    output = _stub_render_inputs(pipeline, monkeypatch, media, shots)
    requested: dict[str, object] = {}
    stills: list[np.ndarray] = []
    frame = np.full((8, 16, 3), 200, dtype=np.uint8)

    def fake_decode(_source: object, *, media: MediaInfo, frame_index: int, **_kw: object):
        requested["frame_index"] = frame_index
        return frame

    monkeypatch.setattr(pipeline_module, "decode_frame", fake_decode)
    monkeypatch.setattr(
        pipeline_module,
        "write_still",
        lambda image, destination, **_kw: (stills.append(image), Path(destination))[1],
    )

    written = pipeline.render_frame(output, shot_id=2, frame_offset=2)

    # Shot 2 starts at frame 4, so offset 2 is absolute frame 6.
    assert requested["frame_index"] == 6
    assert written == output
    assert stills[0].shape == frame.shape
    assert not np.array_equal(stills[0], frame)


def test_render_frame_clamps_an_offset_past_the_end_of_the_shot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline, media, shots = _preview_pipeline(tmp_path)
    output = _stub_render_inputs(pipeline, monkeypatch, media, shots)
    requested: dict[str, object] = {}

    monkeypatch.setattr(
        pipeline_module,
        "decode_frame",
        lambda _source, *, media, frame_index, **_kw: (
            requested.update(frame_index=frame_index),
            np.zeros((8, 16, 3), dtype=np.uint8),
        )[1],
    )
    monkeypatch.setattr(pipeline_module, "write_still", lambda _i, d, **_kw: Path(d))

    pipeline.render_frame(output, shot_id=2, frame_offset=999)

    # Shot 2 holds frames 4-9, so the last renderable frame is 9, never beyond.
    assert requested["frame_index"] == 9


def test_render_frame_rejects_a_shot_outside_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline, media, shots = _preview_pipeline(tmp_path)
    output = _stub_render_inputs(pipeline, monkeypatch, media, shots)
    with pytest.raises(ValidationError):
        pipeline.render_frame(output, shot_id=7)


def test_render_frame_keeps_one_still_per_shot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline, media, shots = _preview_pipeline(tmp_path)
    output = _stub_render_inputs(pipeline, monkeypatch, media, shots)
    stale = pipeline.layout.previews_dir / "shot_0002_f000001.png"
    other_shot = pipeline.layout.previews_dir / "shot_0001_f000000.png"
    for path in (stale, other_shot):
        path.write_bytes(b"old")

    monkeypatch.setattr(
        pipeline_module,
        "decode_frame",
        lambda *_a, **_k: np.zeros((8, 16, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        pipeline_module,
        "write_still",
        lambda _i, destination, **_kw: (Path(destination).write_bytes(b"new"), Path(destination))[1],
    )

    pipeline.render_frame(output, shot_id=2, frame_offset=2)

    assert output.is_file()
    assert not stale.exists()
    assert other_shot.is_file()


def test_worker_render_preview_frame_forwards_the_requested_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class PipelineProbe:
        layout = SimpleNamespace(previews_dir=tmp_path / "previews")

        def render_frame(
            self,
            output: str,
            *,
            shot_id: int,
            frame_offset: int = 0,
            output_mode: str = "anaglyph",
            allow_fallback: bool = False,
        ) -> Path:
            captured.update(
                output=output,
                shot_id=shot_id,
                frame_offset=frame_offset,
                output_mode=output_mode,
                allow_fallback=allow_fallback,
            )
            return Path(output)

    worker = JSONLWorker()
    probe = PipelineProbe()
    monkeypatch.setattr(worker, "_pipeline", lambda *_args, **_kwargs: probe)
    monkeypatch.setattr(
        JSONLWorker, "_depth_manifest", staticmethod(lambda _p: SimpleNamespace(backend="cached"))
    )
    monkeypatch.setattr(
        JSONLWorker, "_depth_status", staticmethod(lambda _p: {"production_ready": True})
    )
    request = WorkerRequest(
        id="preview-frame-test",
        method="render_preview_frame",
        params={"project_dir": str(tmp_path), "shot_id": 3, "frame_offset": 17},
    )
    _validate_parameter_contract(request)
    try:
        result = worker.dispatch(request, CancellationToken())
    finally:
        worker._executor.shutdown(wait=True)

    assert captured["shot_id"] == 3
    assert captured["frame_offset"] == 17
    assert captured["allow_fallback"] is True
    assert str(captured["output"]).endswith("shot_0003_f000017.png")
    assert result["still"] is True
    assert result["preview_path"].endswith("shot_0003_f000017.png")


def test_worker_render_preview_frame_rejects_a_negative_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The desktop bridge screens this too; the worker must not rely on it."""

    worker = JSONLWorker()
    monkeypatch.setattr(worker, "_pipeline", lambda *_args, **_kwargs: SimpleNamespace())
    try:
        with pytest.raises(ValidationError):
            worker.dispatch(
                WorkerRequest(
                    id="preview-frame-test",
                    method="render_preview_frame",
                    params={
                        "project_dir": str(tmp_path),
                        "shot_id": 1,
                        "frame_offset": -1,
                    },
                ),
                CancellationToken(),
            )
    finally:
        worker._executor.shutdown(wait=True)


def test_render_preview_frame_declares_the_frame_parameter() -> None:
    allowed, required = _METHOD_PARAM_CONTRACTS["render_preview_frame"]
    assert "frame_offset" in allowed
    assert {"project_dir", "shot_id"} == required
    assert {"anaglyph_mode", "swap_eyes"} <= allowed


def test_preview_scaling_preserves_aspect_and_never_upscales() -> None:
    assert scaled_preview_size(1280, 720, 640) == (640, 360)
    assert scaled_preview_size(1920, 800, 640) == (640, 266)  # even height
    assert scaled_preview_size(480, 270, 640) == (480, 270)  # already smaller
    assert scaled_preview_size(1280, 720, 0) == (1280, 720)  # disabled


def test_only_a_scaled_render_asks_ffmpeg_to_resize() -> None:
    plain = build_raw_decode_command("clip.mp4")
    assert "-vf" not in plain

    scaled = build_raw_decode_command("clip.mp4", scale=(640, 360))
    assert scaled[scaled.index("-vf") + 1] == "scale=640:360:flags=area"
    assert scaled.index("-vf") > scaled.index("-i")


def test_preview_width_is_bounded_by_configuration() -> None:
    assert RenderConfig().preview_max_width == 640
    with pytest.raises(PydanticValidationError):
        RenderConfig(preview_max_width=16)
    with pytest.raises(PydanticValidationError):
        RenderConfig(preview_max_width=99_999)
