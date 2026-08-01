"""Optional Video Depth Anything Small adapter.

No code or weights are downloaded. A packaged application must supply a
compatible, commercially reviewed Small checkpoint and its model implementation.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from ..config import DepthConfig
from ..errors import DependencyUnavailableError, PipelineCancelled, ValidationError
from .base import CancelCheck, DepthBackend, ProgressCallback
from .limits import (
    ESTIMATED_BYTES_PER_DEPTH_PIXEL as _ESTIMATED_BYTES_PER_DEPTH_PIXEL,
)
from .limits import (
    MAX_DEPTH_WORKING_SET_BYTES,
    max_frames_for_working_set,
)

def validate_depth_working_set(
    frame_count: int,
    config: DepthConfig,
    *,
    shot_id: int,
) -> None:
    estimated = frame_count * config.height * config.width * _ESTIMATED_BYTES_PER_DEPTH_PIXEL
    if estimated > MAX_DEPTH_WORKING_SET_BYTES:
        raise ValidationError(
            "Shot exceeds the bounded depth working-set limit",
            details={
                "shot_id": shot_id,
                "estimated_bytes": estimated,
                "max_bytes": MAX_DEPTH_WORKING_SET_BYTES,
                "depth_width": config.width,
                "depth_height": config.height,
                "frames": frame_count,
            },
        )


def _resize_frame(frame: np.ndarray, height: int, width: int) -> np.ndarray:
    value = np.asarray(frame)
    if value.shape[:2] == (height, width):
        return value
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        interpolation = cv2.INTER_AREA if height < value.shape[0] else cv2.INTER_LINEAR
        return np.asarray(cv2.resize(value, (width, height), interpolation=interpolation))
    y = np.rint(np.linspace(0, value.shape[0] - 1, height)).astype(np.intp)
    x = np.rint(np.linspace(0, value.shape[1] - 1, width)).astype(np.intp)
    return value[y[:, None], x[None, :]]


def _resize_depth_nearest(depth: np.ndarray, height: int, width: int) -> np.ndarray:
    if depth.shape == (height, width):
        return np.asarray(depth, dtype=np.float32)
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        return np.asarray(
            cv2.resize(depth, (width, height), interpolation=cv2.INTER_LINEAR),
            dtype=np.float32,
        )
    y = np.rint(np.linspace(0, depth.shape[0] - 1, height)).astype(np.intp)
    x = np.rint(np.linspace(0, depth.shape[1] - 1, width)).astype(np.intp)
    return np.asarray(depth[y[:, None], x[None, :]], dtype=np.float32)


def _align_chunk_to_overlap(
    chunk: np.ndarray,
    accumulated: np.ndarray,
    accumulated_weights: np.ndarray,
    start: int,
) -> np.ndarray:
    """Robustly align relative-depth scale/offset to existing overlap."""

    overlap_local = np.flatnonzero(accumulated_weights[start : start + len(chunk)] > 0)
    if overlap_local.size == 0:
        return chunk
    global_indexes = start + overlap_local
    reference = accumulated[global_indexes] / accumulated_weights[global_indexes, None, None]
    incoming = chunk[overlap_local]
    finite = np.isfinite(reference) & np.isfinite(incoming)
    if not np.any(finite):
        return chunk
    reference_values = reference[finite]
    incoming_values = incoming[finite]
    reference_median = float(np.median(reference_values))
    incoming_median = float(np.median(incoming_values))
    reference_iqr = float(np.percentile(reference_values, 75) - np.percentile(reference_values, 25))
    incoming_iqr = float(np.percentile(incoming_values, 75) - np.percentile(incoming_values, 25))
    scale = reference_iqr / incoming_iqr if incoming_iqr > 1e-6 else 1.0
    scale = float(np.clip(scale, 0.1, 10.0))
    offset = reference_median - scale * incoming_median
    return np.asarray(chunk * scale + offset, dtype=np.float32)


def _cuda_runtime_is_usable(torch: Any) -> bool:
    """Return whether PyTorch can see at least one usable CUDA device.

    ``torch.cuda.is_available()`` normally covers both the CUDA-enabled build
    and driver checks.  Keep the automatic path defensive because partially
    installed or mismatched Windows runtimes can raise while PyTorch performs
    those checks.  Automatic selection must remain a safe CPU fallback; an
    explicit CUDA request is handled separately with a clear error.
    """

    try:
        cuda = getattr(torch, "cuda", None)
        is_available = getattr(cuda, "is_available", None)
        device_count = getattr(cuda, "device_count", None)
        if not callable(is_available) or not bool(is_available()):
            return False
        if not callable(device_count) or int(device_count()) < 1:
            return False
    except Exception:
        # This is an optional acceleration probe. A broken driver/runtime must
        # not make the default automatic mode less reliable than CPU mode.
        return False
    return True


def _resolve_device(requested: str) -> str:
    if requested == "cpu":
        return "cpu"
    try:
        import torch  # type: ignore[import-not-found]
    except (ImportError, OSError, RuntimeError) as exc:
        if requested == "auto":
            return "cpu"
        raise DependencyUnavailableError(
            f"PyTorch is required for the requested {requested} depth device"
        ) from exc
    cuda_usable = _cuda_runtime_is_usable(torch)
    if requested == "auto":
        return "cuda" if cuda_usable else "cpu"
    if requested == "cuda" and not cuda_usable:
        raise DependencyUnavailableError("CUDA was requested but is not available to PyTorch")
    if requested == "mps":
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        if mps is None or not bool(mps.is_available()):
            raise DependencyUnavailableError("MPS was requested but is not available to PyTorch")
    return requested


def _prepare_model_for_device(model: Any, device: str) -> Any:
    """Move direct-call Torch modules once; adapter-style models own their device policy."""

    if hasattr(model, "infer_video_depth") or hasattr(model, "predict"):
        return model
    try:
        moved = model.to(device)
        prepared = model if moved is None else moved
        if hasattr(prepared, "eval"):
            prepared.eval()
        return prepared
    except Exception as exc:
        raise ValidationError(
            "The packaged depth model could not be prepared for the requested device",
            details={"device": device, "reason": str(exc)},
        ) from exc


class VideoDepthAnythingBackend(DepthBackend):
    name = "video-depth-anything-small"

    def __init__(self, model: Any | None = None, model_path: str | Path | None = None) -> None:
        self._model = model
        self._model_path = Path(model_path).expanduser().resolve() if model_path else None

    def _load_model(self, config: DepthConfig) -> Any:
        if self._model is not None:
            return self._model
        model_path = self._model_path or (
            Path(config.model_path).expanduser().resolve() if config.model_path else None
        )
        if model_path is None or not model_path.is_file():
            raise DependencyUnavailableError(
                "Video Depth Anything Small requires an explicitly configured local checkpoint",
                details={"setting": "depth.model_path", "automatic_download": False},
            )
        try:
            import torch  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DependencyUnavailableError(
                "PyTorch is not installed", details={"install_extra": "ai-stereo-director[depth]"}
            ) from exc
        # TorchScript is the stable packaging boundary supported directly here.
        # Native upstream checkpoints can be adapted by injecting an object with
        # ``infer_video_depth(frames, input_size, device)``.
        try:
            self._model = torch.jit.load(str(model_path), map_location="cpu")
        except Exception as torchscript_error:
            source = config.model_source
            if not source:
                raise DependencyUnavailableError(
                    "The configured checkpoint is not a packaged TorchScript model; "
                    "set depth.model_source to an upstream checkout to load it natively",
                    details={"path": str(model_path), "reason": str(torchscript_error)},
                ) from torchscript_error
            from .vda_native import load_native_model

            self._model = load_native_model(model_path, source)
        return self._model

    def _infer_chunk(
        self,
        model: Any,
        array: np.ndarray,
        *,
        device: str,
        config: DepthConfig,
    ) -> np.ndarray:
        if hasattr(model, "infer_video_depth"):
            result = model.infer_video_depth(
                array, input_size=max(config.width, config.height), device=device
            )
        elif hasattr(model, "predict"):
            result = model.predict(array)
        else:
            import torch  # type: ignore[import-not-found]

            rgb = array[..., ::-1].copy()
            tensor = torch.from_numpy(rgb).permute(0, 3, 1, 2).float().div_(255).to(device)
            with torch.inference_mode():
                result = model(tensor)
        if isinstance(result, (tuple, list)):
            result = result[0]
        if hasattr(result, "detach"):
            result = result.detach().float().cpu().numpy()
        depth = np.asarray(result, dtype=np.float32)
        if depth.ndim == 4 and depth.shape[1] == 1:
            depth = depth[:, 0]
        return depth

    def estimate(
        self,
        frames: Sequence[np.ndarray],
        *,
        shot_id: int,
        config: DepthConfig,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
    ) -> np.ndarray:
        if not frames:
            raise ValidationError("Video Depth Anything needs at least one frame")
        if len(frames) > config.max_shot_frames:
            raise ValidationError(
                "Shot exceeds the configured bounded-memory inference limit",
                details={
                    "shot_id": shot_id,
                    "frames": len(frames),
                    "max_shot_frames": config.max_shot_frames,
                },
            )
        validate_depth_working_set(len(frames), config, shot_id=shot_id)
        if cancel and cancel():
            raise PipelineCancelled("Depth estimation was cancelled")
        model = self._load_model(config)
        device = _resolve_device(config.device)
        model = _prepare_model_for_device(model, device)
        self._model = model
        total = len(frames)
        output = np.zeros((total, config.height, config.width), dtype=np.float32)
        weights = np.zeros(total, dtype=np.float32)
        step = config.chunk_frames - config.chunk_overlap
        start = 0
        while start < total:
            if cancel and cancel():
                raise PipelineCancelled("Depth estimation was cancelled")
            end = min(total, start + config.chunk_frames)
            # Resize before stacking so model input memory is bounded by the
            # configured depth resolution and chunk length.
            array = np.stack(
                [
                    _resize_frame(np.asarray(frames[index]), config.height, config.width)
                    for index in range(start, end)
                ]
            )
            try:
                chunk_depth = self._infer_chunk(model, array, device=device, config=config)
            except PipelineCancelled:
                raise
            except Exception as exc:
                raise ValidationError(
                    "Video Depth Anything inference failed",
                    details={"shot_id": shot_id, "chunk_start": start, "reason": str(exc)},
                ) from exc
            if chunk_depth.ndim != 3 or chunk_depth.shape[0] != end - start:
                raise ValidationError(
                    "Depth model returned an invalid shape",
                    details={
                        "shape": list(chunk_depth.shape),
                        "chunk_frames": end - start,
                    },
                )
            chunk_depth = np.stack(
                [_resize_depth_nearest(plane, config.height, config.width) for plane in chunk_depth]
            )
            chunk_depth = _align_chunk_to_overlap(chunk_depth, output, weights, start)
            for local_index, global_index in enumerate(range(start, end)):
                output[global_index] += chunk_depth[local_index]
                weights[global_index] += 1.0
            if progress:
                progress(end, total)
            if end == total:
                break
            start += step
        output /= np.maximum(weights[:, None, None], 1.0)
        return output
