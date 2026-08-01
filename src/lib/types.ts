export type ViewMode = 'original' | 'anaglyph' | 'split' | 'depth';
export type PresetId = 'dialogue_subtle' | 'action_controlled' | 'vista_deep' | 'closeup_flat' | 'neutral';
export type QueueStatus = 'waiting' | 'processing' | 'complete' | 'paused' | 'failed';
export type WorkspaceView = 'welcome' | 'editor';
export type AnalysisStageId =
  | 'setup'
  | 'inspect'
  | 'normalize'
  | 'shots'
  | 'draft'
  | 'depth'
  | 'features'
  | 'direct';
export type AnalysisStageStatus = 'locked' | 'running' | 'complete' | 'failed' | 'cancelled';
export type AnalysisTier = 'none' | 'sampled' | 'production';

export interface AnalysisShotCoverage {
  shotId: number;
  sampledFrames: number;
  totalFrames: number;
}

export interface AnalysisCoverage {
  shotIds: number[];
  sampledFrames: number;
  totalFrames: number;
  perShot: AnalysisShotCoverage[];
}

export interface AnalysisStageState {
  status: AnalysisStageStatus;
  progress: number;
  completed?: number;
  total?: number;
  message?: string;
  error?: string;
}

export interface WorkspaceArtifacts {
  projectCreated: boolean;
  mediaReady: boolean;
  normalizedReady: boolean;
  shotIds: number[];
  analysisTier: AnalysisTier;
  draftCoverage?: AnalysisCoverage;
  // 'image-analysis' is measured depth from the built-in estimator: weaker
  // than the certified model, but real, and allowed to export.
  depthMode: 'unknown' | 'synthetic' | 'image-analysis' | 'production';
  featureShotIds: number[];
  scriptShotIds: number[];
  scriptRevision?: string;
}

export interface WorkspaceIdentity {
  id: string;
  name: string;
  sourcePath: string;
  projectPath: string;
}

export interface WorkspaceSession {
  identity: WorkspaceIdentity;
  stages: Record<AnalysisStageId, AnalysisStageState>;
  artifacts: WorkspaceArtifacts;
}

export interface CapabilityGate {
  enabled: boolean;
  reason?: string;
}

export interface WorkspaceCapabilities {
  sourceMonitor: CapabilityGate;
  metadata: CapabilityGate;
  shotNavigation: CapabilityGate;
  shotMetrics: CapabilityGate;
  directorEdit: CapabilityGate;
  previewRender: CapabilityGate;
  assistantAnalysis: CapabilityGate;
  finalExport: CapabilityGate;
}

export interface StereoParameters {
  depthStrength: number;
  convergence: number;
  backgroundDisparity: number;
  popoutDisparity: number;
  temporalSmoothing: number;
  transitionFrames: number;
  edgeProtection: boolean;
}

export interface ShotFeatures {
  durationSeconds: number;
  motion: number;
  depthSpread: number;
  speech: number;
  foreground: number;
  brightness: number;
  cutFrequencyContext: number;
  cameraMovement: CameraMovement;
  depthReliability: number;
}

export type CameraMovement = 'static' | 'lateral' | 'vertical' | 'zoom' | 'unstable';

/** The exact, validated feature vocabulary accepted by the Python worker. */
export interface EngineShotFeatures {
  shot_id: number;
  duration_seconds: number;
  motion_score: number;
  speech_ratio: number;
  depth_spread: number;
  foreground_ratio: number;
  brightness: number;
  cut_frequency_context: number;
  camera_movement: CameraMovement;
  depth_reliability: number;
}

export interface Shot {
  id: number;
  name: string;
  startSeconds: number;
  endSeconds: number;
  preset: PresetId;
  confidence: number;
  selected?: boolean;
  status: 'ready' | 'warning' | 'analyzing';
  warning?: string;
  parameters: StereoParameters;
  features: ShotFeatures;
  color: string;
}

