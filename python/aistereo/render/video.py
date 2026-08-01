"""Stream CFR video through the NumPy renderer without moving frames over IPC."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections import deque
from collections.abc import Callable
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import IO, Literal, TypeVar

import numpy as np

from ..artifacts import prepare_output_path, replace_atomic, resolve_project_artifact
from ..config import ComfortConfig, MediaConfig, RenderConfig
from ..depth import load_depth_shot
from ..errors import ExternalToolError, PipelineCancelled, StageError, ValidationError
from ..media.normalize import read_process_log_tail
from ..models import (
    MAX_MEDIA_DIMENSION,
    MAX_RAW_BGR_FRAME_BYTES,
    MAX_RENDER_OUTPUT_PIXELS,
    DepthManifest,
    MediaInfo,
    ShotManifest,
    StereoParameters,
    StereoScript,
    ffmpeg_faststart_args,
    validated_bgr_frame_bytes,
)
from ..qc.metrics import QCAccumulator
from ..security import sanitized_subprocess_env
from .frame import RenderedFrame, render_stereo_frame

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]
OutputMode = Literal["anaglyph", "left", "right", "side-by-side"]
PIPE_IO_POLL_SECONDS = 0.1
PIPE_IO_WATCHDOG_SECONDS = 120.0
_MAX_RENDER_WORKERS = 6
_T = TypeVar("_T")


def interpolate_stereo_parameters(
    previous: StereoParameters,
    target: StereoParameters,
    frame_in_shot: int,
) -> StereoParameters:
    """Ease parameters only inside an explicitly gradual transition."""

    transition_frames = target.transition_frames
    if transition_frames <= 0 or frame_in_shot >= transition_frames:
        return target
    step = max(1, frame_in_shot + 1)
    smoothing = target.temporal_smoothing
    if smoothing >= 1.0 - 1e-7:
        blend = step / transition_frames
    else:
        denominator = 1.0 - smoothing**transition_frames
        blend = (1.0 - smoothing**step) / max(denominator, 1e-9)
    blend = min(1.0, max(0.0, blend))

    def mix(start: float, end: float) -> float:
        return start + (end - start) * blend

    return StereoParameters(
        depth_strength=mix(previous.depth_strength, target.depth_strength),
        convergence_depth_percentile=mix(
            previous.convergence_depth_percentile,
            target.convergence_depth_percentile,
        ),
        max_background_disparity_norm=mix(
            previous.max_background_disparity_norm,
            target.max_background_disparity_norm,
        ),
        max_popout_disparity_norm=mix(
            previous.max_popout_disparity_norm,
            target.max_popout_disparity_norm,
        ),
        temporal_smoothing=target.temporal_smoothing,
        transition_frames=target.transition_frames,
        edge_protection=target.edge_protection,
    )


@dataclass(frozen=True)
class _PendingFrame:
    """One frame rendering on a worker thread, awaiting its turn to be written."""

    shot_id: int
    frame_index: int
    depth: np.ndarray
    future: Future[RenderedFrame]


def render_worker_count() -> int:
    """Threads used for per-frame stereo work.

    The heavy steps are NumPy array operations that release the GIL, so threads
    give real parallelism. One core is left for the decoder, the encoder, and
    the rest of the application.
    """

    return max(1, min(_MAX_RENDER_WORKERS, (os.cpu_count() or 2) - 1))


def scaled_preview_size(width: int, height: int, max_width: int) -> tuple[int, int]:
    """Fit inside ``max_width`` while keeping aspect and even dimensions."""

    if max_width <= 0 or width <= max_width:
        return width, height
    scaled_height = max(2, round(height * max_width / width))
    return max_width - (max_width % 2), scaled_height - (scaled_height % 2)


def build_raw_decode_command(
    source: str | Path,
    ffmpeg_path: str = "ffmpeg",
    *,
    scale: tuple[int, int] | None = None,
) -> list[str]:
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(Path(source).expanduser().resolve()),
        "-map",
        "0:v:0",
        "-vsync",
        "0",
    ]
    if scale is not None:
        # Scaling in the decoder means the renderer, the encoder, and every
        # bounds check downstream all see one consistent frame size.
        command.extend(["-vf", f"scale={scale[0]}:{scale[1]}:flags=area"])
    command.extend(["-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"])
    return command


def build_raw_encode_command(
    output: str | Path,
    *,
    width: int,
    height: int,
    frame_rate: float,
    media_config: MediaConfig,
) -> list[str]:
    try:
        validated_bgr_frame_bytes(
            width,
            height,
            max_width=MAX_MEDIA_DIMENSION * 2,
            max_pixels=MAX_RENDER_OUTPUT_PIXELS,
            context="encoded video frame",
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    destination = Path(output).expanduser().resolve()
    command = [
        media_config.ffmpeg_path,
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
        "-r",
        f"{frame_rate:.12g}",
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        media_config.preset,
        "-crf",
        str(media_config.crf),
        "-pix_fmt",
        "yuv420p",
        "-color_primaries",
        media_config.color_primaries,
        "-color_trc",
        media_config.color_primaries,
        "-colorspace",
        media_config.color_primaries,
    ]
    command.extend(ffmpeg_faststart_args(destination))
    command.append(str(destination))
    return command


def _read_exact(stream: IO[bytes], size: int) -> bytes:
    if size <= 0 or size > MAX_RAW_BGR_FRAME_BYTES:
        raise ValidationError(
            "Raw frame read exceeds the bounded byte limit",
            details={"bytes": size, "max_bytes": MAX_RAW_BGR_FRAME_BYTES},
        )
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_all(stream: IO[bytes], payload: bytes) -> int:
    if len(payload) > MAX_RAW_BGR_FRAME_BYTES:
        raise ValidationError(
            "Raw frame write exceeds the bounded byte limit",
            details={"bytes": len(payload), "max_bytes": MAX_RAW_BGR_FRAME_BYTES},
        )
    view = memoryview(payload)
    written_total = 0
    while written_total < len(view):
        written = stream.write(view[written_total:])
        if written is None or written <= 0:
            raise BrokenPipeError("FFmpeg pipe stopped accepting frame data")
        written_total += written
    stream.flush()
    return written_total


def _await_pipe_io(
    future: Future[_T],
    *,
    process: subprocess.Popen[bytes],
    cancel: CancelCheck | None,
    cancellation_message: str,
    operation: str,
    timeout_seconds: float = PIPE_IO_WATCHDOG_SECONDS,
) -> _T:
    """Poll blocking Windows pipe I/O without blocking cancellation forever."""

    deadline = monotonic() + timeout_seconds
    while True:
        try:
            return future.result(timeout=PIPE_IO_POLL_SECONDS)
        except FutureTimeoutError:
            if cancel and cancel():
                raise PipelineCancelled(cancellation_message) from None
            if monotonic() >= deadline:
                raise ExternalToolError(
                    "FFmpeg pipe I/O stalled",
                    details={
                        "operation": operation,
                        "timeout": timeout_seconds,
                        "process_exit": process.poll(),
                    },
                ) from None


def _wait_for_process(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    cancel: CancelCheck | None,
    cancellation_message: str,
    operation: str,
) -> int:
    deadline = monotonic() + timeout_seconds
    while process.poll() is None:
        if cancel and cancel():
            raise PipelineCancelled(cancellation_message)
        if monotonic() >= deadline:
            raise ExternalToolError(
                "FFmpeg process timed out",
                details={"operation": operation, "timeout": timeout_seconds},
            )
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=PIPE_IO_POLL_SECONDS)
    assert process.returncode is not None
    return process.returncode


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate, escalate, and reap an FFmpeg child without leaving a zombie."""

    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


