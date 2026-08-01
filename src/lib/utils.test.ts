import { describe, expect, it } from 'vitest';
import { clamp, createId, formatTime, shortPath } from './utils';

describe('UI utilities', () => {
  it('clamps finite and invalid values safely', () => {
    expect(clamp(2, 0, 1)).toBe(1);
    expect(clamp(-2, 0, 1)).toBe(0);
    expect(clamp(Number.NaN, 0.2, 1)).toBe(0.2);
  });

  it('formats frame-aware timecode', () => {
    expect(formatTime(65.5, true, 24)).toBe('00:01:05:12');
  });

  it('keeps the useful end of long paths', () => {
    expect(shortPath('D:\\A very long project directory\\shots\\shot_0003.mp4', 30)).toContain('shot_0003.mp4');
  });

  it('creates request ids with a canonical UUID suffix', () => {
    expect(createId('preview')).toMatch(/^preview-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });
});
