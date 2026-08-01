<script lang="ts">
  import {
    AlertTriangle,
    Bot,
    CheckCircle2,
    ChevronDown,
    Clapperboard,
    Info,
    Layers3,
    LockKeyhole,
    Play,
    RefreshCw,
    ShieldCheck,
    Sparkles,
    WandSparkles,
    X
  } from 'lucide-svelte';
  import { PRESETS, presetById } from '../constants';
  import type { AiRecommendation, AnalysisCoverage, AnalysisStageState, AnalysisTier, PresetId, Shot, StereoParameters } from '../types';
  import { percent } from '../utils';
  import SliderRow from './SliderRow.svelte';

  export let shot: Shot;
  export let editingEnabled = true;
  export let featuresReady = false;
  export let previewEnabled = false;
  export let previewRendering = false;
  export let analysisTier: AnalysisTier = 'none';
  export let draftCoverage: AnalysisCoverage | undefined = undefined;
  export let draftStage: AnalysisStageState | undefined = undefined;
  export let productionActive = false;
  export let productionStage: AnalysisStageState | undefined = undefined;
  export let productionStageLabel: string | undefined = undefined;
  export let productionFinalizing = false;
  export let canStartProduction = false;
  export let selectionCount = 1;
  export let llmEnabled = false;
  export let aiBusy = false;
  export let recommendation: AiRecommendation | null = null;
  export let aiNotice: string | null = null;
  export let onApplyPreset: (preset: PresetId) => void;
  export let onParameter: <K extends keyof StereoParameters>(key: K, value: StereoParameters[K]) => void;
  export let onPreview: (kind?: 'still' | 'video') => void;
  export let onAskAi: () => void;
  export let onOpenSettings: () => void;
  export let onStartProduction: () => void = () => {};

  let tab: 'director' | 'qc' | 'info' = 'director';
  let advanced = true;
  let aiOpen = false;
  $: preset = presetById(shot.preset);
  $: if (!editingEnabled && tab === 'qc') tab = 'director';
  $: if (!featuresReady && tab === 'info') tab = 'director';
  $: if (!editingEnabled && aiOpen) aiOpen = false;
  $: warnings = editingEnabled ? [
    ...(shot.warning ? [shot.warning] : []),
    ...(shot.parameters.backgroundDisparity > 0.010 ? ['Background disparity exceeds the local preflight threshold'] : []),
    ...(shot.parameters.popoutDisparity > 0.004 ? ['Pop-out disparity exceeds the local preflight threshold'] : []),
    ...(shot.features.motion > 0.7 && shot.parameters.depthStrength > 0.65 ? ['High motion may amplify stereo discomfort'] : [])
  ] : [];
  $: draftProgress = Math.max(0, Math.min(1, draftStage?.progress ?? 0));
  $: draftPercent = Math.round(draftProgress * 100);
  $: draftWorkProgress = draftStage?.total && draftStage.total > 0
    ? `Progress ${Math.min(draftStage.completed ?? 0, draftStage.total).toLocaleString()} of ${draftStage.total.toLocaleString()}`
    : 'Preparing local analysis';
  $: productionProgress = Math.max(0, Math.min(1, productionStage?.progress ?? 0));
  $: productionPercent = Math.round(productionProgress * 100);
  $: productionWorkProgress = productionStage?.total && productionStage.total > 0
    ? `Progress ${Math.min(productionStage.completed ?? 0, productionStage.total).toLocaleString()} of ${productionStage.total.toLocaleString()}`
    : `Preparing ${productionStageLabel?.toLowerCase() || 'full-frame analysis'}`;
</script>