def render_video(
    source: str | Path,
    output: str | Path,
    *,
    media: MediaInfo,
    manifest: ShotManifest,
    depth_manifest: DepthManifest,
    script: StereoScript,
    project_root: str | Path,
    media_config: MediaConfig | None = None,
    render_config: RenderConfig | None = None,
    comfort_config: ComfortConfig | None = None,
    shot_ids: set[int] | None = None,
    output_mode: OutputMode = "anaglyph",
    scale_width: int | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
    qc: QCAccumulator | None = None,
) -> tuple[Path, int, QCAccumulator]:
    source_path = Path(source).expanduser().resolve()
    output_path = prepare_output_path(output)
    root = Path(project_root).expanduser().resolve()
    if not source_path.is_file():
        raise ValidationError("Normalized video does not exist", details={"path": str(source_path)})
    render_width, render_height = (
        scaled_preview_size(media.width, media.height, scale_width)
        if scale_width is not None
        else (media.width, media.height)
    )
    decode_scale = (
        (render_width, render_height)
        if (render_width, render_height) != (media.width, media.height)
        else None
    )
    try:
        frame_bytes = validated_bgr_frame_bytes(
            render_width, render_height, context="normalized source frame"
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    media_settings = media_config or MediaConfig()
    render_settings = render_config or RenderConfig()
    comfort_settings = comfort_config or ComfortConfig()
    depth_by_id = {item.shot_id: item for item in depth_manifest.shots}
    script_by_id = {item.shot_id: item for item in script.shots}
    selected = shot_ids or {item.shot_id for item in manifest.shots}
    expected = sum(item.frame_count for item in manifest.shots if item.shot_id in selected)
    if expected <= 0:
        raise ValidationError("No frames were selected for rendering")
    output_width = render_width * 2 if output_mode == "side-by-side" else render_width
    try:
        output_frame_bytes = validated_bgr_frame_bytes(
            output_width,
            render_height,
            max_width=MAX_MEDIA_DIMENSION * 2,
            max_pixels=MAX_RENDER_OUTPUT_PIXELS,
            context="rendered output frame",
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}.",
        suffix=f".partial{output_path.suffix}",
        dir=output_path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    accumulator = qc or QCAccumulator()
    rendered_count = 0
    decoded_count = 0
    shot_index = 0
    current_shot = manifest.shots[0] if manifest.shots else None
    current_depth: np.ndarray | None = None
    current_script = None
    with tempfile.TemporaryFile() as decoder_log, tempfile.TemporaryFile() as encoder_log:
        decoder: subprocess.Popen[bytes] | None = None
        encoder: subprocess.Popen[bytes] | None = None
        reader_executor: ThreadPoolExecutor | None = None
        writer_executor: ThreadPoolExecutor | None = None
        render_executor: ThreadPoolExecutor | None = None
        pending: deque[_PendingFrame] = deque()
        try:
            decoder = subprocess.Popen(
                build_raw_decode_command(
                    source_path, media_settings.ffmpeg_path, scale=decode_scale
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=decoder_log,
                shell=False,
                env=sanitized_subprocess_env(),
            )
            encoder = subprocess.Popen(
                build_raw_encode_command(
                    temporary,
                    width=output_width,
                    height=render_height,
                    frame_rate=media.frame_rate,
                    media_config=media_settings,
                ),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=encoder_log,
                shell=False,
                env=sanitized_subprocess_env(),
            )
        except OSError as exc:
            if decoder is not None:
                _stop_process(decoder)
            temporary.unlink(missing_ok=True)
            raise ExternalToolError(
                "Could not start FFmpeg",
                details={"executable": media_settings.ffmpeg_path, "reason": str(exc)},
            ) from exc
        assert decoder is not None and encoder is not None
        try:
            assert decoder.stdout is not None
            assert encoder.stdin is not None
            reader_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="aistereo-ffmpeg-reader"
            )
            writer_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="aistereo-ffmpeg-writer"
            )
            render_workers = render_worker_count()
            render_executor = ThreadPoolExecutor(
                max_workers=render_workers, thread_name_prefix="aistereo-render"
            )
            render_lookahead = render_workers * 2

            def write_rendered_frame(item: _PendingFrame) -> None:
                """Consume one finished frame, strictly in decode order.

                Frames render in parallel but are accumulated and encoded in
                sequence, so QC metrics and the output stream stay identical to
                a single-threaded render.
                """

                nonlocal rendered_count
                result = item.future.result()
                accumulator.add_frame(
                    item.shot_id,
                    item.frame_index,
                    result.disparity_norm,
                    result.holes,
                    result.edge_violations,
                    item.depth,
                )
                if output_mode == "left":
                    output_frame = result.left
                elif output_mode == "right":
                    output_frame = result.right
                elif output_mode == "side-by-side":
                    output_frame = np.concatenate([result.left, result.right], axis=1)
                else:
                    output_frame = result.anaglyph
                contiguous_output = np.ascontiguousarray(output_frame)
                expected_shape = (render_height, output_width, 3)
                if (
                    contiguous_output.dtype != np.uint8
                    or contiguous_output.shape != expected_shape
                    or contiguous_output.nbytes != output_frame_bytes
                ):
                    raise StageError(
                        "Renderer returned an invalid output frame",
                        details={
                            "shape": list(contiguous_output.shape),
                            "dtype": str(contiguous_output.dtype),
                            "bytes": contiguous_output.nbytes,
                            "expected_shape": list(expected_shape),
                            "expected_bytes": output_frame_bytes,
                        },
                    )
                assert encoder is not None and encoder.stdin is not None
                assert writer_executor is not None
                write_future = writer_executor.submit(
                    _write_all, encoder.stdin, contiguous_output.tobytes()
                )
                written = _await_pipe_io(
                    write_future,
                    process=encoder,
                    cancel=cancel,
                    cancellation_message="Rendering was cancelled",
                    operation="encode frame",
                )
                if written != output_frame_bytes:
                    raise StageError(
                        "FFmpeg accepted a partial output frame",
                        details={"bytes": written, "expected": output_frame_bytes},
                    )
                rendered_count += 1
                if progress:
                    progress(rendered_count, expected)

            while True:
                if cancel and cancel():
                    raise PipelineCancelled("Rendering was cancelled")
                read_future = reader_executor.submit(_read_exact, decoder.stdout, frame_bytes)
                raw = _await_pipe_io(
                    read_future,
                    process=decoder,
                    cancel=cancel,
                    cancellation_message="Rendering was cancelled",
                    operation="decode frame",
                )
                if not raw:
                    break
                if len(raw) != frame_bytes:
                    raise StageError(
                        "FFmpeg returned a partial frame",
                        details={
                            "frame": decoded_count,
                            "bytes": len(raw),
                            "expected": frame_bytes,
                        },
                    )
                while current_shot is not None and decoded_count > current_shot.end_frame:
                    shot_index += 1
                    current_shot = (
                        manifest.shots[shot_index] if shot_index < len(manifest.shots) else None
                    )
                    current_depth = None
                    current_script = None
                if current_shot is None:
                    raise StageError("Decoded frames exceed the shot manifest")
                if current_shot.shot_id in selected:
                    if current_depth is None:
                        depth_meta = depth_by_id.get(current_shot.shot_id)
                        current_script = script_by_id.get(current_shot.shot_id)
                        if depth_meta is None or current_script is None:
                            raise StageError(
                                "Render artifacts are incomplete",
                                details={"shot_id": current_shot.shot_id},
                            )
                        depth_path = resolve_project_artifact(
                            root, depth_meta.path, subdirectory="depth"
                        )
                        current_depth = load_depth_shot(depth_path)
                        if current_depth.shape[0] != current_shot.frame_count:
                            raise StageError(
                                "Depth frame count does not match the shot",
                                details={
                                    "shot_id": current_shot.shot_id,
                                    "depth_frames": current_depth.shape[0],
                                    "shot_frames": current_shot.frame_count,
                                },
                            )
                    local_index = decoded_count - current_shot.start_frame
                    if current_script is None:
                        raise StageError(
                            "Stereo script entry was not loaded",
                            details={"shot_id": current_shot.shot_id},
                        )
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                        render_height, render_width, 3
                    )
                    depth = current_depth[local_index]
                    frame_parameters = current_script.parameters
                    if current_shot.transition == "fade" and shot_index > 0:
                        previous_shot = manifest.shots[shot_index - 1]
                        previous_script = script_by_id.get(previous_shot.shot_id)
                        if previous_script is not None:
                            frame_parameters = interpolate_stereo_parameters(
                                previous_script.parameters,
                                current_script.parameters,
                                local_index,
                            )
                    pending.append(
                        _PendingFrame(
                            shot_id=current_shot.shot_id,
                            frame_index=decoded_count,
                            depth=depth,
                            future=render_executor.submit(
                                render_stereo_frame,
                                frame,
                                depth,
                                frame_parameters,
                                render_config=render_settings,
                                comfort_config=comfort_settings,
                            ),
                        )
                    )
                    # Bounded look-ahead: enough frames in flight to keep every
                    # worker busy, few enough that memory stays predictable.
                    while len(pending) >= render_lookahead:
                        write_rendered_frame(pending.popleft())
                decoded_count += 1
            while pending:
                write_rendered_frame(pending.popleft())
            close_future = writer_executor.submit(encoder.stdin.close)
            _await_pipe_io(
                close_future,
                process=encoder,
                cancel=cancel,
                cancellation_message="Rendering was cancelled",
                operation="close encoder input",
            )
            decoder_return = _wait_for_process(
                decoder,
                timeout_seconds=30,
                cancel=cancel,
                cancellation_message="Rendering was cancelled",
                operation="decoder shutdown",
            )
            encoder_return = _wait_for_process(
                encoder,
                timeout_seconds=60,
                cancel=cancel,
                cancellation_message="Rendering was cancelled",
                operation="encoder shutdown",
            )
            if decoder_return != 0 or encoder_return != 0:
                raise ExternalToolError(
                    "FFmpeg failed during rendering",
                    details={
                        "decoder_exit": decoder_return,
                        "encoder_exit": encoder_return,
                        "decoder_stderr": read_process_log_tail(decoder_log),
                        "encoder_stderr": read_process_log_tail(encoder_log),
                    },
                )
            if rendered_count != expected:
                raise StageError(
                    "Rendered frame count differs from the selected manifest",
                    details={"rendered": rendered_count, "expected": expected},
                )
        except BaseException:
            _stop_process(encoder)
            _stop_process(decoder)
            for item in pending:
                item.future.cancel()
            pending.clear()
            if render_executor is not None:
                render_executor.shutdown(wait=True, cancel_futures=True)
            if reader_executor is not None:
                reader_executor.shutdown(wait=True, cancel_futures=True)
            if writer_executor is not None:
                writer_executor.shutdown(wait=True, cancel_futures=True)
            if decoder.stdout is not None:
                with suppress(OSError, ValueError):
                    decoder.stdout.close()
            if encoder.stdin is not None:
                with suppress(OSError, ValueError):
                    encoder.stdin.close()
            temporary.unlink(missing_ok=True)
            raise
        else:
            assert reader_executor is not None and writer_executor is not None
            if render_executor is not None:
                render_executor.shutdown(wait=True, cancel_futures=True)
            reader_executor.shutdown(wait=True, cancel_futures=True)
            writer_executor.shutdown(wait=True, cancel_futures=True)
            if decoder.stdout is not None:
                decoder.stdout.close()
    replace_atomic(temporary, output_path)
    return output_path, rendered_count, accumulator
