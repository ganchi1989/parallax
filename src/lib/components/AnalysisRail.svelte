<script lang="ts">
  import { AlertTriangle, Check, LoaderCircle, LockKeyhole, PauseCircle } from 'lucide-svelte';
  import { ANALYSIS_STAGES, analysisComplete } from '../analysis-state';
  import type { WorkspaceSession } from '../types';

  export let workspace: WorkspaceSession;
  export let onRetry: () => void = () => {};
  export let onStartProduction: () => void = () => {};

  $: complete = analysisComplete(workspace);
  $: sampled = workspace.artifacts.analysisTier === 'sampled';
  $: active = ANALYSIS_STAGES.find((stage) => workspace.stages[stage.id].status === 'running');
  $: failed = ANALYSIS_STAGES.find((stage) => workspace.stages[stage.id].status === 'failed');
  $: cancelled = ANALYSIS_STAGES.find((stage) => workspace.stages[stage.id].status === 'cancelled');
  $: productionPending = sampled && workspace.stages.depth.status === 'locked';
  $: coverage = workspace.artifacts.draftCoverage;
  $: sampleDetail = coverage ? `${coverage.sampledFrames.toLocaleString()} representative frames across ${coverage.shotIds.length} shots` : 'Representative frames from every shot';
  $: readyDetail = workspace.artifacts.depthMode === 'production' ? 'Editing, previews, and final export are verified' : 'Editing and previews are ready; final export requires certified depth';
</script>

