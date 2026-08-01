from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from aistereo.artifacts import ProjectLayout
from aistereo.config import DepthConfig
from aistereo.depth import SyntheticDepthBackend, normalize_depth_shot
from aistereo.director.rules import create_stereo_script
from aistereo.features.extractor import extract_shot_features
from aistereo.models import FeatureManifest, Shot, WorkerRequest
from aistereo.pipeline import AIStereoPipeline
from aistereo.qc.metrics import QCAccumulator
from aistereo.qc.report import build_qc_report
from aistereo.render.frame import render_stereo_frame
from aistereo.state import PipelineStateStore
from aistereo.worker import MAX_REQUEST_BYTES, JSONLWorker


def test_pipeline_state_resume_requires_fingerprint_and_outputs(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path).ensure()
    store = PipelineStateStore(layout, "job")
    output = tmp_path / "result.json"
    store.begin("stage", "abc")
    output.write_text("{}", encoding="utf-8")
    store.complete("stage", [output])
    assert store.can_resume("stage", "abc", [output])
    assert not store.can_resume("stage", "different", [output])
    output.unlink()
    assert not store.can_resume("stage", "abc", [output])


def test_cached_stage_reaches_complete_only_after_cached_result_loads(tmp_path: Path) -> None:
    events: list[tuple[str, int, int, str | None]] = []
    pipeline = AIStereoPipeline(tmp_path, progress=lambda *event: events.append(event))
    output = tmp_path / "cached.json"
    pipeline.state.begin("cached", "fingerprint")
    output.write_text("{}", encoding="utf-8")
    pipeline.state.complete("cached", [output])

    def load_cached() -> dict[str, bool]:
        assert events == [("cached", 0, 1, "Validating cached stage output")]
        return {"ready": True}

    result = pipeline._execute_stage(
        "cached",
        "fingerprint",
        [output],
        lambda: pytest.fail("a resumable stage must not execute its operation"),
        load_cached,
    )

    assert result == {"ready": True}
    assert events == [
        ("cached", 0, 1, "Validating cached stage output"),
        ("cached", 1, 1, "Using cached stage output"),
    ]


def test_cached_stage_does_not_emit_complete_when_cached_result_is_invalid(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, int, int, str | None]] = []
    pipeline = AIStereoPipeline(tmp_path, progress=lambda *event: events.append(event))
    output = tmp_path / "cached.json"
    pipeline.state.begin("cached", "fingerprint")
    output.write_text("{}", encoding="utf-8")
    pipeline.state.complete("cached", [output])

    def reject_cached() -> None:
        raise ValueError("invalid cached result")

    with pytest.raises(ValueError, match="invalid cached result"):
        pipeline._execute_stage(
            "cached",
            "fingerprint",
            [output],
            lambda: pytest.fail("a resumable stage must not execute its operation"),
            reject_cached,
        )

    assert events == [("cached", 0, 1, "Validating cached stage output")]


def test_corrupt_pipeline_state_is_rebuilt_as_an_empty_cache(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path).ensure()
    layout.pipeline_state.write_text('{"stages":', encoding="utf-8")

    store = PipelineStateStore(layout, "recovered-job")

    assert store.state.job_id == "recovered-job"
    assert store.state.stages == {}
    assert (
        json.loads(layout.pipeline_state.read_text(encoding="utf-8"))["job_id"] == "recovered-job"
    )


def test_algorithmic_synthetic_end_to_end_has_guarded_qc() -> None:
    frames = []
    for index in range(4):
        frame = np.zeros((24, 32, 3), dtype=np.uint8)
        frame[:, index : index + 8, 2] = 180
        frames.append(frame)
    shot = Shot(shot_id=1, start_frame=0, end_frame=3, start_time=0, end_time=4 / 24)
    raw = SyntheticDepthBackend().estimate(
        frames, shot_id=1, config=DepthConfig(width=32, height=32, temporal_alpha=0.5)
    )
    depth, _ = normalize_depth_shot(raw, DepthConfig(width=32, height=32))
    feature = extract_shot_features(shot, frames, depth)
    script = create_stereo_script(FeatureManifest(shots=[feature]), video_width=32)
    qc = QCAccumulator()
    for index, (frame, plane) in enumerate(zip(frames, depth, strict=True)):
        rendered = render_stereo_frame(frame, plane, script.shots[0].parameters)
        qc.add_frame(
            1, index, rendered.disparity_norm, rendered.holes, rendered.edge_violations, plane
        )
    report = build_qc_report(qc, expected_frame_count=4, rendered_frame_count=4)
    assert report.frame_count == 4
    assert report.dropped_frames == 0
    assert report.max_popout_disparity_norm <= 0.004


