import { get, writable } from 'svelte/store';
import {
  completeAnalysisStage,
  createWorkspaceSession,
  failAnalysisStage,
  reconcileVerifiedAnalysis,
  startAnalysisStage,
  updateAnalysisProgress
} from './analysis-state';
import { DEFAULT_SETTINGS, PARAMETER_LIMITS, presetById } from './constants';
import { createDemoProject, DEMO_RECENTS } from './demo';
import type {
  AppSettings,
  AppState,
  AnalysisStageId,
  PresetId,
  Project,
  QueueItem,
  StereoParameters,
  ViewMode,
  WorkerEvent,
  WorkspaceArtifacts,
  WorkspaceIdentity
} from './types';
import type { ShotFeatureUpdate } from './project-session';
import { clamp } from './utils';

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const SETTINGS_KEY = 'parallax-forge:settings:v1';
const PROJECT_KEY = 'parallax-forge:project:v1';
const RECENTS_KEY = 'parallax-forge:recents:v1';

function browserStorage(): StorageLike | undefined {
  try {
    return typeof localStorage === 'undefined' ? undefined : localStorage;
  } catch {
    return undefined;
  }
}

function readJson<T>(storage: StorageLike | undefined, key: string, fallback: T): T {
  if (!storage) return fallback;
  try {
    const value = storage.getItem(key);
    return value ? (JSON.parse(value) as T) : fallback;
  } catch {
    return fallback;
  }
}

function cloneProject(project: Project): Project {
  return {
    ...project,
    draftCoverage: project.draftCoverage ? {
      ...project.draftCoverage,
      shotIds: [...project.draftCoverage.shotIds],
      perShot: project.draftCoverage.perShot.map((item) => ({ ...item }))
    } : undefined,
    shots: project.shots.map((shot) => ({
      ...shot,
      parameters: { ...shot.parameters },
      features: { ...shot.features }
    }))
  };
}

function initialState(storage: StorageLike | undefined): AppState {
  const settings = {
    ...DEFAULT_SETTINGS,
    ...readJson<Partial<AppSettings>>(storage, SETTINGS_KEY, {})
  };
  const recentProjects = readJson(storage, RECENTS_KEY, DEMO_RECENTS);
  return {
    view: 'welcome',
    workspace: null,
    project: null,
    selectedShotId: null,
    selectedShotIds: [],
    viewMode: 'split',
    playing: false,
    currentTime: 0,
    zoom: 1,
    queue: [],
    settings,
    recentProjects,
    busy: false
  };
}

