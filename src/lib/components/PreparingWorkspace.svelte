<script lang="ts">
  import { AlertTriangle, Check, Film, Gauge, Layers3, LoaderCircle, LockKeyhole, PauseCircle, ScanLine, Sparkles, Video } from 'lucide-svelte';
  import { ANALYSIS_STAGES, activeAnalysisStage } from '../analysis-state';
  import type { WorkspaceCapabilities, WorkspaceSession } from '../types';

  export let workspace: WorkspaceSession;
  export let capabilities: WorkspaceCapabilities;
  export let sourceUrl: string | null = null;

  $: activeId = activeAnalysisStage(workspace);
  $: activeDefinition = ANALYSIS_STAGES.find((stage) => stage.id === activeId);
  $: activeState = activeId ? workspace.stages[activeId] : null;
  $: failedDefinition = ANALYSIS_STAGES.find((stage)=>workspace.stages[stage.id].status==='failed');
  $: cancelledDefinition = ANALYSIS_STAGES.find((stage)=>workspace.stages[stage.id].status==='cancelled');
  $: terminalDefinition = failedDefinition ?? cancelledDefinition;
  $: terminalState = terminalDefinition ? workspace.stages[terminalDefinition.id] : null;
  $: capabilityRows = [
    { label: 'Source monitor', detail: sourceUrl ? 'Original local footage' : 'Connecting the selected local source', enabled: Boolean(sourceUrl), icon: Video },
    { label: 'Shot navigation', detail: capabilities.shotNavigation.reason ?? 'Verified shot boundaries', enabled: capabilities.shotNavigation.enabled, icon: Film },
    { label: 'Shot metrics', detail: capabilities.shotMetrics.reason ?? 'Dependable feature coverage', enabled: capabilities.shotMetrics.enabled, icon: Gauge },
    { label: 'Quick Director', detail: capabilities.directorEdit.reason ?? 'Sampled comfort-safe draft', enabled: capabilities.directorEdit.enabled, icon: Layers3 },
    { label: 'Final export', detail: capabilities.finalExport.reason ?? 'Certified production depth', enabled: capabilities.finalExport.enabled, icon: Sparkles }
  ];
</script>