def test_worker_ping_dispatch_is_protocol_ready() -> None:
    worker = JSONLWorker()
    request = WorkerRequest(id="ping-1", method="ping", params={})
    result = worker.dispatch(
        request,
        token=__import__("aistereo.state", fromlist=["CancellationToken"]).CancellationToken(),
    )
    worker._executor.shutdown(wait=True)
    assert result["ok"] is True
    assert result["protocol_version"] == "1.0"


def test_worker_rejects_secrets_in_jsonl_without_echoing_them() -> None:
    worker = JSONLWorker()
    events = []
    worker.emit = events.append  # type: ignore[method-assign]
    worker.accept_line(
        json.dumps({"id": "x", "method": "llm_status", "params": {"api_key": "do-not-echo"}})
    )
    worker._executor.shutdown(wait=True)
    serialized = json.dumps(events)
    assert events[0]["type"] == "error"
    assert "do-not-echo" not in serialized


def test_worker_rejects_obfuscated_and_nested_secret_names() -> None:
    secret_keys = [
        "apiKey",
        "openaiApiKey",
        "accessToken",
        "Authorization",
        "credential",
        "bearer",
    ]
    for index, key in enumerate(secret_keys):
        worker = JSONLWorker()
        events = []
        worker.emit = events.append  # type: ignore[method-assign]
        worker.accept_line(
            json.dumps(
                {
                    "id": f"secret-{index}",
                    "method": "llm_status",
                    "params": {"nested": [{key: "must-not-echo"}]},
                }
            )
        )
        worker._executor.shutdown(wait=True)
        assert events[0]["type"] == "error"
        assert "must-not-echo" not in json.dumps(events)


def test_worker_rejects_unknown_parameters_at_direct_jsonl_boundary() -> None:
    worker = JSONLWorker()
    events = []
    worker.emit = events.append  # type: ignore[method-assign]
    worker.accept_line(json.dumps({"id": "strict", "method": "ping", "params": {"extra": 1}}))
    worker._executor.shutdown(wait=True)
    assert events[0]["type"] == "error"
    assert events[0]["error"]["code"] == "invalid_request"
    assert events[0]["error"]["details"]["parameters"] == ["extra"]


