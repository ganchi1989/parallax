"""Deterministic constant-frame-rate normalization."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import IO

from ..artifacts import prepare_output_path, replace_atomic
from ..config import MediaConfig
from ..errors import ExternalToolError, PipelineCancelled, ValidationError
from ..models import (
    MAX_MEDIA_PIXELS,
    MAX_NORMALIZED_WIDTH,
    MediaInfo,
    ffmpeg_faststart_args,
    validated_bgr_frame_bytes,
)
from ..security import sanitized_subprocess_env

CancelCheck = Callable[[], bool]
PROCESS_LOG_TAIL_BYTES = 8 * 1024


def read_process_log_tail(log: IO[bytes]) -> str:
    """Decode only the bounded tail of a seekable file-backed process log."""

    try:
        log.seek(0, os.SEEK_END)
        length = log.tell()
        log.seek(max(0, length - PROCESS_LOG_TAIL_BYTES), os.SEEK_SET)
        payload = log.read(PROCESS_LOG_TAIL_BYTES)
    except (OSError, ValueError):
        return ""
    return payload.decode("utf-8", "replace")


def _temporary_media_path(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=f".partial{destination.suffix}",
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(name)


def build_normalize_video_command(
    source: str | Path,
    output: str | Path,
    config: MediaConfig,
    media_info: MediaInfo,
) -> list[str]:
    output_width, output_height = _normalized_dimensions(media_info, config)
    # The explicit even width is derived from the post-autorotation display
    # aspect before FFmpeg starts, so a hostile panoramic header cannot request
    # an unbounded scale allocation. FPS is applied after autorotation so every
    # downstream stage sees exactly one CFR timeline.
    video_filter = (
        f"scale={output_width}:{output_height}:flags=lanczos,"
        f"fps={config.target_fps:.8g},format=yuv420p"
    )
    destination = Path(output).expanduser().resolve()
    command = [
        config.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(Path(source).expanduser().resolve()),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        video_filter,
        "-vsync",
        "cfr",
        "-c:v",
        "libx264",
        "-preset",
        config.working_preset,
        "-crf",
        str(config.crf),
        "-color_primaries",
        config.color_primaries,
        "-color_trc",
        config.color_primaries,
        "-colorspace",
        config.color_primaries,
    ]
    command.extend(ffmpeg_faststart_args(destination))
    command.append(str(destination))
    return command


def _normalized_dimensions(media_info: MediaInfo, config: MediaConfig) -> tuple[int, int]:
    display_width, display_height = media_info.width, media_info.height
    if media_info.rotation_degrees in {90, 270}:
        display_width, display_height = display_height, display_width
    # Conservative ceiling plus even rounding bounds FFmpeg's scale result.
    output_width = (display_width * config.target_height + display_height - 1) // display_height
    if output_width % 2:
        output_width += 1
    if output_width > MAX_NORMALIZED_WIDTH:
        raise ValidationError(
            "Normalized video width exceeds the supported aspect-ratio limit",
            details={
                "width": output_width,
                "max_width": MAX_NORMALIZED_WIDTH,
                "target_height": config.target_height,
            },
        )
    try:
        validated_bgr_frame_bytes(
            output_width,
            config.target_height,
            max_width=MAX_NORMALIZED_WIDTH,
            max_pixels=MAX_MEDIA_PIXELS,
            context="normalized video frame",
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return output_width, config.target_height


def build_extract_audio_command(
    source: str | Path, output: str | Path, config: MediaConfig
) -> list[str]:
    return [
        config.ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(Path(source).expanduser().resolve()),
        "-map",
        "0:a",
        "-vn",
        "-c:a",
        "copy",
        str(Path(output).expanduser().resolve()),
    ]


def _run_process(command: list[str], cancel: CancelCheck | None = None) -> None:
    # A file-backed stderr sink is continuously writable and cannot fill a pipe
    # buffer while a long encode is running.
    with tempfile.TemporaryFile() as error_log:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=error_log,
                shell=False,
                env=sanitized_subprocess_env(),
            )
        except FileNotFoundError as exc:
            raise ExternalToolError(
                "FFmpeg was not found", details={"executable": command[0]}
            ) from exc
        while process.poll() is None:
            if cancel and cancel():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise PipelineCancelled("Normalization was cancelled")
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=0.1)
        if process.returncode != 0:
            raise ExternalToolError(
                "FFmpeg normalization failed",
                details={
                    "exit_code": process.returncode,
                    "stderr": read_process_log_tail(error_log),
                },
            )


def normalize_media(
    source: str | Path,
    video_output: str | Path,
    audio_output: str | Path,
    media_info: MediaInfo,
    config: MediaConfig,
    *,
    cancel: CancelCheck | None = None,
) -> tuple[Path, Path | None]:
    input_path = Path(source).expanduser().resolve()
    video_path = prepare_output_path(video_output)
    audio_path = prepare_output_path(audio_output)
    if not input_path.is_file():
        raise ValidationError("Input video does not exist", details={"path": str(input_path)})
    video_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    video_temporary = _temporary_media_path(video_path)
    audio_temporary = _temporary_media_path(audio_path) if media_info.audio_streams else None
    try:
        _run_process(
            build_normalize_video_command(input_path, video_temporary, config, media_info), cancel
        )
        replace_atomic(video_temporary, video_path)
        produced_audio: Path | None = None
        if media_info.audio_streams:
            assert audio_temporary is not None
            _run_process(build_extract_audio_command(input_path, audio_temporary, config), cancel)
            replace_atomic(audio_temporary, audio_path)
            produced_audio = audio_path
        else:
            # A source can be replaced in-place between project sessions. Never
            # let an audio stream from the prior source survive a silent re-run.
            audio_path.unlink(missing_ok=True)
        return video_path, produced_audio
    finally:
        video_temporary.unlink(missing_ok=True)
        if audio_temporary is not None:
            audio_temporary.unlink(missing_ok=True)
