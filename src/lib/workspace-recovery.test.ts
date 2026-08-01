import { describe, expect, it } from 'vitest';
import { verifiedWorkspaceRecovery } from './workspace-recovery';

const feature = {
  shot_id: 1,
  duration_seconds: 4,
  motion_score: 0.2,
  speech_ratio: 0.1,
  depth_spread: 0.6,
  foreground_ratio: 0.3,
  brightness: 0.5,
  cut_frequency_context: 0.25,
  camera_movement: 'static',
  depth_reliability: 0.9
};

const script = {
  shots: [{
    shot_id: 1,
    preset: 'neutral',
    confidence: 0.8,
    parameters: {
      depth_strength: 0.4,
      convergence_depth_percentile: 0.55,
      max_background_disparity_norm: 0.006,
      max_popout_disparity_norm: 0,
      temporal_smoothing: 0.9,
      transition_frames: 8,
      edge_protection: true
    }
  }]
};

function summary() {
  return {
    pipeline_state: { stages: {
      analyze_draft: { status: 'complete' },
      estimate_depth: { status: 'complete' },
      extract_features: { status: 'complete' },
      direct: { status: 'complete' }
    } },
    depth_status: { production_ready: true, backend: 'video_depth_anything' },
    features: { shots: [feature] },
    stereo_script: script,
    stereo_script_revision: 'a'.repeat(64),
    draft_analysis: {
      analysis_tier: 'sampled',
      profile: 'representative_frames',
      features: { shots: [feature] },
      script,
      revision: 'b'.repeat(64),
      coverage: {
        shot_ids: [1],
        sampled_frames: 4,
        total_frames: 120,
        per_shot: [{ shot_id: 1, sampled_frames: 4, total_frames: 120 }]
      }
    }
  };
}

describe('completed workspace recovery', () => {
  it('prefers fully validated production artifacts over a cached draft', () => {
    const recovered = verifiedWorkspaceRecovery(summary(), [1]);
    expect(recovered).toMatchObject({ analysisTier: 'production', revision: 'a'.repeat(64), depthMode: 'production' });
  });

  it('recovers a validated sampled draft when production is incomplete', () => {
    const value = summary();
    value.pipeline_state.stages.direct.status = 'running';
    const recovered = verifiedWorkspaceRecovery(value, [1]);
    expect(recovered).toMatchObject({
      analysisTier: 'sampled',
      snapshot: { coverage: { shotIds: [1], sampledFrames: 4, totalFrames: 120 } }
    });
  });

  it('never treats terminal progress or unverified artifacts as completion', () => {
    const value = summary();
    value.pipeline_state.stages.direct.status = 'running';
    value.pipeline_state.stages.analyze_draft.status = 'running';
    Object.assign(value, { progress: { completed: 1, total: 1 } });
    expect(verifiedWorkspaceRecovery(value, [1])).toBeNull();

    const invalid = summary();
    invalid.stereo_script_revision = 'not-a-revision';
    invalid.draft_analysis.coverage.sampled_frames = 5;
    expect(verifiedWorkspaceRecovery(invalid, [1])).toBeNull();
  });
});
