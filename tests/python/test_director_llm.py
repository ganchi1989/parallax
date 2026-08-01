from __future__ import annotations

import json
import urllib.error

import pytest

from aistereo.config import ComfortConfig, LLMConfig
from aistereo.director.comfort_guard import apply_comfort_guard, guard_script_for_render
from aistereo.director.llm import (
    LLMAuthenticationError,
    LLMDirector,
    LLMProviderError,
    OpenAIResponsesPresetProvider,
    _default_transport,
)
from aistereo.director.presets import preset_parameters
from aistereo.director.rules import RuleBasedDirector, create_stereo_script
from aistereo.models import FeatureManifest, LLMPresetRecommendation, ShotFeatures, StereoPreset


def features(**changes: float) -> ShotFeatures:
    values = {
        "shot_id": 1,
        "duration_seconds": 3.0,
        "motion_score": 0.2,
        "speech_ratio": 0.0,
        "depth_spread": 0.4,
        "foreground_ratio": 0.2,
        "brightness": 0.5,
        "cut_frequency_context": 0.4,
        "depth_reliability": 1.0,
    }
    values.update(changes)
    return ShotFeatures(**values)


def test_rule_director_selects_distinct_presets() -> None:
    director = RuleBasedDirector()
    assert director.select(features(speech_ratio=0.8)).preset == StereoPreset.DIALOGUE_SUBTLE
    assert director.select(features(motion_score=0.9)).preset == StereoPreset.ACTION_CONTROLLED
    assert (
        director.select(features(depth_spread=0.8, foreground_ratio=0.1)).preset
        == StereoPreset.VISTA_DEEP
    )
    assert (
        director.select(features(depth_spread=0.1, foreground_ratio=0.6)).preset
        == StereoPreset.CLOSEUP_FLAT
    )


def test_comfort_guard_clamps_and_records_every_material_change() -> None:
    requested = preset_parameters(StereoPreset.ACTION_CONTROLLED)
    requested.depth_strength = 1.8
    requested.max_popout_disparity_norm = 0.02
    requested.max_background_disparity_norm = 0.03
    applied, actions = apply_comfort_guard(
        requested,
        features(motion_score=0.95, depth_reliability=0.2),
        ComfortConfig(),
        confidence=0.2,
        edge_violation=True,
    )
    assert applied.depth_strength <= 1
    assert applied.max_background_disparity_norm <= 0.01
    assert applied.max_popout_disparity_norm == 0
    codes = {action.code for action in actions}
    assert {
        "depth_strength_clamped",
        "reduced_due_to_high_motion",
        "popout_disabled_due_to_low_confidence",
    } <= codes


def test_all_script_decisions_pass_the_guard() -> None:
    manifest = FeatureManifest(
        shots=[features(motion_score=0.9), features(shot_id=2, speech_ratio=0.8)]
    )
    script = create_stereo_script(manifest, video_width=1280, comfort=ComfortConfig())
    assert all(shot.requested_parameters is not None for shot in script.shots)
    assert all(shot.parameters.max_popout_disparity_norm <= 0.004 for shot in script.shots)


def test_manual_script_cannot_bypass_render_time_guard() -> None:
    item = features(motion_score=0.9)
    script = create_stereo_script(FeatureManifest(shots=[item]), video_width=1280)
    script.shots[0].manual_override = True
    script.shots[0].parameters.depth_strength = 2
    script.shots[0].parameters.max_popout_disparity_norm = 0.04
    guarded = guard_script_for_render(script, FeatureManifest(shots=[item]), ComfortConfig())
    assert guarded.shots[0].parameters.depth_strength <= 1
    assert guarded.shots[0].parameters.max_popout_disparity_norm <= 0.004
    assert guarded.shots[0].guard_actions


