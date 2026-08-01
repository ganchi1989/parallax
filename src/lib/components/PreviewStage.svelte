<script lang="ts">
  import {
    Eye,
    FlipHorizontal2,
    Focus,
    Maximize2,
    Pause,
    Play,
    ScanLine,
    SkipBack,
    SkipForward
  } from 'lucide-svelte';
  import type { Project, Shot, ViewMode } from '../types';
  import { formatTime } from '../utils';
  import SceneArt from './SceneArt.svelte';

  export let project: Project;
  export let shot: Shot;
  export let demoMode = false;
  export let originalUrl: string | null = null;
  export let previewUrl: string | null = null;
  export let previewEnabled = false;
  export let previewRendering = false;
  export let previewLockedReason = 'Full-frame analysis must finish before previews can be rendered.';
  export let viewMode: ViewMode;
  export let currentTime: number;
  export let playing: boolean;
  export let showSafeZones = true;
  export let onViewMode: (mode: ViewMode) => void;
  export let onRequestPreview: (kind?: 'still' | 'video') => void = () => {};
  export let onToggleSafeZones: () => void;
  export let onTogglePlayback: () => void;
  export let onPlaybackState: (playing: boolean) => void;
  export let onStepShot: (direction: -1 | 1) => void;
  export let onSeek: (seconds: number) => void;

  let split = 52;
  let fitting = true;
  let frameElement: HTMLDivElement | undefined;
  let previewVideo: HTMLVideoElement | undefined;
  let sourceVideo: HTMLVideoElement | undefined;
  // Previews render one frame at the playhead, so the rendered layer is a still
  // image. A shot rendered to video before this change still plays as video.
  $: previewIsStill = Boolean(previewUrl && /\.png(\?|$)/i.test(previewUrl));
  // The viewer used to hard-code 16:9, which pillarboxed portrait sources and
  // drew the comfort guides around the empty box rather than the picture.
  $: frameAspect = project.width > 0 && project.height > 0
    ? `${project.width} / ${project.height}`
    : '16 / 9';
  $: tallSource = project.height > project.width;
  $: shotProgress = Math.max(0, Math.min(1, (currentTime - shot.startSeconds) / (shot.endSeconds - shot.startSeconds)));
  $: originalAvailable = demoMode || Boolean(originalUrl);
  $: resultAvailable = demoMode || Boolean(previewUrl);
  $: splitAvailable = demoMode || Boolean(originalUrl && previewUrl);
  $: anaglyphActionable = resultAvailable || previewEnabled;
  $: splitActionable = splitAvailable || (originalAvailable && previewEnabled);
  $: depthAvailable = demoMode;
  $: requestedAvailable = viewMode==='original' ? originalAvailable : viewMode==='anaglyph' ? resultAvailable||previewRendering : viewMode==='split' ? splitAvailable||(originalAvailable&&previewRendering) : depthAvailable;
  $: effectiveViewMode = requestedAvailable ? viewMode : originalAvailable ? 'original' : resultAvailable ? 'anaglyph' : viewMode;
  $: if(previewVideo&&previewUrl){const localTime=Math.max(0,currentTime-shot.startSeconds);if(Math.abs(previewVideo.currentTime-localTime)>.18)previewVideo.currentTime=Math.min(localTime,previewVideo.duration||localTime);if(playing&&previewVideo.paused)void previewVideo.play().catch(()=>onPlaybackState(false));else if(!playing&&!previewVideo.paused)previewVideo.pause()}
  $: if(sourceVideo&&originalUrl){if(Math.abs(sourceVideo.currentTime-currentTime)>.18)sourceVideo.currentTime=Math.min(currentTime,sourceVideo.duration||currentTime);if(playing&&sourceVideo.paused)void sourceVideo.play().catch(()=>onPlaybackState(false));else if(!playing&&!sourceVideo.paused)sourceVideo.pause();sourceVideo.muted=true}

  function openFullscreen(){
    if(frameElement?.requestFullscreen)void frameElement.requestFullscreen();
  }

  function selectRenderedView(mode: 'anaglyph' | 'split'){
    onViewMode(mode);
    if(!resultAvailable&&previewEnabled&&!previewRendering)onRequestPreview();
  }