<section class:ready={complete} class:sampled class:failed={Boolean(failed)} class:cancelled={Boolean(cancelled)} class="analysis-rail" aria-label="Project analysis status">
  <div class="rail-summary" aria-live="polite">
    <span class="summary-icon">
      {#if complete || (sampled&&!failed&&!cancelled)}<Check size={13} />{:else if failed}<AlertTriangle size={13} />{:else if cancelled}<PauseCircle size={13} />{:else}<LoaderCircle size={13} />{/if}
    </span>
    <span>
      <strong>{complete ? 'Full-frame analysis complete' : failed ? `${failed.label} needs attention` : cancelled ? 'Full-frame analysis paused' : sampled ? 'Quick Director draft ready' : active ? `${active.label} in progress` : 'Preparing analysis'}</strong>
      <small>{complete ? readyDetail : failed ? `${workspace.stages[failed.id].error ?? 'This stage did not produce a verified artifact.'}${sampled?' Quick draft remains editable.':''}` : cancelled ? (sampled?'Quick draft remains editable; completed full-frame artifacts were kept.':'Completed artifacts remain available') : sampled ? (active?`${active.label} is refining the draft locally`:`${sampleDetail} · full-frame analysis pending`) : active?.description}</small>
    </span>
    {#if failed || cancelled}<button class="retry-button" on:click={onRetry}>Retry</button>{/if}
    {#if productionPending}<button class="production-button" on:click={onStartProduction}>Run full-frame analysis</button>{/if}
  </div>

  <ol>
    {#each ANALYSIS_STAGES as stage, index}
      {@const state = workspace.stages[stage.id]}
      <li class:complete={state.status==='complete'} class:running={state.status==='running'} class:failed={state.status==='failed'} class:cancelled={state.status==='cancelled'} aria-current={state.status==='running' ? 'step' : undefined}>
        <span class="step-icon">
          {#if state.status==='complete'}<Check size={11}/>{:else if state.status==='running'}<LoaderCircle size={11}/>{:else if state.status==='failed'}<AlertTriangle size={11}/>{:else if state.status==='cancelled'}<PauseCircle size={11}/>{:else}<LockKeyhole size={10}/>{/if}
        </span>
        <span class="step-copy"><strong>{stage.label}</strong><small>{state.status==='running' ? `${Math.round(state.progress*100)}%` : state.status}</small></span>
        {#if index < ANALYSIS_STAGES.length-1}<i class="connector"></i>{/if}
        {#if state.status==='running'}<progress max="1" value={state.progress} aria-label={`${stage.label} progress`}></progress>{/if}
      </li>
    {/each}
  </ol>
</section>

<style>
  .analysis-rail{height:64px;flex:0 0 64px;display:grid;grid-template-columns:minmax(285px,340px) 1fr;align-items:center;gap:15px;padding:0 14px;border-bottom:1px solid #28303a;background:linear-gradient(90deg,#10151b,#0d1117 48%,#0c1015);box-shadow:inset 0 1px rgba(255,255,255,.018);position:relative;z-index:20}.rail-summary{display:flex;align-items:center;gap:9px;min-width:0}.summary-icon{width:28px;height:28px;display:grid;place-items:center;flex:0 0 auto;border:1px solid rgba(94,208,194,.22);border-radius:8px;background:rgba(61,143,133,.1);color:#67d2c5}.summary-icon :global(svg){animation:spin 1.5s linear infinite}.ready .summary-icon,.sampled .summary-icon{color:#61cca9;background:rgba(55,151,119,.1)}.ready .summary-icon :global(svg),.sampled .summary-icon :global(svg),.failed .summary-icon :global(svg),.cancelled .summary-icon :global(svg){animation:none}.failed .summary-icon{color:#df936e;border-color:rgba(216,128,90,.25);background:rgba(130,67,44,.1)}.rail-summary>span:nth-child(2){display:grid;gap:4px;min-width:0;flex:1}.rail-summary strong{font-size:11px;color:#d5dbe3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.rail-summary small{font-size:10px;color:#687382;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.retry-button,.production-button{height:26px;min-height:26px;padding:0 8px;border-radius:6px;font-size:10px;white-space:nowrap}.retry-button{border:1px solid rgba(219,139,99,.36);background:rgba(125,65,42,.12);color:#dc966f}.retry-button:hover{background:rgba(144,73,46,.2)}.production-button{border:1px solid rgba(91,207,192,.35);background:rgba(61,143,133,.12);color:#73d7ca}.production-button:hover{background:rgba(61,143,133,.2)}
  ol{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(8,minmax(62px,1fr));align-items:center;min-width:0}li{height:45px;display:flex;align-items:center;gap:6px;position:relative;min-width:0;color:#46505d}.step-icon{width:21px;height:21px;display:grid;place-items:center;z-index:2;flex:0 0 auto;border:1px solid #2b333e;border-radius:50%;background:#11161d;color:#505b69}li.complete .step-icon{border-color:rgba(74,188,157,.35);background:#13231f;color:#63cbaa}li.running .step-icon{border-color:rgba(94,210,197,.55);background:#17302d;color:#70d9cc;box-shadow:0 0 13px rgba(79,205,190,.12)}li.running .step-icon :global(svg){animation:spin 1.5s linear infinite}li.failed .step-icon{border-color:rgba(217,126,91,.5);background:#2a1916;color:#df8e68}li.cancelled .step-icon{color:#c39b67}.step-copy{display:grid;gap:3px;min-width:0;z-index:2}.step-copy strong{font-size:10px;color:#6c7684;white-space:nowrap}.step-copy small{font-size:9px;color:#404956;text-transform:capitalize}li.complete .step-copy strong,li.running .step-copy strong{color:#aeb7c2}li.failed .step-copy strong{color:#d59373}.connector{position:absolute;left:20px;right:-2px;top:22px;height:1px;background:#29313b;z-index:1}li.complete .connector{background:linear-gradient(90deg,#397f72,#2d534e)}progress{position:absolute;left:27px;right:5px;bottom:2px;width:calc(100% - 32px);height:2px;border:0;background:#202831}progress::-webkit-progress-bar{background:#202831}progress::-webkit-progress-value{background:#62d1c4}
  @keyframes spin{to{transform:rotate(360deg)}}
  @media(max-width:1100px){.analysis-rail{grid-template-columns:265px 1fr;gap:10px}.step-copy small{display:none}ol{grid-template-columns:repeat(8,minmax(48px,1fr))}.rail-summary small{display:none}}
  @media(max-width:850px){.analysis-rail{grid-template-columns:1fr;height:54px;flex-basis:54px}.rail-summary{display:none}ol{width:100%}.analysis-rail.failed .rail-summary,.analysis-rail.cancelled .rail-summary{display:flex}.analysis-rail.failed ol,.analysis-rail.cancelled ol{display:none}}
</style>
