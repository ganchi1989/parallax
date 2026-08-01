"""Frames render in parallel but must be written in decode order."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import numpy as np
import pytest

import aistereo.render.video as video_module
from aistereo.artifacts import ProjectLayout
from aistereo.config import ComfortConfig, MediaConfig, RenderConfig
from aistereo.depth.base import save_depth_shot
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
from aistereo.render.video import render_video, render_worker_count

FRAMES = 12
WIDTH = 32
HEIGHT = 24


class _RetainedBuffer(io.BytesIO):
    """Keeps its contents readable after the renderer closes the pipe."""

    def close(self) -> None:  # noqa: D102 - deliberately inert
        pass


class _FakeProcess:
    def __init__(self, stdout: io.BytesIO | None = None, stdin: io.BytesIO | None = None):
        self.stdout = stdout
        self.stdin = stdin
        # A finished process reports both a poll result and a return code.
        self.returncode: int | None = 0

    def poll(self) -> int | None:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = 0


def _project(tmp_path: Path):
    layout = ProjectLayout.at(tmp_path / "project").ensure()
    layout.normalized_video.write_bytes(b"normalized")
    media = MediaInfo(
        path=str(layout.normalized_video),
        width=WIDTH,
        height=HEIGHT,
        frame_rate=24,
        duration_seconds=FRAMES / 24,
        frame_count=FRAMES,
    )
    shots = ShotManifest(
        source_path=str(layout.normalized_video),
        frame_rate=24,
        frame_count=FRAMES,
        shots=[
            Shot(
                shot_id=1,
                start_frame=0,
                end_frame=FRAMES - 1,
                start_time=0,
                end_time=FRAMES / 24,
                transition="start",
            )
        ],
    )
    rng = np.random.default_rng(4)
    planes = rng.random((FRAMES, HEIGHT, WIDTH)).astype(np.float32)
    depth_path = save_depth_shot(layout.depth_dir / "shot_0001.npz", planes)
    depth = DepthManifest(
        backend="monocular-cues",
        shots=[
            DepthShotMetadata(
                shot_id=1,
                path=str(depth_path.relative_to(layout.root)),
                frame_count=FRAMES,
                width=WIDTH,
                height=HEIGHT,
                raw_min=0.0,
                raw_max=1.0,
                invalid_fraction=0.0,
                reliability=1.0,
                backend="monocular-cues",
            )
        ],
    )
    script = StereoScript(
        video_width=WIDTH,
        shots=[
            StereoShot(
                shot_id=1,
                preset=StereoPreset.VISTA_DEEP,
                confidence=1.0,
                parameters=StereoParameters(
                    depth_strength=0.9,
                    convergence_depth_percentile=0.55,
                    max_background_disparity_norm=0.010,
                    max_popout_disparity_norm=0.004,
                    temporal_smoothing=0.9,
                    transition_frames=0,
                    edge_protection=False,
                ),
            )
        ],
    )
    return layout, media, shots, depth, script


def _render_with_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workers: int) -> bytes:
    layout, media, shots, depth, script = _project(tmp_path)
    # Distinct, non-uniform source frames so any reordering is detectable.
    rng = np.random.default_rng(7)
    payload = rng.integers(0, 256, (FRAMES, HEIGHT, WIDTH, 3), dtype=np.uint16).astype(np.uint8)
    written = _RetainedBuffer()
    decoder = _FakeProcess(stdout=io.BytesIO(payload.tobytes()))
    encoder = _FakeProcess(stdin=written)

    def popen(_command: list[str], **kwargs: object) -> _FakeProcess:
        return decoder if kwargs.get("stdout") == subprocess.PIPE else encoder

    monkeypatch.setattr(video_module.subprocess, "Popen", popen)
    monkeypatch.setattr(video_module, "render_worker_count", lambda: workers)
    monkeypatch.setattr(video_module, "replace_atomic", lambda _src, dst: dst)

    _path, count, accumulator = render_video(
        layout.normalized_video,
        tmp_path / "out.mp4",
        media=media,
        manifest=shots,
        depth_manifest=depth,
        script=script,
        project_root=layout.root,
        media_config=MediaConfig(),
        render_config=RenderConfig(),
        comfort_config=ComfortConfig(),
        output_mode="anaglyph",
    )
    assert count == FRAMES
    del accumulator
    return written.getvalue()


def test_worker_count_never_changes_the_rendered_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    single = _render_with_workers(tmp_path / "a", monkeypatch, 1)
    parallel = _render_with_workers(tmp_path / "b", monkeypatch, 4)
    assert len(single) == FRAMES * HEIGHT * WIDTH * 3
    # Frames render concurrently but are encoded strictly in decode order, so
    # the byte stream must match a single-threaded render exactly.
    assert single == parallel


def test_render_worker_count_is_bounded() -> None:
    workers = render_worker_count()
    assert 1 <= workers <= 6
