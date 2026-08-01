<script lang="ts">
  import { Cloud, PanelLeft, Save, Settings, Sparkles } from 'lucide-svelte';
  import BrandMark from './BrandMark.svelte';
  import type { Project, WorkspaceIdentity } from '../types';

  export let project: Project | null = null;
  export let workspace: WorkspaceIdentity | null = null;
  export let preparing = false;
  export let productionActive = false;
  export let saveEnabled = true;
  export let exportEnabled = true;
  export let shotsEnabled = true;
  export let onHome: () => void;
  export let onSave: () => void;
  export let onSettings: () => void;
  export let onExport: () => void;
  export let onToggleShots: () => void;

  $: displayName = project?.name ?? workspace?.name;
</script>

<header class="titlebar" data-tauri-drag-region>
  <button class="brand-button" on:click={onHome} aria-label="Return to project home" title="Project home">
    <BrandMark />
  </button>

  {#if displayName}
    <div class="project-title" data-tauri-drag-region>
      <button class="panel-toggle" disabled={!shotsEnabled} on:click={onToggleShots} title={shotsEnabled ? 'Toggle shot browser' : 'Shot browser unlocks after cut detection'} aria-label={shotsEnabled ? 'Toggle shot browser' : 'Shot browser unavailable — cut detection is still running'}>
        <PanelLeft size={15} />
      </button>
      <span>{displayName}</span>
      {#if project?.dirty}<i title="Unsaved changes"></i>{/if}
      {#if project?.analysisTier==='sampled'}<em class="draft">SAMPLED DRAFT</em>{:else if preparing}<em class="analyzing">ANALYZING</em>{/if}
      {#if project?.depthMode==='synthetic'}<em>TEST DEPTH</em>{:else if project?.depthMode==='image-analysis'}<em class="image-depth">IMAGE DEPTH</em>{/if}
    </div>

    <div class="title-actions">
      <span class="saved"><Cloud size={13} /> {project?.analysisTier==='sampled' ? (productionActive?'Full-frame analysis active':'Quick draft ready · full-frame pending') : preparing ? 'Local analysis active' : 'Local project'}</span>
      <button class="icon-button" disabled={!saveEnabled} on:click={onSave} title={saveEnabled ? 'Save project (Ctrl+S)' : 'Save unlocks with the Director script'} aria-label={saveEnabled ? 'Save project' : 'Save unavailable — Director script is still analyzing'}><Save size={16} /></button>
      <button class="icon-button" on:click={onSettings} title="Settings" aria-label="Settings"><Settings size={16} /></button>
      <button class="export-button" disabled={!exportEnabled} on:click={onExport} title={exportEnabled ? 'Export master' : 'Export unlocks after production depth and direction finish'} aria-label={exportEnabled ? 'Export master' : 'Export unavailable — production depth and direction are required'}><Sparkles size={15} /> {exportEnabled ? 'Export' : 'Export locked'}</button>
    </div>
  {/if}
</header>

<style>
  .titlebar {
    height: var(--titlebar-height);
    display: grid;
    grid-template-columns: auto 1fr;
    align-items: center;
    gap: 18px;
    padding: 0 12px 0 14px;
    border-bottom: 1px solid var(--line-soft);
    background: rgba(9, 11, 16, .94);
    position: relative;
    z-index: 30;
    user-select: none;
  }
  .brand-button { padding: 0; border: 0; background: none; cursor: pointer; }
  .panel-toggle { color: var(--text-dim); background: transparent; border: 0; font-size: 11px; padding: 7px 8px; border-radius: 5px; }
  .panel-toggle:hover:not(:disabled) { color: var(--text); background: var(--surface-hover); }
  .project-title { position: absolute; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: 7px; color: var(--text); font-size: 12px; font-weight: 570; }
  .project-title i { width: 5px; height: 5px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 8px var(--accent); }
  .project-title em { font-style: normal; font-size:11px; letter-spacing: .08em; color: #dea06a; border: 1px solid rgba(218,151,91,.35); background: rgba(118,72,34,.12); border-radius: 4px; padding: 3px 5px; } .project-title em.image-depth { color: #8fbbe8; border-color: rgba(120,170,225,.35); background: rgba(28,52,84,.16); }
  .project-title em.analyzing { color:#6ed5c8;border-color:rgba(94,207,194,.3);background:rgba(48,129,119,.1); }
  .project-title em.draft { color:#b7a7ef;border-color:rgba(151,129,232,.3);background:rgba(98,78,171,.12); }
  .project-title :global(svg) { color: var(--text-muted); }
  .panel-toggle { display: grid; place-items:center; padding: 5px; }
  .title-actions { display: flex; align-items: center; justify-self:end; gap: 7px; }
  .saved { display: inline-flex; align-items: center; gap: 6px; color: var(--text-muted); font-size: 10px; margin-right: 4px; }
  .icon-button { width: 31px; height: 31px; display: grid; place-items: center; color: var(--text-dim); border: 1px solid transparent; background: transparent; border-radius: 7px; }
  .icon-button:hover:not(:disabled) { color: var(--text); border-color: var(--line); background: var(--surface-hover); }
  .icon-button:disabled { color:#3f4854;background:transparent;border-color:transparent; }
  .export-button { display: inline-flex; align-items: center; gap: 7px; color: #07110f; background: linear-gradient(135deg, #73ddd0, #58bfb5); border: 0; border-radius: 7px; padding: 8px 13px; font-size: 11px; font-weight: 720; box-shadow: 0 0 18px rgba(86, 201, 189, .14); }
  .export-button:hover:not(:disabled) { filter: brightness(1.08); }
  .export-button:disabled { color:#56606d;background:#181e25;border:1px solid #29313b;box-shadow:none;filter:none; }
  @media (max-width: 1050px) { .saved { display: none; } }
  @media (max-width: 700px) { .project-title { display: none; } }
</style>
