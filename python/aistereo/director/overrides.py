"""Optimistic, atomic editing of the human-authorable stereo script."""

from __future__ import annotations

import hashlib
import json
import threading

from ..artifacts import ProjectLayout, read_model, write_json_atomic
from ..errors import RevisionConflictError, ValidationError
from ..models import (
    ApplyShotOverridesRequest,
    ApplyShotOverridesResult,
    StereoScript,
)

_WRITE_LOCK = threading.Lock()


def stereo_script_revision(script: StereoScript) -> str:
    canonical = json.dumps(
        script.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def apply_shot_overrides(request: ApplyShotOverridesRequest) -> ApplyShotOverridesResult:
    layout = ProjectLayout.at(request.project_dir).ensure()
    if not layout.stereo_script.is_file():
        raise ValidationError(
            "Project has no stereo script to edit",
            details={"project_dir": str(layout.root)},
        )
    with _WRITE_LOCK:
        script = read_model(layout.stereo_script, StereoScript)
        current_revision = stereo_script_revision(script)
        if current_revision != request.expected_revision:
            raise RevisionConflictError(
                "Stereo script changed since it was loaded",
                details={
                    "expected_revision": request.expected_revision,
                    "current_revision": current_revision,
                },
            )
        shots_by_id = {shot.shot_id: shot for shot in script.shots}
        missing = sorted(
            item.shot_id for item in request.overrides if item.shot_id not in shots_by_id
        )
        if missing:
            raise ValidationError(
                "One or more override shots do not exist",
                details={"shot_ids": missing},
            )
        for override in request.overrides:
            shot = shots_by_id[override.shot_id]
            requested = override.parameters.model_copy(deep=True)
            shot.preset = override.preset
            shot.parameters = requested.model_copy(deep=True)
            shot.requested_parameters = requested
            shot.manual_override = True
            shot.guard_actions = []
        write_json_atomic(layout.stereo_script, script)
        updated = read_model(layout.stereo_script, StereoScript)
        revision = stereo_script_revision(updated)
    return ApplyShotOverridesResult(
        script=updated,
        revision=revision,
        updated_shot_ids=[item.shot_id for item in request.overrides],
        script_path=str(layout.stereo_script),
    )
