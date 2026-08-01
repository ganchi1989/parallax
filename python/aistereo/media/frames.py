"""Sequential FFmpeg raw-frame decoder grouped by shot boundaries."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from pathlib import Path

import numpy as np

from ..errors import ExternalToolError, PipelineCancelled, StageError, ValidationError
from ..models import MAX_MEDIA_PIXELS, MediaInfo, Shot, ShotManifest, validated_bgr_frame_bytes
from ..render.video import (
    _await_pipe_io,
    _read_exact,
    _stop_process,
    _wait_for_process,
    build_raw_decode_command,
)
from ..security import sanitized_subprocess_env
from .normalize import read_process_log_tail


def _resize_bgr(frame: np.ndarray, height: int, width: int) -> np.ndarray:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError:
        y = np.rint(np.linspace(0, frame.shape[0] - 1, height)).astype(np.intp)
        x = np.rint(np.linspace(0, frame.shape[1] - 1, width)).astype(np.intp)
        return frame[y[:, None], x[None, :]]
    interpolation = cv2.INTER_AREA if height < frame.shape[0] else cv2.INTER_LINEAR
    return np.asarray(cv2.resize(frame, (width, height), interpolation=interpolation))


def representative_frame_indices(shot: Shot, limit: int = 12) -> tuple[int, ...]:
    """Choose deterministic, bounded representatives without crossing a shot.

    Long shots are covered by evenly distributed adjacent pairs.  Those pairs
    let motion analysis compare real neighbouring frames rather than treating
    distant representatives as consecutive video.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("representative frame limit must be a positive integer")
    if shot.frame_count <= limit:
        return tuple(range(shot.start_frame, shot.end_frame + 1))
    if limit == 1:
        return (shot.start_frame + (shot.frame_count - 1) // 2,)

    pair_count = limit // 2
    last_pair_start = shot.frame_count - 2
    if pair_count == 1:
        local_pair_starts = [last_pair_start // 2]
    else:
        denominator = pair_count - 1
        local_pair_starts = [
            (index * last_pair_start + denominator // 2) // denominator
            for index in range(pair_count)
        ]
    selected = {
        shot.start_frame + offset
        for pair_start in local_pair_starts
        for offset in (pair_start, pair_start + 1)
    }
    if len(selected) < limit:
        selected.add(shot.start_frame + (shot.frame_count - 1) // 2)
    return tuple(sorted(selected)[:limit])


def _build_sampled_decode_command(
    source: str | Path,
    *,
    ffmpeg_path: str,
    filter_path: str | Path,
    output_height: int,
    output_width: int,
) -> list[str]:
    """Decode only the representatives selected by a bounded filter script."""

    return [
        ffmpeg_path,
        "-hide_banner",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(Path(source).expanduser().resolve()),
        "-map",
        "0:v:0",
        "-/filter:v:0",
        str(Path(filter_path).expanduser().resolve()),
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "pipe:1",
    ]


def _sample_filter(frame_indices: Sequence[int], *, output_height: int, output_width: int) -> str:
    """Build an exact select-before-scale filter for sorted frame indexes.

    Adjacent representatives are collapsed to ranges. The filter is written to
    a file by :func:`_temporary_sample_filter`, so even a cut-heavy feature film
    cannot exceed Windows' process command-line limit.
    """

    indexes = tuple(frame_indices)
    if not indexes or indexes != tuple(sorted(set(indexes))) or indexes[0] < 0:
        raise ValueError("sample filter indexes must be non-negative, sorted, and unique")
    ranges: list[tuple[int, int]] = []
    range_start = indexes[0]
    range_end = range_start
    for index in indexes[1:]:
        if index == range_end + 1:
            range_end = index
            continue
        ranges.append((range_start, range_end))
        range_start = range_end = index
    ranges.append((range_start, range_end))
    terms = [
        f"eq(n\\,{start})" if start == end else f"between(n\\,{start}\\,{end})"
        for start, end in ranges
    ]
    return f"select={'+'.join(terms)},scale={output_width}:{output_height}:flags=area"


@contextmanager
def _temporary_sample_filter(
    frame_indices: Sequence[int], *, output_height: int, output_width: int
) -> Iterator[Path]:
    descriptor, name = tempfile.mkstemp(prefix="aistereo-samples-", suffix=".fffilter")
    path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
            handle.write(
                _sample_filter(
                    frame_indices,
                    output_height=output_height,
                    output_width=output_width,
                )
            )
            handle.write("\n")
        yield path
    finally:
        with suppress(OSError):
            path.unlink(missing_ok=True)


def decode_sampled_shots(
    source: str | Path,
    media: MediaInfo,
    manifest: ShotManifest,
    sample_indices: Mapping[int, Sequence[int]],
    *,
    ffmpeg_path: str = "ffmpeg",
    cancel: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    output_size: tuple[int, int],
) -> Iterator[tuple[Shot, tuple[int, ...], list[np.ndarray]]]:
    """Decode only validated representatives, grouped by their source shot."""

    output_height, output_width = output_size
    try:
        frame_size = validated_bgr_frame_bytes(
            output_width,
            output_height,
            max_pixels=MAX_MEDIA_PIXELS,
            context="sampled analysis frame",
        )
    except ValueError as exc:
        raise ValidationError(
            "Sampled frame dimensions are outside safe bounds", details={"reason": str(exc)}
        ) from exc

    expected_ids = [shot.shot_id for shot in manifest.shots]
    if set(sample_indices) != set(expected_ids):
        raise ValidationError(
            "Sample plan does not exactly cover the shot manifest",
            details={
                "expected_shot_ids": expected_ids,
                "sampled_shot_ids": sorted(sample_indices),
            },
        )
    validated: dict[int, tuple[int, ...]] = {}
    for shot in manifest.shots:
        indexes = tuple(sample_indices[shot.shot_id])
        if (
            not indexes
            or indexes != tuple(sorted(set(indexes)))
            or indexes[0] < shot.start_frame
            or indexes[-1] > shot.end_frame
        ):
            raise ValidationError(
                "Sample plan contains invalid frame indexes",
                details={"shot_id": shot.shot_id},
            )
        validated[shot.shot_id] = indexes

    selected_indices = tuple(index for shot in manifest.shots for index in validated[shot.shot_id])
    if not selected_indices:
        return

    with (
        _temporary_sample_filter(
            selected_indices,
            output_height=output_height,
            output_width=output_width,
        ) as filter_path,
        tempfile.TemporaryFile() as error_log,
    ):
        try:
            process = subprocess.Popen(
                _build_sampled_decode_command(
                    source,
                    ffmpeg_path=ffmpeg_path,
                    filter_path=filter_path,
                    output_height=output_height,
                    output_width=output_width,
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=error_log,
                shell=False,
                env=sanitized_subprocess_env(),
            )
        except OSError as exc:
            raise ExternalToolError(
                "Could not start FFmpeg",
                details={"executable": ffmpeg_path, "reason": str(exc)},
            ) from exc
        reader_executor: ThreadPoolExecutor | None = None
        try:
            assert process.stdout is not None
            reader_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="aistereo-sample-reader"
            )
            decoded = 0
            for shot in manifest.shots:
                indexes = validated[shot.shot_id]
                frames: list[np.ndarray] = []
                for frame_index in indexes:
                    if cancel and cancel():
                        raise PipelineCancelled("Sampled frame decoding was cancelled")
                    read_future = reader_executor.submit(_read_exact, process.stdout, frame_size)
                    raw = _await_pipe_io(
                        read_future,
                        process=process,
                        cancel=cancel,
                        cancellation_message="Sampled frame decoding was cancelled",
                        operation="decode sampled analysis frame",
                    )
                    if len(raw) != frame_size:
                        raise StageError(
                            "Decoded video ended before the representative sample plan",
                            details={
                                "frame": frame_index,
                                "sample": decoded,
                                "bytes": len(raw),
                                "expected": frame_size,
                            },
                        )
                    frames.append(
                        np.frombuffer(raw, dtype=np.uint8)
                        .reshape(output_height, output_width, 3)
                        .copy()
                    )
                    decoded += 1
                    if progress:
                        progress(decoded, len(selected_indices))
                yield shot, indexes, frames
            extra_future = reader_executor.submit(_read_exact, process.stdout, frame_size)
            extra = _await_pipe_io(
                extra_future,
                process=process,
                cancel=cancel,
                cancellation_message="Sampled frame decoding was cancelled",
                operation="verify sampled decoder end-of-stream",
            )
            return_code = _wait_for_process(
                process,
                timeout_seconds=30,
                cancel=cancel,
                cancellation_message="Sampled frame decoding was cancelled",
                operation="sampled decoder shutdown",
            )
            if extra or return_code != 0:
                raise StageError(
                    "Decoded frame count does not match the shot manifest",
                    details={
                        "extra_frame": bool(extra),
                        "decoder_exit": return_code,
                        "stderr": read_process_log_tail(error_log),
                    },
                )
        except BaseException:
            _stop_process(process)
            if reader_executor is not None:
                reader_executor.shutdown(wait=True, cancel_futures=True)
            if process.stdout is not None:
                with suppress(OSError, ValueError):
                    process.stdout.close()
            raise
        else:
            assert reader_executor is not None
            reader_executor.shutdown(wait=True, cancel_futures=True)
            if process.stdout is not None:
                process.stdout.close()


def decode_shots(
    source: str | Path,
    media: MediaInfo,
    manifest: ShotManifest,
    *,
    ffmpeg_path: str = "ffmpeg",
    cancel: Callable[[], bool] | None = None,
    output_size: tuple[int, int] | None = None,
) -> Iterator[tuple[Shot, list[np.ndarray]]]:
    try:
        frame_size = validated_bgr_frame_bytes(
            media.width, media.height, context="decoded source frame"
        )
        if output_size is not None:
            output_height, output_width = output_size
            validated_bgr_frame_bytes(
                output_width,
                output_height,
                max_pixels=MAX_MEDIA_PIXELS,
                context="resized decoded frame",
            )
    except ValueError as exc:
        raise ValidationError(
            "Video frame dimensions are outside safe bounds", details={"reason": str(exc)}
        ) from exc
    with tempfile.TemporaryFile() as error_log:
        try:
            process = subprocess.Popen(
                build_raw_decode_command(source, ffmpeg_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=error_log,
                shell=False,
                env=sanitized_subprocess_env(),
            )
        except OSError as exc:
            raise ExternalToolError(
                "Could not start FFmpeg",
                details={"executable": ffmpeg_path, "reason": str(exc)},
            ) from exc
        reader_executor: ThreadPoolExecutor | None = None
        try:
            assert process.stdout is not None
            reader_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="aistereo-shot-reader"
            )
            decoded = 0
            for shot in manifest.shots:
                frames: list[np.ndarray] = []
                for _ in range(shot.frame_count):
                    if cancel and cancel():
                        raise PipelineCancelled("Frame decoding was cancelled")
                    read_future = reader_executor.submit(_read_exact, process.stdout, frame_size)
                    raw = _await_pipe_io(
                        read_future,
                        process=process,
                        cancel=cancel,
                        cancellation_message="Frame decoding was cancelled",
                        operation="decode shot frame",
                    )
                    if len(raw) != frame_size:
                        raise StageError(
                            "Decoded video ended before the shot manifest",
                            details={"frame": decoded, "bytes": len(raw), "expected": frame_size},
                        )
                    frame = (
                        np.frombuffer(raw, dtype=np.uint8)
                        .reshape(media.height, media.width, 3)
                        .copy()
                    )
                    if output_size is not None:
                        output_height, output_width = output_size
                        frame = _resize_bgr(frame, output_height, output_width)
                    frames.append(frame)
                    decoded += 1
                yield shot, frames
            extra_future = reader_executor.submit(_read_exact, process.stdout, frame_size)
            extra = _await_pipe_io(
                extra_future,
                process=process,
                cancel=cancel,
                cancellation_message="Frame decoding was cancelled",
                operation="verify decoder end-of-stream",
            )
            return_code = _wait_for_process(
                process,
                timeout_seconds=30,
                cancel=cancel,
                cancellation_message="Frame decoding was cancelled",
                operation="shot decoder shutdown",
            )
            if extra or return_code != 0:
                raise StageError(
                    "Decoded frame count does not match the shot manifest",
                    details={
                        "extra_frame": bool(extra),
                        "decoder_exit": return_code,
                        "stderr": read_process_log_tail(error_log),
                    },
                )
        except BaseException:
            _stop_process(process)
            if reader_executor is not None:
                reader_executor.shutdown(wait=True, cancel_futures=True)
            if process.stdout is not None:
                with suppress(OSError, ValueError):
                    process.stdout.close()
            raise
        else:
            assert reader_executor is not None
            reader_executor.shutdown(wait=True, cancel_futures=True)
            if process.stdout is not None:
                process.stdout.close()
