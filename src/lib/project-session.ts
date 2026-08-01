import type {
  AiRecommendation,
  AnalysisCoverage,
  CameraMovement,
  EngineShotFeatures,
  PresetId,
  ShotFeatures
} from './types';

export interface ProjectContext {
  readonly generation: number;
  readonly projectPath: string;
}

export interface ShotFeatureUpdate extends ShotFeatures {
  shotId: number;
}

export interface DraftAnalysisSnapshot {
  features: ShotFeatureUpdate[];
  coverage: AnalysisCoverage;
}

export type AiResult =
  | { kind: 'recommendation'; recommendation: AiRecommendation }
  | { kind: 'fallback'; source: string; notice: string };

const CAMERA_MOVEMENTS = new Set<CameraMovement>([
  'static',
  'lateral',
  'vertical',
  'zoom',
  'unstable'
]);
const PRESETS = new Set<PresetId>([
  'dialogue_subtle',
  'action_controlled',
  'vista_deep',
  'closeup_flat',
  'neutral'
]);

/**
 * Owns the identity of the currently open project. Paths alone are insufficient:
 * closing and reopening the same project must still invalidate pending promises.
 */
export class ProjectSessionGuard {
  private generation = 0;
  private projectPath: string | null = null;

  begin(projectPath: string): ProjectContext {
    const path = projectPath.trim();
    if (!path) throw new Error('A project path is required to begin a project session.');
    this.generation += 1;
    this.projectPath = path;
    return { generation: this.generation, projectPath: path };
  }

  capture(): ProjectContext | null {
    return this.projectPath === null
      ? null
      : { generation: this.generation, projectPath: this.projectPath };
  }

  isCurrent(context: ProjectContext): boolean {
    return context.generation === this.generation && context.projectPath === this.projectPath;
  }

  invalidate(): void {
    this.generation += 1;
    this.projectPath = null;
  }
}

/** Applies an async result only if its originating project is still current. */
export async function resolveForProject<T>(
  guard: ProjectSessionGuard,
  context: ProjectContext,
  promise: Promise<T>,
  apply: (value: T) => void,
  reject?: (error: unknown) => void
): Promise<boolean> {
  try {
    const value = await promise;
    if (!guard.isCurrent(context)) return false;
    apply(value);
    return true;
  } catch (error) {
    if (!guard.isCurrent(context)) return false;
    reject?.(error);
    return false;
  }
}

function objectRecord(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Shot features must be an object.');
  }
  return value as Record<string, unknown>;
}

function numberField(
  value: Record<string, unknown>,
  key: string,
  minimum: number,
  maximum = Number.POSITIVE_INFINITY
): number {
  const parsed = value[key];
  if (typeof parsed !== 'number' || !Number.isFinite(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`Shot feature ${key} is outside its valid range.`);
  }
  return parsed;
}

/** Converts a validated engine artifact without inventing missing semantic data. */
export function parseShotFeature(value: unknown): ShotFeatureUpdate {
  const row = objectRecord(value);
  const shotId = numberField(row, 'shot_id', 1);
  if (!Number.isInteger(shotId)) throw new Error('Shot feature shot_id must be an integer.');
  const cameraMovement = row.camera_movement;
  if (typeof cameraMovement !== 'string' || !CAMERA_MOVEMENTS.has(cameraMovement as CameraMovement)) {
    throw new Error('Shot feature camera_movement is invalid.');
  }
  return {
    shotId,
    durationSeconds: numberField(row, 'duration_seconds', 0),
    motion: numberField(row, 'motion_score', 0, 1),
    speech: numberField(row, 'speech_ratio', 0, 1),
    depthSpread: numberField(row, 'depth_spread', 0, 1),
    foreground: numberField(row, 'foreground_ratio', 0, 1),
    brightness: numberField(row, 'brightness', 0, 1),
    cutFrequencyContext: numberField(row, 'cut_frequency_context', 0, 1),
    cameraMovement: cameraMovement as CameraMovement,
    depthReliability: numberField(row, 'depth_reliability', 0, 1)
  };
}

export function parseShotFeatures(values: unknown[]): ShotFeatureUpdate[] {
  return values.map(parseShotFeature);
}

function exactIds(expected: readonly number[], actual: readonly number[]): boolean {
  if (!expected.length || expected.length !== actual.length) return false;
  const expectedSet = new Set(expected);
  const actualSet = new Set(actual);
  return expectedSet.size === expected.length && actualSet.size === actual.length && [...expectedSet].every((id) => actualSet.has(id));
}

function positiveInteger(value: unknown, label: string, minimum = 1): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) {
    throw new Error(`${label} must be a positive safe integer.`);
  }
  return value;
}