@pytest.mark.parametrize("project_name", ["Cinema Déjà Vu", "界" * 160])
def test_worker_accepts_and_persists_a_bounded_project_name(
    tmp_path: Path, project_name: str
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    project_dir = tmp_path / "project"
    worker = JSONLWorker()
    events = []
    worker.emit = events.append  # type: ignore[method-assign]

    worker.accept_line(
        json.dumps(
            {
                "id": "create-named-project",
                "method": "create_project",
                "params": {
                    "input_path": str(source),
                    "project_dir": str(project_dir),
                    "name": project_name,
                },
            }
        )
    )
    worker._executor.shutdown(wait=True)

    assert events[0]["type"] == "result"
    marker = json.loads(ProjectLayout.at(project_dir).project.read_text(encoding="utf-8"))
    assert marker["name"] == project_name


@pytest.mark.parametrize(
    "project_name",
    ["", "   ", "x" * 161, "two\nlines", "control\u0085name", 42],
)
def test_worker_rejects_invalid_project_names(project_name: object) -> None:
    worker = JSONLWorker()
    events = []
    worker.emit = events.append  # type: ignore[method-assign]
    worker.accept_line(
        json.dumps(
            {
                "id": "invalid-project-name",
                "method": "create_project",
                "params": {
                    "input_path": "unused.mp4",
                    "project_dir": "unused-project",
                    "name": project_name,
                },
            }
        )
    )
    worker._executor.shutdown(wait=True)

    assert events == [
        {
            "type": "error",
            "id": "invalid-project-name",
            "error": {
                "code": "invalid_request",
                "message": "Request contains an invalid project name",
                "retryable": False,
                "details": {"parameter": "name"},
            },
        }
    ]


def test_named_project_contract_still_rejects_unknown_parameters() -> None:
    worker = JSONLWorker()
    events = []
    worker.emit = events.append  # type: ignore[method-assign]
    worker.accept_line(
        json.dumps(
            {
                "id": "strict-named-project",
                "method": "create_project",
                "params": {
                    "input_path": "unused.mp4",
                    "project_dir": "unused-project",
                    "name": "Feature",
                    "display_name": "must remain unsupported",
                },
            }
        )
    )
    worker._executor.shutdown(wait=True)

    assert events[0]["type"] == "error"
    assert events[0]["error"]["code"] == "invalid_request"
    assert events[0]["error"]["details"]["parameters"] == ["display_name"]


@pytest.mark.parametrize("name,value", [("resume", "false"), ("swap_eyes", 0)])
def test_worker_rejects_non_boolean_boolean_parameters(name: str, value: object) -> None:
    worker = JSONLWorker()
    events = []
    worker.emit = events.append  # type: ignore[method-assign]
    worker.accept_line(
        json.dumps(
            {
                "id": "strict-boolean",
                "method": "render_final",
                "params": {"project_dir": "unused", name: value},
            }
        )
    )
    worker._executor.shutdown(wait=True)

    assert events == [
        {
            "type": "error",
            "id": "strict-boolean",
            "error": {
                "code": "invalid_request",
                "message": "Request contains a boolean parameter with the wrong type",
                "retryable": False,
                "details": {"parameter": name},
            },
        }
    ]


def test_worker_bounds_and_drains_oversized_jsonl_lines(monkeypatch) -> None:
    worker = JSONLWorker()
    events = []
    worker.emit = events.append  # type: ignore[method-assign]
    oversized = "x" * (MAX_REQUEST_BYTES + 20)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(oversized + "\n" + json.dumps({"id": "ping-ok", "method": "ping"}) + "\n"),
    )

    worker.run()

    assert events[0]["type"] == "error"
    assert events[0]["id"] == "unknown"
    assert events[0]["error"]["code"] == "invalid_request"
    assert events[1]["type"] == "result"
    assert events[1]["id"] == "ping-ok"


def test_worker_llm_test_reports_connectivity_not_rules_fallback(monkeypatch) -> None:
    monkeypatch.delenv("AISTEREO_LLM_API_KEY", raising=False)
    worker = JSONLWorker()
    token = __import__("aistereo.state", fromlist=["CancellationToken"]).CancellationToken()
    result = worker.dispatch(WorkerRequest(id="llm", method="test_llm", params={}), token)
    worker._executor.shutdown(wait=True)
    assert result["connected"] is False
    assert result["provider"] == "openai-responses"
    assert result["error_code"] == "llm_provider_error"


def test_worker_recommendation_labels_rules_fallback(monkeypatch) -> None:
    class FailingProvider:
        def recommend(self, features):
            raise RuntimeError("offline")

    worker = JSONLWorker()
    monkeypatch.setattr(worker, "_llm_provider", lambda settings: FailingProvider())
    feature = {
        "shot_id": 1,
        "duration_seconds": 2,
        "motion_score": 0.1,
        "speech_ratio": 0.9,
        "depth_spread": 0.2,
        "foreground_ratio": 0.4,
        "brightness": 0.5,
        "cut_frequency_context": 0.2,
    }
    token = __import__("aistereo.state", fromlist=["CancellationToken"]).CancellationToken()
    result = worker.dispatch(
        WorkerRequest(id="ask", method="recommend_preset", params={"features": feature}), token
    )
    worker._executor.shutdown(wait=True)
    assert result["source"] == "rules_fallback"
    assert result["fallback_used"] is True


def test_pipeline_rejects_unknown_output_mode_before_processing(tmp_path: Path) -> None:
    pipeline = object.__new__(AIStereoPipeline)
    with pytest.raises(Exception, match="Unknown render output mode"):
        pipeline.render(tmp_path / "x.mp4", output_mode="mystery")  # type: ignore[arg-type]


def test_worker_accepts_bounded_model_override() -> None:
    worker = JSONLWorker()
    settings = worker._config({"model": "gpt-5.6-terra"})
    worker._executor.shutdown(wait=True)
    assert settings.llm.model == "gpt-5.6-terra"
