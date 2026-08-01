import { convertFileSrc, invoke as tauriInvoke, isTauri } from '@tauri-apps/api/core';
import type { RuntimeInfo, WorkerEvent, WorkerRequest } from './types';

export type Unlisten = () => void;
export type WorkerListener = (event: WorkerEvent) => void;
export interface LlmKeyStatus {
  configured: boolean;
  source: 'credential_store' | 'environment' | 'none';
  securePersistentStorage?: boolean;
  workerRestartRequired?: boolean;
}

export interface NewProjectPlan {
  sourcePath: string;
  sourceName: string;
  baseDirectory: string;
  projectDirectory: string;
  projectName: string;
  folderName: string;
  collisionIndex: number;
  created: boolean;
}

export interface DesktopBridge {
  readonly mode: 'tauri' | 'browser-demo';
  runtimeInfo(): Promise<RuntimeInfo>;
  pickVideo(): Promise<string | null>;
  defaultProjectBase(): Promise<string>;
  pickProjectBaseDirectory(): Promise<string | null>;
  planNewProject(sourceVideo: string, baseDirectory?: string): Promise<NewProjectPlan>;
  allocateNewProject(sourceVideo: string, baseDirectory?: string): Promise<NewProjectPlan>;
  pickProjectDirectory(): Promise<string | null>;
  saveOutput(suggestedName: string, extension: string): Promise<string | null>;
  request(request: WorkerRequest): Promise<unknown>;
  cancelJob(jobId: string): Promise<void>;
  revealOutput(path: string): Promise<void>;
  hasLlmKey(): Promise<boolean>;
  saveLlmKey(key: string): Promise<LlmKeyStatus>;
  deleteLlmKey(): Promise<LlmKeyStatus>;
  restartWorker(): Promise<void>;
  assetUrl(path: string): Promise<string>;
  subscribe(listener: WorkerListener): Promise<Unlisten>;
}

class TauriBridge implements DesktopBridge {
  readonly mode = 'tauri' as const;

  private invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
    return tauriInvoke<T>(command, args);
  }

  async runtimeInfo(): Promise<RuntimeInfo> {
    try {
      return await this.invoke<RuntimeInfo>('runtime_info');
    } catch {
      return { mode: 'tauri', platform: 'Windows', workerReady: false, version: '0.1.0' };
    }
  }

  pickVideo(): Promise<string | null> {
    return this.invoke<string | null>('pick_video');
  }

  defaultProjectBase(): Promise<string> {
    return this.invoke<string>('default_project_base');
  }

  pickProjectBaseDirectory(): Promise<string | null> {
    return this.invoke<string | null>('pick_project_base_directory');
  }

  planNewProject(sourceVideo: string, baseDirectory?: string): Promise<NewProjectPlan> {
    const args = baseDirectory === undefined ? { sourceVideo } : { sourceVideo, baseDirectory };
    return this.invoke<NewProjectPlan>('plan_new_project', args);
  }

  allocateNewProject(sourceVideo: string, baseDirectory?: string): Promise<NewProjectPlan> {
    const args = baseDirectory === undefined ? { sourceVideo } : { sourceVideo, baseDirectory };
    return this.invoke<NewProjectPlan>('allocate_new_project', args);
  }

  pickProjectDirectory(): Promise<string | null> {
    return this.invoke<string | null>('pick_project_directory');
  }

  saveOutput(suggestedName: string, extension: string): Promise<string | null> {
    return this.invoke<string | null>('save_output', { suggestedName, extension });
  }

  request(request: WorkerRequest): Promise<unknown> {
    return this.invoke('worker_request', { request });
  }

  async cancelJob(jobId: string): Promise<void> {
    await this.invoke('cancel_job', { jobId });
  }

  async revealOutput(path: string): Promise<void> {
    await this.invoke('reveal_output', { path });
  }

  hasLlmKey(): Promise<boolean> {
    return this.invoke<LlmKeyStatus>('llm_key_status')
      .then((status) => status.configured);
  }

  saveLlmKey(key: string): Promise<LlmKeyStatus> {
    return this.invoke<LlmKeyStatus>('save_llm_key', { apiKey: key });
  }

  deleteLlmKey(): Promise<LlmKeyStatus> {
    return this.invoke<LlmKeyStatus>('delete_llm_key');
  }

  async restartWorker(): Promise<void> {
    await this.invoke('restart_worker');
  }

  async assetUrl(path: string): Promise<string> {
    const canonicalPath = await this.invoke<string>('authorize_preview_asset', { path });
    return convertFileSrc(canonicalPath);
  }

  async subscribe(listener: WorkerListener): Promise<Unlisten> {
    const { listen } = await import('@tauri-apps/api/event');
    return listen<WorkerEvent>('worker-event', (event) => listener(event.payload));
  }
}

