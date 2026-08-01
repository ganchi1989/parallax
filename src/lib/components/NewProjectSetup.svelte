<script lang="ts">
  import { onMount } from 'svelte';
  import {
    AlertTriangle,
    ArrowRight,
    Check,
    FileVideo2,
    FolderCog,
    HardDrive,
    LoaderCircle,
    RotateCcw,
    X
  } from 'lucide-svelte';
  import type { NewProjectPlan } from '../bridge';

  export let sourcePath: string;
  export let baseDirectory: string | null = null;
  export let defaultBaseDirectory: string | null = null;
  export let usesDefault = true;
  export let plan: NewProjectPlan | null = null;
  export let busy = false;
  export let allocating = false;
  export let error: string | null = null;
  export let onChangeBase: () => void;
  export let onUseDefault: () => void;
  export let onCreate: () => void;
  export let onCancel: () => void;

  let dialogElement: HTMLDivElement;

  const fileName = (path: string) => path.split(/[\\/]/).pop()?.trim() || 'Selected video';
  const parentPath = (path: string) => {
    const index = Math.max(path.lastIndexOf('\\'), path.lastIndexOf('/'));
    if (index <= 0) return path;
    const parent = path.slice(0, index);
    const driveRoot = /^[a-z]:$/i.test(parent) || /^\\\\\?\\[a-z]:$/i.test(parent);
    return driveRoot ? `${parent}${path[index]}` : parent;
  };
  const displayPath = (path: string) => {
    if (path.startsWith('\\\\?\\UNC\\')) return `\\\\${path.slice(8)}`;
    return path.startsWith('\\\\?\\') ? path.slice(4) : path;
  };
  const focusableSelector = [
    'button:not([disabled])',
    'a[href]',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])'
  ].join(',');

  function trapFocus(event: KeyboardEvent) {
    if (event.key !== 'Tab') return;
    const focusable = Array.from(dialogElement.querySelectorAll<HTMLElement>(focusableSelector));
    if (!focusable.length) {
      event.preventDefault();
      dialogElement.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialogElement || !dialogElement.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || active === dialogElement || !dialogElement.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  }

  onMount(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusFrame = requestAnimationFrame(() => {
      if (dialogElement?.isConnected) dialogElement.focus();
    });
    return () => {
      cancelAnimationFrame(focusFrame);
      queueMicrotask(() => {
        if (previousFocus?.isConnected) previousFocus.focus();
      });
    };
  });
  $: sourceName = plan?.sourceName || fileName(sourcePath);
  $: sourceFolder = displayPath(parentPath(sourcePath));
  $: rawShownBase = plan?.baseDirectory || baseDirectory || defaultBaseDirectory || 'Project location unavailable';
  $: shownBase = displayPath(rawShownBase);
  $: destination = plan?.projectDirectory ? displayPath(plan.projectDirectory) : (shownBase === 'Project location unavailable' ? shownBase : `${shownBase}\\…`);
  $: canCreate = Boolean(plan?.projectDirectory) && !plan?.created && !busy && !error;
</script>

