import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import { createDemoProject } from '../demo';
import DirectorPanel from './DirectorPanel.svelte';

describe('DirectorPanel analysis progress', () => {
  it('keeps sampled coverage visible while reporting full-frame progress', () => {
    const shot = createDemoProject().shots[0];
    const { body } = render(DirectorPanel, {
      props: {
        shot,
        editingEnabled: true,
        featuresReady: true,
        previewEnabled: false,
        analysisTier: 'sampled',
        draftCoverage: {
          shotIds: [shot.id],
          sampledFrames: 12,
          totalFrames: 140,
          perShot: [{ shotId: shot.id, sampledFrames: 12, totalFrames: 140 }]
        },
        productionActive: true,
        productionStage: {
          status: 'running',
          progress: 0.45,
          completed: 45,
          total: 100,
          message: 'Decoding full-frame batches'
        },
        productionStageLabel: 'Full-frame depth',
        onApplyPreset: () => {},
        onParameter: () => {},
        onPreview: () => {},
        onAskAi: () => {},
        onOpenSettings: () => {}
      }
    });

    expect(body).toContain('Full-frame depth in progress');
    expect(body).toContain('45%');
    expect(body).toContain('12 of 140 frames sampled');
    expect(body).toContain('Decoding full-frame batches');
    expect(body).toContain('Progress 45 of 100');
    expect(body).toContain('aria-label="Full-frame depth progress"');
  });
});
