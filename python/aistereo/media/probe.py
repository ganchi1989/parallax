"""Strict FFprobe inspection without shell invocation."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
from contextlib import suppress
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol

from ..errors import ExternalToolError, ValidationError
from ..models import AudioStream, MediaInfo
from ..security import sanitized_subprocess_env

MAX_FFPROBE_STDOUT_BYTES = 4 * 1024 * 1024
MAX_FFPROBE_STDERR_BYTES = 1024 * 1024
_FFPROBE_ERROR_TAIL_BYTES = 4000
_FFPROBE_POLL_SECONDS = 0.1


class _BinaryCapture(Protocol):
    def fileno(self) -> int: ...

    def seek(self, offset: int, whence: int = 0) -> int: ...

    def read(self, size: int = -1) -> bytes: ...


def build_ffprobe_command(
    input_path: str | Path,
    ffprobe_path: str = "ffprobe",
    *,
    count_frames: bool = True,
) -> list[str]:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
    ]
    if count_frames:
        command.append("-count_frames")
    command.extend(
        [
            "-of",
            "json",
            str(Path(input_path).expanduser().resolve()),
        ]
    )
    return command


def _ratio(value: Any) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    try:
        result = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def parse_ffprobe(data: dict[str, Any], source_path: str | Path) -> MediaInfo:
    streams = data.get("streams")
    if not isinstance(streams, list):
        raise ValidationError("FFprobe response has no stream list")
    videos = [
        item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"
    ]
    if not videos:
        raise ValidationError("Input contains no video stream")
    video = videos[0]
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    average_rate = _ratio(video.get("avg_frame_rate"))
    nominal_rate = _ratio(video.get("r_frame_rate"))
    frame_rate = average_rate or nominal_rate
    raw_format = data.get("format")
    format_data: dict[str, Any] = raw_format if isinstance(raw_format, dict) else {}
    duration = _number(video.get("duration"), _number(format_data.get("duration")))
    frame_count_raw = video.get("nb_read_frames") or video.get("nb_frames")
    try:
        frame_count = int(str(frame_count_raw))
    except (TypeError, ValueError):
        frame_count = max(0, round(duration * frame_rate))
    rotation = 0
    raw_tags = video.get("tags")
    tags: dict[str, Any] = raw_tags if isinstance(raw_tags, dict) else {}
    try:
        rotation = round(_number(tags.get("rotate"), 0.0))
    except (TypeError, ValueError):
        rotation = 0
    side_data = video.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, dict) and "rotation" in item:
                rotation = round(_number(item.get("rotation"), rotation))
                break
    audio: list[AudioStream] = []
    for item in streams:
        if not isinstance(item, dict) or item.get("codec_type") != "audio":
            continue
        raw_audio_tags = item.get("tags")
        audio_tags: dict[str, Any] = raw_audio_tags if isinstance(raw_audio_tags, dict) else {}
        try:
            channels = int(item["channels"]) if item.get("channels") else None
        except (TypeError, ValueError):
            channels = None
        try:
            sample_rate = int(item["sample_rate"]) if item.get("sample_rate") else None
        except (TypeError, ValueError):
            sample_rate = None
        audio.append(
            AudioStream(
                index=int(item.get("index") or 0),
                codec=str(item.get("codec_name") or "unknown"),
                channels=channels,
                sample_rate=sample_rate,
                language=str(audio_tags["language"]) if audio_tags.get("language") else None,
                duration_seconds=(
                    max(0.0, _number(item.get("duration"))) if item.get("duration") else None
                ),
            )
        )
    variable = bool(
        nominal_rate
        and average_rate
        and abs(nominal_rate - average_rate) / max(nominal_rate, average_rate) > 0.001
    )
    return MediaInfo(
        path=str(Path(source_path).expanduser().resolve()),
        width=width,
        height=height,
        frame_rate=frame_rate,
        duration_seconds=max(0.0, duration),
        frame_count=max(0, frame_count),
        video_codec=str(video.get("codec_name") or "unknown"),
        pixel_format=str(video.get("pix_fmt") or "unknown"),
        rotation_degrees=rotation,
        variable_frame_rate=variable,
        audio_streams=audio,
    )


def _file_size(handle: _BinaryCapture) -> int:
    return os.fstat(handle.fileno()).st_size


def _read_file_tail(handle: _BinaryCapture, *, max_bytes: int) -> str:
    size = _file_size(handle)
    handle.seek(max(0, size - max_bytes))
    return handle.read(max_bytes).decode("utf-8", "replace")


def _capture_limit_error(stdout_size: int, stderr_size: int) -> ExternalToolError | None:
    if stdout_size > MAX_FFPROBE_STDOUT_BYTES:
        return ExternalToolError(
            "FFprobe response exceeded the bounded size limit",
            details={"bytes": stdout_size, "max_bytes": MAX_FFPROBE_STDOUT_BYTES},
        )
    if stderr_size > MAX_FFPROBE_STDERR_BYTES:
        return ExternalToolError(
            "FFprobe diagnostic output exceeded the bounded size limit",
            details={"bytes": stderr_size, "max_bytes": MAX_FFPROBE_STDERR_BYTES},
        )
    return None


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    with suppress(OSError):
        process.kill()
    with suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=5)


def inspect_media(
    input_path: str | Path,
    *,
    ffprobe_path: str = "ffprobe",
    timeout_seconds: float = 60.0,
    count_frames: bool = True,
) -> MediaInfo:
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise ValidationError("Input video does not exist", details={"path": str(source)})
    command = build_ffprobe_command(source, ffprobe_path, count_frames=count_frames)
    with tempfile.TemporaryFile() as stdout_log, tempfile.TemporaryFile() as stderr_log:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout_log,
                stderr=stderr_log,
                shell=False,
                env=sanitized_subprocess_env(),
            )
        except FileNotFoundError as exc:
            raise ExternalToolError(
                "FFprobe was not found", details={"executable": ffprobe_path}
            ) from exc
        except OSError as exc:
            raise ExternalToolError(
                "Could not start FFprobe",
                details={"executable": ffprobe_path, "reason": str(exc)},
            ) from exc
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            stdout_size = _file_size(stdout_log)
            stderr_size = _file_size(stderr_log)
            limit_error = _capture_limit_error(stdout_size, stderr_size)
            if limit_error is not None:
                _kill_and_reap(process)
                raise limit_error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_and_reap(process)
                raise ExternalToolError("FFprobe timed out", details={"timeout": timeout_seconds})
            wait_timeout = min(_FFPROBE_POLL_SECONDS, remaining)
            try:
                return_code = process.wait(timeout=wait_timeout)
                break
            except subprocess.TimeoutExpired as exc:
                if remaining <= _FFPROBE_POLL_SECONDS:
                    _kill_and_reap(process)
                    raise ExternalToolError(
                        "FFprobe timed out", details={"timeout": timeout_seconds}
                    ) from exc

        stdout_size = _file_size(stdout_log)
        stderr_size = _file_size(stderr_log)
        limit_error = _capture_limit_error(stdout_size, stderr_size)
        if limit_error is not None:
            raise limit_error
        if return_code != 0:
            raise ExternalToolError(
                "FFprobe could not inspect the input",
                details={
                    "exit_code": return_code,
                    "stderr": _read_file_tail(
                        stderr_log,
                        max_bytes=_FFPROBE_ERROR_TAIL_BYTES,
                    ),
                    "stderr_truncated": stderr_size > _FFPROBE_ERROR_TAIL_BYTES,
                },
            )
        stdout_log.seek(0)
        raw = stdout_log.read(MAX_FFPROBE_STDOUT_BYTES + 1)
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExternalToolError("FFprobe returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ExternalToolError("FFprobe returned an unexpected response")
    return parse_ffprobe(parsed, source)
