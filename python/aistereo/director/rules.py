"""Deterministic preset selection and guarded stereo script generation."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ComfortConfig
from ..models import (
    FeatureManifest,
    ShotFeatures,
    StereoPreset,
    StereoScript,
    StereoShot,
)
from .comfort_guard import apply_comfort_guard
from .presets import preset_parameters


@dataclass(frozen=True)
class DirectionDecision:
    preset: StereoPreset
    confidence: float
    reason: str


class RuleBasedDirector:
    """Small, auditable rule set; it never emits unrestricted disparities."""

    name = "rules-v0"

    def select(self, features: ShotFeatures) -> DirectionDecision:
        if features.speech_ratio >= 0.48 and features.motion_score <= 0.62:
            confidence = 0.65 + 0.25 * features.speech_ratio - 0.10 * features.motion_score
            return DirectionDecision(
                StereoPreset.DIALOGUE_SUBTLE,
                min(0.96, confidence),
                "speech-dominant shot with controlled motion",
            )
        if (
            features.depth_spread <= 0.24
            and features.foreground_ratio >= 0.30
            and features.motion_score <= 0.45
        ):
            confidence = 0.66 + 0.20 * features.foreground_ratio
            return DirectionDecision(
                StereoPreset.CLOSEUP_FLAT,
                min(0.94, confidence),
                "shallow, foreground-dominant shot",
            )
        if features.motion_score >= 0.58 and features.speech_ratio < 0.45:
            confidence = 0.60 + 0.25 * features.motion_score
            return DirectionDecision(
                StereoPreset.ACTION_CONTROLLED,
                min(0.94, confidence),
                "high visual motion requires controlled stereo",
            )
        if (
            features.motion_score <= 0.34
            and features.depth_spread >= 0.48
            and features.foreground_ratio <= 0.28
        ):
            confidence = 0.62 + 0.25 * features.depth_spread
            return DirectionDecision(
                StereoPreset.VISTA_DEEP,
                min(0.94, confidence),
                "stable shot with broad depth and little foreground occlusion",
            )
        # Neutral confidence stays above the default guard threshold: uncertainty
        # is represented by a conservative no-pop-out preset itself.
        return DirectionDecision(
            StereoPreset.NEUTRAL,
            0.60,
            "no specialist rule met a reliable threshold",
        )


def create_stereo_script(
    features: FeatureManifest,
    *,
    video_width: int,
    comfort: ComfortConfig | None = None,
    director: RuleBasedDirector | object | None = None,
    edge_violations: set[int] | None = None,
    hard_cut_shot_ids: set[int] | None = None,
) -> StereoScript:
    guard_config = comfort or ComfortConfig()
    rules = director or RuleBasedDirector()
    prior = None
    shots: list[StereoShot] = []
    for item in features.shots:
        # The safe default treats every shot boundary as hard. Callers may
        # explicitly omit gradual-transition shots from this set.
        previous = None if hard_cut_shot_ids is None or item.shot_id in hard_cut_shot_ids else prior
        decision = rules.select(item)  # type: ignore[attr-defined]
        requested = preset_parameters(decision.preset)
        applied, actions = apply_comfort_guard(
            requested,
            item,
            guard_config,
            confidence=decision.confidence,
            previous=previous,
            edge_violation=item.shot_id in (edge_violations or set()),
        )
        shots.append(
            StereoShot(
                shot_id=item.shot_id,
                preset=decision.preset,
                confidence=decision.confidence,
                parameters=applied,
                requested_parameters=requested,
                guard_actions=actions,
            )
        )
        prior = applied
    return StereoScript(video_width=video_width, shots=shots)
