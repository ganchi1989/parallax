"""Long-running newline-delimited JSON worker for the thin Tauri host."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from . import __version__
from .artifacts import ProjectLayout, read_model, write_json_atomic
from .config import CERTIFIED_DEPTH_BACKEND, AppConfig, load_config
from .director.llm import (
    OFFICIAL_OPENAI_BASE_URL,
    LLMDirector,
    OpenAIResponsesPresetProvider,
)
from .director.overrides import apply_shot_overrides, stereo_script_revision
from .errors import AIStereoError, SyntheticDepthFinalError, ValidationError
from .media.probe import inspect_media
from .models import (
    ApplyShotOverridesRequest,
    DepthManifest,
    ShotFeatures,
    StereoScript,
    WorkerRequest,
)
from .pipeline import AIStereoPipeline
from .state import CancellationToken

_ALLOWED_METHODS = {
    "ping",
    "create_project",
    "inspect",
    "normalize",
    "detect_shots",
    "analyze_draft",
    "estimate_depth",
    "extract_features",
    "create_stereo_script",
    "direct",
    "render_preview",
    "render_preview_frame",
    "render_final",
    "render",
    "generate_qc",
    "qc",
    "run_pipeline",
    "run",
    "get_project",
    "llm_status",
    "test_llm",
    "recommend_preset",
    "apply_shot_overrides",
    "cancel",
    "cancel_job",
}
_MAX_EVENT_BYTES = 900_000
MAX_REQUEST_BYTES = 256 * 1024
_APPROVED_LLM_MODELS = {"gpt-5.6-terra"}

_PROJECT_STAGE_PARAMS = {"project_dir", "resume", "force_stages"}
_PIPELINE_CONFIG_PARAMS = {"depth_backend", "device"}
_RENDER_CONFIG_PARAMS = {"anaglyph_mode", "swap_eyes", "preview_max_width"}
_METHOD_PARAM_CONTRACTS: dict[str, tuple[set[str], set[str]]] = {
    "ping": (set(), set()),
    "llm_status": ({"model"}, set()),
    "test_llm": ({"model"}, set()),
    "recommend_preset": ({"features", "model"}, {"features"}),
    "apply_shot_overrides": (
        {"project_dir", "expected_revision", "overrides"},
        {"project_dir", "expected_revision", "overrides"},
    ),
    "cancel": ({"job_id"}, {"job_id"}),
    "cancel_job": ({"job_id"}, {"job_id"}),
    "create_project": (
        {
            "input_path",
            "project_dir",
            "name",
            *_PIPELINE_CONFIG_PARAMS,
            *_RENDER_CONFIG_PARAMS,
            "llm_enabled",
            "model",
        },
        {"input_path", "project_dir"},
    ),
    "inspect": ({"input_path", *_PROJECT_STAGE_PARAMS}, set()),
    "estimate_depth": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            "backend",
            "cache_dir",
            "allow_fallback",
        },
        {"project_dir"},
    ),
    "analyze_draft": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            "profile",
            "allow_fallback",
        },
        {"project_dir"},
    ),
    "extract_features": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            "speech_intervals",
            "allow_fallback",
        },
        {"project_dir"},
    ),
    "create_stereo_script": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            "director",
            "llm_enabled",
            "model",
            "allow_fallback",
        },
        {"project_dir"},
    ),
    "direct": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            "director",
            "llm_enabled",
            "model",
            "allow_fallback",
        },
        {"project_dir"},
    ),
    "render_preview": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            *_RENDER_CONFIG_PARAMS,
            "shot_id",
            "output_path",
            "output_mode",
            "allow_fallback",
        },
        {"project_dir", "shot_id"},
    ),
    "render_preview_frame": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            *_RENDER_CONFIG_PARAMS,
            "shot_id",
            "frame_offset",
            "output_path",
            "output_mode",
            "allow_fallback",
        },
        {"project_dir", "shot_id"},
    ),
    "render_final": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            *_RENDER_CONFIG_PARAMS,
            "output_path",
            "output_mode",
        },
        {"project_dir"},
    ),
    "render": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            *_RENDER_CONFIG_PARAMS,
            "output_path",
            "output_mode",
        },
        {"project_dir"},
    ),
    "run_pipeline": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            *_RENDER_CONFIG_PARAMS,
            "output_path",
            "output_mode",
            "director",
            "llm_enabled",
            "model",
        },
        {"project_dir"},
    ),
    "run": (
        {
            *_PROJECT_STAGE_PARAMS,
            *_PIPELINE_CONFIG_PARAMS,
            *_RENDER_CONFIG_PARAMS,
            "output_path",
            "output_mode",
            "director",
            "llm_enabled",
            "model",
        },
        {"project_dir"},
    ),
}
for _method in ("normalize", "detect_shots", "generate_qc", "qc", "get_project"):
    _METHOD_PARAM_CONTRACTS[_method] = (_PROJECT_STAGE_PARAMS, {"project_dir"})


def _validate_parameter_contract(request: WorkerRequest) -> None:
    allowed, required = _METHOD_PARAM_CONTRACTS[request.method]
    unknown = sorted(set(request.params) - allowed)
    missing = sorted(required - set(request.params))
    if unknown:
        raise ValidationError(
            "Request contains unsupported parameters", details={"parameters": unknown}
        )
    if missing:
        raise ValidationError(
            "Request is missing required parameters", details={"parameters": missing}
        )
    if request.method == "inspect":
        has_input = "input_path" in request.params
        has_project = "project_dir" in request.params
        if has_input == has_project or (has_input and len(request.params) != 1):
            raise ValidationError("Inspect requires exactly one input_path or project_dir")
    if request.method == "create_project" and "name" in request.params:
        project_name = request.params["name"]
        if (
            not isinstance(project_name, str)
            or not project_name.strip()
            or len(project_name) > 160
            or any(
                ord(character) < 32 or 127 <= ord(character) <= 159 for character in project_name
            )
        ):
            raise ValidationError(
                "Request contains an invalid project name",
                details={"parameter": "name"},
            )
    for name in ("swap_eyes", "llm_enabled", "resume", "allow_fallback"):
        if name in request.params and not isinstance(request.params[name], bool):
            raise ValidationError(
                "Request contains a boolean parameter with the wrong type",
                details={"parameter": name},
            )
    if "profile" in request.params and request.params["profile"] != "representative_frames":
        raise ValidationError(
            "Request contains an unsupported draft analysis profile",
            details={"parameter": "profile"},
        )


def _boolean_parameter(params: dict[str, Any], name: str, *, default: bool) -> bool:
    value = params.get(name, default)
    if not isinstance(value, bool):
        raise ValidationError(
            "Request contains a boolean parameter with the wrong type",
            details={"parameter": name},
        )
    return value


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _has_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            sensitive_names = (
                "apikey",
                "accesstoken",
                "refreshtoken",
                "authorization",
                "credential",
                "password",
                "secret",
                "bearer",
                "privatekey",
            )
            if normalized == "token" or any(name in normalized for name in sensitive_names):
                return True
            if _has_secret(item):
                return True
    elif isinstance(value, list):
        return any(_has_secret(item) for item in value)
    return False


def _safe_request_id(value: Any) -> str:
    candidate = str(value or "unknown")[:128]
    if not candidate or any(
        ord(character) < 32 or ord(character) == 127 for character in candidate
    ):
        return "unknown"
    return candidate


class JSONLWorker:
    def __init__(self) -> None:
        self._output = sys.stdout
        self._write_lock = threading.Lock()
        self._jobs_lock = threading.Lock()
        self._jobs: dict[str, CancellationToken] = {}
        # Serial product stages avoid project artifact races. stdin remains live,
        # so cancellation is still immediate.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aistereo-job")

    def emit(self, event: dict[str, Any]) -> None:
        try:
            # The Windows child-process pipe may inherit a legacy code page.
            # ASCII-escaped JSON keeps the byte protocol valid UTF-8 without
            # relying on ambient stdout encoding. Strict finite-number output
            # prevents Python's non-standard NaN/Infinity tokens reaching Rust.
            encoded = json.dumps(
                _jsonable(event),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, OverflowError, RecursionError):
            encoded = json.dumps(
                {
                    "type": "error",
                    "id": _safe_request_id(event.get("id")),
                    "error": {
                        "code": "invalid_worker_output",
                        "message": "Worker output could not be serialized safely",
                        "retryable": False,
                    },
                },
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        if len(encoded.encode("utf-8")) > _MAX_EVENT_BYTES:
            encoded = json.dumps(
                {
                    "type": "error",
                    "id": _safe_request_id(event.get("id")),
                    "error": {
                        "code": "result_too_large",
                        "message": "Worker result exceeds the desktop IPC size limit",
                        "retryable": False,
                    },
                },
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        with self._write_lock:
            self._output.write(encoded + "\n")
            self._output.flush()

    def _error(self, request_id: str, error: Exception) -> None:
        if isinstance(error, AIStereoError):
            body = error.as_dict()
        elif isinstance(error, PydanticValidationError):
            body = {
                "code": "invalid_request",
                "message": "Request parameters failed validation",
                "details": {"errors": error.errors(include_input=False, include_url=False)},
                "retryable": False,
            }
        else:
            body = {
                "code": "internal_error",
                "message": "The worker could not complete the request",
                "retryable": False,
            }
        self.emit({"type": "error", "id": request_id, "error": body})

    def _config(self, params: dict[str, Any], *, project_dir: str | None = None) -> AppConfig:
        layout = ProjectLayout.at(project_dir).ensure() if project_dir else None
        path = params.get("config_path")
        has_persisted_config = layout is not None and layout.config.is_file()
        if has_persisted_config:
            assert layout is not None
            settings = read_model(layout.config, AppConfig)
        else:
            settings = load_config(str(path)) if path else AppConfig()
        settings.llm.provider = "openai-responses"
        settings.llm.base_url = OFFICIAL_OPENAI_BASE_URL
        settings.llm.model = "gpt-5.6-terra"
        if "depth_backend" in params or "backend" in params:
            settings.depth.backend = str(params.get("depth_backend") or params.get("backend"))
        if "device" in params:
            settings.depth.device = str(params["device"])  # type: ignore[assignment]
        if "anaglyph_mode" in params:
            settings.render.anaglyph_mode = str(params["anaglyph_mode"])  # type: ignore[assignment]
        if "swap_eyes" in params:
            settings.render.swap_eyes = _boolean_parameter(params, "swap_eyes", default=False)
        if "llm_enabled" in params:
            settings.llm.enabled = _boolean_parameter(params, "llm_enabled", default=False)
        if "model" in params:
            requested_model = str(params["model"])
            if requested_model not in _APPROVED_LLM_MODELS:
                raise ValidationError(
                    "Requested LLM model is not approved for the desktop assistant",
                    details={"model": requested_model},
                )
            settings.llm.model = requested_model
        for env_name, attribute, fallback in (
            ("AISTEREO_FFMPEG_PATH", "ffmpeg_path", "ffmpeg"),
            ("AISTEREO_FFPROBE_PATH", "ffprobe_path", "ffprobe"),
        ):
            configured_tool = os.environ.get(env_name)
            if configured_tool:
                tool_path = Path(configured_tool).expanduser().resolve()
                if not tool_path.is_file():
                    raise ValidationError(
                        "Configured media tool path does not exist",
                        details={"setting": env_name},
                    )
                setattr(settings.media, attribute, str(tool_path))
            else:
                setattr(settings.media, attribute, fallback)
        configured_model = os.environ.get("AISTEREO_DEPTH_MODEL_PATH")
        if configured_model:
            model_path = Path(configured_model).expanduser().resolve()
            if not model_path.is_file():
                raise ValidationError(
                    "Configured depth model path does not exist",
                    details={"setting": "AISTEREO_DEPTH_MODEL_PATH"},
                )
            settings.depth.model_path = str(model_path)
            if (
                not has_persisted_config
                and not path
                and "depth_backend" not in params
                and "backend" not in params
            ):
                settings.depth.backend = "video-depth-anything-small"
        else:
            # Project files are editable/untrusted at the desktop boundary.
            settings.depth.model_path = None
        # A native upstream checkpoint also needs its source tree. Like the model
        # path, this comes from the process environment, never from project data.
        configured_source = os.environ.get("AISTEREO_DEPTH_MODEL_SOURCE")
        if configured_source and settings.depth.model_path:
            source_path = Path(configured_source).expanduser().resolve()
            if not (source_path / "video_depth_anything").is_dir():
                raise ValidationError(
                    "Configured depth model source is not an upstream checkout",
                    details={"setting": "AISTEREO_DEPTH_MODEL_SOURCE"},
                )
            settings.depth.model_source = str(source_path)
        else:
            settings.depth.model_source = None
        return settings

    def _depth_manifest(self, pipeline: AIStereoPipeline) -> DepthManifest:
        if not pipeline.layout.depth_metadata.is_file():
            raise ValidationError("Project has no depth manifest")
        return read_model(pipeline.layout.depth_metadata, DepthManifest)

    def _require_release_depth(self, pipeline: AIStereoPipeline) -> DepthManifest:
        manifest = pipeline.estimate_depth()
        # Synthetic depth is a constant test pattern: it encodes no scene, so an
        # export made from it would be 2D wearing a stereo container. Measured
        # image-analysis depth is a different thing and is allowed to ship; the
        # manifest and QC report still record that it is not the certified model.
        synthetic_shots = sorted(
            item.shot_id for item in manifest.shots if item.backend == "synthetic"
        )
        if manifest.backend == "synthetic" or synthetic_shots:
            raise SyntheticDepthFinalError(
                "Synthetic depth is limited to previews and automated tests",
                details={"backend": manifest.backend, "synthetic_shot_ids": synthetic_shots},
            )
        return pipeline.validate_depth_artifact(for_release=True, require_current_stage=True)

    @staticmethod
    def _depth_status(pipeline: AIStereoPipeline) -> dict[str, Any]:
        manifest = read_model(pipeline.layout.depth_metadata, DepthManifest)
        synthetic_shot_ids = sorted(
            item.shot_id for item in manifest.shots if item.backend == "synthetic"
        )
        fallback_shot_ids = sorted(item.shot_id for item in manifest.shots if item.fallback_used)
        model_failure_shot_ids = sorted(
            item.shot_id for item in manifest.shots if item.error_code is not None
        )
        try:
            pipeline.validate_depth_artifact(for_release=True, require_current_stage=True)
        except AIStereoError:
            production_ready = False
        else:
            production_ready = bool(manifest.shots)
        shot_backends = {item.backend for item in manifest.shots}
        if synthetic_shot_ids or manifest.backend == "synthetic":
            tier = "synthetic"
        elif not production_ready:
            tier = "unknown"
        elif shot_backends == {CERTIFIED_DEPTH_BACKEND}:
            tier = "certified"
        else:
            tier = "image-analysis"
        return {
            "backend": manifest.backend,
            "production_ready": production_ready,
            # How the depth was actually measured, which the configured backend
            # name cannot tell you once a fallback has happened.
            "tier": tier,
            "synthetic_shot_ids": synthetic_shot_ids,
            "fallback_shot_ids": fallback_shot_ids,
            "model_failure_shot_ids": model_failure_shot_ids,
        }

    def _pipeline(
        self,
        request_id: str,
        params: dict[str, Any],
        token: CancellationToken,
        *,
        create: bool = False,
    ) -> AIStereoPipeline:
        project_dir = (
            params.get("project_dir") or params.get("work_dir") or params.get("project_path")
        )
        if not isinstance(project_dir, str) or not project_dir:
            raise ValidationError("project_dir is required")
        settings = self._config(params, project_dir=project_dir)
        # ``AIStereoPipeline.create`` validates an existing project's source
        # before committing its configuration. Do not mutate an existing
        # project here when a create request will subsequently be rejected.
        if not create:
            write_json_atomic(ProjectLayout.at(project_dir).ensure().config, settings)

        def callback(stage: str, completed: int, total: int, message: str | None) -> None:
            self.emit(
                {
                    "type": "progress",
                    "id": request_id,
                    "job_id": request_id,
                    "stage": stage,
                    "completed": max(0, int(completed)),
                    "total": max(0, int(total)),
                    **({"message": message} if message else {}),
                }
            )

        if create:
            input_path = params.get("input_path") or params.get("source_path")
            if not isinstance(input_path, str) or not input_path:
                raise ValidationError("input_path is required")
            return AIStereoPipeline.create(
                project_dir,
                input_path,
                name=str(params["name"]) if params.get("name") else None,
                config=settings,
                job_id=request_id,
                progress=callback,
                cancellation=token,
            )
        return AIStereoPipeline(
            project_dir,
            config=settings,
            job_id=request_id,
            progress=callback,
            cancellation=token,
            resume=_boolean_parameter(params, "resume", default=True),
            force_stages=set(params.get("force_stages") or []),
        )

    def _llm_provider(self, settings: AppConfig) -> OpenAIResponsesPresetProvider:
        return OpenAIResponsesPresetProvider(settings.llm)

    def dispatch(self, request: WorkerRequest, token: CancellationToken) -> Any:
        method, params = request.method, request.params
        if method == "ping":
            return {"ok": True, "version": __version__, "protocol_version": "1.0"}
        if method == "llm_status":
            return self._llm_provider(self._config(params)).status()
        if method in {"test_llm", "recommend_preset"}:
            settings = self._config(params)
            provider = self._llm_provider(settings)
            if method == "test_llm":
                features = ShotFeatures(
                    shot_id=1,
                    duration_seconds=3,
                    motion_score=0.2,
                    speech_ratio=0.7,
                    depth_spread=0.3,
                    foreground_ratio=0.35,
                    brightness=0.5,
                    cut_frequency_context=0.3,
                )
                try:
                    provider.recommend(features)
                except AIStereoError as exc:
                    return {
                        "connected": False,
                        "provider": settings.llm.provider,
                        "model": settings.llm.model,
                        "error_code": exc.code,
                    }
                except Exception:
                    return {
                        "connected": False,
                        "provider": settings.llm.provider,
                        "model": settings.llm.model,
                        "error_code": "llm_provider_error",
                    }
                return {
                    "connected": True,
                    "provider": settings.llm.provider,
                    "model": settings.llm.model,
                }
            else:
                features = ShotFeatures.model_validate(params.get("features"))
            director = LLMDirector(provider, fallback=settings.llm.fallback)
            decision = director.select(features)
            source = (
                "llm"
                if director.last_error is None
                else "neutral_fallback"
                if settings.llm.fallback == "neutral"
                else "rules_fallback"
            )
            return {
                "ok": director.last_error is None,
                "preset": decision.preset,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "fallback_used": director.last_error is not None,
                "source": source,
            }
        if method == "apply_shot_overrides":
            edit_request = ApplyShotOverridesRequest.model_validate(params)
            return apply_shot_overrides(edit_request)
        if method == "inspect" and not (
            params.get("project_dir") or params.get("work_dir") or params.get("project_path")
        ):
            input_path = params.get("input_path") or params.get("source_path")
            if not isinstance(input_path, str):
                raise ValidationError("input_path is required")
            return inspect_media(input_path, ffprobe_path=self._config(params).media.ffprobe_path)
        pipeline = self._pipeline(request.id, params, token, create=method == "create_project")
        if method == "create_project":
            return pipeline.summary()
        if method == "inspect":
            return pipeline.inspect()
        if method == "normalize":
            return pipeline.normalize()
        if method == "detect_shots":
            return pipeline.detect_shots()
        if method == "analyze_draft":
            return pipeline.analyze_draft(
                profile=str(params.get("profile", "representative_frames")),
                allow_fallback=_boolean_parameter(params, "allow_fallback", default=True),
            )
        if method == "estimate_depth":
            return pipeline.estimate_depth(
                backend_name=str(params["backend"]) if params.get("backend") else None,
                cache_dir=str(params["cache_dir"]) if params.get("cache_dir") else None,
                allow_fallback=_boolean_parameter(params, "allow_fallback", default=False),
            )
        if method == "extract_features":
            intervals = params.get("speech_intervals")
            return pipeline.extract_features(
                speech_intervals=intervals,
                allow_fallback=_boolean_parameter(params, "allow_fallback", default=True),
            )
        if method in {"create_stereo_script", "direct"}:
            script = pipeline.direct(
                director_name=str(params.get("director", "rules")),
                allow_fallback=_boolean_parameter(params, "allow_fallback", default=True),
            )
            return {
                "script": script,
                "revision": stereo_script_revision(script),
                "script_path": str(pipeline.layout.stereo_script),
            }
        if method == "render_preview":
            shot_id = int(params.get("shot_id") or 0)
            if shot_id < 1:
                raise ValidationError("shot_id must be at least 1")
            output = params.get("output_path") or str(
                pipeline.layout.previews_dir / f"shot_{shot_id:04d}.mp4"
            )
            path = pipeline.render(
                output,
                shot_id=shot_id,
                output_mode=str(params.get("output_mode", "anaglyph")),  # type: ignore[arg-type]
                allow_fallback=_boolean_parameter(params, "allow_fallback", default=True),
            )
            depth_manifest = self._depth_manifest(pipeline)
            synthetic_depth = not self._depth_status(pipeline)["production_ready"]
            return {
                "preview_path": str(path),
                "depth_backend": depth_manifest.backend,
                "synthetic_depth": synthetic_depth,
            }
        if method == "render_preview_frame":
            shot_id = int(params.get("shot_id") or 0)
            if shot_id < 1:
                raise ValidationError("shot_id must be at least 1")
            frame_offset = int(params.get("frame_offset") or 0)
            if frame_offset < 0:
                raise ValidationError("frame_offset must not be negative")
            output = params.get("output_path") or str(
                pipeline.layout.previews_dir / f"shot_{shot_id:04d}_f{frame_offset:06d}.png"
            )
            path = pipeline.render_frame(
                output,
                shot_id=shot_id,
                frame_offset=frame_offset,
                output_mode=str(params.get("output_mode", "anaglyph")),  # type: ignore[arg-type]
                allow_fallback=_boolean_parameter(params, "allow_fallback", default=True),
            )
            depth_manifest = self._depth_manifest(pipeline)
            return {
                "preview_path": str(path),
                "still": True,
                "frame_offset": frame_offset,
                "depth_backend": depth_manifest.backend,
                "synthetic_depth": not self._depth_status(pipeline)["production_ready"],
            }
        if method in {"render_final", "render"}:
            output = params.get("output_path") or str(
                pipeline.layout.renders_dir / "output_anaglyph.mp4"
            )
            depth_manifest = self._require_release_depth(pipeline)
            path = pipeline.render(
                output,
                output_mode=str(params.get("output_mode", "anaglyph")),  # type: ignore[arg-type]
            )
            return {
                "output_path": str(path),
                "qc_path": str(pipeline.layout.qc_report),
                "depth_backend": depth_manifest.backend,
                "synthetic_depth": depth_manifest.backend == "synthetic"
                or any(
                    item.backend == "synthetic" or item.fallback_used
                    for item in depth_manifest.shots
                ),
            }
        if method in {"generate_qc", "qc"}:
            return pipeline.qc()
        if method in {"run_pipeline", "run"}:
            output = params.get("output_path") or str(
                pipeline.layout.renders_dir / "output_anaglyph.mp4"
            )
            pipeline.inspect()
            pipeline.normalize()
            pipeline.detect_shots()
            pipeline.estimate_depth()
            pipeline.extract_features()
            pipeline.direct(director_name=str(params.get("director", "rules")))
            depth_manifest = self._require_release_depth(pipeline)
            path = pipeline.render(
                output,
                output_mode=str(params.get("output_mode", "anaglyph")),  # type: ignore[arg-type]
            )
            return {
                "output_path": str(path),
                "project": pipeline.summary(),
                "depth_backend": depth_manifest.backend,
                "synthetic_depth": depth_manifest.backend == "synthetic"
                or any(
                    item.backend == "synthetic" or item.fallback_used
                    for item in depth_manifest.shots
                ),
            }
        if method == "get_project":
            summary = pipeline.summary().model_dump(mode="json")
            summary["stereo_script_revision"] = (
                stereo_script_revision(StereoScript.model_validate(summary_script))
                if (summary_script := summary.get("stereo_script")) is not None
                else None
            )
            summary["depth_status"] = (
                self._depth_status(pipeline) if pipeline.layout.depth_metadata.is_file() else None
            )
            return summary
        raise ValidationError("Unknown worker method", details={"method": method})

    def _run_job(self, request: WorkerRequest, token: CancellationToken) -> None:
        try:
            result = self.dispatch(request, token)
            self.emit({"type": "result", "id": request.id, "result": result})
        except Exception as exc:
            self._error(request.id, exc)
        finally:
            with self._jobs_lock:
                self._jobs.pop(request.id, None)

    def accept_line(self, line: str) -> None:
        request_id = "unknown"
        try:
            if len(line.encode("utf-8")) > MAX_REQUEST_BYTES:
                raise ValidationError(
                    f"Worker request exceeds the {MAX_REQUEST_BYTES}-byte IPC limit"
                )
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValidationError("Worker request must be a JSON object")
            request_id = _safe_request_id(raw.get("id"))
            if _has_secret(raw.get("params", {})):
                raise ValidationError(
                    "Secrets must be supplied through the worker environment, not JSONL"
                )
            request = WorkerRequest.model_validate(raw)
            if request.method not in _ALLOWED_METHODS:
                raise ValidationError("Unknown worker method", details={"method": request.method})
            _validate_parameter_contract(request)
            if request.method in {"cancel", "cancel_job"}:
                target = str(request.params.get("job_id") or request.params.get("id") or "")
                with self._jobs_lock:
                    token = self._jobs.get(target)
                if token:
                    token.cancel()
                self.emit(
                    {
                        "type": "result",
                        "id": request.id,
                        "result": {"job_id": target, "cancel_requested": token is not None},
                    }
                )
                return
            token = CancellationToken()
            with self._jobs_lock:
                if request.id in self._jobs:
                    raise ValidationError("A job with this id is already active")
                self._jobs[request.id] = token
            self._executor.submit(self._run_job, request, token)
        except Exception as exc:
            self._error(request_id, exc)

    def _bounded_input_lines(self) -> Iterator[str]:
        # TextIO limits characters rather than encoded bytes. The character
        # bound still caps allocation; accept_line applies the authoritative
        # UTF-8 byte limit before JSON decoding.
        character_limit = MAX_REQUEST_BYTES + 2
        while True:
            line = sys.stdin.readline(character_limit)
            if not line:
                return
            if len(line) == character_limit and not line.endswith("\n"):
                while line and not line.endswith("\n"):
                    line = sys.stdin.readline(character_limit)
                self._error(
                    "unknown",
                    ValidationError(
                        f"Worker request exceeds the {MAX_REQUEST_BYTES}-byte IPC limit"
                    ),
                )
                continue
            yield line

    def run(self) -> None:
        for line in self._bounded_input_lines():
            without_newline = line.rstrip("\r\n")
            if without_newline.strip():
                self.accept_line(without_newline)
        self._executor.shutdown(wait=True, cancel_futures=False)


def main() -> None:
    worker = JSONLWorker()
    # Keep stdout protocol-only even if an optional model prints diagnostics.
    sys.stdout = sys.stderr
    worker.run()


if __name__ == "__main__":
    main()
