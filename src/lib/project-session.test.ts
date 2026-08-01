import { describe, expect, it } from 'vitest';
import {
  interpretAiResult,
  parseDraftAnalysisSnapshot,
  parseShotFeature,
  ProjectSessionGuard,
  resolveForProject,
  toEngineShotFeatures
} from './project-session';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const engineFeatures = {
  shot_id: 17,
  duration_seconds: 4.375,
  motion_score: 0.31,
  speech_ratio: 0.72,
  depth_spread: 0.64,
  foreground_ratio: 0.43,
  brightness: 0.58,
  cut_frequency_context: 0.27,
  camera_movement: 'vertical' as const,
  depth_reliability: 0.89
};

function draftSnapshot() {
  const second = { ...engineFeatures, shot_id: 18 };
  return {
    analysis_tier: 'sampled',
    profile: 'representative_frames',
    features: { shots: [engineFeatures, second] },
    script: { shots: [{ shot_id: 17 }, { shot_id: 18 }] },
    revision: 'b'.repeat(64),
    coverage: {
      shot_ids: [17, 18],
      sampled_frames: 8,
      total_frames: 200,
      per_shot: [
        { shot_id: 17, sampled_frames: 4, total_frames: 90 },
        { shot_id: 18, sampled_frames: 4, total_frames: 110 }
      ]
    }
  };
}

describe('project-scoped frontend data', () => {
  it('round-trips every engine shot feature into the AI request without fabrication', () => {
    const parsed = parseShotFeature(engineFeatures);
    expect(toEngineShotFeatures(parsed.shotId, parsed)).toEqual(engineFeatures);
  });

  it('rejects incomplete engine features instead of filling semantic values', () => {
    const { brightness: _brightness, ...incomplete } = engineFeatures;
    expect(() => parseShotFeature(incomplete)).toThrow(/brightness/);
  });

  it('validates the complete representative-frame draft contract', () => {
    const parsed = parseDraftAnalysisSnapshot(draftSnapshot(), [17, 18]);
    expect(parsed.features.map((item) => item.shotId)).toEqual([17, 18]);
    expect(parsed.coverage).toEqual({
      shotIds: [17, 18],
      sampledFrames: 8,
      totalFrames: 200,
      perShot: [
        { shotId: 17, sampledFrames: 4, totalFrames: 90 },
        { shotId: 18, sampledFrames: 4, totalFrames: 110 }
      ]
    });
  });

  it('rejects an untrusted draft profile, reordered shots, or inconsistent totals', () => {
    expect(() => parseDraftAnalysisSnapshot({ ...draftSnapshot(), profile: 'all_frames' }, [17, 18])).toThrow(/profile/i);
    const reordered = draftSnapshot();
    reordered.coverage.shot_ids = [18, 17];
    expect(() => parseDraftAnalysisSnapshot(reordered, [17, 18])).toThrow(/ordered shot map/i);
    const inconsistent = draftSnapshot();
    inconsistent.coverage.sampled_frames = 9;
    expect(() => parseDraftAnalysisSnapshot(inconsistent, [17, 18])).toThrow(/totals/i);
  });

  it('ignores a late asset promise after close and reopen of the same path', async () => {
    const guard = new ProjectSessionGuard();
    const originalContext = guard.begin('D:\\Projects\\Stereo');
    const asset = deferred<string>();
    const applied: string[] = [];
    const pending = resolveForProject(guard, originalContext, asset.promise, (url) => applied.push(url));

    guard.invalidate();
    const reopenedContext = guard.begin('D:\\Projects\\Stereo');
    asset.resolve('asset://old-project-preview');

    await expect(pending).resolves.toBe(false);
    expect(applied).toEqual([]);
    expect(guard.isCurrent(originalContext)).toBe(false);
    expect(guard.isCurrent(reopenedContext)).toBe(true);
  });

  it('binds an AI recommendation to the originating shot', () => {
    const outcome = interpretAiResult({
      preset: 'vista_deep',
      confidence: 0.91,
      reason: 'Stable layered depth supports the vista preset.',
      source: 'llm',
      fallback_used: false
    }, 17);

    expect(outcome).toEqual({
      kind: 'recommendation',
      recommendation: {
        shotId: 17,
        preset: 'vista_deep',
        confidence: 0.91,
        reason: 'Stable layered depth supports the vista preset.'
      }
    });
  });

  it('labels low-confidence neutral LLM output as a safety fallback', () => {
    const outcome = interpretAiResult({
      preset: 'neutral',
      confidence: 0.42,
      reason: 'assistant confidence was below the safe threshold; neutral fallback',
      source: 'llm',
      fallback_used: false
    }, 17);

    expect(outcome.kind).toBe('fallback');
    if (outcome.kind === 'fallback') {
      expect(outcome.source).toBe('assistant_low_confidence');
      expect(outcome.notice).toMatch(/neutral safety fallback/i);
    }
  });
});
