import type {
  AnalysisStageId,
  AnalysisStageState,
  WorkspaceArtifacts,
  WorkspaceCapabilities,
  WorkspaceIdentity,
  WorkspaceSession
} from './types';
import { clamp } from './utils';

export interface AnalysisStageDefinition {
  id: AnalysisStageId;
  label: string;
  description: string;
}

export const ANALYSIS_STAGES: readonly AnalysisStageDefinition[] = [
  { id: 'setup', label: 'Workspace', description: 'Secure project setup' },
  { id: 'inspect', label: 'Media', description: 'Timing and streams' },
  { id: 'normalize', label: 'Working copy', description: 'Stable frame timing' },
  { id: 'shots', label: 'Shot map', description: 'Cut boundaries' },
  { id: 'draft', label: 'Quick draft', description: 'Representative frames per shot' },
  { id: 'depth', label: 'Depth', description: 'Per-shot geometry' },
  { id: 'features', label: 'Features', description: 'Motion and context' },
  { id: 'direct', label: 'Director', description: 'Comfort-safe script' }
] as const;

const STAGE_INDEX = new Map(ANALYSIS_STAGES.map((stage, index) => [stage.id, index]));
const REVISION_PATTERN = /^[a-f0-9]{64}$/;

function lockedStage(): AnalysisStageState {
  return { status: 'locked', progress: 0 };
}

function initialArtifacts(): WorkspaceArtifacts {
  return {
    projectCreated: false,
    mediaReady: false,
    normalizedReady: false,
    shotIds: [],
    analysisTier: 'none',
    depthMode: 'unknown',
    featureShotIds: [],
    scriptShotIds: []
  };
}

export function createWorkspaceSession(identity: WorkspaceIdentity): WorkspaceSession {
  const stages = Object.fromEntries(
    ANALYSIS_STAGES.map((stage) => [stage.id, lockedStage()])
  ) as Record<AnalysisStageId, AnalysisStageState>;
  stages.setup = { status: 'running', progress: 0, message: 'Creating the local workspace' };
  return { identity, stages, artifacts: initialArtifacts() };
}

function priorStageComplete(session: WorkspaceSession, stage: AnalysisStageId): boolean {
  const index = STAGE_INDEX.get(stage) ?? -1;
  return index <= 0 || session.stages[ANALYSIS_STAGES[index - 1].id].status === 'complete';
}

export function startAnalysisStage(
  session: WorkspaceSession,
  stage: AnalysisStageId,
  message?: string
): WorkspaceSession {
  const current = session.stages[stage];
  if (!priorStageComplete(session, stage) || current.status === 'complete') return session;
  return {
    ...session,
    stages: {
      ...session.stages,
      [stage]: { status: 'running', progress: 0, ...(message ? { message } : {}) }
    }
  };
}

export function updateAnalysisProgress(
  session: WorkspaceSession,
  stage: AnalysisStageId,
  progress: number,
  message?: string,
  completed?: number,
  total?: number
): WorkspaceSession {
  const current = session.stages[stage];
  if (current.status !== 'running') return session;
  const safeProgress = clamp(progress, 0, 1);
  const isCurrentUpdate = safeProgress >= current.progress;
  const hasDeterminateCounts =
    Number.isSafeInteger(completed) &&
    Number.isSafeInteger(total) &&
    completed! >= 0 &&
    total! > 0;
  const safeTotal = hasDeterminateCounts && isCurrentUpdate ? total! : undefined;
  const safeCompleted = hasDeterminateCounts && isCurrentUpdate ? Math.min(completed!, safeTotal!) : undefined;
  return {
    ...session,
    stages: {
      ...session.stages,
      [stage]: {
        ...current,
        progress: Math.max(current.progress, safeProgress),
        ...(hasDeterminateCounts && isCurrentUpdate ? { completed: safeCompleted, total: safeTotal } : {}),
        ...(message && isCurrentUpdate ? { message } : {})
      }
    }
  };
}

export function completeAnalysisStage(
  session: WorkspaceSession,
  stage: AnalysisStageId,
  artifacts: Partial<WorkspaceArtifacts> = {}
): WorkspaceSession {
  const current = session.stages[stage];
  if (current.status !== 'running') return session;
  return {
    ...session,
    stages: {
      ...session.stages,
      [stage]: { ...current, status: 'complete', progress: 1, error: undefined }
    },
    artifacts: { ...session.artifacts, ...artifacts }
  };
}

/**
 * Atomically repairs analysis state after a fresh project summary has validated
 * the corresponding disk artifacts. This is deliberately stricter than a
 * progress update: incomplete artifact coverage can never unlock controls.
 */
export function reconcileVerifiedAnalysis(
  session: WorkspaceSession,
  analysisTier: 'sampled' | 'production',
  artifacts: Partial<WorkspaceArtifacts>
): WorkspaceSession {
  if (session.stages.shots.status !== 'complete') return session;
  const recoveredArtifacts: WorkspaceArtifacts = {
    ...session.artifacts,
    ...artifacts,
    analysisTier
  };
  const capabilities = deriveWorkspaceCapabilities(recoveredArtifacts);
  const valid = analysisTier === 'production'
    ? capabilities.previewRender.enabled
    : capabilities.directorEdit.enabled && recoveredArtifacts.analysisTier === 'sampled';
  if (!valid) return session;

  const recoveredStages: AnalysisStageId[] = analysisTier === 'production'
    ? ['draft', 'depth', 'features', 'direct']
    : ['draft'];
  const stages = { ...session.stages };
  for (const stage of recoveredStages) {
    stages[stage] = {
      ...stages[stage],
      status: 'complete',
      progress: 1,
      error: undefined,
      message: 'Verified from completed project artifacts'
    };
  }
  if (analysisTier === 'sampled') {
    for (const stage of ['depth', 'features', 'direct'] as const) stages[stage] = lockedStage();
  }
  return { ...session, stages, artifacts: recoveredArtifacts };
}

