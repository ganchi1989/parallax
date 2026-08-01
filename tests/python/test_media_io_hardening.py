from __future__ import annotations

import io
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError as PydanticValidationError

import aistereo.media.frames as frames_module
import aistereo.render.video as video_module
from aistereo.config import MediaConfig
from aistereo.depth.base import save_depth_shot
from aistereo.errors import ExternalToolError, PipelineCancelled, ValidationError
from aistereo.media.frames import decode_shots
from aistereo.media.normalize import (
    PROCESS_LOG_TAIL_BYTES,
    build_normalize_video_command,
    read_process_log_tail,
)
from aistereo.media.remux import build_remux_command
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
)
from aistereo.render.video import build_raw_encode_command, render_video


def _media(path: Path, *, width: int = 1920, height: int = 1080) -> MediaInfo:
    return MediaInfo(
        path=str(path),
        width=width,
        height=height,
        frame_rate=24,
        duration_seconds=1 / 24,
        frame_count=1,
    )


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [(".mp4", True), (".mov", True), (".MP4", True), (".mkv", False)],
)
def test_faststart_is_only_used_for_mov_mp4_containers(
    tmp_path: Path, suffix: str, expected: bool
) -> None:
    source = tmp_path / "source.mp4"
    destination = tmp_path / f"output{suffix}"
    media = _media(source)
    commands = [
        build_normalize_video_command(source, destination, MediaConfig(), media),
        build_raw_encode_command(
            destination,
            width=1280,
            height=720,
            frame_rate=24,
            media_config=MediaConfig(),
        ),
        build_remux_command(source, destination),
    ]
    for command in commands:
        assert ("-movflags" in command) is expected
        assert ("+faststart" in command) is expected


def test_media_info_rejects_pixel_count_above_bounded_bgr_frame() -> None:
    with pytest.raises(PydanticValidationError, match="pixel limit"):
        MediaInfo(
            path="oversized.mp4",
            width=8192,
            height=8192,
            frame_rate=24,
            duration_seconds=1,
            frame_count=24,
        )


def test_normalization_rejects_unbounded_panorama_before_ffmpeg(tmp_path: Path) -> None:
    source = tmp_path / "panorama.mp4"
    media = _media(source, width=16_384, height=1024)
    with pytest.raises(ValidationError, match="aspect-ratio limit"):
        build_normalize_video_command(
            source,
            tmp_path / "normalized.mp4",
            MediaConfig(target_height=4320),
            media,
        )


def test_normalization_bounds_post_rotation_display_aspect(tmp_path: Path) -> None:
    source = tmp_path / "portrait-tagged.mp4"
    media = _media(source, width=1080, height=1920)
    media.rotation_degrees = 90
    command = build_normalize_video_command(
        source, tmp_path / "normalized.mp4", MediaConfig(target_height=720), media
    )
    video_filter = command[command.index("-vf") + 1]
    assert video_filter.startswith("scale=1280:720:")


def test_raw_encoder_rejects_oversized_side_by_side_frame(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="pixel limit"):
        build_raw_encode_command(
            tmp_path / "oversized.mp4",
            width=32_768,
            height=4320,
            frame_rate=24,
            media_config=MediaConfig(),
        )