export interface Project {
  id: string;
  name: string;
  sourcePath: string;
  projectPath: string;
  durationSeconds: number;
  width: number;
  height: number;
  fps: number;
  codec: string;
  shots: Shot[];
  updatedAt: string;
  dirty: boolean;
  analysisTier: AnalysisTier;
  draftCoverage?: AnalysisCoverage;
  scriptRevision?: string;
  analysisReady?: boolean;
  depthMode?: 'production' | 'image-analysis' | 'synthetic' | 'unknown';
}

export interface QueueItem {
  id: string;
  title: string;
  detail: string;
  stage: string;
  progress: number;
  status: QueueStatus;
}

export type DepthEngine = 'video-depth-anything-small' | 'monocular-cues';
/** Width a shot preview clip renders at. 0 keeps the full working-copy width. */
export type PreviewQuality = 640 | 960 | 1280 | 0;

export interface AppSettings {
  theme: 'dark';
  device: 'auto' | 'cuda' | 'cpu';
  depthEngine: DepthEngine;
  anaglyphMode: 'calibrated' | 'basic';
  swapEyes: boolean;
  previewClipWidth: PreviewQuality;
  autosave: boolean;
  reduceMotion: boolean;
  showSafeZones: boolean;
  llmEnabled: boolean;
  llmProvider: 'openai';
  llmModel: string;
}

export interface RecentProject {
  name: string;
  path: string;
  modified: string;
  duration: string;
  accent: string;
}

export interface Toast {
  id: string;
  kind: 'success' | 'error' | 'warning' | 'info';
  title: string;
  message?: string;
}

export interface AppState {
  view: WorkspaceView;
  workspace: WorkspaceSession | null;
  project: Project | null;
  selectedShotId: number | null;
  selectedShotIds: number[];
  viewMode: ViewMode;
  playing: boolean;
  currentTime: number;
  zoom: number;
  queue: QueueItem[];
  settings: AppSettings;
  recentProjects: RecentProject[];
  busy: boolean;
}

export interface WorkerRequest<T extends Record<string, unknown> = Record<string, unknown>> {
  id: string;
  method: WorkerMethod;
  params: T;
}

export type WorkerMethod =
  | 'ping' | 'create_project' | 'inspect' | 'normalize' | 'detect_shots' | 'analyze_draft' | 'estimate_depth'
  | 'extract_features' | 'create_stereo_script' | 'direct' | 'render_preview' | 'render_preview_frame' | 'render_final'
  | 'render' | 'generate_qc' | 'qc' | 'run_pipeline' | 'run' | 'get_project' | 'llm_status'
  | 'test_llm' | 'recommend_preset' | 'apply_shot_overrides' | 'cancel' | 'cancel_job';

export interface WorkerProgressEvent {
  type: 'progress';
  id: string;
  job_id: string;
  stage: string;
  completed: number;
  total: number;
  message?: string;
}

export interface WorkerResultEvent {
  type: 'result';
  id: string;
  result: Record<string, unknown>;
}

export interface WorkerErrorEvent {
  type: 'error';
  id: string;
  error: {
    code: string;
    message: string;
    retryable: boolean;
    details?: unknown;
  };
}

export interface WorkerLogEvent {
  type: 'log';
  id?: string;
  level: 'debug' | 'info' | 'warning' | 'warn' | 'error';
  message: string;
}

export type WorkerEvent = WorkerProgressEvent | WorkerResultEvent | WorkerErrorEvent | WorkerLogEvent;

export interface RuntimeInfo {
  mode: 'tauri' | 'browser-demo';
  platform: string;
  workerReady: boolean;
  version: string;
}

export interface AiRecommendation {
  shotId: number;
  preset: PresetId;
  confidence: number;
  reason: string;
}

export interface ExportOptions {
  outputPath: string;
  format: 'anaglyph' | 'side_by_side';
  anaglyphMode: 'calibrated' | 'basic';
  swapEyes: boolean;
}
