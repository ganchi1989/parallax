"""Resumable project pipeline; all product logic remains in Python."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
import uuid
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, TypeVar

import numpy as np

from . import __version__
from .artifacts import (
    ProjectLayout,
    content_fingerprint,
    fingerprint,
    prepare_output_path,
    read_model,
    replace_atomic,
    resolve_project_artifact,
    stable_file_identity,
    write_json_atomic,
)
from .config import (
    CERTIFIED_DEPTH_BACKEND,
    AppConfig,
    canonical_depth_backend,
    load_config,
    sanitize_runtime_paths,
)
from .depth import (
    MonocularCuesDepthBackend,
    SyntheticDepthBackend,
    create_depth_backend,
    load_depth_shot,
    normalize_depth_shot,
    save_depth_shot,
)
from .depth.base import MAX_DEPTH_ARRAY_BYTES, inspect_depth_shot
from .depth.video_depth_anything import (
    max_frames_for_working_set,
    validate_depth_working_set,
)
from .director.comfort_guard import guard_script_for_render
from .director.llm import LLMDirector, OpenAIResponsesPresetProvider
from .director.overrides import stereo_script_revision
from .director.rules import RuleBasedDirector, create_stereo_script
from .errors import (
    AIStereoError,
    ArtifactError,
    DependencyUnavailableError,
    PipelineCancelled,
    StageError,
    SyntheticDepthFinalError,
    ValidationError,
)
from .features import extract_shot_features
from .media.frames import decode_sampled_shots, decode_shots, representative_frame_indices
from .media.normalize import normalize_media
from .media.probe import inspect_media
from .media.remux import remux_audio
from .models import (
    DepthManifest,
    DepthShotMetadata,
    DraftAnalysisSnapshot,
    DraftCoverage,
    DraftShotCoverage,
    FeatureManifest,
    MediaInfo,
    PipelineState,
    ProjectFile,
    ProjectSummary,
    QCReport,
    ShotFeatures,
    ShotManifest,
    StereoScript,
)
from .qc.report import build_qc_report, write_qc_html
from .render.frame import render_stereo_frame
from .render.still import decode_frame, write_still
from .render.video import OutputMode, interpolate_stereo_parameters, render_video
from .shots import detect_shots
from .state import CancellationToken, PipelineStateStore

ProgressEmitter = Callable[[str, int, int, str | None], None]
ResultT = TypeVar("ResultT")
# Sized so any shot the depth budget admits can also have features extracted;
# a fifth ceiling that disagrees with the others is exactly what caused the
# "depth succeeded, next stage failed" reports.
MAX_FEATURE_WORKING_SET_BYTES = 1024 * 1024 * 1024
MAX_FEATURE_WIDTH = 640
_MIN_FEATURE_EDGE = 16
REPRESENTATIVE_FRAME_LIMIT = 12
REPRESENTATIVE_FRAME_PROFILE = "representative_frames"
REPRESENTATIVE_SAMPLER_VERSION = "representative-pairs-v1"
_CACHE_COPY_CHUNK_BYTES = 1024 * 1024
# Fallback depth comes from image analysis rather than a certified model. It
# carries real scene structure, so the Director must not treat it as noise, but
# it stays under the certified tier and never unlocks a production export.
FALLBACK_DEPTH_RELIABILITY = 0.62
# Stage fingerprints include this, so depth cached by an earlier fallback
# implementation is recomputed instead of silently reused.
FALLBACK_DEPTH_IDENTITY = f"{MonocularCuesDepthBackend.name}-v1"
# Backends whose depth measures the actual picture. Synthetic depth is excluded
# on purpose: it is a fixed pattern, so a render made from it carries no stereo.
RELEASE_DEPTH_BACKENDS = frozenset({CERTIFIED_DEPTH_BACKEND, MonocularCuesDepthBackend.name})
FileIdentity = tuple[int, int, int, int, int]
CachedDepthSource = tuple[Path, tuple[int, int, int], str, FileIdentity, int]


class _CachedDepthSourceChanged(ArtifactError):
    """Raised when a cached artifact no longer matches its staged identity."""


def _is_at_or_below(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _file_identity(stat: os.stat_result) -> FileIdentity:
    return stable_file_identity(stat)


def _preflight_cached_source(path: Path) -> tuple[str, FileIdentity]:
    try:
        before = _file_identity(path.stat())
        source_fingerprint = content_fingerprint(path)
        after = _file_identity(path.stat())
    except ArtifactError:
        raise
    except OSError as exc:
        raise ArtifactError(
            "Could not fingerprint cached depth source",
            details={"path": str(path), "reason": str(exc)},
        ) from exc
    if before != after:
        raise _CachedDepthSourceChanged(
            "Cached depth source changed during preflight",
            details={"path": str(path)},
        )
    return source_fingerprint, after


def _assert_cached_source_fingerprint(
    path: Path,
    expected_fingerprint: str,
    *,
    phase: str,
) -> None:
    try:
        actual_fingerprint = content_fingerprint(path)
    except ArtifactError as exc:
        raise _CachedDepthSourceChanged(
            "Cached depth source changed while it was being imported",
            details={"path": str(path), "phase": phase},
        ) from exc
    if actual_fingerprint != expected_fingerprint:
        raise _CachedDepthSourceChanged(
            "Cached depth source changed while it was being imported",
            details={"path": str(path), "phase": phase},
        )


def _load_verified_cached_depth(
    source: CachedDepthSource,
    *,
    snapshot_dir: Path,
) -> np.ndarray:
    path, expected_shape, expected_fingerprint, expected_identity, max_array_bytes = source
    snapshot: Path | None = None
    try:
        try:
            with path.open("rb") as source_handle:
                opened_identity = _file_identity(os.fstat(source_handle.fileno()))
                if opened_identity != expected_identity:
                    raise _CachedDepthSourceChanged(
                        "Cached depth source changed while it was being imported",
                        details={"path": str(path)},
                    )
                digest = hashlib.sha256()
                copied = 0
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{path.stem}.",
                    suffix=".import.npz",
                    dir=snapshot_dir,
                    delete=False,
                ) as snapshot_handle:
                    snapshot = Path(snapshot_handle.name)
                    remaining = expected_identity[2]
                    while remaining:
                        chunk = source_handle.read(min(_CACHE_COPY_CHUNK_BYTES, remaining))
                        if not chunk:
                            break
                        snapshot_handle.write(chunk)
                        digest.update(chunk)
                        copied += len(chunk)
                        remaining -= len(chunk)
                    has_trailing_data = bool(source_handle.read(1))
                    snapshot_handle.flush()
                    os.fsync(snapshot_handle.fileno())
                finished_identity = _file_identity(os.fstat(source_handle.fileno()))
                current_identity = _file_identity(path.stat())
        except _CachedDepthSourceChanged:
            raise
        except OSError as exc:
            raise _CachedDepthSourceChanged(
                "Cached depth source changed while it was being imported",
                details={"path": str(path), "reason": str(exc)},
            ) from exc
        if (
            opened_identity != finished_identity
            or opened_identity != current_identity
            or copied != expected_identity[2]
            or has_trailing_data
            or digest.hexdigest() != expected_fingerprint
        ):
            raise _CachedDepthSourceChanged(
                "Cached depth source changed while it was being imported",
                details={"path": str(path)},
            )
        _assert_cached_source_fingerprint(
            snapshot,
            expected_fingerprint,
            phase="before_snapshot_load",
        )
        try:
            return load_depth_shot(
                snapshot,
                expected_shape=expected_shape,
                max_array_bytes=max_array_bytes,
            )
        finally:
            _assert_cached_source_fingerprint(
                snapshot,
                expected_fingerprint,
                phase="after_snapshot_load",
            )
    finally:
        if snapshot is not None:
            with suppress(OSError):
                snapshot.unlink(missing_ok=True)


def _feature_frame_size(width: int, height: int, downscale_width: int) -> tuple[int, int]:
    """Feature frame size bounded by area, not by width alone.

    Bounding only the width silently triples the cost of portrait footage: a
    320x567 phone video is already narrower than the target width, so no
    reduction happened and the frame stayed full height. Scaling to a fixed
    pixel budget keeps portrait and landscape equally cheap and leaves the
    landscape result identical to the previous width-only formula.
    """

    if width <= 0 or height <= 0:
        raise ValidationError(
            "Feature frame size needs positive media dimensions",
            details={"width": width, "height": height},
        )
    target_width = max(1, min(downscale_width, width, MAX_FEATURE_WIDTH))
    budget_pixels = target_width * max(1, round(target_width * 9 / 16))
    scale = min(1.0, math.sqrt(budget_pixels / float(width * height)))
    return (
        max(_MIN_FEATURE_EDGE, round(width * scale)),
        max(_MIN_FEATURE_EDGE, round(height * scale)),
    )


def _validate_feature_working_set(
    frame_count: int,
    height: int,
    width: int,
    *,
    shot_id: int,
) -> None:
    estimated = frame_count * height * width * 4
    if estimated > MAX_FEATURE_WORKING_SET_BYTES:
        raise ValidationError(
            "Shot exceeds the bounded feature working-set limit",
            details={
                "shot_id": shot_id,
                "estimated_bytes": estimated,
                "max_bytes": MAX_FEATURE_WORKING_SET_BYTES,
                "feature_width": width,
                "feature_height": height,
                "frames": frame_count,
            },
        )


def _noop_progress(stage: str, completed: int, total: int, message: str | None) -> None:
    del stage, completed, total, message


class AIStereoPipeline:
    def __init__(
        self,
        project_dir: str | Path,
        *,
        config: AppConfig | None = None,
        job_id: str | None = None,
        progress: ProgressEmitter | None = None,
        cancellation: CancellationToken | None = None,
        resume: bool = True,
        force_stages: set[str] | None = None,
    ) -> None:
        self.layout = ProjectLayout.at(project_dir).ensure()
        if config is not None:
            self.config = config
        else:
            persisted = (
                read_model(self.layout.config, AppConfig)
                if self.layout.config.exists()
                else AppConfig()
            )
            self.config = sanitize_runtime_paths(persisted)
        self.job_id = job_id or str(uuid.uuid4())
        self.progress = progress or _noop_progress
        self.cancellation = cancellation or CancellationToken()
        self.resume = resume
        self.force_stages = force_stages or set()
        self.state = PipelineStateStore(self.layout, self.job_id)

    @classmethod
    def create(
        cls,
        project_dir: str | Path,
        input_path: str | Path,
        *,
        name: str | None = None,
        config: AppConfig | None = None,
        job_id: str | None = None,
        progress: ProgressEmitter | None = None,
        cancellation: CancellationToken | None = None,
    ) -> AIStereoPipeline:
        source = Path(input_path).expanduser().resolve()
        if not source.is_file():
            raise ValidationError("Input video does not exist", details={"path": str(source)})
        layout = ProjectLayout.at(project_dir).ensure()
        project_exists = layout.project.exists()
        if project_exists:
            existing = read_model(layout.project, ProjectFile)
            if Path(existing.input_path).resolve() != source:
                raise ValidationError(
                    "Project already references a different source",
                    details={"project_dir": str(layout.root)},
                )
            if name is not None and name != existing.name:
                raise ValidationError(
                    "Project already has a different name",
                    details={"project_dir": str(layout.root)},
                )
        else:
            project = ProjectFile(
                name=name or source.stem,
                input_path=str(source),
                app_version=__version__,
            )
            write_json_atomic(layout.project, project)
        if config is not None or not project_exists or not layout.config.exists():
            write_json_atomic(layout.config, config or AppConfig())
        return cls(
            layout.root,
            config=config,
            job_id=job_id,
            progress=progress,
            cancellation=cancellation,
        )

    @property
    def project(self) -> ProjectFile:
        if not self.layout.project.exists():
            raise ValidationError(
                "Project is not initialized", details={"path": str(self.layout.root)}
            )
        return read_model(self.layout.project, ProjectFile)

    def _cancelled(self) -> bool:
        return self.cancellation.is_cancelled()

    def _execute_stage(
        self,
        name: str,
        stage_fingerprint: str,
        outputs: list[Path],
        operation: Callable[[], ResultT],
        load_cached: Callable[[], ResultT],
        *,
        dependency_fingerprint: str | None = None,
    ) -> ResultT:
        if (
            self.resume
            and name not in self.force_stages
            and self.state.can_resume(name, stage_fingerprint, outputs)
        ):
            # A valid cache index only proves that the declared files match the
            # recorded fingerprints. Loading still performs schema and
            # cross-artifact validation, so do not advertise 100% until that
            # boundary has succeeded and the result is ready to publish.
            self.progress(name, 0, 1, "Validating cached stage output")
            cached = load_cached()
            self.progress(name, 1, 1, "Using cached stage output")
            return cached
        self.state.begin(
            name,
            stage_fingerprint,
            dependency_fingerprint=dependency_fingerprint,
        )
        try:
            if self._cancelled():
                raise PipelineCancelled(f"{name} was cancelled")
            result = operation()
            self.state.complete(name, outputs)
            return result
        except PipelineCancelled as exc:
            self.state.fail(name, exc.message, cancelled=True)
            raise
        except Exception as exc:
            self.state.fail(name, str(exc), cancelled=False)
            raise

    def inspect(self) -> MediaInfo:
        project = self.project
        source = Path(project.input_path)
        stage_hash = fingerprint(
            [source, self.config.media.ffprobe_path, "header-only-source-probe-v1"]
        )

        def operation() -> MediaInfo:
            self.progress("inspect", 0, 1, "Inspecting source media")
            # Exact decoded frame coverage is established on the normalized CFR
            # artifact. The initial source probe stays header-only so importing a
            # 4K source does not decode it once merely to decode it again during
            # normalization.
            media = inspect_media(
                source,
                ffprobe_path=self.config.media.ffprobe_path,
                count_frames=False,
            )
            write_json_atomic(self.layout.media, media)
            self.progress("inspect", 1, 1, "Source inspection complete")
            return media

        return self._execute_stage(
            "inspect",
            stage_hash,
            [self.layout.media],
            operation,
            lambda: read_model(self.layout.media, MediaInfo),
        )

    def normalize(self) -> MediaInfo:
        project = self.project
        source_media = self.inspect()
        stage_hash = fingerprint(
            [
                Path(project.input_path),
                self.config.media.model_dump_json(),
                source_media.model_dump_json(),
            ]
        )
        outputs = [self.layout.normalized_video, self.layout.normalized_media]
        if source_media.audio_streams:
            outputs.append(self.layout.audio)

        def operation() -> MediaInfo:
            self.progress("normalize", 0, 1, "Creating a constant-frame-rate intermediate")
            normalize_media(
                project.input_path,
                self.layout.normalized_video,
                self.layout.audio,
                source_media,
                self.config.media,
                cancel=self._cancelled,
            )
            normalized = inspect_media(
                self.layout.normalized_video, ffprobe_path=self.config.media.ffprobe_path
            )
            write_json_atomic(self.layout.normalized_media, normalized)
            self.progress("normalize", 1, 1, "Media normalization complete")
            return normalized

        return self._execute_stage(
            "normalize",
            stage_hash,
            outputs,
            operation,
            lambda: read_model(self.layout.normalized_media, MediaInfo),
        )

    def detect_shots(self) -> ShotManifest:
        media = self.normalize()
        stage_hash = fingerprint(
            [
                self.layout.normalized_video,
                self.config.shots.model_dump_json(),
                media.model_dump_json(),
            ]
        )

        def operation() -> ShotManifest:
            manifest = detect_shots(
                self.layout.normalized_video,
                frame_count=media.frame_count,
                frame_rate=media.frame_rate,
                config=self.config.shots,
                progress=lambda done, total: self.progress("detect_shots", done, total, None),
                cancel=self._cancelled,
            )
            write_json_atomic(self.layout.shots, manifest)
            return manifest

        return self._execute_stage(
            "detect_shots",
            stage_hash,
            [self.layout.shots],
            operation,
            lambda: read_model(self.layout.shots, ShotManifest),
        )

    def _compose_draft_analysis(
        self, snapshot: DraftAnalysisSnapshot | None = None
    ) -> DraftAnalysisSnapshot:
        """Return draft provenance paired with the latest editable canonical script."""

        persisted = snapshot or read_model(self.layout.draft_analysis, DraftAnalysisSnapshot)
        script = persisted.script
        if self.layout.stereo_script.is_file():
            script = read_model(self.layout.stereo_script, StereoScript)
        expected_ids = persisted.coverage.shot_ids
        script_ids = [item.shot_id for item in script.shots]
        if script_ids != expected_ids or script.video_width != persisted.script.video_width:
            raise ValidationError(
                "Canonical stereo script does not match the sampled draft",
                details={
                    "expected_shot_ids": expected_ids,
                    "script_shot_ids": script_ids,
                },
            )
        return persisted.model_copy(
            update={"script": script, "revision": stereo_script_revision(script)},
            deep=True,
        )

    def analyze_draft(
        self,
        *,
        profile: str = REPRESENTATIVE_FRAME_PROFILE,
        allow_fallback: bool = True,
    ) -> DraftAnalysisSnapshot:
        """Build a bounded representative-frame Director draft for every shot.

        This stage is deliberately separate from production depth. It never
        writes to ``depth/`` and therefore cannot satisfy preview or release
        depth validation.
        """

        if profile != REPRESENTATIVE_FRAME_PROFILE:
            raise ValidationError(
                "Unknown draft analysis profile",
                details={
                    "profile": profile,
                    "available": [REPRESENTATIVE_FRAME_PROFILE],
                },
            )
        media = self.normalize()
        shots = self.detect_shots()
        frame_plan = {
            shot.shot_id: representative_frame_indices(shot, REPRESENTATIVE_FRAME_LIMIT)
            for shot in shots.shots
        }
        coverage = DraftCoverage(
            shot_ids=[shot.shot_id for shot in shots.shots],
            sampled_frames=sum(len(indexes) for indexes in frame_plan.values()),
            total_frames=shots.frame_count,
            per_shot=[
                DraftShotCoverage(
                    shot_id=shot.shot_id,
                    sampled_frames=len(frame_plan[shot.shot_id]),
                    total_frames=shot.frame_count,
                )
                for shot in shots.shots
            ],
        )
        try:
            backend_name = canonical_depth_backend(self.config.depth.backend)
        except ValueError as exc:
            raise ValidationError(
                "Unknown depth backend", details={"backend": self.config.depth.backend}
            ) from exc
        model_identity = (
            Path(self.config.depth.model_path)
            if self.config.depth.model_path and backend_name == CERTIFIED_DEPTH_BACKEND
            else None
        )
        stage_hash = fingerprint(
            [
                self.layout.normalized_video,
                self.layout.shots,
                self._depth_stage_identity(),
                model_identity,
                profile,
                REPRESENTATIVE_FRAME_LIMIT,
                REPRESENTATIVE_SAMPLER_VERSION,
                allow_fallback,
                FALLBACK_DEPTH_IDENTITY if allow_fallback else None,
            ]
        )

        def operation() -> DraftAnalysisSnapshot:
            output: list[ShotFeatures] = []
            edge_violations: set[int] = set()
            backend_error: Exception | None = None
            backend: object | None = None
            backend_kwargs: dict[str, Any] = {}
            if backend_name == CERTIFIED_DEPTH_BACKEND:
                backend_kwargs["model_path"] = self.config.depth.model_path
            if backend_name == "cached":
                if not allow_fallback:
                    raise ValidationError(
                        "Cached depth cannot supply representative draft frames without a cache"
                    )
                backend_error = DependencyUnavailableError(
                    "Cached depth is unavailable for representative draft analysis"
                )
            else:
                backend = create_depth_backend(backend_name, **backend_kwargs)

            # Sparse representatives are not a temporal sequence. Explicitly
            # disable normalization carry between distant sampled positions.
            draft_depth_config = self.config.depth.model_copy(update={"temporal_alpha": 0.0})
            processed = 0
            decoded_samples = 0
            depth_processed = 0
            # Reserve one final unit for the atomic artifact/state commit.
            # The result event, not an early 100% progress event, is the only
            # authority that unlocks the Director controls.
            total_work = coverage.sampled_frames * 3 + 1
            fallback_backend: MonocularCuesDepthBackend | None = None

            def report_decode_progress(done: int, total: int) -> None:
                nonlocal decoded_samples
                if total <= 0:
                    return
                decoded_samples = max(
                    decoded_samples,
                    min(coverage.sampled_frames, max(0, int(done))),
                )
                self.progress(
                    "analyze_draft",
                    decoded_samples + depth_processed + processed,
                    total_work,
                    (
                        "Reading representative frames "
                        f"({decoded_samples}/{coverage.sampled_frames})"
                    ),
                )

            def depth_progress_reporter(
                *, frame_count: int, shot_id: int, progress_base: int
            ) -> Callable[[int, int], None]:
                current_depth_progress = 0

                def report(done: int, total: int) -> None:
                    nonlocal current_depth_progress
                    if total <= 0:
                        return
                    bounded_done = min(max(0, int(done)), int(total))
                    mapped = (bounded_done * frame_count + int(total) - 1) // int(total)
                    current_depth_progress = max(current_depth_progress, mapped)
                    self.progress(
                        "analyze_draft",
                        progress_base + current_depth_progress,
                        total_work,
                        f"Estimating depth for shot {shot_id}",
                    )

                return report

            groups: Iterable[tuple[Any, tuple[int, ...], list[np.ndarray]]]
            if coverage.sampled_frames:
                self.progress(
                    "analyze_draft",
                    0,
                    total_work,
                    f"Preparing {coverage.sampled_frames} representative frames",
                )
                groups = decode_sampled_shots(
                    self.layout.normalized_video,
                    media,
                    shots,
                    frame_plan,
                    ffmpeg_path=self.config.media.ffmpeg_path,
                    cancel=self._cancelled,
                    progress=report_decode_progress,
                    output_size=(draft_depth_config.height, draft_depth_config.width),
                )
            else:
                groups = ()
            for shot, indexes, frames in groups:
                if self._cancelled():
                    raise PipelineCancelled("Draft analysis was cancelled")
                # A yielded group proves these samples were decoded even when a
                # test or alternative decoder does not implement progress.
                decoded_samples = max(decoded_samples, processed + len(frames))
                self.progress(
                    "analyze_draft",
                    decoded_samples + depth_processed + processed,
                    total_work,
                    f"Estimating depth for shot {shot.shot_id}",
                )
                fallback = backend_error is not None
                selected_backend = None if backend_error is not None else backend
                report_depth_progress = depth_progress_reporter(
                    frame_count=len(frames),
                    shot_id=shot.shot_id,
                    progress_base=decoded_samples + depth_processed + processed,
                )

                try:
                    if selected_backend is None:
                        assert backend_error is not None
                        raise backend_error
                    raw = selected_backend.estimate(  # type: ignore[attr-defined]
                        frames,
                        shot_id=shot.shot_id,
                        config=draft_depth_config,
                        progress=report_depth_progress,
                        cancel=self._cancelled,
                    )
                except PipelineCancelled:
                    raise
                except Exception as exc:
                    if not allow_fallback or backend_name == "synthetic":
                        raise
                    fallback = True
                    if isinstance(exc, DependencyUnavailableError):
                        backend_error = exc
                    if fallback_backend is None:
                        # Image-analysis depth, not a constant plane: a draft
                        # that cannot show parallax teaches the Director nothing.
                        fallback_backend = MonocularCuesDepthBackend()
                    raw = fallback_backend.estimate(
                        frames,
                        shot_id=shot.shot_id,
                        config=draft_depth_config,
                        progress=report_depth_progress,
                        cancel=self._cancelled,
                    )
                depth_processed += len(frames)
                self.progress(
                    "analyze_draft",
                    decoded_samples + depth_processed + processed,
                    total_work,
                    f"Finalizing features for shot {shot.shot_id}",
                )
                raw_value = np.asarray(raw)
                if raw_value.ndim != 3 or raw_value.shape[0] != len(frames):
                    raise ValidationError(
                        "Draft depth backend returned invalid sampled coverage",
                        details={
                            "shot_id": shot.shot_id,
                            "expected_frames": len(frames),
                            "actual_shape": list(raw_value.shape),
                        },
                    )
                normalized, stats = normalize_depth_shot(raw_value, draft_depth_config)
                if fallback:
                    stats["reliability"] = min(
                        stats["reliability"], FALLBACK_DEPTH_RELIABILITY
                    )
                margin = max(
                    1,
                    round(normalized.shape[2] * self.config.comfort.edge_margin_fraction),
                )
                edge_near = np.concatenate(
                    [normalized[:, :, :margin], normalized[:, :, -margin:]], axis=2
                )
                if float(np.mean(edge_near >= 0.75)) >= 0.02:
                    edge_violations.add(shot.shot_id)
                output.append(
                    extract_shot_features(
                        shot,
                        frames,
                        normalized,
                        frame_indexes=indexes,
                        depth_reliability=float(stats["reliability"]),
                    )
                )
                processed += len(frames)
                self.progress(
                    "analyze_draft",
                    decoded_samples + depth_processed + processed,
                    total_work,
                    f"Shot {shot.shot_id} · {len(frames)} representative frames",
                )
            if coverage.sampled_frames:
                self.progress(
                    "analyze_draft",
                    coverage.sampled_frames * 3,
                    total_work,
                    "Finalizing the validated Director draft",
                )
            features = FeatureManifest(shots=output)
            generated = create_stereo_script(
                features,
                video_width=media.width,
                comfort=self.config.comfort,
                director=RuleBasedDirector(),
                edge_violations=edge_violations,
                hard_cut_shot_ids={
                    shot.shot_id for shot in shots.shots if shot.transition != "fade"
                },
            )
            existing = (
                read_model(self.layout.stereo_script, StereoScript)
                if self.layout.stereo_script.is_file()
                else None
            )
            script = self._carry_manual_overrides(existing, generated)
            revision = stereo_script_revision(script)
            snapshot = DraftAnalysisSnapshot(
                features=features,
                script=script,
                revision=revision,
                coverage=coverage,
            )
            write_json_atomic(self.layout.stereo_script, script)
            write_json_atomic(self.layout.draft_analysis, snapshot)
            if coverage.sampled_frames == 0:
                self.progress("analyze_draft", 0, 0, "No video frames to analyze")
            return snapshot

        return self._execute_stage(
            "analyze_draft",
            stage_hash,
            [self.layout.draft_analysis],
            operation,
            lambda: self._compose_draft_analysis(),
        )

    def estimate_depth(
        self,
        *,
        backend_name: str | None = None,
        cache_dir: str | Path | None = None,
        allow_fallback: bool = False,
    ) -> DepthManifest:
        media = self.normalize()
        shots = self.detect_shots()
        try:
            name = canonical_depth_backend(backend_name or self.config.depth.backend)
        except ValueError as exc:
            raise ValidationError(
                "Unknown depth backend",
                details={"backend": backend_name or self.config.depth.backend},
            ) from exc
        model_identity: Path | None = None
        cache_identity: str | None = None
        cache_root: Path | None = None
        cached_sources: dict[int, CachedDepthSource] = {}
        if self.config.depth.model_path and name == CERTIFIED_DEPTH_BACKEND:
            model_identity = Path(self.config.depth.model_path)
        # One limit, derived from the memory budget, so this pre-flight and the
        # backend's own working-set check can never disagree about a shot.
        frame_limit = min(
            self.config.depth.max_shot_frames,
            max_frames_for_working_set(self.config.depth),
        )
        oversized = [shot.shot_id for shot in shots.shots if shot.frame_count > frame_limit]
        if oversized:
            raise ValidationError(
                "One or more shots exceed the bounded-memory inference limit",
                details={
                    "shot_ids": oversized,
                    "max_shot_frames": frame_limit,
                    "longest_shot_frames": max(shot.frame_count for shot in shots.shots),
                },
            )
        if name == "cached":
            if cache_dir is not None:
                try:
                    cache_root = Path(cache_dir).expanduser().resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise ValidationError(
                        "Cached depth directory does not exist",
                        details={"cache_dir": str(cache_dir)},
                    ) from exc
                if not cache_root.is_dir():
                    raise ValidationError(
                        "Cached depth directory does not exist",
                        details={"cache_dir": str(cache_root)},
                    )
                depth_output_root = self.layout.depth_dir.resolve(strict=True)
                if _is_at_or_below(cache_root, depth_output_root):
                    raise ValidationError(
                        "Cached depth input must be outside the project's depth output",
                        details={
                            "cache_dir": str(cache_root),
                            "depth_output": str(depth_output_root),
                        },
                    )
                cache_identity_parts: list[str | int] = [str(cache_root)]
                for shot in shots.shots:
                    supplied_path = cache_root / f"shot_{shot.shot_id:04d}.npz"
                    try:
                        cache_path = supplied_path.resolve(strict=True)
                    except (OSError, RuntimeError) as exc:
                        raise ValidationError(
                            "Cached depth file does not exist",
                            details={"path": str(supplied_path)},
                        ) from exc
                    if not cache_path.is_file():
                        raise ValidationError(
                            "Cached depth source is not a file",
                            details={"path": str(cache_path)},
                        )
                    if _is_at_or_below(cache_path, depth_output_root):
                        raise ValidationError(
                            "Cached depth input must be outside the project's depth output",
                            details={
                                "path": str(cache_path),
                                "depth_output": str(depth_output_root),
                            },
                        )
                    destination = depth_output_root / supplied_path.name
                    try:
                        aliases_destination = destination.exists() and cache_path.samefile(
                            destination
                        )
                    except OSError as exc:
                        raise ValidationError(
                            "Could not validate cached depth source path",
                            details={"path": str(cache_path)},
                        ) from exc
                    if aliases_destination:
                        raise ValidationError(
                            "Cached depth source aliases the project's depth output",
                            details={
                                "path": str(cache_path),
                                "depth_output": str(destination),
                            },
                        )
                    max_array_bytes = min(
                        MAX_DEPTH_ARRAY_BYTES,
                        max(1, shot.frame_count)
                        * self.config.depth.height
                        * self.config.depth.width
                        * 8,
                    )
                    shape = inspect_depth_shot(
                        cache_path,
                        max_array_bytes=max_array_bytes,
                    )
                    if shape[0] != shot.frame_count:
                        raise ValidationError(
                            "Cached depth frame count does not match the shot",
                            details={
                                "shot_id": shot.shot_id,
                                "expected": shot.frame_count,
                                "actual": shape[0],
                            },
                        )
                    if shape[1] > self.config.depth.height or shape[2] > self.config.depth.width:
                        raise ValidationError(
                            "Cached depth dimensions exceed the configured depth resolution",
                            details={
                                "shot_id": shot.shot_id,
                                "height": shape[1],
                                "width": shape[2],
                                "max_height": self.config.depth.height,
                                "max_width": self.config.depth.width,
                            },
                        )
                    actual_shape_config = self.config.depth.model_copy(
                        update={"height": shape[1], "width": shape[2]}
                    )
                    validate_depth_working_set(
                        shot.frame_count,
                        actual_shape_config,
                        shot_id=shot.shot_id,
                    )
                    source_fingerprint, source_identity = _preflight_cached_source(cache_path)
                    cached_sources[shot.shot_id] = (
                        cache_path,
                        shape,
                        source_fingerprint,
                        source_identity,
                        max_array_bytes,
                    )
                    cache_identity_parts.extend([shot.shot_id, str(cache_path), source_fingerprint])
                cache_identity = fingerprint(cache_identity_parts)
            elif self.layout.depth_metadata.is_file():
                try:
                    existing_manifest = read_model(self.layout.depth_metadata, DepthManifest)
                except AIStereoError:
                    existing_manifest = None
                if existing_manifest is not None and existing_manifest.backend in {
                    "cached",
                    "precomputed",
                }:
                    cache_identity = existing_manifest.backend_source_fingerprint
        else:
            for shot in shots.shots:
                validate_depth_working_set(
                    shot.frame_count,
                    self.config.depth,
                    shot_id=shot.shot_id,
                )
        stage_hash = fingerprint(
            [
                self.layout.normalized_video,
                self.layout.shots,
                self._depth_stage_identity(),
                name,
                model_identity,
                cache_identity,
                allow_fallback,
                FALLBACK_DEPTH_IDENTITY if allow_fallback else None,
            ]
        )
        depth_outputs = [
            self.layout.depth_metadata,
            *(self.layout.depth_dir / f"shot_{shot.shot_id:04d}.npz" for shot in shots.shots),
        ]

        def operation() -> DepthManifest:
            kwargs: dict[str, Any] = {}
            if name == "cached":
                if cache_root is None:
                    raise ValidationError(
                        "Cached depth must be re-supplied because the existing stage is stale"
                    )
                kwargs["cache_dir"] = cache_root
            if name == CERTIFIED_DEPTH_BACKEND:
                kwargs["model_path"] = self.config.depth.model_path
            backend = create_depth_backend(name, **kwargs)
            metadata_items: list[DepthShotMetadata] = []
            if name in {"synthetic", "cached"}:
                groups: Iterable[tuple[Any, list[np.ndarray]]] = (
                    (shot, [np.zeros((1, 1, 3), dtype=np.uint8)] * shot.frame_count)
                    for shot in shots.shots
                )
            else:
                groups = decode_shots(
                    self.layout.normalized_video,
                    media,
                    shots,
                    ffmpeg_path=self.config.media.ffmpeg_path,
                    cancel=self._cancelled,
                    output_size=(self.config.depth.height, self.config.depth.width),
                )
            processed = 0
            for shot, frames in groups:
                selected_backend = backend
                fallback = False
                error_code = None
                current_shot_progress = 0

                def report_shot_depth(
                    done: int,
                    total: int,
                    *,
                    shot_frame_count: int = shot.frame_count,
                    shot_id: int = shot.shot_id,
                    processed_before: int = processed,
                ) -> None:
                    nonlocal current_shot_progress
                    if total <= 0:
                        return
                    bounded_total = max(1, int(total))
                    bounded_done = min(max(0, int(done)), bounded_total)
                    mapped = (
                        bounded_done * shot_frame_count + bounded_total - 1
                    ) // bounded_total
                    current_shot_progress = max(current_shot_progress, mapped)
                    self.progress(
                        "estimate_depth",
                        processed_before + current_shot_progress,
                        shots.frame_count,
                        (
                            f"Shot {shot_id} · depth "
                            f"{current_shot_progress}/{shot_frame_count}"
                        ),
                    )

                try:
                    if name == "cached":
                        if self._cancelled():
                            raise PipelineCancelled("Depth estimation was cancelled")
                        raw = _load_verified_cached_depth(
                            cached_sources[shot.shot_id],
                            snapshot_dir=self.layout.root,
                        )
                    else:
                        raw = selected_backend.estimate(
                            frames,
                            shot_id=shot.shot_id,
                            config=self.config.depth,
                            progress=report_shot_depth,
                            cancel=self._cancelled,
                        )
                except PipelineCancelled:
                    raise
                except _CachedDepthSourceChanged:
                    raise
                except Exception as exc:
                    if not allow_fallback or name == "synthetic":
                        raise
                    fallback = True
                    error_code = exc.code if isinstance(exc, AIStereoError) else type(exc).__name__
                    selected_backend = MonocularCuesDepthBackend()
                    raw = selected_backend.estimate(
                        frames,
                        shot_id=shot.shot_id,
                        config=self.config.depth,
                        progress=report_shot_depth,
                        cancel=self._cancelled,
                    )
                normalized, stats = normalize_depth_shot(raw, self.config.depth)
                if fallback:
                    stats["reliability"] = min(
                        stats["reliability"], FALLBACK_DEPTH_RELIABILITY
                    )
                path = self.layout.depth_dir / f"shot_{shot.shot_id:04d}.npz"
                save_depth_shot(path, normalized, raw=raw, metadata=stats)
                metadata_items.append(
                    DepthShotMetadata(
                        shot_id=shot.shot_id,
                        path=str(path.relative_to(self.layout.root)),
                        frame_count=normalized.shape[0],
                        width=normalized.shape[2],
                        height=normalized.shape[1],
                        raw_min=stats["raw_min"],
                        raw_max=stats["raw_max"],
                        invalid_fraction=stats["invalid_fraction"],
                        reliability=stats["reliability"],
                        backend=selected_backend.name,
                        fallback_used=fallback,
                        error_code=error_code,
                    )
                )
                current = DepthManifest(
                    backend=name,
                    backend_source_fingerprint=cache_identity,
                    shots=metadata_items,
                )
                write_json_atomic(self.layout.depth_metadata, current)
                processed += shot.frame_count
                self.progress(
                    "estimate_depth", processed, shots.frame_count, f"Shot {shot.shot_id}"
                )
            return DepthManifest(
                backend=name,
                backend_source_fingerprint=cache_identity,
                shots=metadata_items,
            )

        return self._execute_stage(
            "estimate_depth",
            stage_hash,
            depth_outputs,
            operation,
            lambda: self.validate_depth_artifact(shots=shots),
        )

    def _depth_stage_identity(self) -> str:
        """Depth settings that actually change the result.

        Model file locations are deliberately excluded: they vary between
        machines and operators, and the model itself is already pinned by the
        separate model-identity entry. Including them made every existing
        project look stale as soon as a path moved.
        """

        return self.config.depth.model_dump_json(exclude={"model_path", "model_source"})

    def validate_depth_artifact(
        self,
        *,
        shots: ShotManifest | None = None,
        for_release: bool = False,
        require_current_stage: bool = False,
    ) -> DepthManifest:
        """Validate manifest provenance, coverage, containment, and every depth array."""

        shot_manifest = shots or read_model(self.layout.shots, ShotManifest)
        manifest = read_model(self.layout.depth_metadata, DepthManifest)
        expected_ids = [shot.shot_id for shot in shot_manifest.shots]
        actual_ids = [item.shot_id for item in manifest.shots]
        if actual_ids != expected_ids:
            raise ValidationError(
                "Depth manifest does not exactly cover the shot manifest",
                details={"expected_shot_ids": expected_ids, "actual_shot_ids": actual_ids},
            )
        if for_release and manifest.backend not in RELEASE_DEPTH_BACKENDS:
            raise ValidationError(
                "Final export requires a measured depth backend",
                details={"backend": manifest.backend, "allowed": sorted(RELEASE_DEPTH_BACKENDS)},
            )
        paths: list[Path] = []
        shots_by_id = {shot.shot_id: shot for shot in shot_manifest.shots}
        for item in manifest.shots:
            # A shot that fell back still has to have fallen back to measured
            # depth. What is never allowed is a shot with no usable depth at all.
            if for_release and item.backend not in RELEASE_DEPTH_BACKENDS:
                raise ValidationError(
                    "Final export depth provenance is not usable",
                    details={
                        "shot_id": item.shot_id,
                        "backend": item.backend,
                        "allowed": sorted(RELEASE_DEPTH_BACKENDS),
                    },
                )
            depth_path = resolve_project_artifact(self.layout.root, item.path, subdirectory="depth")
            paths.append(depth_path)
            shot = shots_by_id[item.shot_id]
            expected_shape = (shot.frame_count, item.height, item.width)
            if (
                item.frame_count != shot.frame_count
                or shot.frame_count > self.config.depth.max_shot_frames
                or item.width > self.config.depth.width
                or item.height > self.config.depth.height
                or (
                    for_release
                    and (
                        item.width != self.config.depth.width
                        or item.height != self.config.depth.height
                    )
                )
            ):
                raise ValidationError(
                    "Depth artifact dimensions exceed or contradict the current configuration",
                    details={
                        "shot_id": item.shot_id,
                        "expected_shape": list(expected_shape),
                        "max_shot_frames": self.config.depth.max_shot_frames,
                        "max_width": self.config.depth.width,
                        "max_height": self.config.depth.height,
                    },
                )
            expected_allocation = shot.frame_count * item.height * item.width * 8
            depth = load_depth_shot(
                depth_path,
                expected_shape=expected_shape,
                max_array_bytes=min(MAX_DEPTH_ARRAY_BYTES, expected_allocation),
            )
            if depth.shape != expected_shape:
                raise ValidationError(
                    "Depth artifact shape does not match its manifest",
                    details={
                        "shot_id": item.shot_id,
                        "expected_shape": list(expected_shape),
                        "actual_shape": list(depth.shape),
                    },
                )
        if require_current_stage:
            model_identity = (
                Path(self.config.depth.model_path)
                if self.config.depth.model_path and manifest.backend == CERTIFIED_DEPTH_BACKEND
                else None
            )
            # Whether the run was *allowed* to fall back is a property of the
            # request, not of the artifact, and the manifest does not record it.
            # Accept either spelling: any real input change fails both.
            outputs = [self.layout.depth_metadata, *paths]
            if not any(
                self.state.can_resume(
                    "estimate_depth",
                    fingerprint(
                        [
                            self.layout.normalized_video,
                            self.layout.shots,
                            self._depth_stage_identity(),
                            manifest.backend,
                            model_identity,
                            manifest.backend_source_fingerprint,
                            allow_fallback,
                            FALLBACK_DEPTH_IDENTITY if allow_fallback else None,
                        ]
                    ),
                    outputs,
                )
                for allow_fallback in (False, True)
            ):
                raise StageError("Depth stage is stale or its outputs changed")
        return manifest

    def extract_features(
        self,
        *,
        speech_intervals: Sequence[tuple[float, float]] | None = None,
        allow_fallback: bool = False,
    ) -> FeatureManifest:
        media = self.normalize()
        shots = self.detect_shots()
        depth_manifest = (
            self.estimate_depth(allow_fallback=True) if allow_fallback else self.estimate_depth()
        )
        stage_hash = fingerprint(
            [
                self.layout.normalized_video,
                self.layout.depth_metadata,
                self.layout.shots,
                repr(list(speech_intervals or [])),
            ]
        )

        def operation() -> FeatureManifest:
            depth_by_id = {item.shot_id: item for item in depth_manifest.shots}
            output = []
            processed = 0
            oversized = [
                shot.shot_id
                for shot in shots.shots
                if shot.frame_count > self.config.depth.max_shot_frames
            ]
            if oversized:
                raise ValidationError(
                    "One or more shots exceed the bounded-memory feature limit",
                    details={
                        "shot_ids": oversized,
                        "max_shot_frames": self.config.depth.max_shot_frames,
                    },
                )
            feature_width, feature_height = _feature_frame_size(
                media.width, media.height, self.config.shots.downscale_width
            )
            for shot in shots.shots:
                _validate_feature_working_set(
                    shot.frame_count,
                    feature_height,
                    feature_width,
                    shot_id=shot.shot_id,
                )
            for shot, frames in decode_shots(
                self.layout.normalized_video,
                media,
                shots,
                ffmpeg_path=self.config.media.ffmpeg_path,
                cancel=self._cancelled,
                output_size=(feature_height, feature_width),
            ):
                metadata = depth_by_id[shot.shot_id]
                depth_path = resolve_project_artifact(
                    self.layout.root, metadata.path, subdirectory="depth"
                )
                depth = load_depth_shot(depth_path)
                output.append(
                    extract_shot_features(
                        shot,
                        frames,
                        depth,
                        speech_intervals=speech_intervals,
                        depth_reliability=metadata.reliability,
                    )
                )
                processed += shot.frame_count
                self.progress(
                    "extract_features", processed, shots.frame_count, f"Shot {shot.shot_id}"
                )
            manifest = FeatureManifest(shots=output)
            write_json_atomic(self.layout.features, manifest)
            return manifest

        return self._execute_stage(
            "extract_features",
            stage_hash,
            [self.layout.features],
            operation,
            lambda: read_model(self.layout.features, FeatureManifest),
        )

    def _direct_dependency_fingerprint(self) -> str:
        return fingerprint(
            [
                self.layout.normalized_media,
                self.layout.features,
                self.layout.depth_metadata,
                self.layout.shots,
                self.config.comfort.model_dump_json(),
            ]
        )

    @staticmethod
    def _validate_editable_script(
        script: StereoScript,
        *,
        media: MediaInfo,
        shots: ShotManifest,
        features: FeatureManifest,
    ) -> None:
        expected_ids = [item.shot_id for item in shots.shots]
        feature_ids = [item.shot_id for item in features.shots]
        script_ids = [item.shot_id for item in script.shots]
        if feature_ids != expected_ids or script_ids != expected_ids:
            raise ValidationError(
                "Stereo script does not exactly cover the current shots",
                details={
                    "expected_shot_ids": expected_ids,
                    "feature_shot_ids": feature_ids,
                    "script_shot_ids": script_ids,
                },
            )
        if script.video_width != media.width:
            raise ValidationError(
                "Stereo script width does not match the current normalized video",
                details={"expected_width": media.width, "script_width": script.video_width},
            )

    @staticmethod
    def _carry_manual_overrides(
        existing: StereoScript | None,
        generated: StereoScript,
    ) -> StereoScript:
        if existing is None or not any(item.manual_override for item in existing.shots):
            return generated
        existing_ids = [item.shot_id for item in existing.shots]
        generated_ids = [item.shot_id for item in generated.shots]
        if existing.video_width != generated.video_width or existing_ids != generated_ids:
            raise ValidationError(
                "Manual stereo overrides cannot be migrated to the changed shot structure",
                details={
                    "existing_shot_ids": existing_ids,
                    "generated_shot_ids": generated_ids,
                    "existing_width": existing.video_width,
                    "generated_width": generated.video_width,
                },
            )
        existing_by_id = {item.shot_id: item for item in existing.shots}
        for item in generated.shots:
            prior = existing_by_id[item.shot_id]
            if not prior.manual_override:
                continue
            item.preset = prior.preset
            item.confidence = prior.confidence
            item.parameters = prior.parameters.model_copy(deep=True)
            item.requested_parameters = (
                prior.requested_parameters.model_copy(deep=True)
                if prior.requested_parameters is not None
                else prior.parameters.model_copy(deep=True)
            )
            item.manual_override = True
            item.guard_actions = []
        return generated

    def _prepare_render_output(self, path: str | Path, *, preview: bool) -> Path:
        root = self.layout.root.resolve()
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        lexical = Path(os.path.abspath(candidate))
        lexical_in_project = lexical == root or root in lexical.parents
        output = prepare_output_path(
            lexical,
            allowed_root=root if lexical_in_project else None,
        )
        source = Path(self.project.input_path).expanduser().resolve()
        if output == source:
            raise ValidationError("Render output cannot overwrite the source video")
        canonical_in_project = output == root or root in output.parents
        if canonical_in_project:
            allowed = (self.layout.previews_dir if preview else self.layout.renders_dir).resolve()
            if output == allowed or allowed not in output.parents:
                raise ValidationError(
                    "Render output must stay in the matching project output folder",
                    details={"allowed_directory": str(allowed), "output_path": str(output)},
                )
        return output

    def direct(
        self,
        *,
        director_name: str = "rules",
        allow_fallback: bool = False,
    ) -> StereoScript:
        media = self.normalize()
        features = (
            self.extract_features(allow_fallback=True)
            if allow_fallback
            else self.extract_features()
        )
        depth_manifest = read_model(self.layout.depth_metadata, DepthManifest)
        shot_manifest = read_model(self.layout.shots, ShotManifest)
        existing_script = (
            read_model(self.layout.stereo_script, StereoScript)
            if self.layout.stereo_script.is_file()
            else None
        )
        dependency_hash = self._direct_dependency_fingerprint()
        stage_hash = fingerprint(
            [
                dependency_hash,
                director_name,
                self.config.llm.model_dump_json(),
            ]
        )

        def operation() -> StereoScript:
            if director_name == "rules":
                director: object = RuleBasedDirector()
            elif director_name == "llm":
                if not self.config.llm.enabled:
                    raise ValidationError("LLM director is disabled in configuration")
                provider = OpenAIResponsesPresetProvider(self.config.llm)
                director = LLMDirector(provider, fallback=self.config.llm.fallback)
            else:
                raise ValidationError("Unknown director", details={"director": director_name})
            edge_violations: set[int] = set()
            margin_fraction = self.config.comfort.edge_margin_fraction
            for item in depth_manifest.shots:
                depth_path = resolve_project_artifact(
                    self.layout.root, item.path, subdirectory="depth"
                )
                depth = load_depth_shot(depth_path)
                margin = max(1, round(depth.shape[2] * margin_fraction))
                edge_near = np.concatenate([depth[:, :, :margin], depth[:, :, -margin:]], axis=2)
                if float(np.mean(edge_near >= 0.75)) >= 0.02:
                    edge_violations.add(item.shot_id)
            script = create_stereo_script(
                features,
                video_width=media.width,
                comfort=self.config.comfort,
                director=director,
                edge_violations=edge_violations,
                hard_cut_shot_ids={
                    shot.shot_id for shot in shot_manifest.shots if shot.transition != "fade"
                },
            )
            script = self._carry_manual_overrides(existing_script, script)
            write_json_atomic(self.layout.stereo_script, script)
            self.progress("direct", len(script.shots), len(script.shots), "Stereo script created")
            return script

        return self._execute_stage(
            "direct",
            stage_hash,
            [self.layout.stereo_script],
            operation,
            lambda: read_model(self.layout.stereo_script, StereoScript),
            dependency_fingerprint=dependency_hash,
        )

    def _preflight_final_depth(self) -> None:
        """Fail before expensive final-render work when depth cannot be used."""

        backend = self.config.depth.backend
        if backend == "synthetic":
            raise SyntheticDepthFinalError(
                "Synthetic depth is limited to previews and automated tests",
                details={"backend": backend},
            )
        if backend not in RELEASE_DEPTH_BACKENDS:
            raise ValidationError(
                "Final export requires a measured depth backend",
                details={"backend": backend, "allowed": sorted(RELEASE_DEPTH_BACKENDS)},
            )
        if self.layout.depth_metadata.is_file():
            # Depth already on disk is the authority. It records what was really
            # measured, including a fallback that the configured backend name
            # cannot predict, so a usable artifact settles this before the
            # checkpoint checks below can veto it.
            with suppress(AIStereoError):
                self.validate_depth_artifact(for_release=True)
                return
        if backend != CERTIFIED_DEPTH_BACKEND:
            # Image-analysis depth needs no checkpoint, so the model-path checks
            # below do not apply to it.
            return
        if not self.config.depth.model_path:
            raise ValidationError(
                "Final export requires a trusted local depth model",
                details={"setting": "AISTEREO_DEPTH_MODEL_PATH"},
            )
        model_path = Path(self.config.depth.model_path).expanduser().resolve()
        if not model_path.is_file():
            raise ValidationError(
                "Configured depth model path does not exist",
                details={"setting": "AISTEREO_DEPTH_MODEL_PATH"},
            )

    def _prepare_render_inputs(
        self,
        output_path: str | Path,
        *,
        shot_id: int | None,
        output_mode: OutputMode,
        allow_fallback: bool,
    ) -> tuple[Path, MediaInfo, ShotManifest, DepthManifest, StereoScript]:
        """Validate and assemble everything a render consumes.

        Shared by the video render and the still preview so a preview can never
        be produced from a script the Comfort Guard has not passed over.
        """

        allowed_modes: set[str] = {"anaglyph", "left", "right", "side-by-side"}
        if output_mode not in allowed_modes:
            raise ValidationError(
                "Unknown render output mode",
                details={"output_mode": output_mode, "available": sorted(allowed_modes)},
            )
        if shot_id is None:
            self._preflight_final_depth()
            # Final output is always fail-closed. Preview callers may opt into
            # synthetic fallback, but a release render must revalidate the
            # certified backend without permitting a fallback artifact.
            allow_fallback = False
        output = self._prepare_render_output(output_path, preview=shot_id is not None)
        media = self.normalize()
        shots = self.detect_shots()
        depth = (
            self.estimate_depth(allow_fallback=True) if allow_fallback else self.estimate_depth()
        )
        if shot_id is None:
            depth = self.validate_depth_artifact(
                shots=shots,
                for_release=True,
                require_current_stage=True,
            )
        # stereo_script.json is intentionally editable. Rendering consumes the
        # artifact on disk and never silently re-runs direction over user edits.
        features = (
            self.extract_features(allow_fallback=True)
            if allow_fallback
            else self.extract_features()
        )
        direct_dependencies = self._direct_dependency_fingerprint()
        if self.layout.stereo_script.is_file() and self.state.dependencies_are_current(
            "direct", direct_dependencies
        ):
            script = read_model(self.layout.stereo_script, StereoScript)
            self._validate_editable_script(script, media=media, shots=shots, features=features)
        else:
            script = self.direct(allow_fallback=True) if allow_fallback else self.direct()
        edge_shots: set[int] = set()
        for item in depth.shots:
            depth_path = resolve_project_artifact(self.layout.root, item.path, subdirectory="depth")
            depth_planes = load_depth_shot(depth_path)
            margin = max(
                1,
                round(depth_planes.shape[2] * self.config.comfort.edge_margin_fraction),
            )
            edge_near = np.concatenate(
                [depth_planes[:, :, :margin], depth_planes[:, :, -margin:]], axis=2
            )
            if float(np.mean(edge_near >= 0.75)) >= 0.02:
                edge_shots.add(item.shot_id)
        script = guard_script_for_render(
            script,
            features,
            self.config.comfort,
            edge_violations=edge_shots,
            hard_cut_shot_ids={shot.shot_id for shot in shots.shots if shot.transition != "fade"},
        )
        applied_script_path = self.layout.director_dir / "applied_stereo_script.json"
        try:
            applied_on_disk = read_model(applied_script_path, StereoScript)
        except AIStereoError:
            applied_on_disk = None
        if applied_on_disk != script:
            write_json_atomic(applied_script_path, script)
        if shot_id is not None and shot_id not in {item.shot_id for item in shots.shots}:
            raise ValidationError("Shot does not exist", details={"shot_id": shot_id})
        return output, media, shots, depth, script

    def render(
        self,
        output_path: str | Path,
        *,
        shot_id: int | None = None,
        output_mode: OutputMode = "anaglyph",
        allow_fallback: bool = False,
    ) -> Path:
        output, media, shots, depth, script = self._prepare_render_inputs(
            output_path,
            shot_id=shot_id,
            output_mode=output_mode,
            allow_fallback=allow_fallback,
        )
        stage_name = "render_preview" if shot_id is not None else "render_final"
        stage_hash = fingerprint(
            [
                self.layout.normalized_video,
                self.layout.depth_metadata,
                self.layout.features,
                self.layout.shots,
                self.layout.stereo_script,
                script.model_dump_json(),
                str(output),
                output_mode,
                shot_id,
                self.config.render.model_dump_json(),
                self.config.comfort.model_dump_json(),
            ]
        )

        def operation() -> Path:
            video_only = output.with_name(f".{output.stem}.video{output.suffix}")
            rendered, count, accumulator = render_video(
                self.layout.normalized_video,
                video_only,
                media=media,
                manifest=shots,
                depth_manifest=depth,
                script=script,
                project_root=self.layout.root,
                media_config=self.config.media,
                render_config=self.config.render,
                comfort_config=self.config.comfort,
                shot_ids={shot_id} if shot_id is not None else None,
                output_mode=output_mode,
                # Final delivery is never scaled; a shot preview is, so a whole
                # shot stays watchable in seconds.
                scale_width=self.config.render.preview_max_width if shot_id is not None else None,
                progress=lambda done, total: self.progress(stage_name, done, total, None),
                cancel=self._cancelled,
            )
            try:
                if shot_id is None:
                    final = remux_audio(
                        rendered,
                        output,
                        audio_path=self.layout.audio if self.layout.audio.exists() else None,
                        ffmpeg_path=self.config.media.ffmpeg_path,
                        cancel=self._cancelled,
                    )
                    guard_actions = {
                        item.shot_id: [action.code for action in item.guard_actions]
                        for item in script.shots
                    }
                    fallback_shots = {item.shot_id for item in depth.shots if item.fallback_used}
                    failures = {item.shot_id for item in depth.shots if item.error_code}
                    qc_warnings: list[str] = []
                    probed_frame_count = count
                    audio_duration: float | None = None
                    video_duration = media.duration_seconds
                    try:
                        final_media = inspect_media(
                            final, ffprobe_path=self.config.media.ffprobe_path
                        )
                        if final_media.frame_count > 0:
                            probed_frame_count = final_media.frame_count
                        video_duration = final_media.duration_seconds
                        audio_durations = [
                            stream.duration_seconds
                            for stream in final_media.audio_streams
                            if stream.duration_seconds is not None
                        ]
                        if audio_durations:
                            audio_duration = max(audio_durations)
                        elif final_media.audio_streams:
                            qc_warnings.append(
                                "Final audio duration was unavailable; A/V timing could not be independently verified"
                            )
                    except AIStereoError:
                        source_media = read_model(self.layout.media, MediaInfo)
                        audio_duration = (
                            source_media.duration_seconds if source_media.audio_streams else None
                        )
                        qc_warnings.append(
                            "Final output probe was unavailable; QC timing uses source/intermediate estimates"
                        )
                    report = build_qc_report(
                        accumulator,
                        expected_frame_count=shots.frame_count,
                        rendered_frame_count=probed_frame_count,
                        audio_duration=audio_duration,
                        video_duration=video_duration,
                        guard_actions=guard_actions,
                        fallback_shots=fallback_shots,
                        depth_model_failures=failures,
                        duplicated_frames=max(0, probed_frame_count - shots.frame_count),
                        depth_backend=depth.backend,
                        synthetic_depth=depth.backend == "synthetic"
                        or any(
                            item.backend == "synthetic" or item.fallback_used
                            for item in depth.shots
                        ),
                        additional_warnings=qc_warnings,
                    )
                    write_json_atomic(self.layout.qc_report, report)
                    write_qc_html(report, self.layout.qc_dir / "report.html")
                    return final
                replace_atomic(rendered, output)
                return output
            finally:
                video_only.unlink(missing_ok=True)

        stage_outputs = [output]
        if shot_id is None:
            stage_outputs.extend([self.layout.qc_report, self.layout.qc_dir / "report.html"])
        return self._execute_stage(
            stage_name,
            stage_hash,
            stage_outputs,
            operation,
            lambda: output,
        )

    def render_frame(
        self,
        output_path: str | Path,
        *,
        shot_id: int,
        frame_offset: int = 0,
        output_mode: OutputMode = "anaglyph",
        allow_fallback: bool = False,
    ) -> Path:
        """Render one frame of a shot to a still, for interactive preview.

        Takes the same validated, Comfort-Guarded script the video render takes,
        so what a director sees here is what the shot will encode to.
        """

        output, media, shots, depth, script = self._prepare_render_inputs(
            output_path,
            shot_id=shot_id,
            output_mode=output_mode,
            allow_fallback=allow_fallback,
        )
        shot_index = next(
            (index for index, item in enumerate(shots.shots) if item.shot_id == shot_id),
            None,
        )
        if shot_index is None:
            raise ValidationError("Shot does not exist", details={"shot_id": shot_id})
        shot = shots.shots[shot_index]
        script_by_id = {item.shot_id: item for item in script.shots}
        current_script = script_by_id.get(shot_id)
        depth_meta = next((item for item in depth.shots if item.shot_id == shot_id), None)
        if current_script is None or depth_meta is None:
            raise StageError("Render artifacts are incomplete", details={"shot_id": shot_id})
        local_index = int(np.clip(frame_offset, 0, max(shot.frame_count - 1, 0)))

        self.progress("render_preview", 0, 2, f"Decoding shot {shot_id}")
        frame = decode_frame(
            self.layout.normalized_video,
            media=media,
            frame_index=shot.start_frame + local_index,
            media_config=self.config.media,
        )
        depth_path = resolve_project_artifact(
            self.layout.root, depth_meta.path, subdirectory="depth"
        )
        depth_planes = load_depth_shot(depth_path)
        if depth_planes.shape[0] != shot.frame_count:
            raise StageError(
                "Depth frame count does not match the shot",
                details={
                    "shot_id": shot_id,
                    "depth_frames": depth_planes.shape[0],
                    "shot_frames": shot.frame_count,
                },
            )
        parameters = current_script.parameters
        if shot.transition == "fade" and shot_index > 0:
            previous_script = script_by_id.get(shots.shots[shot_index - 1].shot_id)
            if previous_script is not None:
                parameters = interpolate_stereo_parameters(
                    previous_script.parameters, current_script.parameters, local_index
                )
        self.progress("render_preview", 1, 2, f"Rendering shot {shot_id}")
        result = render_stereo_frame(
            frame,
            depth_planes[local_index],
            parameters,
            render_config=self.config.render,
            comfort_config=self.config.comfort,
        )
        if output_mode == "left":
            rendered = result.left
        elif output_mode == "right":
            rendered = result.right
        elif output_mode == "side-by-side":
            rendered = np.concatenate([result.left, result.right], axis=1)
        else:
            rendered = result.anaglyph
        written = write_still(rendered, output, media_config=self.config.media)
        self._discard_stale_stills(shot_id, keep=written)
        self.progress("render_preview", 2, 2, f"Shot {shot_id} preview ready")
        return written

    def _discard_stale_stills(self, shot_id: int, *, keep: Path) -> None:
        """Keep one still per shot so the previews folder cannot grow forever."""

        for existing in self.layout.previews_dir.glob(f"shot_{shot_id:04d}_f*.png"):
            if existing != keep:
                with suppress(OSError):
                    existing.unlink()

    def qc(self) -> QCReport:
        if not self.layout.qc_report.exists():
            raise StageError("No final render QC report exists yet")
        return read_model(self.layout.qc_report, QCReport)

    def run(self, output_path: str | Path, *, director_name: str = "rules") -> Path:
        self._preflight_final_depth()
        self.inspect()
        self.normalize()
        self.detect_shots()
        self.estimate_depth()
        self.extract_features()
        self.direct(director_name=director_name)
        return self.render(output_path)

    def summary(self) -> ProjectSummary:
        return ProjectSummary(
            project_dir=str(self.layout.root),
            project=self.project,
            media=read_model(self.layout.normalized_media, MediaInfo)
            if self.layout.normalized_media.exists()
            else None,
            shots=read_model(self.layout.shots, ShotManifest)
            if self.layout.shots.exists()
            else None,
            features=read_model(self.layout.features, FeatureManifest)
            if self.layout.features.exists()
            else None,
            stereo_script=read_model(self.layout.stereo_script, StereoScript)
            if self.layout.stereo_script.exists()
            else None,
            draft_analysis=self._compose_draft_analysis()
            if self.layout.draft_analysis.exists()
            else None,
            qc=read_model(self.layout.qc_report, QCReport)
            if self.layout.qc_report.exists()
            else None,
            pipeline_state=read_model(self.layout.pipeline_state, PipelineState)
            if self.layout.pipeline_state.exists()
            else None,
        )


def open_pipeline(
    project_dir: str | Path,
    *,
    config_path: str | Path | None = None,
    **kwargs: Any,
) -> AIStereoPipeline:
    config = sanitize_runtime_paths(load_config(config_path)) if config_path is not None else None
    return AIStereoPipeline(project_dir, config=config, **kwargs)
