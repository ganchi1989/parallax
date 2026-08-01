"""Errors which may safely cross the JSONL process boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class AIStereoError(Exception):
    """Base error with a stable machine-readable code."""

    code = "engine_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})
        if retryable is not None:
            self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(AIStereoError):
    code = "invalid_request"


class DependencyUnavailableError(AIStereoError):
    code = "dependency_unavailable"


class ExternalToolError(AIStereoError):
    code = "external_tool_failed"


class ArtifactError(AIStereoError):
    code = "artifact_error"


class PipelineCancelled(AIStereoError):
    code = "cancelled"


class StageError(AIStereoError):
    code = "stage_failed"


class RevisionConflictError(AIStereoError):
    code = "revision_conflict"


class SyntheticDepthFinalError(AIStereoError):
    code = "synthetic_depth_final_forbidden"
