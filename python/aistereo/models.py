"""Validated configuration-independent JSON artifact models."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import __version__

PIPELINE_STATE_SCHEMA_VERSION = "2.0"
PIPELINE_ALGORITHM_VERSION = "2026.08.01.1"
MAX_MEDIA_DIMENSION = 16_384
# Slightly above UHD 8K (7,680 x 4,320) while keeping one decoded BGR frame
# bounded to a predictable working-set contribution.
MAX_MEDIA_PIXELS = 36 * 1024 * 1024
MAX_NORMALIZED_WIDTH = 8_192
MAX_RENDER_OUTPUT_PIXELS = MAX_MEDIA_PIXELS * 2
MAX_RAW_BGR_FRAME_BYTES = 256 * 1024 * 1024
FASTSTART_CONTAINER_SUFFIXES = frozenset({".mp4", ".mov"})


def validated_bgr_frame_bytes(
    width: int,
    height: int,
    *,
    max_width: int = MAX_MEDIA_DIMENSION,
    max_height: int = MAX_MEDIA_DIMENSION,
    max_pixels: int = MAX_MEDIA_PIXELS,
    context: str = "video frame",
) -> int:
    """Return the BGR24 byte count after validating bounded dimensions."""

    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise ValueError(f"{context} dimensions must be positive integers")
    if width > max_width or height > max_height:
        raise ValueError(
            f"{context} dimensions exceed the supported limit ({max_width}x{max_height})"
        )
    pixels = width * height
    if pixels > max_pixels:
        raise ValueError(f"{context} exceeds the supported pixel limit ({max_pixels})")
    frame_bytes = pixels * 3
    if frame_bytes > MAX_RAW_BGR_FRAME_BYTES:
        raise ValueError(f"{context} exceeds the raw-frame byte limit ({MAX_RAW_BGR_FRAME_BYTES})")
    return frame_bytes


def ffmpeg_faststart_args(output: str | Path) -> list[str]:
    """Return MOV/MP4-only FFmpeg fast-start flags for an output path."""

    if Path(output).suffix.lower() in FASTSTART_CONTAINER_SUFFIXES:
        return ["-movflags", "+faststart"]
    return []


def utc_now_iso() -> str:
    # The supported target is Python 3.11+, but retaining this spelling keeps
    # the lightweight safety suite runnable on the current 3.10 audit host.
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=True)


class AudioStream(ArtifactModel):
    index: int = Field(ge=0)
    codec: str = "unknown"
    channels: int | None = Field(default=None, ge=1)
    sample_rate: int | None = Field(default=None, ge=1)
    language: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)


class MediaInfo(ArtifactModel):
    schema_version: str = "1.0"
    path: str
    width: int = Field(gt=0, le=MAX_MEDIA_DIMENSION)
    height: int = Field(gt=0, le=MAX_MEDIA_DIMENSION)
    frame_rate: float = Field(gt=0, le=1000)
    duration_seconds: float = Field(ge=0)
    frame_count: int = Field(ge=0)
    video_codec: str = "unknown"
    pixel_format: str = "unknown"
    rotation_degrees: int = 0
    variable_frame_rate: bool = False
    audio_streams: list[AudioStream] = Field(default_factory=list)

    @field_validator("frame_rate", "duration_seconds")
    @classmethod
    def finite_numbers(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return value

    @field_validator("rotation_degrees")
    @classmethod
    def normalized_rotation(cls, value: int) -> int:
        normalized = value % 360
        if normalized not in {0, 90, 180, 270}:
            raise ValueError("rotation must be a multiple of 90 degrees")
        return normalized

    @model_validator(mode="after")
    def bounded_decoded_frame(self) -> MediaInfo:
        validated_bgr_frame_bytes(self.width, self.height, context="source video frame")
        return self


class TransitionType(StrEnum):
    CUT = "cut"
    FADE = "fade"
    START = "start"


class Shot(ArtifactModel):
    shot_id: int = Field(ge=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    transition: TransitionType = TransitionType.CUT

    @model_validator(mode="after")
    def ordered_range(self) -> Shot:
        if self.end_frame < self.start_frame:
            raise ValueError("end_frame must be at least start_frame")
        if self.end_time < self.start_time:
            raise ValueError("end_time must be at least start_time")
        return self

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame + 1


class ShotManifest(ArtifactModel):
    schema_version: str = "1.0"
    source_path: str
    frame_rate: float = Field(gt=0)
    frame_count: int = Field(ge=0)
    shots: list[Shot]

    @model_validator(mode="after")
    def contiguous_and_unique(self) -> ShotManifest:
        if self.frame_count == 0 and self.shots:
            raise ValueError("an empty source cannot contain shots")
        expected = 0
        ids: set[int] = set()
        for shot in self.shots:
            if shot.shot_id in ids:
                raise ValueError("shot ids must be unique")
            ids.add(shot.shot_id)
            if shot.start_frame != expected:
                raise ValueError("shots must be contiguous and start at frame zero")
            expected = shot.end_frame + 1
        if self.shots and expected != self.frame_count:
            raise ValueError("shots must cover every source frame")
        return self


class DepthShotMetadata(ArtifactModel):
    shot_id: int = Field(ge=1)
    path: str
    frame_count: int = Field(ge=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    raw_min: float
    raw_max: float
    invalid_fraction: float = Field(ge=0, le=1)
    reliability: float = Field(ge=0, le=1)
    backend: str = "unknown"
    fallback_used: bool = False
    error_code: str | None = None


class DepthManifest(ArtifactModel):
    schema_version: str = "1.0"
    backend: str
    backend_source_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    near_is_one: bool = True
    shots: list[DepthShotMetadata]


class CameraMovement(StrEnum):
    STATIC = "static"
    LATERAL = "lateral"
    VERTICAL = "vertical"
    ZOOM = "zoom"
    UNSTABLE = "unstable"


class ShotFeatures(ArtifactModel):
    shot_id: int = Field(ge=1)
    duration_seconds: float = Field(ge=0)
    motion_score: float = Field(ge=0, le=1)
    speech_ratio: float = Field(ge=0, le=1)
    depth_spread: float = Field(ge=0, le=1)
    foreground_ratio: float = Field(ge=0, le=1)
    brightness: float = Field(ge=0, le=1)
    cut_frequency_context: float = Field(ge=0, le=1)
    camera_movement: CameraMovement = CameraMovement.STATIC
    depth_reliability: float = Field(default=1, ge=0, le=1)


class FeatureManifest(ArtifactModel):
    schema_version: str = "1.0"
    shots: list[ShotFeatures]


class StereoPreset(StrEnum):
    DIALOGUE_SUBTLE = "dialogue_subtle"
    ACTION_CONTROLLED = "action_controlled"
    VISTA_DEEP = "vista_deep"
    CLOSEUP_FLAT = "closeup_flat"
    NEUTRAL = "neutral"


class LLMPresetRecommendation(ArtifactModel):
    preset: StereoPreset
    narrative_importance: float = Field(ge=0, le=1)
    stereo_emphasis: Literal["low", "medium", "high"]
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)


class StereoParameters(ArtifactModel):
    depth_strength: float = Field(ge=0, le=2)
    convergence_depth_percentile: float = Field(ge=0, le=1)
    max_background_disparity_norm: float = Field(ge=0, le=0.05)
    max_popout_disparity_norm: float = Field(ge=0, le=0.05)
    temporal_smoothing: float = Field(ge=0, le=1)
    transition_frames: int = Field(ge=0, le=1000)
    edge_protection: bool = True

    @field_validator(
        "depth_strength",
        "convergence_depth_percentile",
        "max_background_disparity_norm",
        "max_popout_disparity_norm",
        "temporal_smoothing",
    )
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("stereo parameters must be finite")
        return value


class GuardAction(ArtifactModel):
    code: str
    message: str
    requested: float | bool | None = None
    applied: float | bool | None = None


class StereoShot(ArtifactModel):
    shot_id: int = Field(ge=1)
    preset: StereoPreset
    confidence: float = Field(ge=0, le=1)
    parameters: StereoParameters
    requested_parameters: StereoParameters | None = None
    manual_override: bool = False
    guard_actions: list[GuardAction] = Field(default_factory=list)


class StereoScript(ArtifactModel):
    schema_version: str = "1.0"
    video_width: int = Field(gt=0)
    shots: list[StereoShot]
    created_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def unique_shots(self) -> StereoScript:
        ids = [shot.shot_id for shot in self.shots]
        if len(ids) != len(set(ids)):
            raise ValueError("stereo script shot ids must be unique")
        return self


class DraftShotCoverage(ArtifactModel):
    """Bounded sampled-frame coverage for one detected shot."""

    shot_id: int = Field(ge=1)
    sampled_frames: int = Field(ge=1)
    total_frames: int = Field(ge=1)

    @model_validator(mode="after")
    def sampled_frames_fit_shot(self) -> DraftShotCoverage:
        if self.sampled_frames > self.total_frames:
            raise ValueError("sampled_frames cannot exceed total_frames")
        return self


class DraftCoverage(ArtifactModel):
    """Auditable aggregate coverage for representative-frame analysis."""

    shot_ids: list[int]
    sampled_frames: int = Field(ge=0)
    total_frames: int = Field(ge=0)
    per_shot: list[DraftShotCoverage]

    @model_validator(mode="after")
    def exact_aggregate(self) -> DraftCoverage:
        covered_ids = [item.shot_id for item in self.per_shot]
        if self.shot_ids != covered_ids or len(covered_ids) != len(set(covered_ids)):
            raise ValueError("draft coverage must contain each shot exactly once and in order")
        if self.sampled_frames != sum(item.sampled_frames for item in self.per_shot):
            raise ValueError("sampled_frames does not match per-shot coverage")
        if self.total_frames != sum(item.total_frames for item in self.per_shot):
            raise ValueError("total_frames does not match per-shot coverage")
        return self


class DraftAnalysisSnapshot(ArtifactModel):
    """Compact, sampled analysis that can unlock direction before render depth."""

    analysis_tier: Literal["sampled"] = "sampled"
    profile: Literal["representative_frames"] = "representative_frames"
    features: FeatureManifest
    script: StereoScript
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage: DraftCoverage

    @model_validator(mode="after")
    def exact_shot_coverage(self) -> DraftAnalysisSnapshot:
        feature_ids = [item.shot_id for item in self.features.shots]
        script_ids = [item.shot_id for item in self.script.shots]
        if feature_ids != self.coverage.shot_ids or script_ids != self.coverage.shot_ids:
            raise ValueError("draft features and script must exactly cover sampled shots")
        return self


class ShotOverride(ArtifactModel):
    shot_id: int = Field(ge=1)
    preset: StereoPreset
    parameters: StereoParameters


class ApplyShotOverridesRequest(ArtifactModel):
    project_dir: str = Field(min_length=1, max_length=32767)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    overrides: list[ShotOverride] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique_override_shots(self) -> ApplyShotOverridesRequest:
        ids = [item.shot_id for item in self.overrides]
        if len(ids) != len(set(ids)):
            raise ValueError("override shot ids must be unique")
        return self


class ApplyShotOverridesResult(ArtifactModel):
    script: StereoScript
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_shot_ids: list[int]
    script_path: str


class FrameQCMetrics(ArtifactModel):
    frame_index: int = Field(ge=0)
    max_popout_disparity_norm: float = Field(ge=0)
    max_background_disparity_norm: float = Field(ge=0)
    edge_violation_pixels: int = Field(ge=0)
    hole_pixels: int = Field(ge=0)
    largest_hole_pixels: int = Field(ge=0)
    depth_temporal_change: float = Field(ge=0)


class ShotQCSummary(ArtifactModel):
    shot_id: int = Field(ge=1)
    frame_count: int = Field(ge=0)
    max_popout_disparity_norm: float = Field(ge=0)
    max_background_disparity_norm: float = Field(ge=0)
    edge_violations: int = Field(ge=0)
    hole_pixels: int = Field(ge=0)
    largest_hole_pixels: int = Field(ge=0)
    depth_temporal_change: float = Field(ge=0)
    comfort_overrides: list[str] = Field(default_factory=list)
    fallback_used: bool = False


class QCReport(ArtifactModel):
    schema_version: str = "1.0"
    generated_at: str = Field(default_factory=utc_now_iso)
    frame_count: int = Field(ge=0)
    expected_frame_count: int = Field(ge=0)
    dropped_frames: int = Field(ge=0)
    duplicated_frames: int = Field(ge=0)
    audio_video_duration_difference: float | None = None
    max_popout_disparity_norm: float = Field(ge=0)
    max_background_disparity_norm: float = Field(ge=0)
    disparity_histogram: list[int] = Field(default_factory=list)
    disparity_histogram_edges: list[float] = Field(default_factory=list)
    edge_violations: int = Field(ge=0)
    hole_pixels: int = Field(ge=0)
    largest_hole_pixels: int = Field(ge=0)
    depth_temporal_change: float = Field(ge=0)
    shots_with_comfort_overrides: list[int] = Field(default_factory=list)
    shots_using_fallback: list[int] = Field(default_factory=list)
    depth_model_failures: list[int] = Field(default_factory=list)
    shots: list[ShotQCSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    depth_backend: str = "unknown"
    synthetic_depth: bool = False


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageState(ArtifactModel):
    status: StageStatus = StageStatus.PENDING
    fingerprint: str | None = None
    dependency_fingerprint: str | None = None
    outputs: list[str] = Field(default_factory=list)
    output_fingerprints: dict[str, str] = Field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


class PipelineState(ArtifactModel):
    schema_version: str = PIPELINE_STATE_SCHEMA_VERSION
    engine_version: str = __version__
    algorithm_version: str = PIPELINE_ALGORITHM_VERSION
    job_id: str
    updated_at: str = Field(default_factory=utc_now_iso)
    stages: dict[str, StageState] = Field(default_factory=dict)


class ProjectFile(ArtifactModel):
    schema_version: str = "1.0"
    name: str
    input_path: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    app_version: str = "0.1.0"


class WorkerRequest(ArtifactModel):
    id: str = Field(min_length=1, max_length=128)
    method: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def protocol_identifier(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("worker id cannot contain control characters")
        return value


class ProjectSummary(ArtifactModel):
    project_dir: str
    project: ProjectFile
    media: MediaInfo | None = None
    shots: ShotManifest | None = None
    features: FeatureManifest | None = None
    stereo_script: StereoScript | None = None
    draft_analysis: DraftAnalysisSnapshot | None = None
    qc: QCReport | None = None
    pipeline_state: PipelineState | None = None


def portable_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())