<div class="preparing-workspace">
  <aside class="pending-shots" aria-label="Shot browser pending">
    <header><span><Film size={13}/> SHOT MAP</span><em><LockKeyhole size={11}/> BUILDING</em></header>
    <div class="source-card"><span><Check size={12}/></span><div><strong>{workspace.identity.name}</strong><small>Source securely attached</small></div></div>
    <div class="skeleton-list" aria-hidden="true">
      {#each Array(7) as _, index}<div style={`--delay:${index*80}ms`}><i></i><span></span><b></b></div>{/each}
    </div>
    <footer class:stopped={Boolean(failedDefinition||cancelledDefinition)}>{#if failedDefinition}<AlertTriangle size={12}/>{:else if cancelledDefinition}<PauseCircle size={12}/>{:else}<LoaderCircle size={12}/>{/if}<span>{failedDefinition ? 'Analysis stopped before a verified shot manifest was available' : cancelledDefinition ? 'Analysis is paused; completed artifacts are preserved' : 'Shots appear here as one verified manifest'}</span></footer>
  </aside>

  <main class="pending-monitor">
    <header><span class="active-dot"></span><strong>LIVE WORKSTATION</strong><em>Processing stays in the background</em></header>
    <div class="monitor-stage">
      <div class="ambient"></div>
      <div class="monitor-frame">
        {#if sourceUrl}
          <video src={sourceUrl} controls muted playsinline preload="metadata" aria-label="Selected source video"></video>
        {:else}
          <div class="source-loading"><ScanLine size={27}/><strong>Connecting the local source monitor</strong><span>No generated image is substituted for your footage.</span></div>
        {/if}
        <span class="source-label">ORIGINAL SOURCE</span>
      </div>
    </div>
    <div class:failed={Boolean(failedDefinition)} class:cancelled={Boolean(cancelledDefinition)} class="stage-status" aria-live="polite">
      <span class="stage-spinner">{#if failedDefinition}<AlertTriangle size={15}/>{:else if cancelledDefinition}<PauseCircle size={15}/>{:else}<LoaderCircle size={15}/>{/if}</span>
      <div><strong>{activeDefinition?.label ?? terminalDefinition?.label ?? 'Preparing workstation'}</strong><span>{activeState?.message ?? terminalState?.error ?? terminalState?.message ?? activeDefinition?.description ?? 'Validating local artifacts'}</span></div>
      {#if activeState}<em>{Math.round(activeState.progress*100)}%</em><progress max="1" value={activeState.progress} aria-label="Current analysis stage progress"></progress>{/if}
    </div>
  </main>

  <aside class="unlock-panel" aria-label="Workstation capability readiness">
    <header><span>PROGRESSIVE UNLOCK</span><strong>Only verified tools become active</strong></header>
    <div class="capabilities">
      {#each capabilityRows as item}
        <div class:enabled={item.enabled}>
          <span class="cap-icon"><svelte:component this={item.icon} size={14}/></span>
          <p><strong>{item.label}</strong><small>{item.enabled ? 'Ready now' : item.detail}</small></p>
          <span class="gate">{#if item.enabled}<Check size={11}/>{:else}<LockKeyhole size={10}/>{/if}</span>
        </div>
      {/each}
    </div>
    <footer><LockKeyhole size={11}/><span>Percentages are display-only. Completion artifacts control every unlock.</span></footer>
  </aside>

  <section class="pending-timeline" aria-label="Timeline pending shot detection">
    <header><strong>SHOT TIMELINE</strong><span>Waiting for verified cut boundaries</span></header>
    <div class="ruler">{#each Array(9) as _,index}<i style={`left:${index*12.5}%`}></i>{/each}</div>
    <div class="track"><span>V1</span><div>{#each Array(8) as _,index}<i style={`--w:${8+(index%3)*3}%;--delay:${index*65}ms`}></i>{/each}</div></div>
    <div class="audio"><span>A1</span><div>Source audio will be preserved in final output</div></div>
  </section>
</div>

<style>
  .preparing-workspace{flex:1;min-height:0;display:grid;grid-template-columns:minmax(205px,238px) minmax(420px,1fr) minmax(275px,310px);grid-template-rows:minmax(0,1fr) var(--timeline-height);grid-template-areas:'shots monitor unlock' 'timeline timeline timeline';background:#090c11}.pending-shots{grid-area:shots;border-right:1px solid var(--line-soft);background:#0d1016;display:flex;flex-direction:column;min-height:0}.pending-shots header{height:40px;display:flex;align-items:center;justify-content:space-between;padding:0 10px;border-bottom:1px solid var(--line-soft)}.pending-shots header span,.pending-shots header em{display:flex;align-items:center;gap:6px;font-size:10px;font-style:normal;letter-spacing:.1em;color:#697483}.pending-shots header em{color:#9f8062;letter-spacing:.06em}.source-card{margin:10px;display:flex;align-items:center;gap:8px;padding:9px;border:1px solid rgba(77,190,164,.2);border-radius:7px;background:rgba(49,126,108,.08)}.source-card>span{width:23px;height:23px;display:grid;place-items:center;border-radius:6px;background:rgba(72,187,155,.12);color:#62cbaa}.source-card>div{display:grid;gap:3px;min-width:0}.source-card strong{font-size:10px;color:#bfc6cf;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.source-card small{font-size:9px;color:#607069}.skeleton-list{display:grid;gap:5px;padding:0 10px;overflow:hidden}.skeleton-list>div{height:45px;display:grid;grid-template-columns:45px 1fr auto;align-items:center;gap:8px;padding:5px;border:1px solid #1e242c;border-radius:6px;background:#10141a;animation:pulse 1.8s ease-in-out var(--delay) infinite}.skeleton-list i{height:33px;border-radius:4px;background:#1b222a}.skeleton-list span{height:6px;width:70%;border-radius:3px;background:#212933}.skeleton-list b{width:15px;height:15px;border-radius:50%;background:#1d242c}.pending-shots footer{margin-top:auto;min-height:42px;display:flex;align-items:center;gap:7px;padding:0 11px;border-top:1px solid var(--line-soft);color:#55606e}.pending-shots footer :global(svg){animation:spin 1.5s linear infinite}.pending-shots footer span{font-size:9px;line-height:1.35}
  .pending-monitor{grid-area:monitor;min-width:0;min-height:0;display:flex;flex-direction:column;background:#090b10}.pending-monitor>header{height:45px;display:flex;align-items:center;gap:7px;padding:0 13px;border-bottom:1px solid var(--line-soft);background:#0d1016}.pending-monitor>header strong{font-size:10px;letter-spacing:.11em;color:#87919e}.pending-monitor>header em{margin-left:auto;font-style:normal;font-size:10px;color:#535d6b}.active-dot{width:6px;height:6px;border-radius:50%;background:#62d0c2;box-shadow:0 0 9px #62d0c2}.monitor-stage{flex:1;min-height:200px;display:grid;place-items:center;position:relative;padding:clamp(16px,2.4vw,32px);overflow:hidden;background:radial-gradient(circle at 50% 42%,#151b23,#090b10 62%)}.monitor-stage::before{content:'';position:absolute;inset:0;opacity:.14;background-image:linear-gradient(45deg,#151a21 25%,transparent 25%),linear-gradient(-45deg,#151a21 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#151a21 75%),linear-gradient(-45deg,transparent 75%,#151a21 75%);background-size:18px 18px;background-position:0 0,0 9px,9px -9px,-9px 0}.ambient{position:absolute;width:60%;height:45%;border-radius:50%;background:#3f8f86;filter:blur(100px);opacity:.08}.monitor-frame{position:relative;width:min(100%,900px);aspect-ratio:16/9;overflow:hidden;background:#11161d;box-shadow:0 24px 70px rgba(0,0,0,.58),0 0 0 1px rgba(255,255,255,.11),0 0 0 6px rgba(2,3,5,.45)}.monitor-frame video{width:100%;height:100%;display:block;object-fit:contain;background:#050607}.source-loading{width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;color:#5f6b79;background:radial-gradient(circle,#182029,#0a0d12)}.source-loading strong{font-size:11px;color:#9ca6b3}.source-loading span{font-size:10px}.source-label{position:absolute;left:10px;top:10px;padding:4px 6px;border:1px solid rgba(255,255,255,.14);border-radius:4px;background:rgba(5,7,10,.66);color:#c5cbd3;font-size:9px;letter-spacing:.11em}.stage-status{height:58px;display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:9px;padding:8px 13px 10px;border-top:1px solid var(--line-soft);background:#0e1117;position:relative}.stage-spinner{width:29px;height:29px;display:grid;place-items:center;border-radius:8px;background:rgba(65,161,150,.1);color:#68d4c7}.stage-spinner :global(svg){animation:spin 1.4s linear infinite}.stage-status>div{display:grid;gap:3px}.stage-status strong{font-size:11px;color:#c9d0d8}.stage-status span{font-size:10px;color:#5c6775}.stage-status>em{font-style:normal;font:11px var(--font-mono);color:#84909d}.stage-status progress{position:absolute;left:0;right:0;bottom:0;width:100%;height:2px;border:0}.stage-status progress::-webkit-progress-bar{background:#222a33}.stage-status progress::-webkit-progress-value{background:#61d1c4}
  .unlock-panel{grid-area:unlock;border-left:1px solid var(--line-soft);background:#0d1016;display:flex;flex-direction:column;min-height:0}.unlock-panel>header{padding:14px 13px 12px;border-bottom:1px solid var(--line-soft);display:grid;gap:5px}.unlock-panel>header span{font-size:9px;letter-spacing:.13em;color:#65cfc2}.unlock-panel>header strong{font-size:11px;color:#c5cbd4}.capabilities{padding:9px;display:grid;gap:6px}.capabilities>div{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:8px;padding:9px;border:1px solid #222933;border-radius:7px;background:#10141b;color:#566170}.capabilities>div.enabled{border-color:rgba(68,178,148,.22);background:rgba(46,117,99,.07)}.cap-icon{width:28px;height:28px;display:grid;place-items:center;border-radius:7px;background:#171d25;color:#596575}.enabled .cap-icon{background:rgba(60,165,137,.11);color:#61c9aa}.capabilities p{margin:0;display:grid;gap:3px}.capabilities strong{font-size:10px;color:#8e98a5}.enabled strong{color:#c1c9d2}.capabilities small{font-size:9px;color:#4f5966;line-height:1.3}.gate{width:20px;height:20px;display:grid;place-items:center;border-radius:50%;background:#171d24;color:#46515e}.enabled .gate{background:rgba(57,163,134,.12);color:#60c8a7}.unlock-panel footer{margin-top:auto;display:flex;gap:7px;padding:10px 11px;border-top:1px solid var(--line-soft);color:#505a67}.unlock-panel footer span{font-size:9px;line-height:1.45}
  .pending-timeline{grid-area:timeline;min-height:125px;border-top:1px solid var(--line);background:#0b0e13}.pending-timeline header{height:32px;display:flex;align-items:center;gap:8px;padding:0 10px;border-bottom:1px solid var(--line-soft)}.pending-timeline header strong{font-size:9px;letter-spacing:.13em;color:#697381}.pending-timeline header span{font-size:9px;color:#414a56}.ruler{height:20px;margin-left:27px;position:relative;border-bottom:1px solid #1a1f27}.ruler i{position:absolute;top:11px;height:5px;border-left:1px solid #303641}.track,.audio{height:37px;display:grid;grid-template-columns:27px 1fr;border-bottom:1px solid #171c23}.track>span,.audio>span{display:grid;place-items:center;color:#495361;background:#0e1117;border-right:1px solid #1b2028;font-size:9px}.track>div{display:flex;align-items:center;gap:2px;padding:3px}.track i{display:block;width:var(--w);height:28px;border:1px solid #232b34;border-radius:3px;background:linear-gradient(110deg,#151b22,#202934,#151b22);background-size:200% 100%;animation:shimmer 2s linear var(--delay) infinite}.audio{height:28px}.audio>div{display:flex;align-items:center;padding:0 8px;color:#455b58;font-size:9px;background:rgba(55,119,108,.03)}
  .pending-shots footer.stopped :global(svg),.stage-status.failed .stage-spinner :global(svg),.stage-status.cancelled .stage-spinner :global(svg){animation:none}.stage-status.failed .stage-spinner{color:#dc8e68;background:rgba(145,72,45,.1)}.stage-status.cancelled .stage-spinner{color:#c49c68;background:rgba(132,96,50,.1)}
  @keyframes spin{to{transform:rotate(360deg)}}@keyframes pulse{50%{opacity:.55}}@keyframes shimmer{to{background-position:-200% 0}}
  @media(max-width:900px){.preparing-workspace{grid-template-columns:minmax(0,1fr) 270px;grid-template-areas:'monitor unlock' 'timeline timeline'}.pending-shots{display:none}}
</style>
