import { describe, expect, it } from 'vitest';
import {
  analysisComplete,
  completeAnalysisStage,
  createWorkspaceSession,
  deriveWorkspaceCapabilities,
  failAnalysisStage,
  reconcileVerifiedAnalysis,
  startAnalysisStage,
  updateAnalysisProgress
} from './analysis-state';
import type { WorkspaceArtifacts, WorkspaceIdentity } from './types';

const identity: WorkspaceIdentity = {
  id: 'workspace-1',
  name: 'Local feature',
  sourcePath: 'D:\\Footage\\local-feature.mp4',
  projectPath: 'D:\\Projects\\Local feature'
};

function artifacts(overrides: Partial<WorkspaceArtifacts> = {}): WorkspaceArtifacts {
  return {
    projectCreated: false,
    mediaReady: false,
    normalizedReady: false,
    shotIds: [],
    analysisTier: 'none',
    depthMode: 'unknown',
    featureShotIds: [],
    scriptShotIds: [],
    ...overrides
  };
}

describe('progressive workspace analysis', () => {
  it('opens with only project setup running and every downstream stage locked', () => {
    const session = createWorkspaceSession(identity);

    expect(session.stages.setup.status).toBe('running');
    expect(session.stages.inspect.status).toBe('locked');
    expect(session.stages.direct.status).toBe('locked');
    expect(deriveWorkspaceCapabilities(session.artifacts).sourceMonitor.enabled).toBe(true);
    expect(deriveWorkspaceCapabilities(session.artifacts).shotNavigation.enabled).toBe(false);
  });

  it('keeps progress monotonic and refuses to start stages out of order', () => {
    let session = createWorkspaceSession(identity);
    session = updateAnalysisProgress(session, 'setup', 0.7, 'Analyzing samples', 7, 10);
    session = updateAnalysisProgress(session, 'setup', 0.2, 'Stale update', 2, 10);
    expect(session.stages.setup.progress).toBe(0.7);
    expect(session.stages.setup.completed).toBe(7);
    expect(session.stages.setup.total).toBe(10);
    expect(session.stages.setup.message).toBe('Analyzing samples');

    expect(startAnalysisStage(session, 'shots')).toBe(session);
    session = completeAnalysisStage(session, 'setup', { projectCreated: true });
    session = startAnalysisStage(session, 'inspect');
    expect(session.stages.inspect.status).toBe('running');
  });

  it('retains valid determinate counts and ignores malformed worker counters', () => {
    let session = createWorkspaceSession(identity);
    session = updateAnalysisProgress(session, 'setup', 0.25, 'Sampling', 3, 12);
    expect(session.stages.setup).toMatchObject({ progress: 0.25, completed: 3, total: 12, message: 'Sampling' });

    session = updateAnalysisProgress(session, 'setup', 0.5, 'Still sampling', Number.NaN, -1);
    expect(session.stages.setup).toMatchObject({ progress: 0.5, completed: 3, total: 12, message: 'Still sampling' });

    session = updateAnalysisProgress(session, 'setup', 1.5, undefined, 14, 12);
    expect(session.stages.setup).toMatchObject({ progress: 1, completed: 12, total: 12 });
  });

  it('unlocks capabilities only from complete artifact coverage', () => {
    const revision = 'a'.repeat(64);
    expect(deriveWorkspaceCapabilities(artifacts({ projectCreated: true, mediaReady: true })).metadata.enabled).toBe(true);
    expect(deriveWorkspaceCapabilities(artifacts({ projectCreated: true, mediaReady: true, normalizedReady: true, shotIds: [1, 2] })).shotNavigation.enabled).toBe(true);
    expect(
      deriveWorkspaceCapabilities(
        artifacts({ projectCreated: true, mediaReady: true, normalizedReady: true, shotIds: [1, 2], featureShotIds: [1], scriptShotIds: [1, 2], analysisTier: 'production', depthMode: 'production', scriptRevision: revision })
      ).directorEdit.enabled
    ).toBe(false);

    const previewCapabilities = deriveWorkspaceCapabilities(
      artifacts({
        shotIds: [1, 2],
        featureShotIds: [2, 1],
        scriptShotIds: [1, 2],
        projectCreated: true,
        mediaReady: true,
        normalizedReady: true,
        analysisTier: 'production',
        depthMode: 'synthetic',
        scriptRevision: revision
      })
    );
    expect(previewCapabilities.shotMetrics.enabled).toBe(true);
    expect(previewCapabilities.directorEdit.enabled).toBe(true);
    expect(previewCapabilities.previewRender.enabled).toBe(true);
    expect(previewCapabilities.finalExport.enabled).toBe(false);

    const productionCapabilities = deriveWorkspaceCapabilities(
      artifacts({
        shotIds: [1, 2],
        featureShotIds: [1, 2],
        scriptShotIds: [2, 1],
        projectCreated: true,
        mediaReady: true,
        normalizedReady: true,
        analysisTier: 'production',
        depthMode: 'production',
        scriptRevision: revision
      })
    );
    expect(productionCapabilities.finalExport.enabled).toBe(true);
  });

  it('exports measured depth of either tier but never the synthetic test pattern', () => {
    const revision = 'a'.repeat(64);
    const ready = {
      shotIds: [1, 2],
      featureShotIds: [1, 2],
      scriptShotIds: [1, 2],
      projectCreated: true,
      mediaReady: true,
      normalizedReady: true,
      analysisTier: 'production' as const,
      scriptRevision: revision
    };

    // Image-analysis depth measures the picture, so it is allowed to ship.
    const imageAnalysis = deriveWorkspaceCapabilities(
      artifacts({ ...ready, depthMode: 'image-analysis' })
    );
    expect(imageAnalysis.finalExport.enabled).toBe(true);
    expect(imageAnalysis.previewRender.enabled).toBe(true);

    // Synthetic depth is a constant pattern; an export from it carries no stereo.
    const synthetic = deriveWorkspaceCapabilities(artifacts({ ...ready, depthMode: 'synthetic' }));
    expect(synthetic.finalExport.enabled).toBe(false);
    expect(synthetic.finalExport.reason).toMatch(/synthetic/i);

    const unknown = deriveWorkspaceCapabilities(artifacts({ ...ready, depthMode: 'unknown' }));
    expect(unknown.finalExport.enabled).toBe(false);
  });

  it('rejects duplicate, foreign, and malformed analysis artifacts', () => {
    const revision = 'not-a-revision';
    const duplicateFeatures = deriveWorkspaceCapabilities(
      artifacts({
        shotIds: [1, 2],
        featureShotIds: [1, 1],
        scriptShotIds: [1, 2],
        projectCreated: true,
        mediaReady: true,
        normalizedReady: true,
        analysisTier: 'production',
        depthMode: 'production',
        scriptRevision: revision
      })
    );
    expect(duplicateFeatures.shotMetrics.enabled).toBe(false);
    expect(duplicateFeatures.directorEdit.enabled).toBe(false);

    expect(
      deriveWorkspaceCapabilities(artifacts({ projectCreated: true, mediaReady: true, normalizedReady: true, shotIds: [1, 1] })).shotNavigation.enabled
    ).toBe(false);
    expect(
      deriveWorkspaceCapabilities(artifacts({ projectCreated: true, mediaReady: true, normalizedReady: true, shotIds: [0, 2] })).shotNavigation.enabled
    ).toBe(false);
    expect(
      deriveWorkspaceCapabilities(artifacts({
        projectCreated: true,
        mediaReady: true,
        normalizedReady: true,
        analysisTier: 'production',
        shotIds: [1, 2],
        featureShotIds: [1, 2],
        scriptShotIds: [1],
        depthMode: 'production',
        scriptRevision: 'a'.repeat(64)
      })).directorEdit.enabled
    ).toBe(false);
  });

  it('unlocks sampled Director tools without unlocking preview or export', () => {
    const sampled = deriveWorkspaceCapabilities(artifacts({
      projectCreated: true,
      mediaReady: true,
      normalizedReady: true,
      shotIds: [1, 2],
      featureShotIds: [2, 1],
      scriptShotIds: [1, 2],
      scriptRevision: 'd'.repeat(64),
      analysisTier: 'sampled',
      draftCoverage: {
        shotIds: [1, 2],
        sampledFrames: 8,
        totalFrames: 240,
        perShot: [
          { shotId: 1, sampledFrames: 4, totalFrames: 120 },
          { shotId: 2, sampledFrames: 4, totalFrames: 120 }
        ]
      }
    }));

    expect(sampled.shotMetrics.enabled).toBe(true);
    expect(sampled.directorEdit.enabled).toBe(true);
    expect(sampled.assistantAnalysis.enabled).toBe(true);
    expect(sampled.previewRender.enabled).toBe(false);
    expect(sampled.previewRender.reason).toContain('Full-frame');
    expect(sampled.finalExport.enabled).toBe(false);
  });

  it('does not unlock a sampled draft with partial artifact coverage', () => {
    const sampled = deriveWorkspaceCapabilities(artifacts({
      projectCreated: true,
      mediaReady: true,
      normalizedReady: true,
      shotIds: [1, 2],
      featureShotIds: [1],
      scriptShotIds: [1, 2],
      scriptRevision: 'e'.repeat(64),
      analysisTier: 'sampled'
    }));

    expect(sampled.shotMetrics.enabled).toBe(false);
    expect(sampled.directorEdit.enabled).toBe(false);
    expect(sampled.finalExport.enabled).toBe(false);
  });

  it('rejects malformed sampled frame coverage', () => {
    const base: Partial<WorkspaceArtifacts> = {
      projectCreated: true,
      mediaReady: true,
      normalizedReady: true,
      shotIds: [1, 2],
      featureShotIds: [1, 2],
      scriptShotIds: [1, 2],
      scriptRevision: 'f'.repeat(64),
      analysisTier: 'sampled'
    };
    const invalidCoverage: Array<WorkspaceArtifacts['draftCoverage']> = [
      undefined,
      { shotIds: [1, 2], sampledFrames: 8, totalFrames: 200, perShot: [
        { shotId: 1, sampledFrames: 4, totalFrames: 100 },
        { shotId: 1, sampledFrames: 4, totalFrames: 100 }
      ] },
      { shotIds: [2, 1], sampledFrames: 8, totalFrames: 200, perShot: [
        { shotId: 2, sampledFrames: 4, totalFrames: 100 },
        { shotId: 1, sampledFrames: 4, totalFrames: 100 }
      ] },
      { shotIds: [1, 2], sampledFrames: 9, totalFrames: 200, perShot: [
        { shotId: 1, sampledFrames: 4, totalFrames: 100 },
        { shotId: 2, sampledFrames: 4, totalFrames: 100 }
      ] },
      { shotIds: [1, 2], sampledFrames: 8, totalFrames: 200, perShot: [
        { shotId: 1, sampledFrames: 0, totalFrames: 100 },
        { shotId: 2, sampledFrames: 8, totalFrames: 100 }
      ] }
    ];

    for (const draftCoverage of invalidCoverage) {
      expect(deriveWorkspaceCapabilities(artifacts({ ...base, draftCoverage })).directorEdit.enabled).toBe(false);
    }
  });

  it('preserves completed upstream work when a later stage fails', () => {
    let session = createWorkspaceSession(identity);
    session = completeAnalysisStage(session, 'setup', { projectCreated: true });
    session = startAnalysisStage(session, 'inspect');
    session = completeAnalysisStage(session, 'inspect', { mediaReady: true });
    session = startAnalysisStage(session, 'normalize');
    session = failAnalysisStage(session, 'normalize', 'FFmpeg unavailable');

    expect(session.stages.setup.status).toBe('complete');
    expect(session.stages.inspect.status).toBe('complete');
    expect(session.stages.normalize.status).toBe('failed');
    expect(session.artifacts.mediaReady).toBe(true);
    expect(analysisComplete(session)).toBe(false);

    session = startAnalysisStage(session, 'normalize', 'Retrying working copy');
    expect(session.stages.normalize.status).toBe('running');
    expect(session.stages.setup.status).toBe('complete');
    expect(session.artifacts.mediaReady).toBe(true);
  });

  it('atomically clears a stale 100% draft stage from verified production artifacts', () => {
    let session = createWorkspaceSession(identity);
    session = completeAnalysisStage(session, 'setup', { projectCreated: true });
    const completedStages: Array<[
      'inspect' | 'normalize' | 'shots',
      Partial<WorkspaceArtifacts>
    ]> = [
      ['inspect', { mediaReady: true }],
      ['normalize', { normalizedReady: true }],
      ['shots', { shotIds: [1] }]
    ];
    for (const [stage, stageArtifacts] of completedStages) {
      session = startAnalysisStage(session, stage);
      session = completeAnalysisStage(session, stage, stageArtifacts);
    }
    session = startAnalysisStage(session, 'draft', 'Using cached stage output');
    session = updateAnalysisProgress(session, 'draft', 1, 'Using cached stage output', 1, 1);

    session = reconcileVerifiedAnalysis(session, 'production', {
      analysisTier: 'production',
      depthMode: 'synthetic',
      featureShotIds: [1],
      scriptShotIds: [1],
      scriptRevision: 'a'.repeat(64)
    });

    expect(activeStage(session)).toBeNull();
    expect(session.stages.direct.status).toBe('complete');
    expect(deriveWorkspaceCapabilities(session.artifacts).previewRender.enabled).toBe(true);
  });

  it('refuses recovery when artifact coverage cannot unlock the claimed tier', () => {
    let session = createWorkspaceSession(identity);
    session = completeAnalysisStage(session, 'setup', { projectCreated: true });
    const unchanged = reconcileVerifiedAnalysis(session, 'production', {
      analysisTier: 'production',
      depthMode: 'production',
      featureShotIds: [1],
      scriptShotIds: [1],
      scriptRevision: 'a'.repeat(64)
    });
    expect(unchanged).toBe(session);
  });
});

function activeStage(session: ReturnType<typeof createWorkspaceSession>) {
  return Object.entries(session.stages).find(([, stage]) => stage.status === 'running')?.[0] ?? null;
}
