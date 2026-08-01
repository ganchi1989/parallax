"""Application configuration and conservative safety defaults."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ValidationError

CERTIFIED_DEPTH_BACKEND = "video-depth-anything-small"
MONOCULAR_DEPTH_BACKEND = "monocular-cues"
_DEPTH_BACKEND_ALIASES = {
    "synthetic": "synthetic",
    "cached": "cached",
    "precomputed": "cached",
    "monocular-cues": MONOCULAR_DEPTH_BACKEND,
    "monocular": MONOCULAR_DEPTH_BACKEND,
    "image-analysis": MONOCULAR_DEPTH_BACKEND,
    "video-depth-anything-small": CERTIFIED_DEPTH_BACKEND,
    "vda-small": CERTIFIED_DEPTH_BACKEND,
    "video-depth-anything": CERTIFIED_DEPTH_BACKEND,
}


def canonical_depth_backend(value: str) -> str:
    """Return the one persisted spelling for a supported depth backend."""

    normalized = value.strip().lower().replace("_", "-")
    try:
        return _DEPTH_BACKEND_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(
            "depth backend must be synthetic, cached, monocular-cues, "
            "or video-depth-anything-small"
        ) from exc


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MediaConfig(ConfigModel):
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    target_fps: float = Field(default=24.0, gt=0, le=120)
    target_height: int = Field(default=720, ge=144, le=4320)
    crf: int = Field(default=18, ge=0, le=51)
    working_preset: str = "veryfast"
    preset: str = "medium"
    color_primaries: str = "bt709"


class ShotDetectionConfig(ConfigModel):
    backend: Literal["auto", "scenedetect", "opencv"] = "auto"
    content_threshold: float = Field(default=27.0, ge=0, le=255)
    min_scene_frames: int = Field(default=12, ge=1)
    downscale_width: int = Field(default=320, ge=32, le=1920)


class DepthConfig(ConfigModel):
    backend: str = "synthetic"
    width: int = Field(default=384, ge=32, le=2048)
    height: int = Field(default=216, ge=32, le=2048)
    lower_percentile: float = Field(default=2.0, ge=0, le=49)
    upper_percentile: float = Field(default=98.0, ge=51, le=100)
    temporal_alpha: float = Field(default=0.82, ge=0, le=1)
    spatial_smoothing: float = Field(default=0.15, ge=0, le=1)
    device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    model_path: str | None = None
    # Upstream source tree for a native (non-TorchScript) checkpoint.
    model_source: str | None = None
    chunk_frames: int = Field(default=32, ge=2, le=256)
    chunk_overlap: int = Field(default=4, ge=0, le=64)
    max_shot_frames: int = Field(default=3000, ge=32, le=10000)

    @field_validator("backend")
    @classmethod
    def canonical_backend(cls, value: str) -> str:
        return canonical_depth_backend(value)

    @model_validator(mode="after")
    def valid_chunk_overlap(self) -> DepthConfig:
        if self.chunk_overlap >= self.chunk_frames:
            raise ValueError("depth chunk_overlap must be smaller than chunk_frames")
        return self


class ComfortConfig(ConfigModel):
    max_popout_disparity_norm: float = Field(default=0.004, ge=0, le=0.05)
    max_background_disparity_norm: float = Field(default=0.010, ge=0, le=0.05)
    max_depth_strength: float = Field(default=1.0, ge=0, le=2)
    max_convergence_change: float = Field(default=0.18, ge=0, le=1)
    high_motion_threshold: float = Field(default=0.68, ge=0, le=1)
    high_motion_reduction: float = Field(default=0.72, ge=0, le=1)
    low_reliability_threshold: float = Field(default=0.55, ge=0, le=1)
    unreliable_depth_reduction: float = Field(default=0.60, ge=0, le=1)
    low_confidence_threshold: float = Field(default=0.55, ge=0, le=1)
    edge_margin_fraction: float = Field(default=0.05, ge=0, le=0.25)
    edge_disparity_limit_norm: float = Field(default=0.002, ge=0, le=0.05)
    subtitle_safe: bool = True


class RenderConfig(ConfigModel):
    anaglyph_mode: Literal["basic", "calibrated"] = "calibrated"
    swap_eyes: bool = False
    gamma: float = Field(default=2.2, gt=0.1, le=5)
    saturation: float = Field(default=0.90, ge=0, le=2)
    leakage_compensation: float = Field(default=0.04, ge=0, le=0.5)
    inpaint_radius: int = Field(default=3, ge=0, le=20)
    # Shot previews are for judging depth, not for delivery. Rendering them at a
    # reduced width keeps a whole shot playable in seconds instead of minutes;
    # final output is never scaled.
    preview_max_width: int = Field(default=640, ge=160, le=3840)
    # Dubois-style RGB mixing matrices. Values are configurable for glasses/display calibration.
    left_matrix: list[list[float]] = Field(
        default_factory=lambda: [
            [0.437, 0.449, 0.164],
            [-0.062, -0.062, -0.024],
            [-0.048, -0.050, -0.017],
        ]
    )
    right_matrix: list[list[float]] = Field(
        default_factory=lambda: [
            [-0.011, -0.032, -0.007],
            [0.377, 0.761, 0.009],
            [-0.026, -0.093, 1.234],
        ]
    )

    @field_validator("left_matrix", "right_matrix")
    @classmethod
    def matrix_is_finite_3x3(cls, value: list[list[float]]) -> list[list[float]]:
        if len(value) != 3 or any(len(row) != 3 for row in value):
            raise ValueError("anaglyph matrices must be 3x3")
        if not all(math.isfinite(item) for row in value for item in row):
            raise ValueError("anaglyph matrices must be finite")
        return value


class LLMConfig(ConfigModel):
    enabled: bool = False
    provider: Literal["openai-responses"] = "openai-responses"
    model: str = Field(default="gpt-5.6-terra", min_length=1, max_length=128)
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    fallback: Literal["rules", "neutral"] = "rules"

    @field_validator("base_url")
    @classmethod
    def secure_base_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("LLM base_url must be an HTTPS origin/path without credentials")
        return normalized


class AppConfig(ConfigModel):
    schema_version: str = "1.0"
    media: MediaConfig = Field(default_factory=MediaConfig)
    shots: ShotDetectionConfig = Field(default_factory=ShotDetectionConfig)
    depth: DepthConfig = Field(default_factory=DepthConfig)
    comfort: ComfortConfig = Field(default_factory=ComfortConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def _runtime_file_from_environment(
    environment: Mapping[str, str],
    variable: str,
) -> str | None:
    selected = environment.get(variable)
    if not selected:
        return None
    path = Path(selected).expanduser().resolve()
    if not path.is_file():
        raise ValidationError(
            "Configured runtime dependency does not exist",
            details={"setting": variable},
        )
    return str(path)


def sanitize_runtime_paths(
    config: AppConfig,
    *,
    environment: Mapping[str, str] | None = None,
) -> AppConfig:
    """Replace editable runtime paths with process-trusted selections.

    Project and CLI configuration files are portable creative configuration,
    not an authority for executable or model paths. The desktop host and CLI
    may select those dependencies only through the dedicated environment
    variables. Bare FFmpeg command names remain the development fallback.
    """

    selected_environment = os.environ if environment is None else environment
    settings = config.model_copy(deep=True)
    settings.media.ffmpeg_path = (
        _runtime_file_from_environment(selected_environment, "AISTEREO_FFMPEG_PATH") or "ffmpeg"
    )
    settings.media.ffprobe_path = (
        _runtime_file_from_environment(selected_environment, "AISTEREO_FFPROBE_PATH") or "ffprobe"
    )
    settings.depth.model_path = _runtime_file_from_environment(
        selected_environment, "AISTEREO_DEPTH_MODEL_PATH"
    )
    if settings.depth.backend == CERTIFIED_DEPTH_BACKEND and settings.depth.model_path is None:
        raise ValidationError(
            "Video Depth Anything requires a trusted local model",
            details={"setting": "AISTEREO_DEPTH_MODEL_PATH"},
        )
    return settings


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(
    path: str | Path | None = None, overrides: dict[str, Any] | None = None
) -> AppConfig:
    """Load JSON or YAML configuration.

    Shipped ``.yaml`` files intentionally use JSON syntax (which is valid YAML),
    keeping PyYAML out of the production dependency set. User-authored YAML is
    supported when PyYAML is installed.
    """

    data: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path).expanduser().resolve()
        try:
            text = config_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValidationError(
                f"Cannot read configuration: {config_path}", details={"reason": str(exc)}
            ) from exc
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError as exc:
                raise ValidationError(
                    "Configuration is YAML but PyYAML is not installed",
                    details={"path": str(config_path)},
                ) from exc
            loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ValidationError("Configuration root must be an object")
        data = loaded
    if overrides:
        data = _deep_merge(data, overrides)
    try:
        return AppConfig.model_validate(data)
    except Exception as exc:
        raise ValidationError("Invalid configuration", details={"reason": str(exc)}) from exc


def save_config(config: AppConfig, path: str | Path) -> None:
    from .artifacts import write_json_atomic

    write_json_atomic(path, config.model_dump(mode="json"))
