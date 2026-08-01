"""Versioned, bounded creative presets."""

from __future__ import annotations

from ..models import StereoParameters, StereoPreset

PRESETS: dict[StereoPreset, StereoParameters] = {
    StereoPreset.DIALOGUE_SUBTLE: StereoParameters(
        depth_strength=0.55,
        convergence_depth_percentile=0.58,
        max_background_disparity_norm=0.007,
        max_popout_disparity_norm=0.002,
        temporal_smoothing=0.90,
        transition_frames=10,
        edge_protection=True,
    ),
    StereoPreset.ACTION_CONTROLLED: StereoParameters(
        depth_strength=0.68,
        convergence_depth_percentile=0.54,
        max_background_disparity_norm=0.008,
        max_popout_disparity_norm=0.0025,
        temporal_smoothing=0.72,
        transition_frames=4,
        edge_protection=True,
    ),
    StereoPreset.VISTA_DEEP: StereoParameters(
        depth_strength=0.90,
        convergence_depth_percentile=0.70,
        max_background_disparity_norm=0.010,
        max_popout_disparity_norm=0.001,
        temporal_smoothing=0.92,
        transition_frames=12,
        edge_protection=True,
    ),
    StereoPreset.CLOSEUP_FLAT: StereoParameters(
        depth_strength=0.38,
        convergence_depth_percentile=0.72,
        max_background_disparity_norm=0.005,
        max_popout_disparity_norm=0.001,
        temporal_smoothing=0.94,
        transition_frames=10,
        edge_protection=True,
    ),
    StereoPreset.NEUTRAL: StereoParameters(
        depth_strength=0.45,
        convergence_depth_percentile=0.55,
        max_background_disparity_norm=0.006,
        max_popout_disparity_norm=0.0,
        temporal_smoothing=0.92,
        transition_frames=8,
        edge_protection=True,
    ),
}


def preset_parameters(preset: StereoPreset) -> StereoParameters:
    return PRESETS[preset].model_copy(deep=True)