export function createAppStore(storage: StorageLike | undefined = browserStorage()) {
  const store = writable<AppState>(initialState(storage));
  const undoStack: Project[] = [];
  const redoStack: Project[] = [];

  const persist = (state: AppState): void => {
    if (!storage) return;
    try {
      storage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
      storage.setItem(RECENTS_KEY, JSON.stringify(state.recentProjects));
      if (state.project) storage.setItem(PROJECT_KEY, JSON.stringify(state.project));
    } catch {
      // Persistence is best-effort; the editor must remain usable in private browsing.
    }
  };

  store.subscribe(persist);

  const recordHistory = (state: AppState): void => {
    if (!state.project) return;
    undoStack.push(cloneProject(state.project));
    if (undoStack.length > 50) undoStack.shift();
    redoStack.length = 0;
  };

  const mutateShots = (mutator: (shot: Project['shots'][number]) => Project['shots'][number]): void => {
    store.update((state) => {
      if (!state.project) return state;
      recordHistory(state);
      const targetIds = state.selectedShotIds.length
        ? state.selectedShotIds
        : state.selectedShotId === null
          ? []
          : [state.selectedShotId];
      return {
        ...state,
        project: {
          ...state.project,
          dirty: true,
          updatedAt: new Date().toISOString(),
          shots: state.project.shots.map((shot) => (targetIds.includes(shot.id) ? mutator(shot) : shot))
        }
      };
    });
  };

  return {
    subscribe: store.subscribe,

    loadDemo(sourcePath?: string, projectPath?: string): Project {
      const persisted = readJson<Project | null>(storage, PROJECT_KEY, null);
      const freshDemo = createDemoProject(sourcePath, projectPath);
      const canonicalFeatures = new Map(freshDemo.shots.map((shot)=>[shot.id,shot.features]));
      const project = !sourcePath && persisted?.id === 'demo-echoes-aster'
        ? {...freshDemo,...persisted,shots:persisted.shots.map((shot)=>({...shot,features:{...canonicalFeatures.get(shot.id),...shot.features}}))}
        : freshDemo;
      undoStack.length = 0;
      redoStack.length = 0;
      store.update((state) => ({
        ...state,
        view: 'editor',
        workspace: null,
        project,
        selectedShotId: project.shots[2]?.id ?? project.shots[0]?.id ?? null,
        selectedShotIds: [],
        currentTime: project.shots[2]?.startSeconds ?? 0,
        playing: false,
        busy: false,
        recentProjects: [
          {
            name: project.name,
            path: `${project.projectPath}\\${project.name.toLowerCase().replace(/\s+/g, '-')}.aistereo`,
            modified: 'Just now',
            duration: `${Math.floor(project.durationSeconds / 60)
              .toString()
              .padStart(2, '0')}:${Math.floor(project.durationSeconds % 60)
              .toString()
              .padStart(2, '0')}`,
            accent: '#61d3c5'
          },
          ...state.recentProjects.filter((recent) => recent.name !== project.name)
        ].slice(0, 6)
      }));
      return project;
    },

    openProject(project: Project): void {
      undoStack.length = 0;
      redoStack.length = 0;
      store.update((state) => ({
        ...state,
        view: 'editor',
        project: cloneProject(project),
        selectedShotId: project.shots[0]?.id ?? null,
        selectedShotIds: [],
        currentTime: 0,
        playing: false
      }));
    },

    openWorkspace(identity: WorkspaceIdentity): void {
      undoStack.length = 0;
      redoStack.length = 0;
      store.update((state) => ({
        ...state,
        view: 'editor',
        workspace: createWorkspaceSession(identity),
        project: null,
        selectedShotId: null,
        selectedShotIds: [],
        currentTime: 0,
        playing: false,
        busy: false
      }));
    },

    startWorkspaceStage(stage: AnalysisStageId, message?: string): void {
      store.update((state) => ({
        ...state,
        workspace: state.workspace ? startAnalysisStage(state.workspace, stage, message) : null
      }));
    },

    updateWorkspaceProgress(
      stage: AnalysisStageId,
      progress: number,
      message?: string,
      completed?: number,
      total?: number
    ): void {
      store.update((state) => ({
        ...state,
        workspace: state.workspace
          ? updateAnalysisProgress(state.workspace, stage, progress, message, completed, total)
          : null
      }));
    },

    completeWorkspaceStage(
      stage: AnalysisStageId,
      artifacts: Partial<WorkspaceArtifacts> = {}
    ): void {
      store.update((state) => ({
        ...state,
        workspace: state.workspace
          ? completeAnalysisStage(state.workspace, stage, artifacts)
          : null
      }));
    },

    reconcileWorkspaceAnalysis(
      analysisTier: 'sampled' | 'production',
      artifacts: Partial<WorkspaceArtifacts>
    ): void {
      store.update((state) => ({
        ...state,
        workspace: state.workspace
          ? reconcileVerifiedAnalysis(state.workspace, analysisTier, artifacts)
          : null
      }));
    },

    failWorkspaceStage(
      stage: AnalysisStageId,
      error: string,
      cancelled = false
    ): void {
      store.update((state) => ({
        ...state,
        workspace: state.workspace
          ? failAnalysisStage(state.workspace, stage, error, cancelled)
          : null
      }));
    },

    closeProject(): void {
      store.update((state) => ({
        ...state,
        view: 'welcome',
        workspace: null,
        project: null,
        selectedShotId: null,
        selectedShotIds: [],
        playing: false,
        currentTime: 0,
        queue: state.queue.filter((item) => item.status === 'processing')
      }));
    },

    setBusy(busy: boolean): void {
      store.update((state) => ({ ...state, busy }));
    },

    setViewMode(viewMode: ViewMode): void {
      store.update((state) => ({ ...state, viewMode }));
    },

    selectShot(id: number, additive = false, range = false): void {
      store.update((state) => {
        if (!state.project?.shots.some((shot) => shot.id === id)) return state;
        let selectedShotIds: number[] = [];
        if (range && state.selectedShotId !== null) {
          const ids = state.project.shots.map((shot) => shot.id);
          const from = ids.indexOf(state.selectedShotId);
          const to = ids.indexOf(id);
          selectedShotIds = ids.slice(Math.min(from, to), Math.max(from, to) + 1);
        } else if (additive) {
          const current = new Set(state.selectedShotIds.length ? state.selectedShotIds : [state.selectedShotId].filter(Boolean) as number[]);
          current.has(id) ? current.delete(id) : current.add(id);
          selectedShotIds = [...current];
        }
        const shot = state.project.shots.find((candidate) => candidate.id === id);
        return {
          ...state,
          selectedShotId: id,
          selectedShotIds,
          currentTime: shot?.startSeconds ?? state.currentTime,
          playing: false
        };
      });
    },

    stepShot(direction: -1 | 1): void {
      store.update((state) => {
        if (!state.project?.shots.length) return state;
        const currentIndex = state.project.shots.findIndex((shot) => shot.id === state.selectedShotId);
        const index = clamp(currentIndex + direction, 0, state.project.shots.length - 1);
        const shot = state.project.shots[index];
        return { ...state, selectedShotId: shot.id, selectedShotIds: [], currentTime: shot.startSeconds, playing: false };
      });
    },

    applyPreset(presetId: PresetId): void {
      const preset = presetById(presetId);
      mutateShots((shot) => ({ ...shot, preset: preset.id, parameters: { ...preset.parameters } }));
    },

    updateParameter<K extends keyof StereoParameters>(key: K, rawValue: StereoParameters[K]): void {
      mutateShots((shot) => {
        let value = rawValue;
        const limit = PARAMETER_LIMITS[key as keyof typeof PARAMETER_LIMITS];
        if (limit && typeof rawValue === 'number') {
          value = clamp(rawValue, limit.min, limit.max) as StereoParameters[K];
        }
        return { ...shot, parameters: { ...shot.parameters, [key]: value } };
      });
    },

    togglePlayback(): void {
      store.update((state) => ({ ...state, playing: !state.playing }));
    },

    setPlaying(playing: boolean): void {
      store.update((state) => ({ ...state, playing }));
    },

    setCurrentTime(currentTime: number): void {
      store.update((state) => ({
        ...state,
        currentTime: clamp(currentTime, 0, state.project?.durationSeconds ?? 0)
      }));
    },

    tick(deltaSeconds: number): void {
      store.update((state) => {
        if (!state.playing || !state.project) return state;
        const next = state.currentTime + deltaSeconds;
        if (next >= state.project.durationSeconds) return { ...state, currentTime: state.project.durationSeconds, playing: false };
        const activeShot = state.project.shots.find((shot) => next >= shot.startSeconds && next < shot.endSeconds);
        return { ...state, currentTime: next, selectedShotId: activeShot?.id ?? state.selectedShotId };
      });
    },

    setZoom(zoom: number): void {
      store.update((state) => ({ ...state, zoom: clamp(zoom, 0.55, 2.5) }));
    },

    updateSettings(settings: Partial<AppSettings>): void {
      store.update((state) => ({ ...state, settings: { ...state.settings, ...settings } }));
    },

    markSaved(): void {
      store.update((state) => ({
        ...state,
        project: state.project ? { ...state.project, dirty: false, updatedAt: new Date().toISOString() } : null
      }));
    },

    applyEngineState(
      shots: Project['shots'],
      scriptRevision: string,
      analysisReady = true,
      analysisTier: Project['analysisTier'] = 'production',
      draftCoverage?: Project['draftCoverage']
    ): void {
      store.update((state) => ({
        ...state,
        project: state.project ? {
          ...state.project,
          shots,
          scriptRevision,
          analysisReady,
          analysisTier,
          draftCoverage: draftCoverage ?? state.project.draftCoverage,
          dirty: false,
          updatedAt: new Date().toISOString()
        } : null
      }));
    },

    mergeShotFeatures(features: ShotFeatureUpdate[]): void {
      const byId=new Map(features.map((item)=>[item.shotId,item]));
      store.update((state)=>({
        ...state,
        project: state.project ? {...state.project,shots:state.project.shots.map((shot)=>{
          const item=byId.get(shot.id);
          if(!item)return shot;
          const {shotId: _shotId,...shotFeatures}=item;
          return {...shot,features:shotFeatures};
        })}:null
      }));
    },

    setScriptRevision(scriptRevision: string, dirty = false): void {
      store.update((state)=>({...state,project:state.project?{...state.project,scriptRevision,dirty,updatedAt:new Date().toISOString()}:null}));
    },

    setDepthMode(depthMode: NonNullable<Project['depthMode']>): void {
      store.update((state)=>({...state,project:state.project?{...state.project,depthMode}:null}));
    },

    enqueue(item: QueueItem): void {
      store.update((state) => ({ ...state, queue: [item, ...state.queue.filter((entry) => entry.id !== item.id)].slice(0, 5) }));
    },

    removeQueueItem(id: string): void {
      store.update((state) => ({ ...state, queue: state.queue.filter((item) => item.id !== id) }));
    },

    applyWorkerEvent(event: WorkerEvent): void {
      store.update((state) => {
        const id = event.type === 'progress' ? event.job_id : event.type === 'result' || event.type === 'error' ? event.id : event.id;
        if (!id) return state;
        const existing = state.queue.find((item) => item.id === id);
        if (!existing) return state;
        if (event.type === 'progress') {
          const progress = event.total > 0 ? clamp(event.completed / event.total, 0, 1) : 0;
          return {
            ...state,
            queue: state.queue.map((item) =>
              item.id === id ? { ...item, stage: event.stage, progress, status: 'processing' as const } : item
            )
          };
        }
        if (event.type === 'result') {
          return {
            ...state,
            queue: state.queue.map((item) =>
              item.id === id ? { ...item, stage: 'Complete', detail: 'Output ready', progress: 1, status: 'complete' as const } : item
            )
          };
        }
        if (event.type === 'error') {
          return {
            ...state,
            queue: state.queue.map((item) =>
              item.id === id ? { ...item, stage: 'Failed', detail: event.error.message, status: 'failed' as const } : item
            )
          };
        }
        return state;
      });
    },

    undo(): boolean {
      const previous = undoStack.pop();
      const current = get(store).project;
      if (!previous || !current) return false;
      redoStack.push(cloneProject(current));
      store.update((state) => ({ ...state, project: { ...previous, scriptRevision: current.scriptRevision, analysisReady: current.analysisReady, analysisTier: current.analysisTier, draftCoverage: current.draftCoverage, depthMode: current.depthMode, projectPath: current.projectPath, sourcePath: current.sourcePath, dirty: true } }));
      return true;
    },

    redo(): boolean {
      const next = redoStack.pop();
      const current = get(store).project;
      if (!next || !current) return false;
      undoStack.push(cloneProject(current));
      store.update((state) => ({ ...state, project: { ...next, scriptRevision: current.scriptRevision, analysisReady: current.analysisReady, analysisTier: current.analysisTier, draftCoverage: current.draftCoverage, depthMode: current.depthMode, projectPath: current.projectPath, sourcePath: current.sourcePath, dirty: true } }));
      return true;
    },

    snapshot(): AppState {
      return get(store);
    }
  };
}

export const appStore = createAppStore();
export type AppStore = ReturnType<typeof createAppStore>;