<aside class:not-ready={!editingEnabled} class="director-panel" aria-label="Stereo Director controls">
  <div class="panel-tabs" role="tablist">
    <button class:active={tab==='director'} on:click={() => tab='director'}>Director</button>
    <button disabled={!editingEnabled} class:active={tab==='qc'} on:click={() => tab='qc'} title={editingEnabled ? 'Comfort preflight' : 'Unlocks after the Director script'}>Comfort <span class:warn={warnings.length}>{editingEnabled ? warnings.length : '·'}</span></button>
    <button disabled={!featuresReady} class:active={tab==='info'} on:click={() => tab='info'} title={featuresReady ? 'Inspect shot analysis' : 'Unlocks after feature extraction'}>Inspect</button>
  </div>

  {#if tab === 'director'}
    <div class="panel-scroll">
      {#if analysisTier==='sampled'}
        <div class:finalizing={productionFinalizing} class:production-active={productionActive} class="draft-ready" role={productionActive?'status':undefined} aria-live={productionActive?'polite':undefined}>
          <span class="draft-icon">{#if productionActive}<RefreshCw size={13}/>{:else}<CheckCircle2 size={13}/>{/if}</span>
          <div>
            <span class="draft-heading"><strong>{productionFinalizing?'Applying full-frame direction':productionActive?`${productionStageLabel || 'Full-frame analysis'} in progress`:'Sampled Director draft ready'}</strong>{#if productionActive}<em>{productionPercent}%</em>{/if}</span>
            <small>{draftCoverage?`${draftCoverage.sampledFrames.toLocaleString()} of ${draftCoverage.totalFrames.toLocaleString()} frames sampled across ${draftCoverage.shotIds.length} shots.`:'Representative frames were sampled inside every shot.'} {productionFinalizing?'Controls are briefly paused while your edits are carried forward.':productionActive?'You can keep editing while full-frame analysis runs locally.':'Full-frame analysis is optional until you need previews or an export-readiness check.'}</small>
            {#if productionActive}
              <span class="production-stage-copy">{productionStage?.message || `${productionStageLabel || 'Full-frame analysis'} is starting locally`}</span>
              <span class="production-progress-copy">{productionWorkProgress}</span>
              <progress max="1" value={productionProgress} aria-label={`${productionStageLabel || 'Full-frame analysis'} progress`}></progress>
            {/if}
          </div>
          {#if canStartProduction}<button on:click={onStartProduction}>Run full-frame analysis</button>{/if}
        </div>
      {:else if !editingEnabled}
        <div class="analysis-lock" role="status" aria-live="polite">
          <span class="mini-spinner"></span>
          <div>
            <span class="analysis-heading"><strong>Building quick Director draft</strong><em>{draftPercent}%</em></span>
            <small>{draftStage?.message || 'Representative frames from every shot are being analyzed locally.'}</small>
            <span class="analysis-progress-copy">{draftWorkProgress}</span>
            <progress max="1" value={draftProgress} aria-label="Quick Director draft progress"></progress>
          </div>
        </div>
      {/if}
      <div class="shot-title">
        <div><span>SHOT {String(shot.id).padStart(2,'0')}{#if selectionCount > 1} · {selectionCount} SELECTED{/if}</span><h2>{selectionCount > 1 ? 'Batch direction' : shot.name}</h2></div>
        <span class="confidence"><i></i>{editingEnabled ? `${Math.round(shot.confidence*100)}%${analysisTier==='sampled'?' draft':''} confidence` : 'Direction pending'}</span>
      </div>

      <section class="inspector-section presets">
        <div class="section-title"><span>DIRECTOR PRESET</span></div>
        <div class="preset-grid">
          {#each PRESETS as item}
            <button disabled={!editingEnabled} class:active={editingEnabled&&shot.preset===item.id} on:click={() => onApplyPreset(item.id)} title={editingEnabled ? item.description : 'Unlocks after the Director script is ready'}>
              <span class="preset-symbol" style={`--preset:${item.color}`}><i></i><i></i><i></i></span>
              <strong>{item.shortLabel}</strong>
              {#if editingEnabled&&shot.preset===item.id}<CheckCircle2 size={11}/>{/if}
            </button>
          {/each}
        </div>
        <p class="preset-description"><i style={`--preset:${preset.color}`}></i>{editingEnabled ? preset.description : 'Direction pending — a verified preset has not been applied yet.'}</p>
      </section>

      <section class="inspector-section ai-section" class:open={aiOpen || recommendation}>
        <button class="ai-heading" disabled={!editingEnabled} on:click={() => (aiOpen = !aiOpen)}>
          <span class="ai-icon"><Sparkles size={13}/></span>
          <span><strong>Assistant</strong><small>Bounded creative recommendation</small></span>
          <ChevronDown class={aiOpen || recommendation ? 'rotated' : ''} size={13}/>
        </button>
        {#if aiOpen || recommendation}
          <div class="ai-body">
            {#if !llmEnabled}
              <div class="ai-locked"><LockKeyhole size={13}/><p><strong>Assistant is off</strong><span>Connect OpenAI in Settings. It only recommends a tested preset; Comfort Guard remains final.</span></p><button on:click={onOpenSettings}>Set up</button></div>
            {:else if recommendation?.shotId === shot.id}
              <div class="recommendation">
                <div><span class="rec-label">RECOMMENDS</span><strong><i style={`--preset:${presetById(recommendation.preset).color}`}></i>{presetById(recommendation.preset).label}</strong></div>
                <span class="rec-confidence">{percent(recommendation.confidence)} sure</span>
              </div>
              <p class="reason">“{recommendation.reason}”</p>
              <div class="ai-actions"><button on:click={onAskAi}>Regenerate</button><button class="apply" on:click={() => onApplyPreset(recommendation!.preset)}>Apply preset</button></div>
            {:else}
              {#if aiNotice}<div class="ai-notice"><AlertTriangle size={12}/><span>{aiNotice}</span></div>{/if}
              <p class="ai-copy">Ask the assistant to interpret the shot summary and choose one of five validated presets. Numerical parameters remain bounded locally.</p>
              <button class="ask-button" on:click={onAskAi} disabled={aiBusy}>{#if aiBusy}<span class="mini-spinner"></span> Reading shot…{:else}<WandSparkles size={13}/> Ask AI about this shot{/if}</button>
            {/if}
          </div>
        {/if}
      </section>

      <section class="inspector-section controls">
        <div class="section-title"><span>STEREO GEOMETRY</span><button disabled={!editingEnabled} class="reset-all" on:click={() => onApplyPreset(shot.preset)}>Reset</button></div>
        <SliderRow disabled={!editingEnabled} label="Depth strength" value={shot.parameters.depthStrength} min={0} max={1} step={.01} unit="%" hint="Overall separation intensity within comfort limits" onChange={(v)=>onParameter('depthStrength',v)} onReset={()=>onParameter('depthStrength',preset.parameters.depthStrength)}/>
        <SliderRow disabled={!editingEnabled} label="Screen plane" value={shot.parameters.convergence} min={.1} max={.9} step={.01} unit="%" hint="Depth percentile placed at zero parallax" onChange={(v)=>onParameter('convergence',v)} onReset={()=>onParameter('convergence',preset.parameters.convergence)}/>
        <SliderRow disabled={!editingEnabled} label="Background limit" value={shot.parameters.backgroundDisparity} min={0} max={.02} step={.001} unit="×w" onChange={(v)=>onParameter('backgroundDisparity',v)} onReset={()=>onParameter('backgroundDisparity',preset.parameters.backgroundDisparity)}/>
        <SliderRow disabled={!editingEnabled} label="Pop-out limit" value={shot.parameters.popoutDisparity} min={0} max={.008} step={.001} unit="×w" onChange={(v)=>onParameter('popoutDisparity',v)} onReset={()=>onParameter('popoutDisparity',preset.parameters.popoutDisparity)}/>
      </section>

      <section class="inspector-section advanced">
        <button disabled={!editingEnabled} class="section-title toggle-heading" on:click={() => (advanced=!advanced)}><span>STABILITY & PROTECTION</span><ChevronDown class={advanced ? 'rotated' : ''} size={12}/></button>
        {#if advanced}
          <div class="advanced-body">
            <SliderRow disabled={!editingEnabled} label="Temporal smoothing" value={shot.parameters.temporalSmoothing} min={0} max={1} step={.01} unit="%" onChange={(v)=>onParameter('temporalSmoothing',v)}/>
            <SliderRow disabled={!editingEnabled} label="Transition frames" value={shot.parameters.transitionFrames} min={0} max={24} step={1} unit="fr" onChange={(v)=>onParameter('transitionFrames',v)}/>
            <label class="switch-row"><span><strong>Edge protection</strong><small>Reduce window violations</small></span><input disabled={!editingEnabled} type="checkbox" checked={shot.parameters.edgeProtection} on:change={(e)=>onParameter('edgeProtection',e.currentTarget.checked)}/><i></i></label>
          </div>
        {/if}
      </section>

      {#if !editingEnabled}
        <section class="guard-ok pending"><LockKeyhole size={15}/><div><strong>{productionFinalizing?'Applying full-frame analysis':'Stereo preflight locked'}</strong><span>{productionFinalizing?'Your sampled edits are secured before the full-frame script replaces the draft.':featuresReady ? 'Real metrics are ready; the versioned Director script is still being built.' : 'Real motion and depth features are still being extracted.'}</span></div></section>
      {:else if warnings.length}
        <section class="guard-card"><span class="guard-icon"><ShieldCheck size={16}/></span><div><strong>Local preflight flags</strong><p>{warnings[0]}</p><button on:click={() => tab='qc'}>Review {warnings.length} {warnings.length===1?'flag':'flags'} →</button></div></section>
      {:else}
        <section class="guard-ok"><ShieldCheck size={15}/><div><strong>Local preflight is clear</strong><span>Render-time Comfort Guard remains the final validator.</span></div></section>
      {/if}
    </div>
    <div class="panel-action">
      <button disabled={!previewEnabled||previewRendering} on:click={() => onPreview('still')} title={previewEnabled?'Render the frame under the playhead in about a second':analysisTier==='sampled'?'Run full-frame analysis to unlock previews':'Preview unlocks after direction'}><Play size={13} fill="currentColor"/> {!previewEnabled ? (analysisTier==='sampled' ? 'Full-frame preview locked' : 'Preview locked') : previewRendering ? 'Rendering…' : 'Render preview frame'} <kbd>R</kbd></button>
      {#if previewEnabled}
        <button class="clip-action" disabled={previewRendering} on:click={() => onPreview('video')} title="Render every frame of this shot so the 3D can be played back"><Clapperboard size={13}/> Render 3D clip <kbd>⇧R</kbd></button>
      {/if}
    </div>
  {:else if tab === 'qc'}
    <div class="panel-scroll qc-view">
      <div class="qc-score"><ShieldCheck size={24}/><div><span>LOCAL PREFLIGHT</span><strong>{warnings.length ? 'Review flags' : 'No local flags'}</strong></div><em class:review={warnings.length}>{warnings.length ? 'Review' : 'Clear'}</em></div>
      <section><h3>Preflight checks</h3>{#if warnings.length}{#each warnings as warning}<div class="qc-item warning"><AlertTriangle size={13}/><p><strong>Review before rendering</strong><span>{warning}</span></p></div>{/each}{:else}<div class="qc-item ok"><CheckCircle2 size={13}/><p><strong>No local flags</strong><span>Requested parameters fit the UI preflight thresholds. The renderer validates and clamps independently.</span></p></div>{/if}</section>
      <section><h3>Disparity envelope</h3><div class="envelope"><span class="negative" style={`width:${Math.min(50,shot.parameters.popoutDisparity/.008*50)}%`}></span><i></i><span class="positive" style={`width:${Math.min(50,shot.parameters.backgroundDisparity/.02*50)}%`}></span></div><div class="envelope-labels"><span>− {shot.parameters.popoutDisparity.toFixed(3)} ×w</span><b>SCREEN</b><span>+ {shot.parameters.backgroundDisparity.toFixed(3)} ×w</span></div></section>
      <section><h3>Shot metrics</h3><div class="metric-grid"><div><span>Motion</span><strong>{percent(shot.features.motion)}</strong></div><div><span>Depth spread</span><strong>{percent(shot.features.depthSpread)}</strong></div><div><span>Speech</span><strong>{percent(shot.features.speech)}</strong></div><div><span>Foreground</span><strong>{percent(shot.features.foreground)}</strong></div></div></section>
      <div class="qc-note"><Info size={12}/><p>Comfort metrics are conservative guidance, not a universal viewing guarantee. Validate the final master with your target glasses and display.</p></div>
    </div>
  {:else}
    <div class="panel-scroll inspect-view">
      <div class="inspect-hero"><Layers3 size={21}/><div><span>SHOT ANALYSIS</span><strong>Shot {String(shot.id).padStart(2,'0')}</strong></div></div>
      <dl><div><dt>Preset</dt><dd>{preset.label}</dd></div><div><dt>Confidence</dt><dd>{percent(shot.confidence)}</dd></div><div><dt>Duration</dt><dd>{(shot.endSeconds-shot.startSeconds).toFixed(2)} s</dd></div><div><dt>Motion feature</dt><dd>{percent(shot.features.motion)}</dd></div><div><dt>Hard-cut boundary</dt><dd>Isolated</dd></div></dl>
      <div class="data-path"><span>STEREO SCRIPT</span><code>director/stereo_script.json</code></div>
    </div>
  {/if}
</aside>

<style>
  .director-panel{display:flex;flex-direction:column;min-width:0;min-height:0;border-left:1px solid var(--line-soft);background:#0d1016}.not-ready .presets,.not-ready .controls,.not-ready .advanced,.not-ready .ai-section,.not-ready .panel-action{opacity:.5}.analysis-lock,.draft-ready{display:flex;align-items:center;gap:9px;padding:10px 13px;border-bottom:1px solid rgba(218,157,93,.2);background:rgba(112,70,33,.08)}.analysis-lock{align-items:flex-start}.analysis-lock>div,.draft-ready>div{display:grid;gap:4px;min-width:0;flex:1}.analysis-heading{display:flex;align-items:center;justify-content:space-between;gap:8px}.analysis-heading em{font:10px var(--font-mono);font-style:normal;color:#d2ad82;font-variant-numeric:tabular-nums}.analysis-lock strong{font-size:11px;color:#caa981}.analysis-lock small{font-size:10px;line-height:1.4;color:#766b60;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.analysis-progress-copy{font-size:9px;color:#625a53;font-variant-numeric:tabular-nums}.analysis-lock progress{width:100%;height:3px;border:0;border-radius:3px;overflow:hidden;background:#29231f}.analysis-lock progress::-webkit-progress-bar{background:#29231f}.analysis-lock progress::-webkit-progress-value{background:linear-gradient(90deg,#b87549,#dfae72)}.analysis-lock progress::-moz-progress-bar{background:linear-gradient(90deg,#b87549,#dfae72)}.draft-ready{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:start;border-color:rgba(92,205,190,.22);background:linear-gradient(100deg,rgba(50,130,120,.12),rgba(77,64,132,.07))}.draft-icon{width:25px;height:25px;display:grid;place-items:center;flex:0 0 auto;border-radius:7px;background:rgba(87,197,181,.12);color:#6bd4c6}.draft-ready strong{font-size:11px;color:#bcd8d4}.draft-ready small{font-size:9px;line-height:1.4;color:#66817d}.draft-ready>button{grid-column:2;justify-self:start;height:26px;padding:0 8px;border:1px solid rgba(95,207,193,.3);border-radius:6px;background:rgba(64,151,140,.12);color:#73d7ca;font-size:9px;white-space:nowrap}.draft-ready>button:hover{background:rgba(64,151,140,.2)}.draft-ready .draft-icon :global(.lucide-refresh-cw){animation:spin 1.3s linear infinite}.panel-tabs{height:38px;display:grid;grid-template-columns:1fr 1.15fr 1fr;border-bottom:1px solid var(--line-soft);padding:0 8px}.panel-tabs button{position:relative;border:0;background:none;color:#606a79;font-size:11px;font-weight:620}.panel-tabs button:hover:not(:disabled){color:#adb5c1}.panel-tabs button:disabled{color:#343c47}.panel-tabs button.active{color:#d8dde5}.panel-tabs button.active::after{content:'';position:absolute;left:8px;right:8px;bottom:-1px;height:2px;background:#65d3c6}.panel-tabs span{display:inline-grid;place-items:center;min-width:14px;height:14px;border-radius:7px;background:#242b34;color:#7a8594;font-size:10px;margin-left:3px}.panel-tabs span.warn{background:rgba(214,137,75,.14);color:#d69661}.panel-scroll{flex:1;min-height:0;overflow:auto}.shot-title{padding:14px 13px 12px;display:flex;justify-content:space-between;gap:6px;border-bottom:1px solid var(--line-soft)}.shot-title>div>span{font-size:10px;color:#64cfc2;letter-spacing:.12em;font-weight:700}.shot-title h2{font-size:12px;color:#d7dce4;margin:5px 0 0;font-weight:600;white-space:nowrap;max-width:150px;overflow:hidden;text-overflow:ellipsis}.confidence{align-self:end;display:flex;align-items:center;gap:4px;color:#697484;font-size:10px;white-space:nowrap}.confidence i{width:5px;height:5px;border-radius:50%;background:#5ac7a9}.inspector-section{padding:13px;border-bottom:1px solid var(--line-soft)}.section-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;color:#626c7b}.section-title>span{font-size:10px;letter-spacing:.13em;font-weight:720}.section-title button,.reset-all{border:0;background:none;color:#505a68;font-size:10px;padding:2px}.preset-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:5px}.preset-grid button{height:54px;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;position:relative;border:1px solid #252b35;background:#11151c;color:#777f8c;border-radius:7px}.preset-grid button:hover:not(:disabled){border-color:#3a4350;color:#adb5c0}.preset-grid button:disabled{color:#4d5662;border-color:#202630;background:#10141a}.preset-grid button.active{color:#d6dce5;border-color:color-mix(in srgb,var(--preset,#63cec2) 50%,#3b4450);background:#172027;box-shadow:inset 0 0 18px rgba(93,205,193,.05)}.preset-grid button strong{font-size:10px;font-weight:620;white-space:nowrap}.preset-grid button :global(svg){position:absolute;right:5px;top:5px;color:#64cfc1}.preset-symbol{height:13px;display:flex;align-items:end;gap:2px}.preset-symbol i{display:block;width:4px;background:var(--preset);border-radius:1px}.preset-symbol i:nth-child(1){height:6px;opacity:.5}.preset-symbol i:nth-child(2){height:12px}.preset-symbol i:nth-child(3){height:8px;opacity:.72}.preset-description{display:flex;align-items:center;margin:8px 1px 0;color:#687382;font-size:10px;line-height:1.4}.preset-description i{width:5px;height:5px;border-radius:50%;background:var(--preset);margin-right:5px;flex:0 0 auto}
  .draft-heading{display:flex;align-items:center;justify-content:space-between;gap:8px}.draft-heading em{font:10px var(--font-mono);font-style:normal;color:#79d8cb;font-variant-numeric:tabular-nums}.production-stage-copy{font-size:10px;line-height:1.35;color:#84aaa5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.production-progress-copy{font-size:9px;color:#587b76;font-variant-numeric:tabular-nums}.draft-ready progress{width:100%;height:3px;border:0;border-radius:3px;overflow:hidden;background:#1c2b2a}.draft-ready progress::-webkit-progress-bar{background:#1c2b2a}.draft-ready progress::-webkit-progress-value{background:linear-gradient(90deg,#428e85,#69d3c5)}.draft-ready progress::-moz-progress-bar{background:linear-gradient(90deg,#428e85,#69d3c5)}
  .ai-section{padding:0;background:linear-gradient(120deg,rgba(91,72,165,.06),transparent)}.ai-heading{width:100%;display:grid;grid-template-columns:auto 1fr auto;gap:8px;align-items:center;text-align:left;padding:11px 13px;border:0;background:none;color:#788291}.ai-icon{width:25px;height:25px;display:grid;place-items:center;color:#a897f4;border:1px solid rgba(145,127,238,.25);background:rgba(112,91,212,.1);border-radius:7px}.ai-heading>span:nth-child(2){display:grid;gap:3px}.ai-heading strong{font-size:11px;color:#c2c7d3}.ai-heading small{font-size:10px;color:#5d6675}:global(.rotated){transform:rotate(180deg)}.ai-body{padding:0 13px 12px}.ai-copy{font-size:10px;color:#697382;line-height:1.55;margin:0 0 9px}.ai-notice{display:flex;gap:6px;padding:7px;margin-bottom:8px;border:1px solid rgba(213,147,87,.24);background:rgba(118,73,37,.09);border-radius:6px;color:#d89b68}.ai-notice span{font-size:10px;line-height:1.4;color:#8c7768}.ask-button{width:100%;height:29px;display:flex;align-items:center;justify-content:center;gap:6px;border:1px solid rgba(139,119,235,.28);background:rgba(104,83,198,.1);color:#b6a9f3;border-radius:6px;font-size:11px}.ask-button:hover{background:rgba(104,83,198,.16)}.ask-button:disabled{opacity:.7}.mini-spinner{width:10px;height:10px;border:1px solid #574a8d;border-top-color:#b9a9ff;border-radius:50%;animation:spin .7s linear infinite}.ai-locked{display:grid;grid-template-columns:auto 1fr auto;gap:7px;align-items:start;color:#8276bd}.ai-locked p{display:grid;gap:3px;margin:0}.ai-locked strong{font-size:11px;color:#aab1bd}.ai-locked p span{font-size:10px;line-height:1.4;color:#626c7a}.ai-locked button,.ai-actions button{border:1px solid #343b49;background:#161b22;color:#929baa;border-radius:5px;font-size:10px;padding:5px 7px}.recommendation{display:flex;align-items:center;justify-content:space-between}.recommendation>div{display:grid;gap:4px}.rec-label{font-size:10px;letter-spacing:.12em;color:#766ba9}.recommendation strong{display:flex;align-items:center;gap:5px;font-size:11px;color:#c9cfda}.recommendation strong i{width:5px;height:5px;border-radius:50%;background:var(--preset)}.rec-confidence{font-size:10px;color:#756c9a}.reason{margin:8px 0;color:#747e8d;font-size:10px;line-height:1.5}.ai-actions{display:flex;justify-content:flex-end;gap:5px}.ai-actions .apply{color:#11161b;background:#bbb1ee;border-color:#bbb1ee;font-weight:700}
  .controls,.advanced-body{display:grid;gap:14px}.controls .section-title{margin-bottom:0}.toggle-heading{width:100%;margin:0;border:0;background:none}.advanced{padding:12px 13px}.advanced .section-title{margin:0}.advanced-body{padding-top:13px}.switch-row{display:flex;align-items:center;justify-content:space-between}.switch-row>span{display:grid;gap:3px}.switch-row strong{font-size:11px;color:#969fad;font-weight:500}.switch-row small{font-size:10px;color:#555f6e}.switch-row input{position:absolute;opacity:0}.switch-row>i{width:26px;height:14px;background:#252c35;border-radius:9px;position:relative;transition:.18s}.switch-row>i::after{content:'';position:absolute;width:10px;height:10px;left:2px;top:2px;border-radius:50%;background:#77818f;transition:.18s}.switch-row input:checked+i{background:#285e59}.switch-row input:checked+i::after{left:14px;background:#73d9cd}
  .guard-card,.guard-ok{margin:11px 10px;display:flex;gap:9px;padding:10px;border:1px solid rgba(212,140,78,.25);background:rgba(101,67,38,.09);border-radius:7px}.guard-icon{width:27px;height:27px;display:grid;place-items:center;background:rgba(213,140,80,.12);color:#dc9b65;border-radius:6px;flex:0 0 auto}.guard-card>div{display:grid;gap:4px}.guard-card strong,.guard-ok strong{font-size:11px;color:#c6cbd4}.guard-card p{margin:0;font-size:10px;line-height:1.45;color:#767e8a}.guard-card button{border:0;background:none;text-align:left;padding:0;color:#d09262;font-size:10px}.guard-ok{border-color:rgba(83,188,158,.2);background:rgba(52,115,100,.08);color:#5ac4a8}.guard-ok.pending{border-color:rgba(190,142,87,.2);background:rgba(105,75,42,.08);color:#b68b60}.guard-ok div{display:grid;gap:3px}.guard-ok span{font-size:10px;color:#687382}.panel-action{padding:9px 11px;display:grid;gap:6px;border-top:1px solid var(--line-soft);background:#0c0f14}.panel-action>button{position:relative;width:100%;height:33px;display:flex;align-items:center;justify-content:center;gap:7px;border:1px solid #35423f;background:linear-gradient(180deg,#22332f,#182521);color:#83dacd;border-radius:7px;font-size:11px;font-weight:650}.panel-action>button:hover:not(:disabled){border-color:#44554f}.panel-action>button:disabled{color:#59636f;border-color:#283039;background:#151a21}.panel-action .clip-action{height:30px;border-color:#2f3a46;background:linear-gradient(180deg,#1b2430,#141b24);color:#93a7c4;font-weight:600}.panel-action .clip-action:hover:not(:disabled){border-color:#3d4d5e;color:#b3c4dc}.panel-action kbd{position:absolute;right:10px;color:#65736f;background:#111915;border:1px solid #30403c;border-radius:3px;font-size:10px;padding:2px 4px}
  .qc-view,.inspect-view{padding:12px}.qc-score{display:flex;align-items:center;gap:10px;border:1px solid #26303a;background:linear-gradient(120deg,#15211f,#11151c);padding:12px;border-radius:8px;color:#60cfbd}.qc-score>div{display:grid;gap:3px;flex:1}.qc-score span{font-size:10px;letter-spacing:.12em;color:#65736f}.qc-score strong{font-size:13px;color:#d7e4e1}.qc-score em{font-style:normal;font-size:10px;color:#67c5aa;background:rgba(72,183,153,.1);padding:4px 6px;border-radius:8px}.qc-score em.review{color:#d69a68;background:rgba(210,142,84,.1)}.qc-view section{padding:15px 0;border-bottom:1px solid var(--line-soft)}.qc-view h3{margin:0 0 9px;font-size:10px;letter-spacing:.12em;color:#626d7c}.qc-item{display:flex;gap:8px;padding:8px;border-radius:6px}.qc-item.warning{color:#d89761;background:rgba(207,133,73,.08)}.qc-item.ok{color:#56c5a5;background:rgba(72,174,145,.07)}.qc-item p{display:grid;gap:3px;margin:0}.qc-item strong{font-size:11px;color:#b9c0ca}.qc-item span{font-size:10px;line-height:1.4;color:#687281}.envelope{height:8px;display:flex;align-items:center;justify-content:center;background:#181d24;border-radius:5px;overflow:hidden}.envelope i{height:14px;width:1px;background:#ecf0f2;z-index:1}.envelope .negative{height:100%;align-self:center;background:linear-gradient(90deg,transparent,#e86b81);margin-left:auto}.envelope .positive{height:100%;background:linear-gradient(90deg,#50cbd0,transparent);margin-right:auto}.envelope-labels{display:grid;grid-template-columns:1fr auto 1fr;margin-top:5px;color:#596473;font-size:10px}.envelope-labels b{font-size:10px;color:#9da5af}.envelope-labels span:last-child{text-align:right}.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px}.metric-grid div{display:flex;justify-content:space-between;padding:7px;background:#12161d;border-radius:5px;font-size:10px}.metric-grid span{color:#616b79}.metric-grid strong{color:#aeb5c0}.qc-note{display:flex;gap:7px;padding:10px 2px;color:#596474}.qc-note p{margin:0;font-size:10px;line-height:1.5}.inspect-hero{display:flex;align-items:center;gap:9px;padding:11px;border:1px solid #29313b;background:#12171e;border-radius:7px;color:#6ad2c4}.inspect-hero>div{display:grid;gap:3px}.inspect-hero span{font-size:10px;color:#5c6674;letter-spacing:.12em}.inspect-hero strong{font-size:11px;color:#cbd1d9}.inspect-view dl{margin:13px 0}.inspect-view dl div{display:flex;justify-content:space-between;padding:9px 2px;border-bottom:1px solid #191e26;font-size:11px}.inspect-view dt{color:#616b79}.inspect-view dd{color:#a7afba;margin:0}.data-path{padding:9px;border:1px solid #252c35;border-radius:6px;background:#10141a;display:grid;gap:5px}.data-path span{font-size:10px;color:#586271;letter-spacing:.12em}.data-path code{font-family:var(--font-mono);font-size:10px;color:#78bdb4}@keyframes spin{to{transform:rotate(360deg)}}
</style>
