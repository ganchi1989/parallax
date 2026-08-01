export function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

export function formatTime(seconds: number, includeFrames = false, fps = 24): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const wholeSeconds = Math.floor(safe % 60);
  const frames = Math.floor((safe - Math.floor(safe)) * fps);
  const base = [hours, minutes, wholeSeconds].map((part) => String(part).padStart(2, '0')).join(':');
  return includeFrames ? `${base}:${String(frames).padStart(2, '0')}` : base;
}

export function shortPath(path: string, maxLength = 52): string {
  if (path.length <= maxLength) return path;
  const separator = path.includes('\\') ? '\\' : '/';
  const parts = path.split(separator);
  if (parts.length < 3) return `…${path.slice(-(maxLength - 1))}`;
  return `${parts[0]}${separator}…${separator}${parts.slice(-2).join(separator)}`;
}

export function createId(prefix = 'id'): string {
  const runtimeCrypto = typeof globalThis !== 'undefined'
    ? globalThis.crypto as Partial<Crypto> | undefined
    : undefined;
  if (runtimeCrypto?.randomUUID) {
    return `${prefix}-${runtimeCrypto.randomUUID()}`;
  }
  const bytes = new Uint8Array(16);
  if (runtimeCrypto?.getRandomValues) {
    runtimeCrypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');
  const uuid = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  return `${prefix}-${uuid}`;
}

export function percent(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}
