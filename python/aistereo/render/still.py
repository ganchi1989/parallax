"""Decode one frame and write one still, for previews that must feel instant.

Rendering a whole shot to MP4 costs the length of the shot times the per-frame
render. A director adjusting depth needs one frame at the playhead, now, so this
path decodes exactly one frame, renders it, and writes a PNG.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..artifacts import prepare_output_path, replace_atomic
from ..config import MediaConfig
from ..errors import ExternalToolError, ValidationError
from ..models import (
    MAX_MEDIA_DIMENSION,
    MAX_RENDER_OUTPUT_PIXELS,
    MediaInfo,
    validated_bgr_frame_bytes,
)
from ..security import sanitized_subprocess_env

STILL_TIMEOUT_SECONDS = 60.0


def build_still_decode_command(
    source: str | Path,
    *,
    timestamp_seconds: float,
    ffmpeg_path: str = "ffmpeg",
) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        # Seeking before -i lets FFmpeg jump to the nearest keyframe and decode
        # forward, which is accurate on the CFR working copy and far cheaper
        # than decoding the file from the beginning.
        "-ss",
        f"{max(timestamp_seconds, 0.0):.6f}",
        "-i",
        str(Path(source).expanduser().resolve()),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "pipe:1",
    ]


def build_still_encode_command(
    output: str | Path,
    *,
    width: int,
    height: int,
    ffmpeg_path: str = "ffmpeg",
) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s:v",
        f"{width}x{height}",
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-c:v",
        "png",
        str(Path(output).expanduser().resolve()),
    ]


def decode_frame(
    source: str | Path,
    *,
    media: MediaInfo,
    frame_index: int,
    media_config: MediaConfig | None = None,
) -> np.ndarray:
    """Return one BGR frame from a CFR working copy."""

    settings = media_config or MediaConfig()
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise ValidationError("Normalized video does not exist", details={"path": str(source_path)})
    if frame_index < 0:
        raise ValidationError("Frame index must not be negative", details={"frame": frame_index})
    if media.frame_rate <= 0:
        raise ValidationError("Working copy has no usable frame rate")
    try:
        frame_bytes = validated_bgr_frame_bytes(
            media.width, media.height, context="normalized source frame"
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    # Aim at the middle of the frame's display interval so floating-point drift
    # cannot land on the previous frame.
    timestamp = (frame_index + 0.5) / media.frame_rate
    try:
        completed = subprocess.run(
            build_still_decode_command(
                source_path,
                timestamp_seconds=timestamp,
                ffmpeg_path=settings.ffmpeg_path,
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            timeout=STILL_TIMEOUT_SECONDS,
            env=sanitized_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ExternalToolError(
            "FFmpeg timed out decoding a preview frame",
            details={"timeout": STILL_TIMEOUT_SECONDS},
        ) from exc
    except OSError as exc:
        raise ExternalToolError(
            "Could not start FFmpeg",
            details={"executable": settings.ffmpeg_path, "reason": str(exc)},
        ) from exc
    if completed.returncode != 0 or len(completed.stdout) < frame_bytes:
        raise ExternalToolError(
            "FFmpeg did not return a complete preview frame",
            details={
                "exit_code": completed.returncode,
                "bytes": len(completed.stdout),
                "expected": frame_bytes,
                "stderr": completed.stderr[-2048:].decode("utf-8", "replace"),
            },
        )
    return np.frombuffer(completed.stdout[:frame_bytes], dtype=np.uint8).reshape(
        media.height, media.width, 3
    )


def write_still(
    frame: np.ndarray,
    output: str | Path,
    *,
    media_config: MediaConfig | None = None,
) -> Path:
    """Write a BGR frame to a PNG, replacing the destination atomically."""

    settings = media_config or MediaConfig()
    image = np.ascontiguousarray(frame)
    if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
        raise ValidationError("A still must be a uint8 [height, width, 3] BGR frame")
    height, width = image.shape[:2]
    try:
        validated_bgr_frame_bytes(
            width,
            height,
            max_width=MAX_MEDIA_DIMENSION * 2,
            max_pixels=MAX_RENDER_OUTPUT_PIXELS,
            context="rendered still",
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    destination = prepare_output_path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.partial{destination.suffix}")
    try:
        completed = subprocess.run(
            build_still_encode_command(
                temporary,
                width=width,
                height=height,
                ffmpeg_path=settings.ffmpeg_path,
            ),
            input=image.tobytes(),
            capture_output=True,
            shell=False,
            timeout=STILL_TIMEOUT_SECONDS,
            env=sanitized_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        temporary.unlink(missing_ok=True)
        raise ExternalToolError(
            "FFmpeg timed out writing a preview still",
            details={"timeout": STILL_TIMEOUT_SECONDS},
        ) from exc
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ExternalToolError(
            "Could not start FFmpeg",
            details={"executable": settings.ffmpeg_path, "reason": str(exc)},
        ) from exc
    if completed.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise ExternalToolError(
            "FFmpeg could not write the preview still",
            details={
                "exit_code": completed.returncode,
                "stderr": completed.stderr[-2048:].decode("utf-8", "replace"),
            },
        )
    replace_atomic(temporary, destination)
    return destination
