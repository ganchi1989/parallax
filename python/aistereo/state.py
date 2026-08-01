"""Crash-safe stage state and cancellation primitives."""

from __future__ import annotations

import threading
from pathlib import Path

from . import __version__
from .artifacts import ProjectLayout, fingerprint, read_model, write_json_atomic
from .errors import ArtifactError
from .models import (
    PIPELINE_ALGORITHM_VERSION,
    PIPELINE_STATE_SCHEMA_VERSION,
    PipelineState,
    StageState,
    StageStatus,
    utc_now_iso,
)


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class PipelineStateStore:
    def __init__(self, layout: ProjectLayout, job_id: str) -> None:
        self.layout = layout
        if layout.pipeline_state.exists():
            try:
                loaded = read_model(layout.pipeline_state, PipelineState)
            except ArtifactError:
                # Pipeline state is a derived cache index, not user-authored
                # project data. A truncated or obsolete cache must never make
                # an otherwise valid project impossible to reopen.
                self.state = PipelineState(job_id=job_id)
                self.save()
                return
            if (
                loaded.schema_version != PIPELINE_STATE_SCHEMA_VERSION
                or loaded.engine_version != __version__
                or loaded.algorithm_version != PIPELINE_ALGORITHM_VERSION
            ):
                self.state = PipelineState(job_id=job_id)
                self.save()
            else:
                self.state = loaded
                self.state.job_id = job_id
        else:
            self.state = PipelineState(job_id=job_id)
            self.save()

    def save(self) -> None:
        self.state.updated_at = utc_now_iso()
        write_json_atomic(self.layout.pipeline_state, self.state)

    def can_resume(self, stage: str, stage_fingerprint: str, outputs: list[Path]) -> bool:
        current = self.state.stages.get(stage)
        expected_outputs = [str(path.expanduser().resolve()) for path in outputs]
        recorded_outputs = (
            [str(Path(path).expanduser().resolve()) for path in current.outputs] if current else []
        )
        current_fingerprints = (
            {str(path.expanduser().resolve()): fingerprint([path]) for path in outputs}
            if current and all(path.is_file() for path in outputs)
            else {}
        )
        return bool(
            current
            and current.status == StageStatus.COMPLETE
            and current.fingerprint == stage_fingerprint
            and recorded_outputs == expected_outputs
            and current.output_fingerprints
            and current.output_fingerprints == current_fingerprints
        )

    def dependencies_are_current(self, stage: str, dependency_fingerprint: str) -> bool:
        """Check generated-input provenance without rejecting an intentional output edit."""

        current = self.state.stages.get(stage)
        return bool(
            current
            and current.status == StageStatus.COMPLETE
            and current.dependency_fingerprint == dependency_fingerprint
        )

    def begin(
        self,
        stage: str,
        stage_fingerprint: str,
        *,
        dependency_fingerprint: str | None = None,
    ) -> None:
        self.state.stages[stage] = StageState(
            status=StageStatus.RUNNING,
            fingerprint=stage_fingerprint,
            dependency_fingerprint=dependency_fingerprint,
            started_at=utc_now_iso(),
        )
        self.save()

    def complete(self, stage: str, outputs: list[Path]) -> None:
        current = self.state.stages[stage]
        missing = [str(path) for path in outputs if not path.is_file()]
        if missing:
            raise ArtifactError(
                "Stage did not produce every declared output", details={"missing": missing}
            )
        current.status = StageStatus.COMPLETE
        resolved_outputs = [path.expanduser().resolve() for path in outputs]
        current.outputs = [str(path) for path in resolved_outputs]
        current.output_fingerprints = {str(path): fingerprint([path]) for path in resolved_outputs}
        current.completed_at = utc_now_iso()
        current.error = None
        self.save()

    def fail(self, stage: str, message: str, *, cancelled: bool = False) -> None:
        current = self.state.stages.get(stage, StageState())
        current.status = StageStatus.CANCELLED if cancelled else StageStatus.FAILED
        current.error = message[:1000]
        current.completed_at = utc_now_iso()
        self.state.stages[stage] = current
        self.save()
