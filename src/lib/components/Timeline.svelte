<script lang="ts">
  import { Minus, Plus } from 'lucide-svelte';
  import type { Project } from '../types';
  import { formatTime } from '../utils';

  export let project: Project;
  export let currentTime: number;
  export let selectedShotId: number | null;
  /** Shots with a rendered stereo preview, and shots rendering right now. */
  export let previewedShotIds: number[] = [];
  export let renderingShotIds: number[] = [];
  export let zoom = 1;
  export let onSeek: (seconds: number) => void;
  export let onSelect: (id: number, additive: boolean, range: boolean) => void;
  export let onZoom: (zoom: number) => void;

  $: previewed = new Set(previewedShotIds);
  $: rendering = new Set(renderingShotIds);
  $: playhead = (currentTime / project.durationSeconds) * 100;
  $: scaleWidth = Math.max(100, zoom * 100);

  function seekFromPointer(event: MouseEvent) {
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const local = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
    onSeek((local / rect.width) * project.durationSeconds);
  }
</script>

<section class="timeline-panel" aria-label="Shot timeline">
  <header>
    <div class="timeline-title"><strong>SHOT TIMELINE</strong><span>{project.shots.length} shots · {formatTime(project.durationSeconds)}</span></div>
    <div class="timeline-tools">
      <Minus size={10}/><input type="range" min=".55" max="2.5" step=".05" value={zoom} on:input={(e)=>onZoom(e.currentTarget.valueAsNumber)} aria-label="Timeline zoom"/><Plus size={10}/>
    </div>
  </header>
  <div class="timeline-viewport">
    <div class="timeline-content" style={`width:${scaleWidth}%`} on:click={seekFromPointer} role="presentation">
      <div class="ruler">
        {#each Array(9) as _,i}<span style={`left:${i*12.5}%`}><i></i>{formatTime(project.durationSeconds*(i/8))}</span>{/each}
      </div>
      <div class="video-track">
        <span class="track-label">V1</span>
        <div class="clips">
          {#each project.shots as shot}
            <button
              class:selected={shot.id===selectedShotId}
              class:warning={shot.status==='warning'}
              class:rendered={previewed.has(shot.id)}
              class:rendering={rendering.has(shot.id)}
              title={rendering.has(shot.id) ? 'Rendering this shot in 3D' : previewed.has(shot.id) ? 'Rendered in 3D' : 'Not rendered yet - select it and press R'}
              style={`width:${((shot.endSeconds-shot.startSeconds)/project.durationSeconds)*100}%;--clip:${shot.color}`}
              on:click|stopPropagation={(event)=>onSelect(shot.id,event.ctrlKey||event.metaKey,event.shiftKey)}
            >
              <div class="clip-art"><i></i><i></i><i></i></div><span>{String(shot.id).padStart(2,'0')} · {shot.name}</span>{#if shot.status==='warning'}<em>!</em>{/if}<b class="render-dot" aria-hidden="true"></b>
            </button>
          {/each}
        </div>
      </div>
      <div class="audio-track">
        <span class="track-label">A1</span>
        <div class="audio-status">Compatible source audio streams remuxed into the final output</div>
      </div>
      <div class="playhead" style={`left:${playhead}%`}><span></span><i></i></div>
    </div>
  </div>
</section>

<style>
  .timeline-panel{height:var(--timeline-height);min-height:125px;display:flex;flex-direction:column;border-top:1px solid var(--line);background:#0b0e13;min-width:0}.timeline-panel header{height:32px;display:flex;align-items:center;justify-content:space-between;padding:0 10px;border-bottom:1px solid var(--line-soft)}.timeline-title{display:flex;align-items:center;gap:8px}.timeline-title strong{font-size:10px;letter-spacing:.13em;color:#737d8c}.timeline-title span{font-size:10px;color:#434c59}.timeline-tools{display:flex;align-items:center;gap:5px;color:#566170}.timeline-tools input{appearance:none;width:68px;height:2px;background:#2a313b}.timeline-tools input::-webkit-slider-thumb{appearance:none;width:8px;height:8px;border-radius:50%;background:#8994a1}
  .timeline-viewport{flex:1;min-height:0;overflow-x:auto;overflow-y:hidden}.timeline-content{height:100%;min-width:100%;position:relative}.ruler{height:20px;margin-left:27px;position:relative;border-bottom:1px solid #1a1f27}.ruler span{position:absolute;top:4px;color:#48515e;font-size:10px;font-variant-numeric:tabular-nums;transform:translateX(-1px)}.ruler span i{position:absolute;left:0;top:10px;height:5px;border-left:1px solid #303641}.video-track,.audio-track{height:37px;display:grid;grid-template-columns:27px 1fr;border-bottom:1px solid #171c23}.track-label{display:grid;place-items:center;color:#495361;background:#0e1117;border-right:1px solid #1b2028;font-size:10px}.clips{display:flex;min-width:0;padding:2px 0;gap:1px}.clips button{height:32px;min-width:34px;position:relative;overflow:hidden;border:1px solid color-mix(in srgb,var(--clip) 25%,#262c34);background:color-mix(in srgb,var(--clip) 10%,#171b22);border-radius:3px;text-align:left;color:#969fab;padding:0}.clips button:hover{filter:brightness(1.2)}.clips button.selected{border-color:var(--clip);box-shadow:inset 0 0 0 1px rgba(255,255,255,.12)}.clips button span{position:absolute;left:4px;bottom:3px;right:3px;z-index:2;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 1px 2px #000}/* Rendered clips read as solid; un-rendered ones stay dim with a hollow dot,
     so a glance at the timeline says which shots still need a render. */
  .render-dot{position:absolute;right:5px;top:5px;width:6px;height:6px;border-radius:50%;border:1px solid rgba(150,163,180,.55);background:transparent}
  .rendered .render-dot{border-color:#63cbaa;background:#63cbaa;box-shadow:0 0 6px rgba(99,203,170,.65)}
  .rendering .render-dot{border-color:#6fd0c4;background:transparent;animation:clip-pulse 1s ease-in-out infinite}
  .rendered{box-shadow:inset 0 -2px 0 #63cbaa}
  .rendered .clip-art{opacity:.62}
  .rendering{box-shadow:inset 0 -2px 0 rgba(111,208,196,.5)}
  @keyframes clip-pulse{0%,100%{opacity:.35}50%{opacity:1}}
  .clip-art{position:absolute;inset:0;display:flex;opacity:.34}.clip-art i{flex:1;background:linear-gradient(150deg,var(--clip),transparent 65%);border-right:1px solid rgba(255,255,255,.04)}.clip-art i:nth-child(2){filter:hue-rotate(25deg)}.clip-art i:nth-child(3){filter:hue-rotate(-25deg)}.clips em{position:absolute;right:3px;top:3px;width:14px;height:14px;display:grid;place-items:center;border-radius:50%;background:#d18b56;color:#1a100a;font-style:normal;font-size:10px;font-weight:800}.audio-track{height:28px}.audio-status{display:flex;align-items:center;padding:0 8px;background:rgba(60,123,113,.035);color:#526d68;font-size:10px}.playhead{position:absolute;top:15px;bottom:0;width:1px;background:#ef6b74;z-index:5;pointer-events:none}.playhead span{position:absolute;top:0;left:-4px;width:9px;height:8px;background:#ef6b74;clip-path:polygon(0 0,100% 0,50% 100%)}.playhead i{position:absolute;top:5px;bottom:0;left:-2px;width:5px;background:rgba(239,107,116,.05)}
</style>