class DemoBridge implements DesktopBridge {
  readonly mode = 'browser-demo' as const;
  private listeners = new Set<WorkerListener>();
  private timers = new Map<string, ReturnType<typeof setTimeout>[]>();
  private demoHasKey = false;
  private demoProjectDirectories = new Set<string>();

  async runtimeInfo(): Promise<RuntimeInfo> {
    return { mode: 'browser-demo', platform: 'Browser preview', workerReady: true, version: '0.1.0-demo' };
  }

  async pickVideo(): Promise<string | null> {
    await this.pause(350);
    return 'D:\\Films\\Echoes of Aster\\aster_canyon_prores.mov';
  }

  async defaultProjectBase(): Promise<string> {
    await this.pause(80);
    return 'D:\\Parallax Projects';
  }

  async pickProjectBaseDirectory(): Promise<string | null> {
    await this.pause(220);
    return 'D:\\Films\\Stereo Projects';
  }

  async planNewProject(sourceVideo: string, baseDirectory?: string): Promise<NewProjectPlan> {
    await this.pause(120);
    return this.demoProjectPlan(sourceVideo, baseDirectory ?? 'D:\\Parallax Projects', false);
  }

  async allocateNewProject(sourceVideo: string, baseDirectory?: string): Promise<NewProjectPlan> {
    await this.pause(160);
    const plan = this.demoProjectPlan(sourceVideo, baseDirectory ?? 'D:\\Parallax Projects', true);
    this.demoProjectDirectories.add(plan.projectDirectory.toLowerCase());
    return plan;
  }

  async pickProjectDirectory(): Promise<string | null> {
    await this.pause(220);
    return 'D:\\Films\\Echoes of Aster';
  }

  async saveOutput(suggestedName: string, extension: string): Promise<string | null> {
    await this.pause(240);
    const cleanExtension = extension.replace(/^\./, '');
    return `D:\\Films\\Echoes of Aster\\renders\\${suggestedName}.${cleanExtension}`;
  }

  async request(request: WorkerRequest): Promise<{ accepted: true; id: string }> {
    const renderMethods = new Set([
      'render_preview',
      'render_preview_frame',
      'render_final',
      'run',
      'estimate_depth'
    ]);
    if (renderMethods.has(request.method)) {
      this.simulateJob(request);
    } else if (request.method === 'recommend_preset') {
      const timer = setTimeout(() => {
        this.emit({ type: 'result', id: request.id, result: {
          preset: 'action_controlled',
          confidence: 0.87,
          reason: 'Strong lateral movement and a short cut benefit from responsive depth, while restrained pop-out protects comfort near the frame edge.',
          source: 'llm',
          fallback_used: false
        }});
      }, 1200);
      this.timers.set(request.id, [timer]);
    } else if (request.method === 'test_llm') {
      const timer = setTimeout(() => this.emit({ type: 'result', id: request.id, result: { connected: true, model: 'gpt-5.6-terra' } }), 850);
      this.timers.set(request.id, [timer]);
    } else {
      const timer = setTimeout(() => {
        this.emit({ type: 'result', id: request.id, result: { demo: true, method: request.method } });
      }, 260);
      this.timers.set(request.id, [timer]);
    }
    return { accepted: true, id: request.id };
  }

  async cancelJob(jobId: string): Promise<void> {
    for (const timer of this.timers.get(jobId) ?? []) clearTimeout(timer);
    this.timers.delete(jobId);
    this.emit({ type: 'log', id: jobId, level: 'warning', message: 'Processing cancelled by user.' });
  }

  async revealOutput(_path: string): Promise<void> {
    await this.pause(120);
  }

  async hasLlmKey(): Promise<boolean> {
    return this.demoHasKey;
  }

  async saveLlmKey(key: string): Promise<LlmKeyStatus> {
    void key;
    throw new Error('API keys are disabled in the browser demo. Use the native desktop app.');
  }

  async deleteLlmKey(): Promise<LlmKeyStatus> {
    await this.pause(120);
    this.demoHasKey = false;
    return { configured: false, source: 'none', securePersistentStorage: true, workerRestartRequired: false };
  }

  async restartWorker(): Promise<void> {
    await this.pause(180);
  }

  async assetUrl(_path: string): Promise<string> {
    return '';
  }

