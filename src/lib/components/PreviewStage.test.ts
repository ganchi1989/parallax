import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import { createDemoProject } from '../demo';
import PreviewStage from './PreviewStage.svelte';

function renderPreviewStage(options: {
  previewEnabled: boolean;
  previewRendering?: boolean;
  previewUrl?: string | null;
  viewMode?: 'original' | 'anaglyph' | 'split' | 'depth';
  previewLockedReason?: string;
}) {
  const project = createDemoProject();
  const shot = project.shots[0];
  return render(PreviewStage, {
    props: {
      project,
      shot,
      demoMode: false,
      originalUrl: '/source.mp4',
      previewUrl: options.previewUrl ?? null,
      previewEnabled: options.previewEnabled,
      previewRendering: options.previewRendering ?? false,
      previewLockedReason: options.previewLockedReason,
      viewMode: options.viewMode ?? 'original',
      currentTime: shot.startSeconds,
      playing: false,
      onViewMode: () => {},
      onRequestPreview: () => {},
      onToggleSafeZones: () => {},
      onTogglePlayback: () => {},
      onPlaybackState: () => {},
      onStepShot: () => {},
      onSeek: () => {}
    }
  }).body;
}

describe('PreviewStage render-on-demand controls', () => {
  it('keeps Anaglyph safely locked until production preview rendering is verified', () => {
    const body = renderPreviewStage({
      previewEnabled: false,
      previewLockedReason: 'Full-frame direction is not verified'
    });

    expect(body).toMatch(/<button disabled=""[^>]+title="Full-frame direction is not verified"[^>]*>/);
    expect(body).toContain('Anaglyph');
  });

  it('makes Anaglyph actionable before a preview artifact exists', () => {
    const body = renderPreviewStage({ previewEnabled: true });

    expect(body).toMatch(/<button(?![^>]*disabled)[^>]+title="Render and open this shot in Anaglyph view"[^>]*>/);
    expect(body).toContain('aria-busy="false"');
  });

  it('shows an explicit busy state while the requested preview renders', () => {
    const body = renderPreviewStage({
      previewEnabled: true,
      previewRendering: true,
      viewMode: 'anaglyph'
    });

    expect(body).toContain('aria-busy="true"');
    expect(body).toContain('Rendering…');
    expect(body).toContain('Rendering anaglyph preview');
    expect(body).toContain('role="status"');
  });
});

describe('PreviewStage source framing', () => {
  function renderWithSize(width: number, height: number) {
    const project = { ...createDemoProject(), width, height };
    const shot = project.shots[0];
    return render(PreviewStage, {
      props: {
        project,
        shot,
        demoMode: false,
        originalUrl: '/source.mp4',
        previewUrl: null,
        previewEnabled: true,
        viewMode: 'original' as const,
        currentTime: shot.startSeconds,
        playing: false,
        onViewMode: () => {},
        onRequestPreview: () => {},
        onToggleSafeZones: () => {},
        onTogglePlayback: () => {},
        onPlaybackState: () => {},
        onStepShot: () => {},
        onSeek: () => {}
      }
    });
  }

  it('frames portrait sources at their own aspect instead of pillarboxing them', () => {
    // A phone clip: the viewer used to force 16:9 and draw the comfort guides
    // around the empty box either side of the picture.
    const portrait = renderWithSize(320, 567);
    expect(portrait.body).toContain('--frame-aspect:320 / 567');
    expect(portrait.body).toMatch(/class="[^"]*\btall\b/);
  });

  it('leaves landscape sources sized by width', () => {
    const landscape = renderWithSize(1920, 1080);
    expect(landscape.body).toContain('--frame-aspect:1920 / 1080');
    expect(landscape.body).not.toMatch(/class="[^"]*\btall\b/);
  });
});

