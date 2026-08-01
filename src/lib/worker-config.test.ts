import { describe, expect, it } from 'vitest';
import { DEFAULT_SETTINGS } from './constants';
import { CONFIGURED_PIPELINE_METHODS, draftAnalysisParams, estimateDepthParams, MONOCULAR_DEPTH_BACKEND, pipelineConfig, PRODUCTION_DEPTH_BACKEND, renderConfig } from './worker-config';

describe('pipeline worker configuration', () => {
  it('keeps the exact production depth selector across every config-bearing stage', () => {
    const payloads = Object.fromEntries(
      CONFIGURED_PIPELINE_METHODS.map((method) => [method, pipelineConfig('auto')])
    );

    for (const method of CONFIGURED_PIPELINE_METHODS) {
      expect(payloads[method]).toEqual({
        depth_backend: PRODUCTION_DEPTH_BACKEND,
        device: 'auto'
      });
      expect(payloads[method]).not.toHaveProperty('backend');
    }
  });

  it('forces only an explicit retry and never sends the legacy backend alias', () => {
    expect(estimateDepthParams('D:\\project', 'cuda')).toEqual({
      project_dir: 'D:\\project',
      depth_backend: PRODUCTION_DEPTH_BACKEND,
      device: 'cuda',
      allow_fallback: true
    });
    expect(estimateDepthParams('D:\\project', 'cuda', true)).toMatchObject({
      force_stages: ['estimate_depth']
    });
    expect(estimateDepthParams('D:\\project', 'cuda', true)).not.toHaveProperty('backend');
  });

  it('uses the engine-owned representative-frame profile for quick drafts', () => {
    expect(draftAnalysisParams('D:\\project', 'cuda')).toEqual({
      project_dir: 'D:\\project',
      depth_backend: PRODUCTION_DEPTH_BACKEND,
      device: 'cuda',
      profile: 'representative_frames',
      allow_fallback: true
    });
    expect(CONFIGURED_PIPELINE_METHODS).toContain('analyze_draft');
  });

  it('sends the chosen depth engine to every stage that estimates depth', () => {
    expect(pipelineConfig('auto', MONOCULAR_DEPTH_BACKEND)).toEqual({
      depth_backend: MONOCULAR_DEPTH_BACKEND,
      device: 'auto'
    });
    expect(draftAnalysisParams('D:\\project', 'cpu', MONOCULAR_DEPTH_BACKEND)).toMatchObject({
      depth_backend: MONOCULAR_DEPTH_BACKEND
    });
    expect(estimateDepthParams('D:\\project', 'cpu', false, MONOCULAR_DEPTH_BACKEND)).toMatchObject({
      depth_backend: MONOCULAR_DEPTH_BACKEND
    });
    expect(DEFAULT_SETTINGS.depthEngine).toBe(PRODUCTION_DEPTH_BACKEND);
  });

  it('carries the colour matrix into renders so a preview matches the export', () => {
    expect(renderConfig(DEFAULT_SETTINGS)).toEqual({
      anaglyph_mode: 'calibrated',
      swap_eyes: false,
      preview_max_width: 1280
    });
    expect(renderConfig({ ...DEFAULT_SETTINGS, anaglyphMode: 'basic', swapEyes: true })).toEqual({
      anaglyph_mode: 'basic',
      swap_eyes: true,
      preview_max_width: 1280
    });
    // 0 means "render the clip at the working-copy width", so the engine is
    // told nothing and keeps its own default.
    expect(renderConfig({ ...DEFAULT_SETTINGS, previewClipWidth: 0 })).not.toHaveProperty(
      'preview_max_width'
    );
    expect(CONFIGURED_PIPELINE_METHODS).toContain('render_preview_frame');
  });
});
