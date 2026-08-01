import type { AppSettings, PresetId, StereoParameters } from './types';

export interface PresetDefinition {
  id: PresetId;
  label: string;
  shortLabel: string;
  description: string;
  color: string;
  parameters: StereoParameters;
}

export const DEFAULT_SETTINGS: AppSettings = {
  theme: 'dark',
  device: 'auto',
  depthEngine: 'video-depth-anything-small',
  anaglyphMode: 'calibrated',
  swapEyes: false,
  previewClipWidth: 1280,
  autosave: true,
  reduceMotion: false,
  showSafeZones: true,
  llmEnabled: false,
  llmProvider: 'openai',
  llmModel: 'gpt-5.6-terra'
};

export const PARAMETER_LIMITS = {
  depthStrength: { min: 0, max: 1, step: 0.01 },
  convergence: { min: 0.1, max: 0.9, step: 0.01 },
  backgroundDisparity: { min: 0, max: 0.02, step: 0.001 },
  popoutDisparity: { min: 0, max: 0.008, step: 0.001 },
  temporalSmoothing: { min: 0, max: 1, step: 0.01 },
  transitionFrames: { min: 0, max: 24, step: 1 }
} as const;

export const PRESETS: PresetDefinition[] = [
  {
    id: 'dialogue_subtle',
    label: 'Dialogue Subtle',
    shortLabel: 'Dialogue',
    description: 'Stable, gentle depth for faces and speech.',
    color: '#8b7cf6',
    parameters: {
      depthStrength: 0.55,
      convergence: 0.58,
      backgroundDisparity: 0.007,
      popoutDisparity: 0.002,
      temporalSmoothing: 0.9,
      transitionFrames: 10,
      edgeProtection: true
    }
  },
  {
    id: 'action_controlled',
    label: 'Action Controlled',
    shortLabel: 'Action',
    description: 'Responsive depth with tighter comfort limits.',
    color: '#f29a62',
    parameters: {
      depthStrength: 0.68,
      convergence: 0.54,
      backgroundDisparity: 0.008,
      popoutDisparity: 0.0025,
      temporalSmoothing: 0.72,
      transitionFrames: 4,
      edgeProtection: true
    }
  },
  {
    id: 'vista_deep',
    label: 'Vista Deep',
    shortLabel: 'Vista',
    description: 'Layered background depth without pop-out.',
    color: '#46c8be',
    parameters: {
      depthStrength: 0.9,
      convergence: 0.7,
      backgroundDisparity: 0.01,
      popoutDisparity: 0.001,
      temporalSmoothing: 0.92,
      transitionFrames: 12,
      edgeProtection: true
    }
  },
  {
    id: 'closeup_flat',
    label: 'Close-up Flat',
    shortLabel: 'Close-up',
    description: 'Shallow, edge-protected treatment for close-ups.',
    color: '#e96d93',
    parameters: {
      depthStrength: 0.38,
      convergence: 0.72,
      backgroundDisparity: 0.005,
      popoutDisparity: 0.001,
      temporalSmoothing: 0.94,
      transitionFrames: 10,
      edgeProtection: true
    }
  },
  {
    id: 'neutral',
    label: 'Neutral',
    shortLabel: 'Neutral',
    description: 'Conservative fallback with maximum stability.',
    color: '#8f99aa',
    parameters: {
      depthStrength: 0.45,
      convergence: 0.55,
      backgroundDisparity: 0.006,
      popoutDisparity: 0,
      temporalSmoothing: 0.92,
      transitionFrames: 8,
      edgeProtection: true
    }
  }
];

export const presetById = (id: PresetId): PresetDefinition =>
  PRESETS.find((preset) => preset.id === id) ?? PRESETS[PRESETS.length - 1];