/** Validates the complete, provenance-labelled snapshot that may unlock sampled editing. */
export function parseDraftAnalysisSnapshot(value: unknown, expectedShotIds: readonly number[]): DraftAnalysisSnapshot {
  const result = objectRecord(value);
  if (result.analysis_tier !== 'sampled') throw new Error('Quick analysis did not return a sampled draft.');
  if (result.profile !== 'representative_frames') throw new Error('Quick analysis returned an unexpected sampling profile.');

  const featuresValue = objectRecord(result.features);
  const featureRows = Array.isArray(featuresValue.shots) ? featuresValue.shots : [];
  const features = parseShotFeatures(featureRows);
  if (!exactIds(expectedShotIds, features.map((item) => item.shotId))) throw new Error('Quick analysis did not cover every detected shot.');

  const scriptValue = objectRecord(result.script);
  const scriptRows = Array.isArray(scriptValue.shots) ? scriptValue.shots : [];
  const scriptIds = scriptRows.map((raw) => positiveInteger(objectRecord(raw).shot_id, 'Director script shot_id'));
  if (!exactIds(expectedShotIds, scriptIds)) throw new Error('Quick Director script does not cover every detected shot.');
  if (typeof result.revision !== 'string' || !/^[a-f0-9]{64}$/.test(result.revision)) throw new Error('Quick Director script revision is invalid.');

  const coverageValue = objectRecord(result.coverage);
  const coverageIds = Array.isArray(coverageValue.shot_ids)
    ? coverageValue.shot_ids.map((shotId) => positiveInteger(shotId, 'Coverage shot_id'))
    : [];
  if (coverageIds.length !== expectedShotIds.length || !expectedShotIds.every((shotId, index) => coverageIds[index] === shotId)) throw new Error('Quick analysis coverage does not match the ordered shot map.');
  const sampledFrames = positiveInteger(coverageValue.sampled_frames, 'Coverage sampled_frames');
  const totalFrames = positiveInteger(coverageValue.total_frames, 'Coverage total_frames');
  if (sampledFrames < expectedShotIds.length || totalFrames < sampledFrames) throw new Error('Quick analysis returned invalid frame coverage.');

  const perShotRows = Array.isArray(coverageValue.per_shot) ? coverageValue.per_shot : [];
  const perShot = perShotRows.map((raw) => {
    const item = objectRecord(raw);
    const shotId = positiveInteger(item.shot_id, 'Per-shot coverage shot_id');
    const sampled = positiveInteger(item.sampled_frames, 'Per-shot sampled_frames');
    const total = positiveInteger(item.total_frames, 'Per-shot total_frames');
    if (total < sampled) throw new Error('Per-shot frame coverage is invalid.');
    return { shotId, sampledFrames: sampled, totalFrames: total };
  });
  if (perShot.length !== expectedShotIds.length || !expectedShotIds.every((shotId, index) => perShot[index]?.shotId === shotId)) throw new Error('Per-shot coverage does not match the ordered shot map.');
  if (perShot.reduce((sum, item) => sum + item.sampledFrames, 0) !== sampledFrames || perShot.reduce((sum, item) => sum + item.totalFrames, 0) !== totalFrames) throw new Error('Quick analysis frame totals are inconsistent.');

  return { features, coverage: { shotIds: coverageIds, sampledFrames, totalFrames, perShot } };
}

export function toEngineShotFeatures(shotId: number, features: ShotFeatures): EngineShotFeatures {
  return {
    shot_id: shotId,
    duration_seconds: features.durationSeconds,
    motion_score: features.motion,
    speech_ratio: features.speech,
    depth_spread: features.depthSpread,
    foreground_ratio: features.foreground,
    brightness: features.brightness,
    cut_frequency_context: features.cutFrequencyContext,
    camera_movement: features.cameraMovement,
    depth_reliability: features.depthReliability
  };
}

export function interpretAiResult(
  result: Record<string, unknown>,
  shotId: number
): AiResult {
  const presetValue = String(result.preset ?? 'neutral');
  const preset = PRESETS.has(presetValue as PresetId) ? presetValue as PresetId : 'neutral';
  const confidenceValue = Number(result.confidence ?? 0);
  const confidence = Number.isFinite(confidenceValue)
    ? Math.min(1, Math.max(0, confidenceValue))
    : 0;
  const reason = String(
    result.reason ?? 'A conservative neutral treatment best matches the available shot evidence.'
  );
  const source = String(result.source ?? 'local');
  const lowConfidenceNeutral =
    preset === 'neutral' &&
    confidence < 0.55 &&
    /below (?:the )?safe threshold|neutral fallback/i.test(reason);

  if (lowConfidenceNeutral) {
    return {
      kind: 'fallback',
      source: 'assistant_low_confidence',
      notice: 'Assistant confidence was below the safe threshold, so the neutral safety fallback was kept. No AI recommendation was applied.'
    };
  }
  if (source !== 'llm' || result.fallback_used === true) {
    return {
      kind: 'fallback',
      source,
      notice: `Assistant unavailable — ${source} selected a safe local fallback. No AI recommendation was applied.`
    };
  }
  return {
    kind: 'recommendation',
    recommendation: { shotId, preset, confidence, reason }
  };
}
