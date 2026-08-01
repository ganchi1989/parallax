<script lang="ts">
  import {
    AlertTriangle,
    Check,
    ChevronRight,
    FileVideo2,
    FolderOpen,
    HardDrive,
    Info,
    ShieldCheck,
    Sparkles,
    X
  } from 'lucide-svelte';
  import type { ExportOptions, Project } from '../types';
  import { shortPath } from '../utils';

  export let project: Project;
  export let defaultMode: 'calibrated' | 'basic';
  /** Depth good enough to ship. Certified and image-analysis both qualify. */
  export let productionReady = false;
  /** Which tier produced it, so the confirmation does not overstate the result. */
  export let depthTier: Project['depthMode'] = 'unknown';
  /** Why the engine says export is locked, shown verbatim rather than guessed at. */
  export let blockedReason: string | undefined = undefined;
  export let onChooseOutput: (suggestedName: string) => Promise<string | null>;
  export let onExport: (options: ExportOptions) => void;
  export let onClose: () => void;

  let step: 1 | 2 = 1;
  let format: ExportOptions['format'] = 'anaglyph';
  let anaglyphMode: ExportOptions['anaglyphMode'] = defaultMode;
  let swapEyes = false;
  const fileStem = project.name.toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'stereo_master';
  let outputPath = `${project.projectPath}\\renders\\${fileStem}_anaglyph.mp4`;
  let outputChosen = false;
  let choosing = false;

  $: warningCount = project.shots.filter((shot) => shot.status === 'warning').length;
  $: scriptReady = project.analysisReady === true && /^[a-f0-9]{64}$/.test(project.scriptRevision ?? '');
  $: exportReady = productionReady && scriptReady;
  $: certified = depthTier === 'production';
  $: if (!outputChosen) outputPath = `${project.projectPath}\\renders\\${fileStem}_${format}.mp4`;

  async function choosePath() {
    choosing = true;
    try {
      const chosen = await onChooseOutput(`${fileStem}_${format}`);
      if (chosen) {outputPath = chosen;outputChosen = true;}
    } finally {
      choosing = false;
    }
  }

  function submit() {
    if (!exportReady) return;
    onExport({
      outputPath,
      format,
      anaglyphMode,
      swapEyes
    });
  }
</script>

