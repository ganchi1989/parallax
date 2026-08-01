<script lang="ts">
  import { AlertTriangle, Check, Film, Glasses, ListFilter, LoaderCircle, Search } from 'lucide-svelte';
  import type { Project, Shot } from '../types';
  import { formatTime } from '../utils';
  import { presetById } from '../constants';
  import SceneArt from './SceneArt.svelte';

  export let project: Project;
  export let demoMode = false;
  export let featuresReady = demoMode || Boolean(project.analysisReady);
  export let scriptReady = demoMode || Boolean(project.analysisReady);
  export let productionActive = false;
  /** Shots with a rendered stereo preview, and shots rendering right now. */
  export let previewedShotIds: number[] = [];
  export let renderingShotIds: number[] = [];
  export let selectedShotId: number | null;
  export let selectedShotIds: number[] = [];
  export let onSelect: (id: number, additive: boolean, range: boolean) => void;
  export let onRenderAll: () => void = () => {};
  export let renderAllEnabled = false;
  export let renderAllBusy = false;

  let search = '';
  let warningsOnly = false;
  $: previewed = new Set(previewedShotIds);
  $: rendering = new Set(renderingShotIds);
  $: pendingRenders = project.shots.filter((shot) => !previewed.has(shot.id)).length;
  $: shots = project.shots.filter((shot) =>
    (!warningsOnly || shot.status === 'warning') &&
    (!search || shot.name.toLowerCase().includes(search.toLowerCase()) || String(shot.id).includes(search))
  );

  function selected(shot: Shot): boolean {
    return shot.id === selectedShotId || selectedShotIds.includes(shot.id);
  }
</script>