def test_render_guard_resets_convergence_prior_at_hard_cut() -> None:
    first = features(shot_id=1)
    second = features(shot_id=2)
    script = create_stereo_script(FeatureManifest(shots=[first, second]), video_width=1280)
    for shot, convergence in zip(script.shots, (0.1, 0.9), strict=True):
        shot.manual_override = True
        shot.parameters.convergence_depth_percentile = convergence
    hard_cuts = guard_script_for_render(
        script,
        FeatureManifest(shots=[first, second]),
        ComfortConfig(max_convergence_change=0.18),
        hard_cut_shot_ids={1, 2},
    )
    gradual = guard_script_for_render(
        script,
        FeatureManifest(shots=[first, second]),
        ComfortConfig(max_convergence_change=0.18),
        hard_cut_shot_ids={1},
    )
    assert hard_cuts.shots[1].parameters.convergence_depth_percentile == 0.9
    assert gradual.shots[1].parameters.convergence_depth_percentile == pytest.approx(0.28)


def test_llm_provider_sends_only_features_and_strict_schema() -> None:
    captured = {}

    def transport(url, headers, body, timeout):
        captured.update(url=url, headers=headers, body=body, timeout=timeout)
        recommendation = {
            "preset": "dialogue_subtle",
            "narrative_importance": 0.6,
            "stereo_emphasis": "low",
            "reason": "stable dialogue",
            "confidence": 0.88,
        }
        return {"output_text": json.dumps(recommendation)}

    provider = OpenAIResponsesPresetProvider(
        LLMConfig(), api_key="test-secret", transport=transport
    )
    result = provider.recommend(features(speech_ratio=0.8))
    assert result.preset == StereoPreset.DIALOGUE_SUBTLE
    assert captured["body"]["store"] is False
    assert captured["body"]["max_output_tokens"] == 300
    assert "test-secret" not in json.dumps(captured["body"])
    assert captured["headers"]["User-Agent"].startswith("ai-stereo-director/")
    user_text = captured["body"]["input"][1]["content"][0]["text"]
    assert set(json.loads(user_text)) == {"shot_features"}


def test_llm_invalid_schema_falls_back_to_rules_without_numeric_control() -> None:
    def invalid_transport(url, headers, body, timeout):
        return {"output_text": '{"preset":"unbounded_extreme"}'}

    provider = OpenAIResponsesPresetProvider(LLMConfig(), api_key="x", transport=invalid_transport)
    director = LLMDirector(provider)
    decision = director.select(features(speech_ratio=0.9))
    assert decision.preset == StereoPreset.DIALOGUE_SUBTLE
    assert director.last_error is not None


def test_llm_low_confidence_falls_back_to_neutral() -> None:
    class UncertainProvider:
        def recommend(self, features):
            return LLMPresetRecommendation(
                preset="vista_deep",
                narrative_importance=0.4,
                stereo_emphasis="medium",
                reason="The visual evidence is ambiguous.",
                confidence=0.3,
            )

    director = LLMDirector(UncertainProvider())
    decision = director.select(features(depth_spread=0.9))
    assert decision.preset == StereoPreset.NEUTRAL
    assert decision.confidence == 0.3
    assert "safe threshold" in decision.reason
    assert director.last_error is None


def test_llm_status_never_exposes_key() -> None:
    provider = OpenAIResponsesPresetProvider(LLMConfig(), api_key="super-secret")
    assert provider.status()["configured"] is True
    assert "secret" not in json.dumps(provider.status()).lower()


def test_default_llm_transport_caps_response_body(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b"x" * size

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())
    try:
        _default_transport("https://api.openai.com/v1/responses", {}, {}, 1)
    except LLMProviderError as exc:
        assert "safety limit" in exc.message
    else:
        raise AssertionError("oversized response was accepted")


def test_default_llm_transport_maps_401_without_response_text(monkeypatch) -> None:
    def fail(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "secret server detail", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fail)
    try:
        _default_transport("https://api.openai.com/v1/responses", {}, {}, 1)
    except LLMAuthenticationError as exc:
        assert exc.code == "llm_authentication_failed"
        assert "secret server detail" not in exc.message
    else:
        raise AssertionError("HTTP 401 was not mapped")
