import { parseDraftAnalysisSnapshot, parseShotFeatures } from './project-session';
import type { DraftAnalysisSnapshot, ShotFeatureUpdate } from './project-session';
import type { Project } from './types';

const REVISION_PATTERN = /^[a-f0-9]{64}$/;

export interface SampledWorkspaceRecovery {
  analysisTier: 'sampled';
  draft: Record<string, unknown>;
  snapshot: DraftAnalysisSnapshot;
}

export interface ProductionWorkspaceRecovery {
  analysisTier: 'production';
  script: Record<string, unknown>;
  revision: string;
  features: ShotFeatureUpdate[];
  depthMode: Exclude<NonNullable<Project['depthMode']>, 'unknown'>;
}

export type WorkspaceRecovery = SampledWorkspaceRecovery | ProductionWorkspaceRecovery;

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function exactShotIds(expected: readonly number[], actual: readonly number[]): boolean {
  if (!expected.length || expected.length !== actual.length) return false;
  const expectedSet = new Set(expected);
  const actualSet = new Set(actual);
  return expectedSet.size === expected.length
    && actualSet.size === actual.length
    && [...expectedSet].every((shotId) => actualSet.has(shotId));
}

function completedPipelineStage(summary: Record<string, unknown>, stage: string): boolean {
  const pipelineState = record(summary.pipeline_state);
  const stages = record(pipelineState.stages);
  return record(stages[stage]).status === 'complete';
}

function depthMode(summary: Record<string, unknown>): ProductionWorkspaceRecovery['depthMode'] | null {
  const status = record(summary.depth_status);
  if (status.production_ready === true) return 'production';
  const hasRows = (value: unknown): boolean => Array.isArray(value) && value.length > 0;
  if (
    String(status.backend ?? '').toLowerCase() === 'synthetic'
    || hasRows(status.synthetic_shot_ids)
    || hasRows(status.fallback_shot_ids)
    || hasRows(status.model_failure_shot_ids)
  ) return 'synthetic';
  return null;
}

function productionRecovery(
  summary: Record<string, unknown>,
  expectedShotIds: readonly number[]
): ProductionWorkspaceRecovery | null {
  if (!['estimate_depth', 'extract_features', 'direct'].every((stage) => completedPipelineStage(summary, stage))) {
    return null;
  }
  const recoveredDepthMode = depthMode(summary);
  if (!recoveredDepthMode) return null;

  try {
    const featureManifest = record(summary.features);
    const featureRows = Array.isArray(featureManifest.shots) ? featureManifest.shots : [];
    const features = parseShotFeatures(featureRows);
    if (!exactShotIds(expectedShotIds, features.map((item) => item.shotId))) return null;

    const script = record(summary.stereo_script);
    const scriptRows = Array.isArray(script.shots) ? script.shots : [];
    const scriptIds = scriptRows.map((value) => Number(record(value).shot_id));
    const revision = String(summary.stereo_script_revision ?? '');
    if (!REVISION_PATTERN.test(revision) || !exactShotIds(expectedShotIds, scriptIds)) return null;

    return { analysisTier: 'production', script, revision, features, depthMode: recoveredDepthMode };
  } catch {
    return null;
  }
}

function sampledRecovery(
  summary: Record<string, unknown>,
  expectedShotIds: readonly number[]
): SampledWorkspaceRecovery | null {
  if (!completedPipelineStage(summary, 'analyze_draft')) return null;
  try {
    const draft = record(summary.draft_analysis);
    const snapshot = parseDraftAnalysisSnapshot(draft, expectedShotIds);
    return { analysisTier: 'sampled', draft, snapshot };
  } catch {
    return null;
  }
}

/**
 * Finds the most advanced workspace state that can be reconstructed from a
 * fresh get_project response. Progress counters are intentionally ignored:
 * only complete pipeline records plus fully validated artifacts may recover UI state.
 */
export function verifiedWorkspaceRecovery(
  summaryValue: unknown,
  expectedShotIds: readonly number[]
): WorkspaceRecovery | null {
  if (!expectedShotIds.length || expectedShotIds.some((shotId) => !Number.isSafeInteger(shotId) || shotId < 1)) {
    return null;
  }
  const summary = record(summaryValue);
  return productionRecovery(summary, expectedShotIds) ?? sampledRecovery(summary, expectedShotIds);
}