<aside class="shot-panel" aria-label="Shot browser" style={`--thumb-aspect:${project.width > 0 && project.height > 0 ? `${project.width} / ${project.height}` : '16 / 9'}`}>
  <div class="panel-head">
    <div><span class="kicker">{project.analysisTier==='sampled'?'QUICK DIRECTOR DRAFT':'DIRECTOR SCRIPT'}</span><h2>Shots <em>{project.shots.length}</em></h2></div>
    <button class:active={warningsOnly} on:click={() => (warningsOnly = !warningsOnly)} title="Show warnings only" aria-label="Filter warnings"><ListFilter size={15} /></button>
  </div>
  <div class="search"><Search size={13} /><input bind:value={search} aria-label="Search shots" placeholder="Search shots" /></div>
  <div class="list-label"><span>SHOT</span><span>Timeline order</span></div>
  <div class="shot-list">
    {#each shots as shot}
      <button
        class:selected={selected(shot)}
        class:warning={shot.status === 'warning'}
        class="shot-row"
        on:click={(event) => onSelect(shot.id, event.ctrlKey || event.metaKey, event.shiftKey)}
      >
        <div class="thumb" class:rendered={previewed.has(shot.id)} class:busy={rendering.has(shot.id)}>{#if demoMode}<SceneArt variant={shot.id} mode={shot.id % 4 === 0 ? 'anaglyph' : 'original'} label={`Demo thumbnail for shot ${shot.id}`} />{:else}<div class="thumb-placeholder"><Film size={15}/><b>{String(shot.id).padStart(2,'0')}</b></div>{/if}<span>{formatTime(shot.endSeconds-shot.startSeconds)}</span></div>
        <div class="shot-copy">
          <div class:analyzing={shot.status==='analyzing'} class="shot-number"><span>{String(shot.id).padStart(2,'0')}</span>{#if shot.status === 'warning'}<AlertTriangle size={10}/>{:else if shot.status==='analyzing'}<LoaderCircle size={10}/>{:else}<Check size={10}/>{/if}</div>
          <strong>{shot.name}</strong>
          {#if shot.status==='analyzing'&&!featuresReady}<small class="waiting">Metrics locked · analyzing</small>{:else if shot.status==='analyzing'}<small class="waiting ready">Metrics ready · direction pending</small>{:else}<small class:draft={project.analysisTier==='sampled'}><i style={`--tag:${presetById(shot.preset).color}`}></i>{project.analysisTier==='sampled'?'Draft · ':''}{presetById(shot.preset).shortLabel} · {Math.round(shot.confidence*100)}%</small>{/if}
          {#if rendering.has(shot.id)}<small class="render-state rendering"><LoaderCircle size={9}/> Rendering 3D…</small>{:else if previewed.has(shot.id)}<small class="render-state ready"><Glasses size={9}/> 3D rendered</small>{:else}<small class="render-state pending"><i></i> Not rendered</small>{/if}
        </div>
      </button>
    {:else}
      <div class="empty"><Film size={24}/><span>No matching shots</span></div>
    {/each}
  </div>
  {#if renderAllEnabled}
    <div class="batch-bar">
      <button disabled={renderAllBusy || pendingRenders === 0} on:click={onRenderAll} title="Render every shot that has no 3D clip yet">
        {#if renderAllBusy}<LoaderCircle size={12}/> Rendering all…{:else if pendingRenders === 0}<Glasses size={12}/> All shots rendered{:else}<Glasses size={12}/> Render all ({pendingRenders}){/if}
      </button>
    </div>
  {/if}
  <div class:running={!scriptReady||productionActive} class="panel-foot"><span><i></i> {project.analysisTier==='sampled' ? (productionActive?'Quick draft ready · full-frame analysis running':'Quick draft ready') : scriptReady ? 'Script synced' : featuresReady ? 'Features ready' : 'Analysis running'}</span><span>{project.analysisTier==='sampled' ? (project.dirty?'Draft modified':'Full-frame pending') : scriptReady ? (project.dirty ? 'Modified' : 'Saved') : 'Controls locked'}</span></div>
</aside>

<style>
  .shot-panel{display:flex;flex-direction:column;min-width:0;min-height:0;border-right:1px solid var(--line-soft);background:#0c0f15}
  .panel-head{height:69px;display:flex;align-items:center;justify-content:space-between;padding:0 14px;border-bottom:1px solid var(--line-soft)}
  .kicker{font-size:10px;color:#5d6675;letter-spacing:.17em;font-weight:720}.panel-head h2{font-size:13px;margin:5px 0 0;font-weight:600;color:#d9dee6}.panel-head h2 em{font-style:normal;color:#697383;font-size:11px;margin-left:4px;font-weight:500}
  .panel-head button{width:29px;height:29px;display:grid;place-items:center;border:1px solid #242a34;background:#12161d;color:#6e7888;border-radius:6px}.panel-head button:hover,.panel-head button.active{color:#76d8cb;border-color:#3b615e}
  .search{margin:11px 12px 7px;height:30px;display:flex;align-items:center;gap:7px;padding:0 9px;border:1px solid #222832;background:#10131a;border-radius:6px;color:#515b6a}.search:focus-within{border-color:#3d746e}.search input{width:100%;border:0;outline:0;color:#c6ccd5;background:transparent;font-size:11px}.search input::placeholder{color:#505967}
  .list-label{display:flex;justify-content:space-between;align-items:center;padding:4px 13px 7px;color:#4f5867;font-size:10px;letter-spacing:.1em}.list-label span:last-child{letter-spacing:0;color:#596372}
  .shot-list{flex:1;min-height:0;overflow:auto;padding:0 7px 10px}.shot-row{width:100%;min-width:0;display:grid;grid-template-columns:76px 1fr auto;align-items:center;gap:9px;padding:7px;border:1px solid transparent;background:transparent;border-radius:8px;text-align:left;color:inherit;position:relative;margin-bottom:3px}.shot-row:hover{background:#12161e}.shot-row.selected{background:linear-gradient(100deg,rgba(50,91,90,.28),rgba(24,28,37,.66));border-color:rgba(91,188,177,.32);box-shadow:inset 2px 0 #66cfc2}.shot-row.warning:not(.selected){border-color:rgba(211,137,78,.08)}
  .batch-bar{padding:7px 10px;border-top:1px solid var(--line-soft)}.batch-bar button{width:100%;height:29px;display:flex;align-items:center;justify-content:center;gap:6px;border:1px solid rgba(91,207,192,.32);border-radius:6px;background:rgba(61,143,133,.12);color:#73d7ca;font-size:10px;font-weight:600}.batch-bar button:hover:not(:disabled){background:rgba(61,143,133,.22)}.batch-bar button:disabled{color:#5b6470;border-color:#283039;background:#141920}.batch-bar :global(.lucide-loader-circle){animation:shot-spin 1.1s linear infinite}.render-state{display:flex;align-items:center;gap:4px;font-size:9px;letter-spacing:.02em}.render-state.ready{color:#63cbaa}.render-state.rendering{color:#6fd0c4}.render-state.rendering :global(svg){animation:shot-spin 1.1s linear infinite}.render-state.pending{color:#5c6675}.render-state.pending i{width:5px;height:5px;border-radius:50%;border:1px solid #454f5d}@keyframes shot-spin{to{transform:rotate(360deg)}}.thumb.rendered{box-shadow:inset 0 0 0 1px rgba(99,203,170,.55)}.thumb.busy{box-shadow:inset 0 0 0 1px rgba(111,208,196,.4)}.thumb{width:76px;aspect-ratio:var(--thumb-aspect,16/9);max-height:64px;border-radius:5px;overflow:hidden;position:relative;background:#20242b}.thumb-placeholder{width:100%;height:100%;display:flex;align-items:center;justify-content:center;gap:5px;background:linear-gradient(145deg,#1a1f27,#101319);color:#485361}.thumb-placeholder b{font-size:11px;color:#687382}.thumb span{position:absolute;right:3px;bottom:3px;background:rgba(3,5,8,.72);border-radius:3px;padding:2px 3px;color:#b9c1cc;font-size:10px;font-variant-numeric:tabular-nums}
  .shot-copy{min-width:0;display:grid;gap:4px}.shot-number{display:flex;align-items:center;gap:5px;font-size:10px;color:#667181}.shot-number :global(svg){color:#58bcab}.shot-number.analyzing :global(svg){color:#6a9e9a;animation:spin 1.5s linear infinite}.warning .shot-number :global(svg){color:#d59662}.shot-copy strong{font-size:11px;color:#c5cbd4;font-weight:570;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.shot-copy small{display:flex;align-items:center;font-size:10px;color:#596372;white-space:nowrap}.shot-copy small i{width:5px;height:5px;border-radius:50%;background:var(--tag);margin-right:5px}.shot-copy small.waiting{color:#65716f}.shot-copy small.waiting.ready,.shot-copy small.draft{color:#62a99f}
  .empty{display:grid;place-items:center;gap:8px;color:#535d6b;font-size:11px;padding:40px 0}.panel-foot{height:31px;display:flex;align-items:center;justify-content:space-between;padding:0 12px;border-top:1px solid var(--line-soft);font-size:10px;color:#4e5867}.panel-foot span:first-child{display:flex;align-items:center;gap:5px}.panel-foot i{width:5px;height:5px;border-radius:50%;background:#58c8ae}.panel-foot.running i{background:#c08d5d;box-shadow:0 0 7px rgba(192,141,93,.35)}
  @keyframes spin{to{transform:rotate(360deg)}}
</style>