export function failAnalysisStage(
  session: WorkspaceSession,
  stage: AnalysisStageId,
  error: string,
  cancelled = false
): WorkspaceSession {
  const current = session.stages[stage];
  if (current.status === 'complete') return session;
  return {
    ...session,
    stages: {
      ...session.stages,
      [stage]: {
        ...current,
        status: cancelled ? 'cancelled' : 'failed',
        error,
        message: cancelled ? 'Analysis paused' : 'Analysis needs attention'
      }
    }
  };
}

function exactShotCoverage(expected: readonly number[], actual: readonly number[]): boolean {
  if (!expected.length || expected.length !== actual.length) return false;
  const expectedSet = new Set(expected);
  const actualSet = new Set(actual);
  return (
    expectedSet.size === expected.length &&
    actualSet.size === actual.length &&
    expectedSet.size === actualSet.size &&
    [...expectedSet].every((id) => actualSet.has(id))
  );
}

function validDraftCoverage(artifacts: WorkspaceArtifacts): boolean {
  const coverage = artifacts.draftCoverage;
  const expected = artifacts.shotIds;
  if (!coverage || !expected.length || coverage.shotIds.length !== expected.length || coverage.perShot.length !== expected.length) return false;
  if (!expected.every((shotId, index) => coverage.shotIds[index] === shotId && coverage.perShot[index]?.shotId === shotId)) return false;
  if (!Number.isSafeInteger(coverage.sampledFrames) || !Number.isSafeInteger(coverage.totalFrames) || coverage.sampledFrames < expected.length || coverage.totalFrames < coverage.sampledFrames) return false;
  const perShotIds = new Set<number>();
  let sampledFrames = 0;
  let totalFrames = 0;
  for (const item of coverage.perShot) {
    if (perShotIds.has(item.shotId) || !Number.isSafeInteger(item.sampledFrames) || item.sampledFrames < 1 || !Number.isSafeInteger(item.totalFrames) || item.totalFrames < item.sampledFrames) return false;
    perShotIds.add(item.shotId);
    sampledFrames += item.sampledFrames;
    totalFrames += item.totalFrames;
  }
  return sampledFrames === coverage.sampledFrames && totalFrames === coverage.totalFrames;
}

function gate(enabled: boolean, reason?: string) {
  return enabled ? { enabled: true } : { enabled: false, reason };
}

export function deriveWorkspaceCapabilities(
  artifacts: WorkspaceArtifacts
): WorkspaceCapabilities {
  const mediaReady = artifacts.projectCreated && artifacts.mediaReady;
  const normalizedReady = mediaReady && artifacts.normalizedReady;
  const shotsReady = normalizedReady && artifacts.shotIds.length > 0 && artifacts.shotIds.every((id)=>Number.isInteger(id)&&id>0) && new Set(artifacts.shotIds).size === artifacts.shotIds.length;
  const featuresReady = shotsReady && exactShotCoverage(artifacts.shotIds, artifacts.featureShotIds);
  const depthReady = shotsReady && artifacts.depthMode !== 'unknown';
  const scriptReady = Boolean(artifacts.scriptRevision && REVISION_PATTERN.test(artifacts.scriptRevision)) && exactShotCoverage(artifacts.shotIds,artifacts.scriptShotIds);
  const tierArtifactsReady = artifacts.analysisTier === 'production' || (artifacts.analysisTier === 'sampled' && validDraftCoverage(artifacts));
  const directionReady = featuresReady && scriptReady && tierArtifactsReady;
  const productionDirectionReady = directionReady && artifacts.analysisTier === 'production';

  return {
    sourceMonitor: gate(true),
    metadata: gate(mediaReady, 'Inspecting source timing and streams'),
    shotNavigation: gate(shotsReady, 'Finding shot boundaries'),
    shotMetrics: gate(directionReady, 'Sampling representative frames for every shot'),
    directorEdit: gate(directionReady, 'Building the quick Director draft'),
    previewRender: gate(
      productionDirectionReady && depthReady,
      directionReady ? 'Full-frame analysis is required for previews' : 'Direction must finish first'
    ),
    assistantAnalysis: gate(directionReady, 'Shot features and direction must finish first'),
    finalExport: gate(
      productionDirectionReady
        && (artifacts.depthMode === 'production' || artifacts.depthMode === 'image-analysis'),
      artifacts.depthMode === 'synthetic'
        ? 'Synthetic test depth carries no scene geometry and cannot be exported'
        : directionReady
          ? 'Run full-frame analysis to check final export readiness'
          : 'Measured depth and direction must finish first'
    )
  };
}

export function activeAnalysisStage(session: WorkspaceSession): AnalysisStageId | null {
  return ANALYSIS_STAGES.find((stage) => session.stages[stage.id].status === 'running')?.id ?? null;
}

export function analysisComplete(session: WorkspaceSession): boolean {
  return ANALYSIS_STAGES.every((stage) => session.stages[stage.id].status === 'complete');
}
