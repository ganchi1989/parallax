import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { compile } from 'svelte/compiler';
import { transformWithEsbuild } from 'vite';
import { describe, expect, it } from 'vitest';

/**
 * Svelte 5 runs a legacy `$:` body untracked and derives its dependencies from
 * the identifiers written in the statement itself. A statement that only calls
 * a helper — `$: value = compute()` — therefore compiles to an effect with an
 * empty dependency list, runs once at mount, and keeps its initial value for
 * the rest of the session even when the store it reads changes.
 *
 * That silently froze every capability gate (Director editing, preview, export)
 * at the locked state the app starts in, so the workstation stayed stuck on
 * "Building quick Director draft" no matter how far analysis actually got.
 */
async function compileApp(): Promise<string> {
  const path = fileURLToPath(new URL('./App.svelte', import.meta.url));
  const source = readFileSync(path, 'utf8');
  const script = source.match(/<script lang="ts">([\s\S]*?)<\/script>/);
  expect(script, 'App.svelte must expose a TypeScript instance script').toBeTruthy();
  const javascript = await transformWithEsbuild(script![1], 'App.ts', { loader: 'ts' });
  const plain = source.replace(script![0], `<script>${javascript.code}</script>`);
  return compile(plain, { generate: 'client', runes: false, filename: 'App.svelte' }).js.code;
}

describe('App reactive statements', () => {
  it('gives every reactive statement at least one tracked dependency', async () => {
    const code = await compileApp();
    const effects = code.match(/\$\.legacy_pre_effect\(\s*\(\) => (\{\}|\()/g) ?? [];
    expect(effects.length, 'compiled output no longer looks like legacy reactive statements').toBeGreaterThan(0);
    expect(effects.filter((effect) => effect.includes('{}'))).toEqual([]);
  });

  it('recomputes workspace capabilities whenever the app store changes', async () => {
    const code = await compileApp();
    const statement = code
      .split('$.legacy_pre_effect(')
      .slice(1)
      .find((chunk) => /\$\.set\(workspaceCapabilities/.test(chunk.slice(0, 300)));
    expect(statement, 'no reactive statement assigns workspaceCapabilities').toBeTruthy();
    const dependencies = statement!.slice(0, statement!.indexOf(', () =>'));
    expect(dependencies).toContain('$appStore');
  });
});
