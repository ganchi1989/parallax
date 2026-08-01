"""Shot manifest generation with optional PySceneDetect and OpenCV fallback."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..config import ShotDetectionConfig
from ..errors import DependencyUnavailableError, PipelineCancelled, ValidationError
from ..models import Shot, ShotManifest, TransitionType

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


def _boundaries_to_manifest(
    source: Path,
    boundaries: list[int],
    frame_count: int,
    frame_rate: float,
    transition_frames: set[int] | None = None,
) -> ShotManifest:
    if frame_count <= 0:
        return ShotManifest(source_path=str(source), frame_rate=frame_rate, frame_count=0, shots=[])
    cleaned = sorted({frame for frame in boundaries if 0 < frame < frame_count})
    starts = [0, *cleaned]
    ends = [frame - 1 for frame in cleaned] + [frame_count - 1]
    gradual = transition_frames or set()
    shots = [
        Shot(
            shot_id=index + 1,
            start_frame=start,
            end_frame=end,
            start_time=start / frame_rate,
            end_time=(end + 1) / frame_rate,
            transition=(
                TransitionType.START
                if index == 0
                else TransitionType.FADE
                if start in gradual
                else TransitionType.CUT
            ),
        )
        for index, (start, end) in enumerate(zip(starts, ends, strict=True))
    ]
    return ShotManifest(
        source_path=str(source), frame_rate=frame_rate, frame_count=frame_count, shots=shots
    )


def _detect_scenedetect(
    source: Path,
    frame_count: int,
    frame_rate: float,
    config: ShotDetectionConfig,
) -> ShotManifest:
    try:
        from scenedetect import (  # type: ignore[import-not-found,import-untyped]
            ContentDetector,
            detect,
        )
    except ImportError as exc:
        raise DependencyUnavailableError("PySceneDetect is not installed") from exc
    scenes = detect(
        str(source),
        ContentDetector(
            threshold=config.content_threshold,
            min_scene_len=config.min_scene_frames,
        ),
        start_in_scene=True,
        show_progress=False,
    )
    boundaries = [int(start.get_frames()) for start, _ in scenes[1:]]
    if scenes:
        detected_end = int(scenes[-1][1].get_frames())
        if detected_end > 0:
            frame_count = detected_end
    return _boundaries_to_manifest(source, boundaries, frame_count, frame_rate)


def _resize_gray(frame: np.ndarray, width: int) -> np.ndarray:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DependencyUnavailableError("OpenCV is required for fallback shot detection") from exc
    height = max(1, round(frame.shape[0] * width / frame.shape[1]))
    resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)


def _detect_opencv(
    source: Path,
    frame_count: int,
    frame_rate: float,
    config: ShotDetectionConfig,
    progress: ProgressCallback | None,
    cancel: CancelCheck | None,
) -> ShotManifest:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DependencyUnavailableError(
            "Shot detection needs PySceneDetect or OpenCV",
            details={"install_extra": "ai-stereo-director[video]"},
        ) from exc
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise ValidationError("OpenCV could not open the normalized video")
    actual_fps = float(capture.get(cv2.CAP_PROP_FPS)) or frame_rate
    reported_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if reported_count > 0:
        frame_count = reported_count
    boundaries: list[int] = []
    previous: np.ndarray | None = None
    last_boundary = 0
    index = 0
    # ContentDetector's threshold roughly corresponds to mean channel change.
    # The fallback uses mean grayscale change and preserves the same 0..255 scale.
    try:
        while True:
            if cancel and cancel():
                raise PipelineCancelled("Shot detection was cancelled")
            ok, frame = capture.read()
            if not ok:
                break
            current = _resize_gray(frame, min(config.downscale_width, frame.shape[1]))
            if previous is not None and index - last_boundary >= config.min_scene_frames:
                score = float(np.mean(np.abs(current.astype(np.float32) - previous)))
                if score >= config.content_threshold:
                    boundaries.append(index)
                    last_boundary = index
            previous = current
            index += 1
            if progress and (index % 10 == 0 or index == frame_count):
                progress(index, max(frame_count, index))
    finally:
        capture.release()
    frame_count = index
    return _boundaries_to_manifest(source, boundaries, frame_count, actual_fps)


def detect_shots(
    video_path: str | Path,
    *,
    frame_count: int,
    frame_rate: float,
    config: ShotDetectionConfig | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> ShotManifest:
    source = Path(video_path).expanduser().resolve()
    if not source.is_file():
        raise ValidationError(
            "Video for shot detection does not exist", details={"path": str(source)}
        )
    settings = config or ShotDetectionConfig()
    if settings.backend in {"auto", "scenedetect"}:
        try:
            return _detect_scenedetect(source, frame_count, frame_rate, settings)
        except DependencyUnavailableError:
            if settings.backend == "scenedetect":
                raise
    return _detect_opencv(source, frame_count, frame_rate, settings, progress, cancel)


__all__ = ["_boundaries_to_manifest", "detect_shots"]
