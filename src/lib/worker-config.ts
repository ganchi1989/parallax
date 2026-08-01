import type { AppSettings } from './types';

export const PRODUCTION_DEPTH_BACKEND = 'video-depth-anything-small' as const;
export const MONOCULAR_DEPTH_BACKEND = 'monocular-cues' as const;
export const DRAFT_ANALYSIS_PROFILE = 'representative_frames' as const;

export interface PipelineConfigParams {
  depth_backend: AppSettings['depthEngine'];
  device: AppSettings['device'];
}

export interface RenderConfigParams {
  anaglyph_mode: AppSettings['anaglyphMode'];
  swap_eyes: boolean;
  preview_max_width?: number;
}

/** Every project request reconstructs config, so repeat this exact payload. */
export function pipelineConfig(
  device: AppSettings['device'],
  depthEngine: AppSettings['depthEngine'] = PRODUCTION_DEPTH_BACKEND
): PipelineConfigParams {
  return { depth_backend: depthEngine, device };
}

/**
 * Colour matrix and eye order travel with every render request, so a preview
 * shows the same composition the export will produce.
 */
export function renderConfig(settings: AppSettings): RenderConfigParams {
  return {
    anaglyph_mode: settings.anaglyphMode,
    swap_eyes: settings.swapEyes,
    // 0 means "do not scale"; the engine keeps the working-copy width.
    ...(settings.previewClipWidth ? { preview_max_width: settings.previewClipWidth } : {})
  };
}

export function estimateDepthParams(
  projectDir: string,
  device: AppSettings['device'],
  forceRetry = false,
  depthEngine: AppSettings['depthEngine'] = PRODUCTION_DEPTH_BACKEND
): Record<string, unknown> {
  return {
    project_dir: projectDir,
    ...pipelineConfig(device, depthEngine),
    allow_fallback: true,
    ...(forceRetry ? { force_stages: ['estimate_depth'] } : {})
  };
}

export function draftAnalysisParams(
  projectDir: string,
  device: AppSettings['device'],
  depthEngine: AppSettings['depthEngine'] = PRODUCTION_DEPTH_BACKEND
): Record<string, unknown> {
  return {
    project_dir: projectDir,
    ...pipelineConfig(device, depthEngine),
    profile: DRAFT_ANALYSIS_PROFILE,
    allow_fallback: true
  };
}

export const CONFIGURED_PIPELINE_METHODS = [
  'create_project',
  'analyze_draft',
  'estimate_depth',
  'extract_features',
  'direct',
  'render_preview',
  'render_preview_frame',
  'render_final'
] as const;
