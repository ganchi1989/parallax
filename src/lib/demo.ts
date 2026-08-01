import { presetById } from './constants';
import type { Project, RecentProject, Shot } from './types';

const shotSeeds: Array<{
  name: string;
  duration: number;
  preset: Shot['preset'];
  confidence: number;
  status: Shot['status'];
  warning?: string;
  features: Omit<Shot['features'], 'durationSeconds'>;
  color: string;
}> = [
  {
    name: 'Establishing canyon',
    duration: 7.4,
    preset: 'vista_deep',
    confidence: 0.94,
    status: 'ready',
    features: { motion: 0.12, depthSpread: 0.88, speech: 0, foreground: 0.17, brightness: 0.72, cutFrequencyContext: 0.18, cameraMovement: 'static', depthReliability: 0.96 },
    color: '#54c6c0'
  },
  {
    name: 'Approach through arch',
    duration: 4.8,
    preset: 'action_controlled',
    confidence: 0.86,
    status: 'ready',
    features: { motion: 0.68, depthSpread: 0.72, speech: 0, foreground: 0.28, brightness: 0.61, cutFrequencyContext: 0.42, cameraMovement: 'lateral', depthReliability: 0.9 },
    color: '#e59a62'
  },
  {
    name: 'Mara close-up',
    duration: 5.6,
    preset: 'dialogue_subtle',
    confidence: 0.91,
    status: 'ready',
    features: { motion: 0.16, depthSpread: 0.31, speech: 0.76, foreground: 0.61, brightness: 0.57, cutFrequencyContext: 0.26, cameraMovement: 'static', depthReliability: 0.94 },
    color: '#8b7cf6'
  },
  {
    name: 'Crossing the ridge',
    duration: 3.2,
    preset: 'action_controlled',
    confidence: 0.79,
    status: 'warning',
    warning: 'Foreground crosses the left window edge',
    features: { motion: 0.82, depthSpread: 0.76, speech: 0, foreground: 0.44, brightness: 0.65, cutFrequencyContext: 0.58, cameraMovement: 'unstable', depthReliability: 0.84 },
    color: '#f08266'
  },
  {
    name: 'Map exchange',
    duration: 6.9,
    preset: 'dialogue_subtle',
    confidence: 0.89,
    status: 'ready',
    features: { motion: 0.21, depthSpread: 0.42, speech: 0.83, foreground: 0.53, brightness: 0.52, cutFrequencyContext: 0.22, cameraMovement: 'static', depthReliability: 0.93 },
    color: '#a078e8'
  },
  {
    name: 'Descent',
    duration: 4.1,
    preset: 'neutral',
    confidence: 0.64,
    status: 'warning',
    warning: 'Low depth confidence — pop-out disabled',
    features: { motion: 0.55, depthSpread: 0.49, speech: 0.04, foreground: 0.38, brightness: 0.43, cutFrequencyContext: 0.49, cameraMovement: 'vertical', depthReliability: 0.46 },
    color: '#8994a6'
  },
  {
    name: 'Stone detail',
    duration: 3.7,
    preset: 'closeup_flat',
    confidence: 0.9,
    status: 'ready',
    features: { motion: 0.08, depthSpread: 0.2, speech: 0, foreground: 0.81, brightness: 0.48, cutFrequencyContext: 0.31, cameraMovement: 'static', depthReliability: 0.92 },
    color: '#dc7495'
  },
  {
    name: 'Valley reveal',
    duration: 8.2,
    preset: 'vista_deep',
    confidence: 0.96,
    status: 'ready',
    features: { motion: 0.14, depthSpread: 0.93, speech: 0, foreground: 0.12, brightness: 0.79, cutFrequencyContext: 0.16, cameraMovement: 'zoom', depthReliability: 0.97 },
    color: '#50bfb3'
  },
  {
    name: 'Fade to black',
    duration: 2.7,
    preset: 'neutral',
    confidence: 0.82,
    status: 'ready',
    features: { motion: 0.03, depthSpread: 0.08, speech: 0, foreground: 0.08, brightness: 0.08, cutFrequencyContext: 0.37, cameraMovement: 'static', depthReliability: 0.79 },
    color: '#6d7583'
  }
];

export const DEMO_RECENTS: RecentProject[] = [
  {
    name: 'Echoes of Aster',
    path: 'D:\\Films\\Echoes of Aster\\echoes.aistereo',
    modified: '12 minutes ago',
    duration: '00:46',
    accent: '#61d3c5'
  },
  {
    name: 'Night Transit',
    path: 'D:\\Projects\\Night Transit\\night-transit.aistereo',
    modified: 'Yesterday',
    duration: '01:18',
    accent: '#8e7cf3'
  },
  {
    name: 'Glasshouse Tests',
    path: 'C:\\Stereo Projects\\Glasshouse\\glasshouse.aistereo',
    modified: '4 days ago',
    duration: '00:32',
    accent: '#df8e67'
  }
];

export function createDemoProject(
  sourcePath = 'D:\\Films\\Echoes of Aster\\aster_canyon_prores.mov',
  projectPath = 'D:\\Films\\Echoes of Aster'
): Project {
  let cursor = 0;
  const shots: Shot[] = shotSeeds.map((seed, index) => {
    const startSeconds = cursor;
    cursor += seed.duration;
    const preset = presetById(seed.preset);
    return {
      id: index + 1,
      name: seed.name,
      startSeconds,
      endSeconds: cursor,
      preset: seed.preset,
      confidence: seed.confidence,
      status: seed.status,
      warning: seed.warning,
      features: { ...seed.features, durationSeconds: seed.duration },
      color: seed.color,
      parameters: { ...preset.parameters }
    };
  });

  const fileName = sourcePath.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') ?? 'Untitled stereo project';
  return {
    id: 'demo-echoes-aster',
    name: sourcePath.includes('aster_canyon') ? 'Echoes of Aster' : fileName.replace(/[_-]/g, ' '),
    sourcePath,
    projectPath,
    durationSeconds: cursor,
    width: 3840,
    height: 2160,
    fps: 24,
    codec: 'ProRes 422',
    shots,
    updatedAt: new Date().toISOString(),
    dirty: false
    ,analysisTier: 'production'
    ,analysisReady: true
    ,scriptRevision: '0'.repeat(64)
    ,depthMode: 'synthetic'
  };
}