describe('PreviewStage aspect adaptivity', () => {
  function frameFor(width: number, height: number) {
    const project = { ...createDemoProject(), width, height };
    const shot = project.shots[0];
    return render(PreviewStage, {
      props: {
        project,
        shot,
        demoMode: false,
        originalUrl: '/source.mp4',
        previewUrl: null,
        previewEnabled: true,
        viewMode: 'original' as const,
        currentTime: shot.startSeconds,
        playing: false,
        onViewMode: () => {},
        onRequestPreview: () => {},
        onToggleSafeZones: () => {},
        onTogglePlayback: () => {},
        onPlaybackState: () => {},
        onStepShot: () => {},
        onSeek: () => {}
      }
    }).body;
  }

  it('adapts to any aspect ratio rather than a fixed set', () => {
    const cases: [number, number, boolean][] = [
      [1920, 1080, false], // 16:9
      [1440, 1080, false], // 4:3
      [2048, 858, false],  // 2.39:1 scope
      [1080, 1080, false], // square
      [1080, 1350, true],  // 4:5
      [1080, 1920, true],  // 9:16
      [320, 567, true]     // the WeChat clip
    ];
    for (const [width, height, tall] of cases) {
      const body = frameFor(width, height);
      expect(body).toContain(`--frame-aspect:${width} / ${height}`);
      expect(/class="[^"]*\btall\b/.test(body)).toBe(tall);
    }
  });

  it('falls back to 16:9 only when the source size is unknown', () => {
    expect(frameFor(0, 0)).toContain('--frame-aspect:16 / 9');
  });
});

describe('render state indicators', () => {
  it('marks each shot as rendered, rendering, or not rendered', async () => {
    const ShotList = (await import('./ShotList.svelte')).default;
    const project = createDemoProject();
    const { body } = render(ShotList, {
      props: {
        project,
        demoMode: false,
        featuresReady: true,
        scriptReady: true,
        previewedShotIds: [project.shots[0].id],
        renderingShotIds: [project.shots[1].id],
        selectedShotId: project.shots[0].id,
        selectedShotIds: [],
        onSelect: () => {}
      }
    });
    expect(body).toContain('3D rendered');
    expect(body).toContain('Rendering 3D');
    expect(body).toContain('Not rendered');
  });

  it('separates rendered clips from pending ones on the timeline', async () => {
    const Timeline = (await import('./Timeline.svelte')).default;
    const project = createDemoProject();
    const { body } = render(Timeline, {
      props: {
        project,
        currentTime: 0,
        selectedShotId: project.shots[0].id,
        previewedShotIds: [project.shots[0].id],
        renderingShotIds: [project.shots[1].id],
        zoom: 1,
        onSeek: () => {},
        onSelect: () => {},
        onZoom: () => {}
      }
    });
    expect(body).toMatch(/class="[^"]*\brendered\b/);
    expect(body).toContain('Rendered in 3D');
    expect(body).toContain('Not rendered yet');
  });
});

describe('batch render control', () => {
  async function shotList(props: Record<string, unknown>) {
    const ShotList = (await import('./ShotList.svelte')).default;
    const project = createDemoProject();
    return render(ShotList, {
      props: {
        project,
        demoMode: false,
        featuresReady: true,
        scriptReady: true,
        selectedShotId: project.shots[0].id,
        selectedShotIds: [],
        onSelect: () => {},
        renderAllEnabled: true,
        ...props
      }
    }).body;
  }

  it('counts how many shots still need a render', async () => {
    const project = createDemoProject();
    const body = await shotList({ previewedShotIds: [project.shots[0].id] });
    expect(body).toContain(`Render all (${project.shots.length - 1})`);
  });

  it('reports completion instead of offering a no-op', async () => {
    const project = createDemoProject();
    const body = await shotList({ previewedShotIds: project.shots.map((shot) => shot.id) });
    expect(body).toContain('All shots rendered');
  });

  it('shows progress while a batch is running', async () => {
    expect(await shotList({ renderAllBusy: true })).toContain('Rendering all');
  });
});