<div class="modal-backdrop" role="presentation" on:mousedown={(event) => event.currentTarget === event.target && onClose()}>
  <div class="export-modal" role="dialog" aria-modal="true" aria-labelledby="export-title">
    <header>
      <div><span>DELIVERABLE</span><h2 id="export-title">Export stereo master</h2></div>
      <div class="steps" aria-label={`Step ${step} of 2`}><i class:active={step === 1}>1</i><span></span><i class:active={step === 2}>2</i></div>
      <button on:click={onClose} aria-label="Close export"><X size={17}/></button>
    </header>

    {#if step === 1}
      <main>
        <div class="page-heading"><h3>Choose a stereo format</h3><p>Your editable Director script and cached depth remain untouched.</p></div>
        <div class="format-grid">
          <label class:active={format === 'anaglyph'}>
            <input type="radio" bind:group={format} value="anaglyph"/>
            <span class="format-art anaglyph"><i></i><i></i></span>
            <div><strong>Red-cyan anaglyph</strong><small>Plays on a standard display with glasses</small></div>
            {#if format === 'anaglyph'}<Check size={15}/>{/if}
          </label>
          <label class:active={format === 'side_by_side'}>
            <input type="radio" bind:group={format} value="side_by_side"/>
            <span class="format-art sbs"><i></i><i></i></span>
            <div><strong>Side-by-side</strong><small>Stereo pair for compatible players</small></div>
            {#if format === 'side_by_side'}<Check size={15}/>{/if}
          </label>
        </div>

        {#if format === 'anaglyph'}
          <div class="inline-options">
            <label><span><strong>Color matrix</strong><small>Calibrated mode reduces luminance mismatch</small></span><select bind:value={anaglyphMode}><option value="calibrated">Calibrated</option><option value="basic">Basic channels</option></select></label>
            <label class="toggle"><span><strong>Swap eyes</strong><small>Use when red is worn over the right eye</small></span><input type="checkbox" bind:checked={swapEyes}/><i></i></label>
          </div>
        {/if}

        <div class="pipeline-profile">
          <FileVideo2 size={17}/>
          <p><strong>Validated engine profile</strong><span>The renderer chooses its production encoding profile. Output timing is preserved and compatible source audio streams are remuxed.</span></p>
        </div>

        {#if !productionReady}
          <div class="export-blocker" role="alert"><AlertTriangle size={17}/><p><strong>Measured depth required</strong><span>{blockedReason ?? (depthTier === 'synthetic' ? 'This project used synthetic test depth, which contains no scene geometry. Run depth analysis with the neural model or the built-in image-analysis engine, then export.' : 'Depth analysis has not produced a usable result for every shot yet. Run full-frame analysis before final export.')}</span></p></div>
        {:else if !scriptReady}
          <div class="export-blocker" role="alert"><AlertTriangle size={17}/><p><strong>Director analysis required</strong><span>Wait for the versioned stereo script to finish before final export.</span></p></div>
        {/if}
      </main>
    {:else}
      <main>
        <div class="page-heading"><h3>Review your master</h3><p>The queued render is resumable and keeps completed stage caches.</p></div>
        <div class="review-card">
          <span class="review-icon"><FileVideo2 size={20}/></span>
          <div><span>OUTPUT FORMAT</span><strong>{format === 'anaglyph' ? 'Anaglyph master' : 'Side-by-side stereo'}</strong><small>Engine-configured production profile</small></div>
          <button on:click={() => step = 1}>Edit</button>
        </div>
        <div class="output-group"><span>OUTPUT FILE</span><div><HardDrive size={15}/><code title={outputPath}>{shortPath(outputPath, 68)}</code><button on:click={choosePath} disabled={choosing}><FolderOpen size={14}/>{choosing ? 'Choosing...' : 'Change'}</button></div></div>
        <div class="pipeline-facts">
          <div><Check size={14}/><p><strong>QC report included</strong><span>The engine writes the report alongside the render.</span></p></div>
          <div><Check size={14}/><p><strong>Reusable cache retained</strong><span>Completed stages can be reused after safe edits.</span></p></div>
        </div>
        <div class="preflight">
          <header><span><ShieldCheck size={15}/> Local preflight</span><em>{project.shots.length} shots inspected</em></header>
          {#if scriptReady}<div><Check size={13}/><span>Project has a versioned Director script</span></div>{:else}<div class="notice"><AlertTriangle size={13}/><span>Director script is not ready</span></div>{/if}
          {#if productionReady}<div><Check size={13}/><span>{certified ? 'Certified neural depth is available' : 'Image-analysis depth is available; the neural model resolves fine detail better'}</span></div>{:else}<div class="notice"><AlertTriangle size={13}/><span>{blockedReason ?? 'Usable depth is not ready; export remains locked'}</span></div>{/if}
          {#if warningCount}<div class="notice"><AlertTriangle size={13}/><span>{warningCount} {warningCount === 1 ? 'shot has' : 'shots have'} local review flags</span></div>{/if}
        </div>
        <div class="disclaimer"><Info size={13}/><span>Viewing comfort depends on display size, viewing distance, glasses, and the viewer. Review the rendered QC output before delivery.</span></div>
      </main>
    {/if}

    <footer>
      <button class="secondary" on:click={step === 1 ? onClose : () => step = 1}>{step === 1 ? 'Cancel' : 'Back'}</button>
      <span>{step === 1 ? 'Step 1 of 2' : exportReady ? 'Ready for the processing queue' : 'Export is locked'}</span>
      {#if step === 1}<button class="primary" on:click={() => step = 2}>Review export <ChevronRight size={14}/></button>{:else}<button class="primary render" on:click={submit} disabled={!exportReady}><Sparkles size={14}/> Start export</button>{/if}
    </footer>
  </div>
</div>

<style>
  .modal-backdrop{position:fixed;inset:0;z-index:79;display:grid;place-items:center;background:rgba(2,4,7,.76);backdrop-filter:blur(8px);padding:20px}.export-modal{width:min(720px,94vw);height:min(590px,90vh);display:flex;flex-direction:column;border:1px solid #333b47;background:#0d1016;border-radius:12px;box-shadow:0 35px 100px rgba(0,0,0,.68);overflow:hidden}.export-modal>header{min-height:64px;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:0 18px;border-bottom:1px solid var(--line-soft);background:#10131a}.export-modal>header>div:first-child span{font-size:10px;color:#697484;letter-spacing:.14em}.export-modal>header h2{font-size:16px;margin:4px 0 0;color:#dce1e8}.export-modal>header>button{justify-self:end;width:34px;height:34px;display:grid;place-items:center;border:0;background:transparent;color:#778292;border-radius:7px}.export-modal>header>button:hover{background:#1b2028;color:#d5dbe3}.steps{display:flex;align-items:center}.steps i{width:24px;height:24px;display:grid;place-items:center;border:1px solid #3a424e;border-radius:50%;font-style:normal;font-size:11px;color:#748090}.steps i.active{color:#101619;background:#68d2c5;border-color:#68d2c5}.steps span{width:36px;height:1px;background:#313843}.export-modal>main{flex:1;overflow:auto;padding:22px 26px}.page-heading{margin-bottom:18px}.page-heading h3{margin:0 0 6px;font-size:17px;color:#dde2e8}.page-heading p{margin:0;font-size:11px;color:#727d8c}.format-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.format-grid label{min-height:128px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;position:relative;text-align:center;border:1px solid #2c3540;background:#11151c;border-radius:9px;cursor:pointer}.format-grid label:hover{border-color:#4a5664}.format-grid label.active{border-color:#4b8e87;background:#14201f;box-shadow:inset 0 0 25px rgba(84,192,179,.045)}.format-grid input{position:absolute;opacity:0}.format-grid label>div{display:grid;gap:4px}.format-grid strong{font-size:12px;color:#c9d0d8}.format-grid small{font-size:10px;color:#707b89}.format-grid label>:global(svg){position:absolute;right:11px;top:11px;color:#67d0c2}.format-art{width:72px;height:40px;display:flex;gap:3px;align-items:center;justify-content:center;border-radius:6px;background:linear-gradient(150deg,#292f38,#181c22);overflow:hidden}.format-art i{width:29px;height:29px;background:linear-gradient(145deg,#586878,#242a31);border-radius:3px}.format-art.anaglyph{position:relative}.format-art.anaglyph i{position:absolute;width:41px;height:29px;mix-blend-mode:screen}.format-art.anaglyph i:first-child{background:#a4293b;left:11px}.format-art.anaglyph i:last-child{background:#1d8e9b;right:11px}.inline-options{margin-top:11px;padding:8px 12px;border:1px solid #28303a;background:#10141a;border-radius:8px}.inline-options>label{min-height:48px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1f252e}.inline-options>label:last-child{border:0}.inline-options>label>span{display:grid;gap:4px}.inline-options strong{font-size:11px;color:#b9c1cb}.inline-options small{font-size:10px;color:#667180}.inline-options select{height:34px;border:1px solid #36404d;background:#171b22;color:#aeb7c3;border-radius:6px;font-size:11px;padding:0 9px}.toggle input{position:absolute;opacity:0}.toggle>i{width:34px;height:20px;background:#29313a;border-radius:10px;position:relative}.toggle>i::after{content:'';position:absolute;left:3px;top:3px;width:14px;height:14px;border-radius:50%;background:#7d8997;transition:.16s}.toggle input:checked+i{background:#2a645e}.toggle input:checked+i::after{left:17px;background:#70d7ca}.pipeline-profile,.export-blocker{margin-top:12px;display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:center;padding:12px;border:1px solid #29323d;border-radius:8px;background:#11161d;color:#69c7ba}.pipeline-profile p,.export-blocker p{display:grid;gap:4px;margin:0}.pipeline-profile strong,.export-blocker strong{font-size:11px;color:#bdc6cf}.pipeline-profile span,.export-blocker span{font-size:10px;line-height:1.45;color:#6f7a88}.export-blocker{border-color:rgba(215,143,82,.4);background:rgba(100,58,25,.1);color:#df9b61}.export-blocker strong{color:#deb17f}.review-card{display:grid;grid-template-columns:auto 1fr auto;gap:11px;align-items:center;border:1px solid #2d3641;background:#11161d;border-radius:8px;padding:13px}.review-icon{width:38px;height:38px;display:grid;place-items:center;border-radius:8px;background:rgba(82,190,178,.09);color:#62cbbd}.review-card>div{display:grid;gap:4px}.review-card>div>span,.output-group>span{font-size:10px;letter-spacing:.11em;color:#697483}.review-card strong{font-size:12px;color:#c7ced7}.review-card small{font-size:10px;color:#6d7887}.review-card button{min-height:32px;border:0;background:none;color:#70cfc3;font-size:11px;padding:0 8px}.output-group{margin:16px 0}.output-group>span{display:block;margin-bottom:8px}.output-group>div{min-height:44px;display:flex;align-items:center;gap:9px;padding:0 10px;border:1px solid #2d3641;background:#10141a;border-radius:8px;color:#6d7886}.output-group code{flex:1;min-width:0;font-family:var(--font-mono);font-size:10px;color:#9ba5b2;overflow:hidden;text-overflow:ellipsis}.output-group button{min-height:32px;display:flex;align-items:center;gap:6px;border:1px solid #394350;background:#171c23;color:#a4adba;border-radius:6px;font-size:11px;padding:0 10px}.pipeline-facts{display:grid;grid-template-columns:1fr 1fr;gap:9px}.pipeline-facts>div{display:grid;grid-template-columns:auto 1fr;gap:8px;padding:11px;border:1px solid #28313b;border-radius:8px;background:#10141a;color:#64c8b8}.pipeline-facts p{display:grid;gap:3px;margin:0}.pipeline-facts strong{font-size:11px;color:#b9c1ca}.pipeline-facts span{font-size:10px;line-height:1.35;color:#697482}.preflight{margin-top:14px;border:1px solid #2b3833;background:rgba(41,93,79,.055);border-radius:8px;padding:11px}.preflight header{display:flex;justify-content:space-between;margin-bottom:8px}.preflight header span{display:flex;align-items:center;gap:7px;color:#69c9b0;font-size:11px}.preflight header em{font-style:normal;color:#65766f;font-size:10px}.preflight>div{display:flex;align-items:center;gap:7px;color:#65b29d;font-size:10px;padding:4px}.preflight>div span{color:#7b8784}.preflight .notice{color:#d29562}.disclaimer{display:flex;gap:8px;padding:11px 2px;color:#697482}.disclaimer span{font-size:10px;line-height:1.45}.export-modal>footer{min-height:58px;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:0 18px;border-top:1px solid var(--line-soft);background:#10131a}.export-modal>footer>span{font-size:10px;color:#626d7b}.export-modal>footer button{min-height:34px;border-radius:7px;font-size:11px}.secondary{justify-self:start;border:1px solid #39424e;background:#161b22;color:#a2abb7;padding:0 14px}.primary{justify-self:end;display:flex;align-items:center;gap:7px;border:0;background:#dfe6e7;color:#101517;font-weight:700;padding:0 14px}.primary.render{background:linear-gradient(135deg,#72dbcf,#61c2b8)}.primary:disabled{cursor:not-allowed;opacity:.4}
  @media(max-width:620px){.format-grid,.pipeline-facts{grid-template-columns:1fr}.format-grid label{min-height:90px;flex-direction:row}.export-modal>main{padding:18px}.export-modal>footer>span{display:none}.export-modal>footer{grid-template-columns:1fr 1fr}}
</style>
