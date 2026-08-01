<script lang="ts">
  import {
    ArrowRight,
    Clock3,
    FileVideo2,
    FolderOpen,
    Gauge,
    HardDrive,
    Layers3,
    Play,
    ShieldCheck,
    Sparkles,
    WandSparkles
  } from 'lucide-svelte';
  import type { RecentProject } from '../types';
  import BrandMark from './BrandMark.svelte';

  export let recentProjects: RecentProject[] = [];
  export let busy = false;
  export let onImport: (path?: string) => void;
  export let onOpenProject: () => void;
  export let onOpenRecent: (project: RecentProject) => void;
  export let onDemo: () => void;
  export let onSettings: () => void;

  let dragging = false;

  function drop(event: DragEvent) {
    event.preventDefault();
    dragging = false;
    if (event.dataTransfer?.files?.length) onImport();
  }
</script>

<div class="welcome-shell">
  <div class="ambient ambient-a"></div>
  <div class="ambient ambient-b"></div>
  <header class="welcome-header">
    <BrandMark size={32} />
    <div class="header-actions">
      <span class="offline"><i></i> Offline workspace</span>
      <button class="text-button" on:click={onSettings}>Preferences</button>
    </div>
  </header>

  <main>
    <section class="hero">
      <div class="eyebrow"><Sparkles size={13} /> SHOT-AWARE STEREO AUTHORING</div>
      <h1>Give flat footage<br /><span>cinematic dimension.</span></h1>
      <p class="lede">Direct comfortable, expressive stereo shot by shot, with a deterministic pipeline that stays entirely on your machine.</p>

      <div
        class:dragging
        class:busy
        class="drop-zone"
        role="button"
        tabindex="0"
        aria-label="Import a source video"
        on:click={() => !busy && onImport()}
        on:keydown={(event) => (event.key === 'Enter' || event.key === ' ') && !busy && onImport()}
        on:dragover={(event) => { event.preventDefault(); dragging = true; }}
        on:dragleave={() => (dragging = false)}
        on:drop={drop}
      >
        <div class="drop-icon">{#if busy}<span class="spinner"></span>{:else}<FileVideo2 size={25} strokeWidth={1.6} />{/if}</div>
        <div>
          <strong>{busy ? 'Opening the secure video picker...' : dragging ? 'Choose the dropped file securely' : 'Choose a video to begin'}</strong>
          <span>{busy ? 'Select one source, then review its project destination' : 'MP4, MOV, MKV, or AVI; you will review the project location before anything is created'}</span>
        </div>
        <button class="primary" disabled={busy} on:click|stopPropagation={() => onImport()}>
          <FolderOpen size={15} /> Choose video
        </button>
      </div>

      <div class="hero-actions">
        <button class="demo-button" on:click={onDemo} disabled={busy}><Play size={14} fill="currentColor" /> Explore demo project</button>
        <span>No media leaves your computer.</span>
      </div>
      <div class="project-default"><HardDrive size={12}/><span>Projects default to <code>D:\Parallax Projects</code>. You can change this before creating.</span></div>
    </section>

    <section class="value-strip" aria-label="Product capabilities">
      <div><span class="feature-icon teal"><Layers3 size={17} /></span><p><strong>Shot-aware direction</strong><small>Creative controls reset cleanly at every cut.</small></p></div>
      <div><span class="feature-icon violet"><ShieldCheck size={17} /></span><p><strong>Comfort Guard</strong><small>Conservative limits protect every render.</small></p></div>
      <div><span class="feature-icon orange"><Gauge size={17} /></span><p><strong>Resume anywhere</strong><small>Stage caching keeps iteration fast.</small></p></div>
      <div><span class="feature-icon pink"><HardDrive size={17} /></span><p><strong>Local by design</strong><small>Footage and depth stay under your control.</small></p></div>
    </section>

    <section class="recent-section">
      <div class="section-heading">
        <div><span>YOUR WORK</span><h2>Recent projects</h2></div>
        <button class="text-button" on:click={onOpenProject}><FolderOpen size={13} /> Open project</button>
      </div>
      <div class="recent-grid">
        {#each recentProjects as recent, index}
          <button class="recent-card" on:click={() => onOpenRecent(recent)}>
            <div class="recent-art" style={`--recent-accent:${recent.accent}; --shift:${index * 18}deg`}>
              <div class="horizon"></div><div class="ridge one"></div><div class="ridge two"></div>
              <span>{recent.duration}</span>
            </div>
            <div class="recent-copy">
              <strong>{recent.name}</strong>
              <span><Clock3 size={11} /> {recent.modified}</span>
            </div>
            <ArrowRight class="arrow" size={15} />
          </button>
        {/each}
        <button class="new-card" on:click={() => onImport()}>
          <span><WandSparkles size={20} /></span><strong>New stereo project</strong><small>Import a source clip</small>
        </button>
      </div>
    </section>
  </main>

  <footer><span>PARALLAX FORGE 0.1 - PRODUCTION PREVIEW</span><span>Windows - Offline-first</span></footer>
</div>

<style>
  .welcome-shell { min-height: 100%; position: relative; overflow: auto; background: #080a0f; color: var(--text); }
  .welcome-shell::before { content: ''; position: fixed; inset: 0; pointer-events: none; opacity: .22; background-image: linear-gradient(rgba(255,255,255,.022) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.022) 1px, transparent 1px); background-size: 44px 44px; mask-image: linear-gradient(to bottom, black, transparent 70%); }
  .ambient { position: fixed; border-radius: 50%; filter: blur(110px); pointer-events: none; opacity: .11; }
  .ambient-a { width: 520px; height: 300px; background: #40bdae; left: 15%; top: -170px; }
  .ambient-b { width: 420px; height: 320px; background: #7466e4; right: 5%; top: 130px; opacity: .08; }
  .welcome-header { height: 72px; display: flex; align-items: center; justify-content: space-between; padding: 0 clamp(24px, 5vw, 72px); border-bottom: 1px solid rgba(255,255,255,.055); position: relative; z-index: 2; }
  .header-actions, .hero-actions, .section-heading, .text-button, .recent-copy span { display: flex; align-items: center; }
  .header-actions { gap: 18px; }
  .offline { display: flex; align-items: center; gap: 7px; color: var(--text-muted); font-size: 10px; letter-spacing: .04em; }
  .offline i { width: 6px; height: 6px; background: #5bd6b6; border-radius: 50%; box-shadow: 0 0 8px #5bd6b6; }
  .text-button { gap: 6px; background: none; color: var(--text-dim); border: 0; font-size: 11px; padding: 7px; }
  .text-button:hover { color: var(--text); }
  main { width: min(1110px, calc(100% - 48px)); margin: 0 auto; padding: clamp(42px, 7vh, 76px) 0 40px; position: relative; z-index: 1; }
  .hero { max-width: 770px; margin: 0 auto; text-align: center; }
  .eyebrow { display: inline-flex; align-items: center; gap: 7px; border: 1px solid rgba(101,217,204,.22); background: rgba(67,151,143,.08); border-radius: 999px; padding: 7px 11px; color: #78dace; font-size:11px; font-weight: 700; letter-spacing: .15em; }
  h1 { margin: 22px 0 13px; font-size: clamp(38px, 5vw, 61px); line-height: 1.03; font-weight: 620; letter-spacing: -.046em; color: #f1f3f7; }
  h1 span { background: linear-gradient(100deg, #7be0d4 7%, #8b82ee 55%, #da81a0); background-clip: text; color: transparent; }
  .lede { width: min(650px, 92%); margin: 0 auto; color: #8a93a3; font-size: 14px; line-height: 1.65; }
  .drop-zone { margin: 32px auto 0; border: 1px dashed #343b49; border-radius: 15px; min-height: 104px; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 17px; padding: 15px 17px; text-align: left; background: linear-gradient(145deg, rgba(26,31,40,.72), rgba(14,17,23,.84)); box-shadow: 0 24px 70px rgba(0,0,0,.3), inset 0 1px rgba(255,255,255,.025); cursor: pointer; transition: .2s ease; }
  .drop-zone:hover, .drop-zone.dragging { border-color: rgba(98,216,202,.65); background: linear-gradient(145deg, rgba(37,54,59,.72), rgba(17,22,29,.9)); transform: translateY(-1px); }
  .drop-zone.busy { cursor: wait; }
  .drop-icon { width: 55px; height: 55px; display: grid; place-items: center; border: 1px solid #343b46; background: #11151c; color: #68d4c7; border-radius: 12px; }
  .drop-zone strong, .drop-zone span { display: block; }
  .drop-zone strong { font-size: 13px; color: #e2e6ee; margin-bottom: 6px; }
  .drop-zone span { font-size: 10px; color: var(--text-muted); }
  .primary { display: inline-flex; align-items: center; gap: 8px; border: 0; border-radius: 8px; padding: 10px 14px; background: #e7ebef; color: #11151b; font-size: 11px; font-weight: 700; }
  .primary:disabled { opacity: .55; }
  .hero-actions { justify-content: center; gap: 14px; margin: 15px 0 0; }
  .hero-actions > span { color: #555f6d; font-size:11px; }
  .project-default { display:flex;align-items:center;justify-content:center;gap:6px;margin-top:9px;color:#596675;font-size:10px; }.project-default :global(svg){color:#64bcb1}.project-default code{color:#798895;font-family:var(--font-mono);font-size:10px}
  .demo-button { display: inline-flex; align-items: center; gap: 7px; background: none; color: #9fa9ba; border: 0; font-size: 10px; padding: 7px; }
  .demo-button:hover { color: #74d8cb; }
  .value-strip { margin: 55px 0 47px; padding: 19px 2px; display: grid; grid-template-columns: repeat(4, 1fr); border-block: 1px solid rgba(255,255,255,.055); }
  .value-strip > div { display: flex; align-items: center; gap: 11px; padding: 2px 17px; border-right: 1px solid rgba(255,255,255,.055); text-align: left; }
  .value-strip > div:first-child { padding-left: 0; } .value-strip > div:last-child { border: 0; }
  .feature-icon { width: 32px; height: 32px; display: grid; place-items: center; border-radius: 8px; flex: 0 0 auto; }
  .teal { color:#60d5c7; background:rgba(76,198,185,.1) }.violet{color:#9184f2;background:rgba(135,119,235,.1)}.orange{color:#e79d6e;background:rgba(222,143,89,.1)}.pink{color:#e27e9e;background:rgba(220,103,142,.1)}
  .value-strip p { margin: 0; display: grid; gap: 4px; }.value-strip strong{font-size:10px;color:#cfd4dd}.value-strip small{font-size:11px;color:#626c7b;line-height:1.4}
  .section-heading { justify-content: space-between; margin-bottom: 14px; }
  .section-heading span { color:#5f6877;font-size:11px;letter-spacing:.16em;font-weight:700 }.section-heading h2{margin:5px 0 0;font-size:17px;font-weight:570}
  .recent-grid { display:grid;grid-template-columns:repeat(4,1fr);gap:12px; }
  .recent-card,.new-card{position:relative;text-align:left;border:1px solid #232832;background:#10131a;border-radius:11px;padding:8px;min-width:0;color:inherit;transition:.18s ease}.recent-card:hover,.new-card:hover{border-color:#3c4654;transform:translateY(-2px);box-shadow:0 15px 35px rgba(0,0,0,.22)}
  .recent-art{height:87px;border-radius:7px;position:relative;overflow:hidden;background:linear-gradient(170deg,#29313c,#6d5553);filter:hue-rotate(var(--shift))}.recent-art::after{content:'';position:absolute;inset:0;background:linear-gradient(transparent,rgba(5,7,10,.58))}.recent-art span{position:absolute;right:7px;bottom:6px;z-index:2;background:rgba(3,5,8,.66);padding:3px 5px;border-radius:4px;color:#cbd2dc;font-size:11px}.horizon{position:absolute;width:36px;height:36px;border-radius:50%;background:var(--recent-accent);filter:blur(1px);right:20%;top:17px;opacity:.72}.ridge{position:absolute;left:-10%;width:120%;height:80%;bottom:-40%;background:#252b32;transform:rotate(8deg)}.ridge.two{bottom:-56%;background:#12161c;transform:rotate(-6deg)}
  .recent-copy{display:grid;gap:5px;padding:10px 5px 5px}.recent-copy strong{font-size:10px;color:#d6dbe4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.recent-copy span{gap:5px;color:#616b7a;font-size:11px}:global(.arrow){position:absolute;right:13px;bottom:17px;color:#4f5968;opacity:0;transition:.18s}.recent-card:hover :global(.arrow){opacity:1;transform:translateX(2px)}
  .new-card{display:flex;min-height:137px;align-items:center;justify-content:center;flex-direction:column;text-align:center;border-style:dashed;background:rgba(16,19,26,.45)}.new-card>span{width:34px;height:34px;display:grid;place-items:center;border-radius:9px;background:#171c24;color:#657283;margin-bottom:9px}.new-card strong{font-size:10px;color:#b7bec9}.new-card small{font-size:11px;color:#596372;margin-top:5px}
  footer{height:44px;border-top:1px solid rgba(255,255,255,.045);display:flex;align-items:center;justify-content:space-between;padding:0 clamp(24px,5vw,72px);font-size:11px;color:#444d5b;letter-spacing:.09em}
  .spinner{width:20px;height:20px;border:2px solid #2d3e41;border-top-color:#69d8cb;border-radius:50%;animation:spin .75s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
  @media(max-width:900px){.value-strip{grid-template-columns:repeat(2,1fr);row-gap:18px}.value-strip>div:nth-child(2){border-right:0}.recent-grid{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:620px){.drop-zone{grid-template-columns:auto 1fr}.drop-zone .primary{grid-column:1/-1;justify-content:center}.value-strip{grid-template-columns:1fr}.value-strip>div{border-right:0}.recent-grid{grid-template-columns:1fr 1fr}.header-actions .offline{display:none}}
</style>