</script>

<section class="preview-panel" aria-label="Stereo preview" aria-busy={previewRendering}>
  <div class="viewer-toolbar">
    <div class="view-tabs" aria-label="Preview display mode">
      <button disabled={!originalAvailable} class:active={effectiveViewMode === 'original'} aria-pressed={effectiveViewMode === 'original'} on:click={() => onViewMode('original')} title={originalAvailable ? 'Original source' : 'Source monitor is not ready'}>Original</button>
      <button disabled={!anaglyphActionable} class:active={effectiveViewMode === 'anaglyph'} class:rendering={previewRendering&&!resultAvailable} aria-pressed={effectiveViewMode === 'anaglyph'} on:click={() => selectRenderedView('anaglyph')} title={resultAvailable ? 'Rendered anaglyph preview' : previewRendering ? 'Rendering this shot now' : previewEnabled ? 'Render and open this shot in Anaglyph view' : previewLockedReason}><i class="glasses"></i> {previewRendering&&!resultAvailable ? 'Rendering…' : 'Anaglyph'}</button>
      <button disabled={!splitActionable} class:active={effectiveViewMode === 'split'} aria-pressed={effectiveViewMode === 'split'} on:click={() => selectRenderedView('split')} title={splitAvailable ? 'Compare source and preview' : previewRendering ? 'Rendering this shot now' : previewEnabled&&originalAvailable ? 'Render this shot, then compare it with the source' : previewLockedReason}>Compare</button>
      <button disabled={!depthAvailable} class:active={effectiveViewMode === 'depth'} aria-pressed={effectiveViewMode === 'depth'} on:click={() => onViewMode('depth')} title={depthAvailable ? 'Demo depth view' : 'Depth image viewing is not available yet'}>Depth</button>
    </div>
    <div class="viewer-tools">
      <span>{project.width} x {project.height} <i>/</i> {project.fps} fps</span>
      <button class:active={showSafeZones} on:click={onToggleSafeZones} title="Toggle comfort safe zones" aria-label="Toggle comfort safe zones"><Focus size={15}/></button>
      <button on:click={() => (fitting = !fitting)} class:active={fitting} title="Fit viewer" aria-label="Fit viewer"><ScanLine size={14}/></button>
      <button on:click={openFullscreen} title="Fullscreen preview" aria-label="Fullscreen preview"><Maximize2 size={15}/></button>
    </div>
  </div>

  <div class="stage-wrap">
    <div class="stage-glow"></div>
    <div bind:this={frameElement} class:fill={!fitting} class:tall={tallSource} class="frame" style={`--frame-aspect:${frameAspect}`}>
      {#if effectiveViewMode === 'split'}
        <div class="layer original">{#if originalUrl}<video bind:this={sourceVideo} src={originalUrl} preload="metadata" muted playsinline on:timeupdate={(event)=>onSeek(event.currentTarget.currentTime)} on:ended={()=>onPlaybackState(false)} aria-label="Muted original video monitor"></video>{:else if demoMode}<SceneArt variant={shot.id} mode="original" label="Demo original frame" />{:else}<div class="media-placeholder"><ScanLine size={24}/><strong>Original proxy unavailable</strong><span>Source metadata and timing remain authoritative.</span></div>{/if}</div>
        <div class="layer result" style={`clip-path:inset(0 0 0 ${split}%)`}>{#if previewUrl && previewIsStill}<img src={previewUrl} alt="Rendered anaglyph preview frame" />{:else if previewUrl}<video bind:this={previewVideo} src={previewUrl} preload="metadata" muted playsinline on:ended={()=>onPlaybackState(false)} aria-label="Rendered anaglyph shot preview"></video>{:else if demoMode}<SceneArt variant={shot.id} mode="anaglyph" label="Demo anaglyph result" />{:else if previewRendering}<div class="media-placeholder result-placeholder render-placeholder" role="status"><i class="render-spinner"></i><strong>Rendering anaglyph preview</strong><span>This view will update automatically.</span></div>{:else}<div class="media-placeholder result-placeholder"><Eye size={24}/><strong>Preview ready to render</strong><span>Rendering starts when this view is selected.</span></div>{/if}</div>
        <div class="split-line" style={`left:${split}%`}><span><FlipHorizontal2 size={11}/></span></div>
        <input class="split-control" type="range" min="8" max="92" bind:value={split} aria-label="Before and after split position" />
        <span class="view-label left">ORIGINAL</span><span class="view-label right">DIRECTED ANAGLYPH{previewIsStill ? ' · FRAME' : ''}</span>
      {:else}
        {#if effectiveViewMode==='original'&&originalUrl}<video bind:this={sourceVideo} src={originalUrl} preload="metadata" muted playsinline on:timeupdate={(event)=>onSeek(event.currentTarget.currentTime)} on:ended={()=>onPlaybackState(false)} aria-label="Muted original video monitor"></video>{:else if effectiveViewMode==='anaglyph'&&previewUrl&&previewIsStill}<img src={previewUrl} alt="Rendered anaglyph preview frame" />{:else if effectiveViewMode==='anaglyph'&&previewUrl}<video bind:this={previewVideo} src={previewUrl} preload="metadata" muted playsinline on:timeupdate={(event)=>onSeek(shot.startSeconds+event.currentTarget.currentTime)} on:ended={()=>onPlaybackState(false)} aria-label="Rendered anaglyph shot preview"></video>{:else if demoMode}<SceneArt variant={shot.id} mode={effectiveViewMode === 'depth' ? 'depth' : effectiveViewMode === 'anaglyph' ? 'anaglyph' : 'original'} label={`Demo ${effectiveViewMode} frame`} />{:else if effectiveViewMode==='anaglyph'&&previewRendering}<div class="media-placeholder render-placeholder" role="status"><i class="render-spinner"></i><strong>Rendering anaglyph preview</strong><span>This view will update automatically when the shot is ready.</span></div>{:else}<div class="media-placeholder"><ScanLine size={24}/><strong>{effectiveViewMode==='depth'?'Depth viewer not generated':effectiveViewMode==='anaglyph'?'Preview ready to render':'Original proxy unavailable'}</strong><span>{effectiveViewMode==='anaglyph'?'Rendering starts when this view is selected.':'No procedural image is substituted for project media.'}</span></div>{/if}
        <span class="view-label left">{effectiveViewMode === 'depth' && !demoMode ? 'DEPTH PREVIEW UNAVAILABLE' : effectiveViewMode==='anaglyph'&&previewIsStill ? 'ANAGLYPH · FRAME AT PLAYHEAD' : effectiveViewMode.toUpperCase()}</span>
      {/if}
      {#if showSafeZones}<div class="safe-frame" aria-hidden="true"><i></i><i></i></div>{/if}
      <div class="frame-meta"><span>SHOT {String(shot.id).padStart(2,'0')}</span><span>{shot.name}</span></div>
      {#if shot.status === 'warning'}<div class="guard-badge"><Eye size={11}/> COMFORT GUARD ACTIVE</div>{/if}
      {#if project.analysisTier==='sampled'}<div class="test-depth-badge draft-badge">SAMPLED DRAFT · PREVIEW &amp; EXPORT LOCKED</div>{:else if project.depthMode==='synthetic'}<div class="test-depth-badge">TEST DEPTH - EXPORT LOCKED</div>{:else if project.depthMode==='image-analysis'}<div class="test-depth-badge image-badge">IMAGE-ANALYSIS DEPTH</div>{/if}
    </div>
  </div>

  <div class="transport">
    <div class="transport-left"><span>Monitor muted</span></div>
    <div class="transport-center">
      <button on:click={() => onStepShot(-1)} title="Previous shot" aria-label="Previous shot"><SkipBack size={15} fill="currentColor"/></button>
      <button class="play" on:click={onTogglePlayback} aria-label={playing ? 'Pause preview' : 'Play preview'}>{#if playing}<Pause size={17} fill="currentColor"/>{:else}<Play size={17} fill="currentColor"/>{/if}</button>
      <button on:click={() => onStepShot(1)} title="Next shot" aria-label="Next shot"><SkipForward size={15} fill="currentColor"/></button>
    </div>
    <div class="timecode"><strong>{formatTime(currentTime, true, project.fps)}</strong><span>/ {formatTime(project.durationSeconds, true, project.fps)}</span></div>
  </div>
  <div class="shot-scrubber"><div style={`width:${shotProgress * 100}%`}></div><input type="range" min={shot.startSeconds} max={shot.endSeconds} step={1/project.fps} value={currentTime} on:input={(e) => onSeek(e.currentTarget.valueAsNumber)} aria-label="Shot playhead" /></div>
</section>

<style>
  .preview-panel{min-width:0;min-height:0;display:flex;flex-direction:column;background:#090b10}
  .viewer-toolbar{height:45px;display:flex;align-items:center;justify-content:space-between;padding:0 13px;border-bottom:1px solid var(--line-soft);background:#0d1016}.view-tabs{height:100%;display:flex;align-items:center;gap:2px}.view-tabs button{height:28px;padding:0 10px;display:flex;align-items:center;gap:6px;border:0;border-radius:6px;background:transparent;color:#687282;font-size:11px;font-weight:570;position:relative}.view-tabs button:hover{color:#aeb6c1}.view-tabs button:disabled{color:#343d48;background:transparent}.view-tabs button.active{color:#dbe0e8;background:#181d25;box-shadow:inset 0 0 0 1px #282f39}.view-tabs button.active::after{content:'';position:absolute;height:2px;left:9px;right:9px;bottom:-9px;background:#61d2c4;border-radius:3px}.view-tabs button.rendering{color:#e1b274}.glasses{width:10px;height:5px;display:block;border-left:4px solid #e35d72;border-right:4px solid #55c8d3;border-radius:3px}
  .viewer-tools{display:flex;align-items:center;gap:5px}.viewer-tools>span{font-size:10px;color:#505967;margin-right:4px;font-variant-numeric:tabular-nums}.viewer-tools>span i{font-style:normal;color:#2f3640}.viewer-tools button,.transport button{width:28px;height:28px;display:grid;place-items:center;border:0;background:transparent;color:#626d7d;border-radius:6px}.viewer-tools button:hover,.viewer-tools button.active,.transport button:hover{background:#181d25;color:#c1c8d2}
  .stage-wrap{flex:1;min-height:190px;position:relative;display:grid;place-items:center;padding:clamp(15px,2.3vw,32px);overflow:hidden;background:radial-gradient(circle at 50% 42%,#151a22 0,#0a0c11 58%)}.stage-wrap::before{content:'';position:absolute;inset:0;background-image:linear-gradient(45deg,#0e1117 25%,transparent 25%),linear-gradient(-45deg,#0e1117 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#0e1117 75%),linear-gradient(-45deg,transparent 75%,#0e1117 75%);background-size:18px 18px;background-position:0 0,0 9px,9px -9px,-9px 0;opacity:.2}.stage-glow{position:absolute;width:60%;height:60%;background:#497d78;filter:blur(100px);opacity:.06}
  .frame{position:relative;width:min(100%,920px);aspect-ratio:var(--frame-aspect,16/9);max-height:100%;overflow:hidden;background:#1d2229;box-shadow:0 22px 65px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.12),0 0 0 6px rgba(2,3,5,.5)}.frame.tall{width:auto;height:100%;max-width:100%}.frame video,.frame img{width:100%;height:100%;display:block;object-fit:contain;background:#050608}.media-placeholder{width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;background:radial-gradient(circle at center,#171d25,#090c11);color:#566170;text-align:center}.media-placeholder strong{font-size:11px;color:#929caa}.media-placeholder span{font-size:11px;color:#535d6b}.result-placeholder{background:radial-gradient(circle at center,#17211f,#090c11);color:#5caea3}.render-placeholder{background:radial-gradient(circle at center,#172321,#090c11)}.render-spinner{width:19px;height:19px;border:2px solid rgba(97,210,196,.2);border-top-color:#61d2c4;border-radius:50%;animation:preview-spin .8s linear infinite}.test-depth-badge{position:absolute;top:10px;right:10px;z-index:7;padding:5px 7px;border:1px solid rgba(231,157,85,.55);border-radius:4px;background:rgba(45,28,13,.84);color:#e2a069;font-size:11px;font-weight:750;letter-spacing:.08em}.test-depth-badge.image-badge{border-color:rgba(120,170,225,.5);background:rgba(24,42,68,.84);color:#8fbbe8}.test-depth-badge.draft-badge{border-color:rgba(154,133,234,.5);background:rgba(37,28,68,.84);color:#b9a9f3}.frame.fill{width:112%;max-width:none}.frame.tall.fill{width:auto;height:112%;max-height:none;max-width:100%}.layer{position:absolute;inset:0}.split-line{position:absolute;top:0;bottom:0;width:1px;background:rgba(232,240,244,.82);z-index:4;pointer-events:none}.split-line span{position:absolute;top:50%;left:50%;width:25px;height:25px;display:grid;place-items:center;transform:translate(-50%,-50%);border-radius:50%;color:#172025;background:#e4ebed;box-shadow:0 2px 12px rgba(0,0,0,.4)}.split-control{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:ew-resize;z-index:5}.view-label{position:absolute;top:10px;z-index:3;border:1px solid rgba(255,255,255,.15);background:rgba(6,8,11,.65);backdrop-filter:blur(8px);color:#c5cbd2;border-radius:4px;padding:4px 6px;font-size:10px;letter-spacing:.11em}.view-label.left{left:10px}.view-label.right{right:10px}.safe-frame{position:absolute;inset:7%;z-index:2;border:1px solid rgba(255,255,255,.17);pointer-events:none}.safe-frame::before,.safe-frame::after,.safe-frame i::before,.safe-frame i::after{content:'';position:absolute;width:12px;height:12px;border-color:rgba(102,220,205,.5)}.safe-frame::before{left:-1px;top:-1px;border-left:1px solid;border-top:1px solid}.safe-frame::after{right:-1px;top:-1px;border-right:1px solid;border-top:1px solid}.safe-frame i:first-child::before{left:-1px;bottom:-1px;border-left:1px solid;border-bottom:1px solid}.safe-frame i:last-child::after{right:-1px;bottom:-1px;border-right:1px solid;border-bottom:1px solid}.frame-meta{position:absolute;left:11px;bottom:10px;z-index:3;display:flex;align-items:center;gap:7px;color:#e3e7ec;font-size:10px;text-shadow:0 1px 3px #000}.frame-meta span:first-child{border-right:1px solid rgba(255,255,255,.3);padding-right:7px;color:#75d7cb;font-weight:720;letter-spacing:.1em}.guard-badge{position:absolute;z-index:3;right:11px;bottom:10px;display:flex;align-items:center;gap:5px;padding:4px 6px;border-radius:4px;background:rgba(33,26,18,.75);border:1px solid rgba(222,159,93,.35);color:#dda06c;font-size:10px;letter-spacing:.09em}
  .transport{height:48px;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:0 13px;border-top:1px solid var(--line-soft);background:#0e1117}.transport-left{display:flex;align-items:center;gap:3px}.transport-left span{font-size:10px;color:#535d6c}.transport-center{display:flex;align-items:center;gap:7px}.transport .play{width:31px;height:31px;border-radius:50%;color:#11161a;background:#dbe3e6;box-shadow:0 4px 14px rgba(0,0,0,.3)}.transport .play:hover{color:#111;background:#fff}.timecode{justify-self:end;display:flex;align-items:center;gap:5px;font-variant-numeric:tabular-nums}.timecode strong{font-size:11px;color:#cbd1da;letter-spacing:.04em}.timecode span{font-size:10px;color:#505967}.shot-scrubber{height:2px;position:relative;background:#252b34}.shot-scrubber>div{height:100%;background:#63d5c8}.shot-scrubber input{position:absolute;inset:-6px 0;width:100%;height:14px;opacity:0;cursor:pointer}
  @keyframes preview-spin{to{transform:rotate(360deg)}}
  :global(.reduce-motion) .render-spinner{animation:none;border-color:#61d2c4}
  @media(max-width:1060px){.viewer-tools>span{display:none}.view-tabs button{padding:0 7px}.stage-wrap{padding:15px}}
</style>