<div class="setup-backdrop" role="presentation" on:mousedown={(event) => event.currentTarget === event.target && !allocating && onCancel()}>
  <div bind:this={dialogElement} class="setup-modal" role="dialog" aria-modal="true" aria-labelledby="new-project-title" aria-describedby="new-project-description" tabindex="-1" on:keydown={trapFocus}>
    <header>
      <div>
        <span>NEW PROJECT</span>
        <h2 id="new-project-title">Set up your workspace</h2>
      </div>
      <button class="icon-button" on:click={onCancel} disabled={allocating} aria-label="Cancel new project"><X size={16}/></button>
    </header>

    <div class="setup-body">
      <div class="heading-copy">
        <h3>One source. One organized project.</h3>
        <p id="new-project-description">Review both locations, then create the workspace. Parallax Forge creates the project subfolder automatically.</p>
      </div>

      <div class="project-route">
        <article class="location-card source-card">
          <div class="card-heading"><span class="location-icon source"><FileVideo2 size={18}/></span><em>SOURCE VIDEO</em></div>
          <strong title={sourceName}>{sourceName}</strong>
          <code title={sourceFolder}>{sourceFolder}</code>
          <small><Check size={11}/> Your original video stays exactly where it is.</small>
        </article>

        <span class="route-arrow" aria-hidden="true"><ArrowRight size={17}/></span>

        <article class="location-card destination-card">
          <div class="card-heading"><span class="location-icon destination"><HardDrive size={18}/></span><em>PROJECT DESTINATION</em></div>
          <strong title={plan?.folderName ?? 'Automatic project subfolder'}>{plan?.folderName ?? 'Automatic project subfolder'}</strong>
          <code title={destination}>{destination}</code>
          <div class="destination-actions">
            <button on:click={onChangeBase} disabled={busy}><FolderCog size={12}/> Change</button>
            {#if !usesDefault}
              <button class="use-default" on:click={onUseDefault} disabled={busy}><RotateCcw size={11}/> Use default</button>
            {:else}
              <span>Default location</span>
            {/if}
          </div>
        </article>
      </div>

      {#if plan && plan.collisionIndex > 0}
        <div class="collision-note" role="status">
          <FolderCog size={14}/>
          <p><strong>Name adjusted automatically</strong><span>A project with the preferred name already exists, so this one will use “{plan.folderName}”. Nothing will be overwritten.</span></p>
        </div>
      {/if}

      {#if error}
        <div class="setup-error" role="alert">
          <AlertTriangle size={15}/>
          <p><strong>Destination needs attention</strong><span>{error}</span></p>
        </div>
      {/if}

      <div class="privacy-line"><HardDrive size={12}/><span>Media and project files remain on this computer.</span></div>
    </div>

    <footer>
      <button class="cancel" on:click={onCancel} disabled={allocating}>Cancel</button>
      <button class="create" on:click={onCreate} disabled={!canCreate}>
        {#if busy}<LoaderCircle class="spin" size={14}/>{:else}<ArrowRight size={14}/>{/if}
        {allocating ? 'Creating workspace…' : busy ? 'Checking destination…' : 'Create workspace'}
      </button>
    </footer>
  </div>
</div>

<style>
  .setup-backdrop { position: fixed; inset: 0; z-index: 88; display: grid; place-items: center; padding: 22px; background: rgba(2, 4, 8, .8); backdrop-filter: blur(11px); }
  .setup-modal { width: min(790px, 96vw); border: 1px solid #343c48; border-radius: 14px; overflow: hidden; background: linear-gradient(155deg, #11151c, #0c0f15 70%); box-shadow: 0 40px 120px rgba(0, 0, 0, .72), 0 0 50px rgba(77, 192, 180, .045); }
  header { min-height: 64px; display: flex; align-items: center; justify-content: space-between; padding: 0 18px 0 20px; border-bottom: 1px solid #232a33; background: rgba(17, 21, 28, .9); }
  header > div { display: grid; gap: 4px; }
  header span { color: #64cfc1; font-size: 10px; font-weight: 750; letter-spacing: .16em; }
  header h2 { margin: 0; color: #e0e4ea; font-size: 16px; font-weight: 620; }
  .icon-button { width: 29px; height: 29px; display: grid; place-items: center; border: 0; border-radius: 7px; background: transparent; color: #697482; }
  .icon-button:hover:not(:disabled) { color: #d3d8df; background: #1c222b; }
  .setup-body { padding: 24px; }
  .heading-copy { margin-bottom: 19px; }
  .heading-copy h3 { margin: 0 0 7px; color: #e7eaf0; font-size: 19px; font-weight: 610; letter-spacing: -.015em; }
  .heading-copy p { margin: 0; max-width: 620px; color: #747f8e; font-size: 11px; line-height: 1.6; }
  .project-route { display: grid; grid-template-columns: minmax(0, 1fr) 34px minmax(0, 1fr); align-items: stretch; }
  .location-card { min-width: 0; padding: 16px; border: 1px solid #29313b; border-radius: 10px; background: rgba(16, 20, 27, .9); }
  .destination-card { border-color: rgba(91, 197, 185, .3); background: linear-gradient(145deg, rgba(36, 70, 68, .17), rgba(16, 20, 27, .94)); }
  .card-heading { display: flex; align-items: center; gap: 8px; margin-bottom: 13px; }
  .location-icon { width: 31px; height: 31px; display: grid; place-items: center; border-radius: 8px; }
  .location-icon.source { color: #9c91ec; background: rgba(125, 107, 222, .12); }
  .location-icon.destination { color: #69d4c6; background: rgba(75, 192, 178, .12); }
  .card-heading em { color: #677281; font-size: 10px; font-style: normal; font-weight: 720; letter-spacing: .12em; }
  .location-card > strong { display: block; overflow: hidden; margin-bottom: 7px; color: #d6dbe3; font-size: 12px; font-weight: 620; text-overflow: ellipsis; white-space: nowrap; }
  .location-card code { display: block; min-height: 34px; overflow-wrap: anywhere; color: #778391; font-family: var(--font-mono); font-size: 10px; line-height: 1.55; }
  .source-card small { display: flex; align-items: center; gap: 5px; margin-top: 12px; color: #66857f; font-size: 10px; }
  .source-card small :global(svg) { color: #65c8b9; }
  .route-arrow { display: grid; place-items: center; color: #48535f; }
  .destination-actions { display: flex; align-items: center; gap: 7px; margin-top: 10px; min-height: 27px; }
  .destination-actions button { display: inline-flex; align-items: center; gap: 5px; height: 27px; padding: 0 9px; border: 1px solid #3a4652; border-radius: 6px; background: #171c23; color: #a6afb9; font-size: 10px; }
  .destination-actions button:hover:not(:disabled) { border-color: #547168; color: #d1d8dc; }
  .destination-actions .use-default { border-color: transparent; background: transparent; color: #6f7a87; }
  .destination-actions > span { color: #5dbbac; font-size: 10px; }
  .collision-note, .setup-error { display: grid; grid-template-columns: auto 1fr; align-items: start; gap: 9px; margin-top: 13px; padding: 11px 12px; border: 1px solid rgba(216, 147, 91, .25); border-radius: 8px; background: rgba(111, 66, 31, .1); color: #d89a66; }
  .collision-note p, .setup-error p { display: grid; gap: 3px; margin: 0; }
  .collision-note strong, .setup-error strong { color: #cfb193; font-size: 10px; }
  .collision-note span, .setup-error span { color: #86796f; font-size: 10px; line-height: 1.5; }
  .setup-error { border-color: rgba(222, 99, 105, .32); background: rgba(121, 43, 50, .11); color: #df777c; }
  .setup-error strong { color: #dc9ca0; }
  .setup-error span { color: #a27d80; }
  .privacy-line { display: flex; align-items: center; justify-content: center; gap: 6px; margin-top: 17px; color: #56616e; font-size: 10px; }
  footer { height: 60px; display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding: 0 18px; border-top: 1px solid #232a33; background: rgba(15, 19, 25, .92); }
  footer button { height: 34px; border-radius: 7px; padding: 0 15px; font-size: 11px; font-weight: 650; }
  footer .cancel { border: 1px solid #303842; background: #151a21; color: #89939f; }
  footer .create { min-width: 158px; display: inline-flex; align-items: center; justify-content: center; gap: 7px; border: 0; background: linear-gradient(135deg, #76dfd2, #58bfb5); color: #07110f; box-shadow: 0 0 20px rgba(82, 197, 184, .12); }
  button:disabled { cursor: not-allowed; opacity: .45; }
  :global(.spin) { animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (max-width: 680px) {
    .setup-body { padding: 19px; }
    .project-route { grid-template-columns: 1fr; gap: 8px; }
    .route-arrow { transform: rotate(90deg); height: 24px; }
  }
</style>
