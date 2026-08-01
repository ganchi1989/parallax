import { describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import { createAppStore } from './app-store';
import { PRESETS } from './constants';
import { parseShotFeature } from './project-session';

describe('app store stereo controls', () => {
  it('opens, hydrates, and closes a progressive workspace without inventing a project', () => {
    const store = createAppStore(undefined);
    store.openWorkspace({
      id: 'workspace-1',
      name: 'Feature Film',
      sourcePath: 'D:\\Footage\\feature.mp4',
      projectPath: 'D:\\Projects\\Feature Film'
    });

    let state = get(store);
    expect(state.view).toBe('editor');
    expect(state.project).toBeNull();
    expect(state.workspace?.stages.setup.status).toBe('running');

    store.completeWorkspaceStage('setup', { projectCreated: true });
    store.startWorkspaceStage('inspect');
    expect(get(store).workspace?.stages.inspect.status).toBe('running');

    const project = store.loadDemo('D:\\Footage\\feature.mp4', 'D:\\Projects\\Feature Film');
    store.openWorkspace({id:'workspace-2',name:project.name,sourcePath:project.sourcePath,projectPath:project.projectPath});
    store.openProject(project);
    expect(get(store).workspace?.identity.id).toBe('workspace-2');
    expect(get(store).project?.id).toBe(project.id);

    store.closeProject();
    state = get(store);
    expect(state.view).toBe('welcome');
    expect(state.workspace).toBeNull();
    expect(state.project).toBeNull();
  });

  it('keeps UI presets aligned with the engine preset contract', () => {
    const expected = {
      dialogue_subtle: [0.55, 0.58, 0.007, 0.002, 0.9, 10],
      action_controlled: [0.68, 0.54, 0.008, 0.0025, 0.72, 4],
      vista_deep: [0.9, 0.7, 0.01, 0.001, 0.92, 12],
      closeup_flat: [0.38, 0.72, 0.005, 0.001, 0.94, 10],
      neutral: [0.45, 0.55, 0.006, 0, 0.92, 8]
    } as const;
    for (const preset of PRESETS) {
      const p = preset.parameters;
      expect([p.depthStrength,p.convergence,p.backgroundDisparity,p.popoutDisparity,p.temporalSmoothing,p.transitionFrames]).toEqual(expected[preset.id]);
    }
  });
  it('clamps unsafe numerical parameters at the UI boundary', () => {
    const store = createAppStore(undefined);
    store.loadDemo();
    store.updateParameter('popoutDisparity', 4);
    const state = get(store);
    const selected = state.project?.shots.find((shot) => shot.id === state.selectedShotId);
    expect(selected?.parameters.popoutDisparity).toBe(0.008);
  });

  it('applies presets to a selected shot and supports undo', () => {
    const store = createAppStore(undefined);
    store.loadDemo();
    store.applyPreset('closeup_flat');
    let state = get(store);
    expect(state.project?.shots.find((shot) => shot.id === state.selectedShotId)?.preset).toBe('closeup_flat');
    expect(store.undo()).toBe(true);
    state = get(store);
    expect(state.project?.shots.find((shot) => shot.id === state.selectedShotId)?.preset).toBe('dialogue_subtle');
  });

  it('resets smoothing behavior by selecting a shot boundary directly', () => {
    const store = createAppStore(undefined);
    store.loadDemo();
    store.selectShot(5);
    const state = get(store);
    expect(state.currentTime).toBe(state.project?.shots[4].startSeconds);
    expect(state.playing).toBe(false);
  });

  it('preserves the latest engine revision through undo', () => {
    const store = createAppStore(undefined);
    store.loadDemo();
    store.applyPreset('vista_deep');
    const newRevision = 'a'.repeat(64);
    store.setScriptRevision(newRevision);
    expect(store.undo()).toBe(true);
    expect(get(store).project?.scriptRevision).toBe(newRevision);
    expect(get(store).project?.dirty).toBe(true);
  });

  it('keeps sampled provenance and coverage through local edit history', () => {
    const store = createAppStore(undefined);
    store.loadDemo();
    const current = get(store).project!;
    const coverage = {
      shotIds: current.shots.map((shot) => shot.id),
      sampledFrames: current.shots.length * 4,
      totalFrames: 2400,
      perShot: current.shots.map((shot, index) => ({
        shotId: shot.id,
        sampledFrames: 4,
        totalFrames: index === current.shots.length - 1 ? 2400 - (current.shots.length - 1) * 100 : 100
      }))
    };
    store.applyEngineState(current.shots, 'd'.repeat(64), true, 'sampled', coverage);
    store.applyPreset('vista_deep');
    expect(store.undo()).toBe(true);

    const restored = get(store).project!;
    expect(restored.analysisTier).toBe('sampled');
    expect(restored.draftCoverage).toEqual(coverage);
    expect(restored.scriptRevision).toBe('d'.repeat(64));
  });

  it('preserves the complete engine feature row on a shot', () => {
    const store = createAppStore(undefined);
    store.loadDemo();
    store.mergeShotFeatures([parseShotFeature({
      shot_id: 3,
      duration_seconds: 5.625,
      motion_score: 0.23,
      speech_ratio: 0.81,
      depth_spread: 0.44,
      foreground_ratio: 0.67,
      brightness: 0.39,
      cut_frequency_context: 0.52,
      camera_movement: 'zoom',
      depth_reliability: 0.88
    })]);

    expect(get(store).project?.shots.find((shot) => shot.id === 3)?.features).toEqual({
      durationSeconds: 5.625,
      motion: 0.23,
      speech: 0.81,
      depthSpread: 0.44,
      foreground: 0.67,
      brightness: 0.39,
      cutFrequencyContext: 0.52,
      cameraMovement: 'zoom',
      depthReliability: 0.88
    });
  });
});
