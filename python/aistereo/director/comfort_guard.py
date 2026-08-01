"""Non-negotiable final boundary between direction and rendering."""

from __future__ import annotations

from ..config import ComfortConfig
from ..errors import ValidationError
from ..models import (
    FeatureManifest,
    GuardAction,
    ShotFeatures,
    StereoParameters,
    StereoScript,
    StereoShot,
)


def _action(
    actions: list[GuardAction],
    code: str,
    message: str,
    requested: float | bool | None,
    applied: float | bool | None,
) -> None:
    if requested != applied:
        actions.append(
            GuardAction(code=code, message=message, requested=requested, applied=applied)
        )


def apply_comfort_guard(
    requested: StereoParameters,
    features: ShotFeatures,
    config: ComfortConfig,
    *,
    confidence: float,
    previous: StereoParameters | None = None,
    edge_violation: bool = False,
) -> tuple[StereoParameters, list[GuardAction]]:
    """Clamp and reduce a decision; there is deliberately no bypass flag."""

    applied = requested.model_copy(deep=True)
    actions: list[GuardAction] = []

    old = applied.depth_strength
    applied.depth_strength = min(old, config.max_depth_strength)
    _action(
        actions,
        "depth_strength_clamped",
        "Depth strength exceeded the project limit",
        old,
        applied.depth_strength,
    )

    old = applied.max_popout_disparity_norm
    applied.max_popout_disparity_norm = min(old, config.max_popout_disparity_norm)
    _action(
        actions,
        "popout_clamped",
        "Foreground disparity exceeded the project limit",
        old,
        applied.max_popout_disparity_norm,
    )

    old = applied.max_background_disparity_norm
    applied.max_background_disparity_norm = min(old, config.max_background_disparity_norm)
    _action(
        actions,
        "background_clamped",
        "Background disparity exceeded the project limit",
        old,
        applied.max_background_disparity_norm,
    )

    if features.motion_score >= config.high_motion_threshold:
        old_strength = applied.depth_strength
        applied.depth_strength *= config.high_motion_reduction
        _action(
            actions,
            "reduced_due_to_high_motion",
            "Rapid image motion reduced stereo magnitude",
            old_strength,
            applied.depth_strength,
        )
        old_popout = applied.max_popout_disparity_norm
        applied.max_popout_disparity_norm *= config.high_motion_reduction
        _action(
            actions,
            "popout_reduced_due_to_high_motion",
            "Rapid image motion reduced foreground disparity",
            old_popout,
            applied.max_popout_disparity_norm,
        )

    if features.depth_reliability < config.low_reliability_threshold:
        old_strength = applied.depth_strength
        applied.depth_strength *= config.unreliable_depth_reduction
        _action(
            actions,
            "reduced_due_to_unreliable_depth",
            "Low depth confidence reduced stereo magnitude",
            old_strength,
            applied.depth_strength,
        )

    if confidence < config.low_confidence_threshold:
        old = applied.max_popout_disparity_norm
        applied.max_popout_disparity_norm = 0.0
        _action(
            actions,
            "popout_disabled_due_to_low_confidence",
            "Low direction confidence disables foreground pop-out",
            old,
            applied.max_popout_disparity_norm,
        )

    if edge_violation and applied.edge_protection:
        old = applied.max_popout_disparity_norm
        applied.max_popout_disparity_norm = 0.0
        _action(
            actions,
            "popout_disabled_due_to_edge_violation",
            "Foreground at the image boundary disables pop-out",
            old,
            applied.max_popout_disparity_norm,
        )

    if previous is not None:
        requested_convergence = applied.convergence_depth_percentile
        low = max(0.0, previous.convergence_depth_percentile - config.max_convergence_change)
        high = min(1.0, previous.convergence_depth_percentile + config.max_convergence_change)
        applied.convergence_depth_percentile = min(
            high, max(low, applied.convergence_depth_percentile)
        )
        _action(
            actions,
            "convergence_change_clamped",
            "Shot-to-shot convergence jump exceeded the comfort limit",
            requested_convergence,
            applied.convergence_depth_percentile,
        )

    # A second final clamp protects against arithmetic and future rule changes.
    applied.depth_strength = min(config.max_depth_strength, max(0.0, applied.depth_strength))
    applied.max_popout_disparity_norm = min(
        config.max_popout_disparity_norm, max(0.0, applied.max_popout_disparity_norm)
    )
    applied.max_background_disparity_norm = min(
        config.max_background_disparity_norm,
        max(0.0, applied.max_background_disparity_norm),
    )
    return applied, actions


def guard_script_for_render(
    script: StereoScript,
    features: FeatureManifest,
    config: ComfortConfig,
    *,
    edge_violations: set[int] | None = None,
    hard_cut_shot_ids: set[int] | None = None,
) -> StereoScript:
    """Reapply the mandatory guard at the final render boundary.

    Generated scripts are recalculated from their recorded requested values,
    making this operation idempotent. Explicit manual overrides are treated as
    new requests and clamped exactly once.
    """

    features_by_id = {item.shot_id: item for item in features.shots}
    previous: StereoParameters | None = None
    guarded: list[StereoShot] = []
    for shot in script.shots:
        prior_for_guard = (
            None if hard_cut_shot_ids is None or shot.shot_id in hard_cut_shot_ids else previous
        )
        item = features_by_id.get(shot.shot_id)
        if item is None:
            raise ValidationError(
                "Stereo script references a shot without features",
                details={"shot_id": shot.shot_id},
            )
        requested = (
            shot.parameters.model_copy(deep=True)
            if shot.manual_override or shot.requested_parameters is None
            else shot.requested_parameters.model_copy(deep=True)
        )
        applied, actions = apply_comfort_guard(
            requested,
            item,
            config,
            confidence=shot.confidence,
            previous=prior_for_guard,
            edge_violation=shot.shot_id in (edge_violations or set()),
        )
        guarded.append(
            shot.model_copy(
                update={
                    "parameters": applied,
                    "requested_parameters": requested,
                    "guard_actions": actions,
                },
                deep=True,
            )
        )
        previous = applied
    return script.model_copy(update={"shots": guarded}, deep=True)