  async subscribe(listener: WorkerListener): Promise<Unlisten> {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private simulateJob(request: WorkerRequest): void {
    const isStill = request.method === 'render_preview_frame';
    const isPreview = isStill || request.method === 'render_preview';
    const stages = isPreview
      ? ['Preparing shot', 'Warping stereo views', 'Composing anaglyph']
      : ['Normalizing media', 'Estimating depth', 'Directing shots', 'Rendering stereo', 'Quality control'];
    const points = isPreview ? [8, 34, 66, 100] : [5, 22, 49, 76, 100];
    const timers = points.map((progress, index) =>
      setTimeout(() => {
        if (progress < 100) {
          this.emit({
            type: 'progress',
            id: request.id,
            job_id: request.id,
            stage: stages[Math.min(index, stages.length - 1)],
            completed: progress,
            total: 100,
            message: isPreview ? `Shot preview - ${progress}%` : `Final render - ${progress}%`
          });
          return;
        }
        this.emit({
          type: 'result',
          id: request.id,
          result: {
            preview_path: isStill
              ? 'D:\\Films\\Echoes of Aster\\previews\\shot_0003_f000000.png'
              : isPreview
                ? 'D:\\Films\\Echoes of Aster\\previews\\shot_0003.mp4'
                : undefined,
            still: isStill ? true : undefined,
            output_path: isPreview ? undefined : 'D:\\Films\\Echoes of Aster\\renders\\echoes_anaglyph.mp4',
            qc_passed: true
          }
        });
        this.timers.delete(request.id);
      }, (isStill ? 90 : 550) + index * (isStill ? 70 : isPreview ? 520 : 800))
    );
    this.timers.set(request.id, timers);
  }

  private emit(event: WorkerEvent): void {
    for (const listener of this.listeners) listener(event);
  }

  private demoProjectPlan(sourceVideo: string, baseDirectory: string, created: boolean): NewProjectPlan {
    const sourceName = sourceVideo.split(/[\\/]/).pop()?.trim() || 'Untitled video';
    const rawStem = sourceName.replace(/\.[^.]+$/, '');
    const truncateUtf16 = (value: string, maximumUnits: number) => {
      if (value.length <= maximumUnits) return value;
      let truncated = value.slice(0, Math.max(0, maximumUnits));
      const finalUnit = truncated.charCodeAt(truncated.length - 1);
      if (finalUnit >= 0xd800 && finalUnit <= 0xdbff) truncated = truncated.slice(0, -1);
      return truncated;
    };
    let preferredName = rawStem
      .replace(/[<>:"/\\|?*\u0000-\u001f\u007f-\u009f]/g, ' ')
      .replace(/[_\s]+/g, ' ')
      .replace(/[. ]+$/g, '')
      .trim() || 'Untitled Project';
    if (/^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)/i.test(preferredName)) {
      const reservedSuffix = ' Project';
      const deviceStemDisarmed = preferredName.replace(/\./g, ' ');
      const reservedPrefix = truncateUtf16(deviceStemDisarmed, 96 - reservedSuffix.length).replace(/[. ]+$/g, '') || 'Untitled';
      preferredName = `${reservedPrefix}${reservedSuffix}`;
    }
    preferredName = truncateUtf16(preferredName, 96).replace(/[. ]+$/g, '') || 'Untitled Project';
    const trimmedBase = baseDirectory.trim().replace(/[\\/]+$/, '');
    const normalizedBase = /^[a-z]:$/i.test(trimmedBase) ? `${trimmedBase}\\` : trimmedBase;
    const join = (folderName: string) => normalizedBase.endsWith('\\')
      ? `${normalizedBase}${folderName}`
      : `${normalizedBase}\\${folderName}`;
    let collisionIndex = 0;
    let nextSuffix = 2;
    let folderName = preferredName;
    while (this.demoProjectDirectories.has(join(folderName).toLowerCase())) {
      collisionIndex = nextSuffix;
      const suffix = ` (${nextSuffix})`;
      const maximumStemUnits = 96 - suffix.length;
      const collisionStem = truncateUtf16(preferredName, maximumStemUnits).replace(/[. ]+$/g, '') || truncateUtf16('Untitled Project', maximumStemUnits);
      folderName = `${collisionStem}${suffix}`;
      nextSuffix += 1;
    }
    return {
      sourcePath: sourceVideo,
      sourceName,
      baseDirectory: normalizedBase,
      projectDirectory: join(folderName),
      projectName: folderName,
      folderName,
      collisionIndex,
      created
    };
  }

  private pause(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

export const isTauriRuntime = (): boolean => isTauri();

export const createDesktopBridge = (): DesktopBridge =>
  isTauriRuntime() ? new TauriBridge() : new DemoBridge();

export const desktopBridge: DesktopBridge = createDesktopBridge();