def test_process_log_reader_reads_only_bounded_tail() -> None:
    class GuardedLog(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            assert 0 <= size <= PROCESS_LOG_TAIL_BYTES
            return super().read(size)

    log = GuardedLog(b"discarded-prefix" + b"x" * PROCESS_LOG_TAIL_BYTES + b"tail")
    result = read_process_log_tail(log)
    assert len(result.encode("utf-8")) == PROCESS_LOG_TAIL_BYTES
    assert result.endswith("tail")
    assert "discarded-prefix" not in result


class _BlockingReader:
    def __init__(self, released: threading.Event) -> None:
        self.released = released
        self.closed = False

    def read(self, _size: int = -1) -> bytes:
        self.released.wait(5)
        return b""

    def close(self) -> None:
        self.closed = True
        self.released.set()


class _BlockingWriter:
    def __init__(self, released: threading.Event) -> None:
        self.released = released
        self.closed = False

    def write(self, payload: bytes | memoryview) -> int:
        self.released.wait(5)
        return len(payload)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True
        self.released.set()


class _FakeProcess:
    def __init__(self, *, stdout: object | None = None, stdin: object | None = None) -> None:
        self.stdout = stdout
        self.stdin = stdin
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        for stream in (self.stdout, self.stdin):
            released = getattr(stream, "released", None)
            if released is not None:
                released.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.terminate()

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return self.returncode


def test_blocking_decoder_read_is_cancelled_and_reaped(tmp_path: Path, monkeypatch) -> None:
    released = threading.Event()
    process = _FakeProcess(stdout=_BlockingReader(released))
    monkeypatch.setattr(frames_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    source = tmp_path / "source.mp4"
    manifest = ShotManifest(
        source_path=str(source),
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
    checks = 0

    def cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    started = time.monotonic()
    with pytest.raises(PipelineCancelled):
        next(decode_shots(source, _media(source, width=2, height=2), manifest, cancel=cancel))
    assert time.monotonic() - started < 2
    assert process.terminated is True
    assert process.returncode is not None
    assert process.stdout is not None and process.stdout.closed is True


def test_blocking_encoder_write_is_cancelled_and_both_children_are_reaped(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "normalized.mp4"
    source.write_bytes(b"fixture")
    project = tmp_path / "project"
    depth_path = save_depth_shot(project / "depth" / "shot_0001.npz", np.zeros((1, 2, 2)))
    media = _media(source, width=2, height=2)
    shot = Shot(
        shot_id=1,
        start_frame=0,
        end_frame=0,
        start_time=0,
        end_time=1 / 24,
        transition="start",
    )
    manifest = ShotManifest(source_path=str(source), frame_rate=24, frame_count=1, shots=[shot])
    depth_manifest = DepthManifest(
        backend="cached",
        shots=[
            DepthShotMetadata(
                shot_id=1,
                path=str(depth_path.relative_to(project)),
                frame_count=1,
                width=2,
                height=2,
                raw_min=0,
                raw_max=1,
                invalid_fraction=0,
                reliability=1,
                backend="cached",
            )
        ],
    )
    parameters = StereoParameters(
        depth_strength=0,
        convergence_depth_percentile=0.5,
        max_background_disparity_norm=0,
        max_popout_disparity_norm=0,
        temporal_smoothing=0,
        transition_frames=0,
    )
    script = StereoScript(
        video_width=2,
        shots=[
            StereoShot(
                shot_id=1,
                preset=StereoPreset.NEUTRAL,
                confidence=1,
                parameters=parameters,
            )
        ],
    )
    decoder = _FakeProcess(stdout=io.BytesIO(bytes(range(12))))
    released = threading.Event()
    encoder = _FakeProcess(stdin=_BlockingWriter(released))

    def popen(_command: list[str], **kwargs: object) -> _FakeProcess:
        return decoder if kwargs.get("stdout") == subprocess.PIPE else encoder

    monkeypatch.setattr(video_module.subprocess, "Popen", popen)
    zero_float = np.zeros((2, 2), dtype=np.float32)
    zero_bool = np.zeros((2, 2), dtype=bool)
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    monkeypatch.setattr(
        video_module,
        "render_stereo_frame",
        lambda *_args, **_kwargs: SimpleNamespace(
            left=image,
            right=image,
            anaglyph=image,
            disparity_norm=zero_float,
            holes=zero_bool,
            edge_violations=zero_bool,
        ),
    )
    checks = 0

    def cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    output = tmp_path / "cancelled.mp4"
    started = time.monotonic()
    with pytest.raises(PipelineCancelled):
        render_video(
            source,
            output,
            media=media,
            manifest=manifest,
            depth_manifest=depth_manifest,
            script=script,
            project_root=project,
            output_mode="left",
            cancel=cancel,
        )
    assert time.monotonic() - started < 2
    assert decoder.terminated is True
    assert encoder.terminated is True
    assert decoder.returncode is not None and encoder.returncode is not None
    assert not output.exists()


def test_pipe_watchdog_reports_stall_and_cleanup_can_join_worker() -> None:
    released = threading.Event()
    writer = _BlockingWriter(released)
    process = _FakeProcess(stdin=writer)
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(video_module._write_all, writer, b"frame")
    try:
        with pytest.raises(ExternalToolError, match="stalled"):
            video_module._await_pipe_io(
                future,
                process=process,  # type: ignore[arg-type]
                cancel=None,
                cancellation_message="cancelled",
                operation="test write",
                timeout_seconds=0.15,
            )
    finally:
        video_module._stop_process(process)  # type: ignore[arg-type]
        executor.shutdown(wait=True, cancel_futures=True)
    assert process.returncode is not None
