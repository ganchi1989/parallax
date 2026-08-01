"""Copy rendered video and original audio into the final container."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from ..artifacts import prepare_output_path, replace_atomic
from ..errors import ExternalToolError, PipelineCancelled, ValidationError
from ..models import ffmpeg_faststart_args
from ..security import sanitized_subprocess_env
from .normalize import read_process_log_tail


def build_remux_command(
    video_path: str | Path,
    output_path: str | Path,
    *,
    audio_path: str | Path | None = None,
    ffmpeg_path: str = "ffmpeg",
    audio_codec: str = "copy",
) -> list[str]:
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(Path(video_path).expanduser().resolve()),
    ]
    if audio_path is not None:
        command.extend(["-i", str(Path(audio_path).expanduser().resolve())])
    command.extend(["-map", "0:v:0"])
    if audio_path is not None:
        # The normalization stage keeps every source audio stream in the MKA
        # intermediate. Map them all back into the final container so alternate
        # languages, commentary, and accessibility tracks are not discarded.
        command.extend(["-map", "1:a?", "-c:a", audio_codec])
        if audio_codec == "aac":
            command.extend(["-b:a", "192k"])
    destination = Path(output_path).expanduser().resolve()
    command.extend(["-c:v", "copy", "-map_metadata", "0"])
    command.extend(ffmpeg_faststart_args(destination))
    command.append(str(destination))
    return command


def remux_audio(
    video_path: str | Path,
    output_path: str | Path,
    *,
    audio_path: str | Path | None = None,
    ffmpeg_path: str = "ffmpeg",
    cancel: Callable[[], bool] | None = None,
    timeout_seconds: float | None = None,
) -> Path:
    video = Path(video_path).expanduser().resolve()
    output = prepare_output_path(output_path)
    audio = Path(audio_path).expanduser().resolve() if audio_path else None
    if not video.is_file():
        raise ValidationError("Rendered video does not exist", details={"path": str(video)})
    if audio is not None and not audio.is_file():
        audio = None
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds if timeout_seconds else None
    codecs = ["copy"]
    if audio is not None and output.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        codecs.append("aac")
    last_error = ""
    last_code: int | None = None
    completed_output: Path | None = None
    for audio_codec in codecs:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}.",
            suffix=f".partial{output.suffix}",
            dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        command = build_remux_command(
            video,
            temporary,
            audio_path=audio,
            ffmpeg_path=ffmpeg_path,
            audio_codec=audio_codec,
        )
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
                temporary.unlink(missing_ok=True)
                raise ExternalToolError(
                    "FFmpeg was not found", details={"executable": ffmpeg_path}
                ) from exc
            while process.poll() is None:
                if cancel and cancel():
                    process.terminate()
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=5)
                    if process.poll() is None:
                        process.kill()
                    temporary.unlink(missing_ok=True)
                    raise PipelineCancelled("Audio remux was cancelled")
                if deadline is not None and time.monotonic() >= deadline:
                    process.terminate()
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=5)
                    if process.poll() is None:
                        process.kill()
                    temporary.unlink(missing_ok=True)
                    raise ExternalToolError(
                        "Audio remux timed out", details={"timeout": timeout_seconds}
                    )
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.1)
            if process.returncode == 0:
                completed_output = temporary
                break
            last_error = read_process_log_tail(error_log)
            last_code = process.returncode
        temporary.unlink(missing_ok=True)
    else:
        raise ExternalToolError(
            "Audio remux failed",
            details={"exit_code": last_code, "stderr": last_error},
        )
    assert completed_output is not None
    replace_atomic(completed_output, output)
    return output
