<script lang="ts">
  import {
    Bot,
    Check,
    Cpu,
    Eye,
    Gauge,
    HardDrive,
    Info,
    KeyRound,
    LockKeyhole,
    Monitor,
    RefreshCw,
    ShieldCheck,
    Sparkles,
    Trash2,
    X
  } from 'lucide-svelte';
  import type { AppSettings } from '../types';
  import BrandMark from './BrandMark.svelte';

  export let settings: AppSettings;
  export let hasLlmKey = false;
  export let credentialStorageAvailable = true;
  export let keyBusy = false;
  export let testState: 'idle'|'testing'|'success'|'error' = 'idle';
  export let onUpdate: (settings: Partial<AppSettings>) => void;
  export let onClose: () => void;
  export let onSaveKey: (key: string) => void;
  export let onDeleteKey: () => void;
  export let onTestLlm: () => void;

  let tab: 'general'|'performance'|'assistant'|'about' = 'general';
  let apiKey = '';
  let revealKey = false;
  let replacingKey = false;

  $: keyIsValid = apiKey.length >= 16 && apiKey.length <= 1024 && !/[\s\x00-\x1f\x7f]/.test(apiKey);

  function saveKey() {
    if (!keyIsValid) return;
    onSaveKey(apiKey);
    apiKey = '';
    revealKey = false;
    replacingKey = false;
  }
</script>

<div class="modal-backdrop" role="presentation" on:mousedown={(e)=>e.currentTarget===e.target&&onClose()}>
  <div class="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
    <header><div><span>APPLICATION</span><h2 id="settings-title">Settings</h2></div><button on:click={onClose} aria-label="Close settings"><X size={16}/></button></header>
    <div class="settings-body">
      <nav aria-label="Settings sections">
        <button class:active={tab==='general'} on:click={()=>tab='general'}><Monitor size={14}/>General</button>
        <button class:active={tab==='performance'} on:click={()=>tab='performance'}><Gauge size={14}/>Performance</button>
        <button class:active={tab==='assistant'} on:click={()=>tab='assistant'}><Sparkles size={14}/>AI Assistant {#if hasLlmKey}<i></i>{/if}</button>
        <button class:active={tab==='about'} on:click={()=>tab='about'}><Info size={14}/>About</button>
      </nav>
      <main>
        {#if tab==='general'}
          <div class="page-heading"><h3>General</h3><p>Workspace behavior and project defaults.</p></div>
          <div class="setting-group"><h4>WORKSPACE</h4><label class="toggle"><span><strong>Autosave projects</strong><small>Persist script edits as you work</small></span><input type="checkbox" checked={settings.autosave} on:change={(e)=>onUpdate({autosave:e.currentTarget.checked})}/><i></i></label><label class="toggle"><span><strong>Show comfort safe zones</strong><small>Overlay window-edge protection guides</small></span><input type="checkbox" checked={settings.showSafeZones} on:change={(e)=>onUpdate({showSafeZones:e.currentTarget.checked})}/><i></i></label><label class="toggle"><span><strong>Reduce interface motion</strong><small>Minimize decorative transitions</small></span><input type="checkbox" checked={settings.reduceMotion} on:change={(e)=>onUpdate({reduceMotion:e.currentTarget.checked})}/><i></i></label></div>
          <div class="setting-group"><h4>NEW PROJECTS</h4><div class="read-row"><span><strong>Encoding profile</strong><small>Selected and validated by the production engine</small></span><em>Engine configured</em></div><label class="select-row"><span><strong>Anaglyph matrix</strong><small>Calibrated keeps colour and reduces ghosting; basic gives the strong classic red/cyan separation</small></span><select value={settings.anaglyphMode} on:change={(e)=>onUpdate({anaglyphMode:e.currentTarget.value as AppSettings['anaglyphMode']})}><option value="calibrated">Calibrated (Dubois)</option><option value="basic">Basic red/cyan channels</option></select></label><label class="toggle"><span><strong>Swap eyes</strong><small>Use if depth looks inverted through your glasses</small></span><input type="checkbox" checked={settings.swapEyes} on:change={(e)=>onUpdate({swapEyes:e.currentTarget.checked})}/><i></i></label><label class="select-row"><span><strong>Preview clip quality</strong><small>Lower renders a playable shot sooner; full matches what the export will look like</small></span><select value={String(settings.previewClipWidth)} on:change={(e)=>onUpdate({previewClipWidth:Number(e.currentTarget.value) as AppSettings['previewClipWidth']})}><option value="640">Fast · 640 px</option><option value="960">Balanced · 960 px</option><option value="1280">Sharp · 1280 px</option><option value="0">Full working-copy width</option></select></label></div>
        {:else if tab==='performance'}
          <div class="page-heading"><h3>Performance</h3><p>Choose how local depth inference uses your hardware.</p></div>
          <div class="hardware-card"><span><Cpu size={18}/></span><div><strong>Runtime-validated acceleration</strong><small>The engine verifies the requested device when a job starts</small></div><em>LOCAL</em></div>
          <div class="setting-group"><h4>PROCESSING DEVICE</h4><div class="radio-grid">{#each [['auto','Automatic','Uses fastest available device'],['cuda','NVIDIA CUDA','Maximum practical speed'],['cpu','CPU only','Portable, considerably slower']] as option}<label class:active={settings.device===option[0]}><input type="radio" name="device" value={option[0]} checked={settings.device===option[0]} on:change={()=>onUpdate({device:option[0] as AppSettings['device']})}/><span><strong>{option[1]}</strong><small>{option[2]}</small></span>{#if settings.device===option[0]}<Check size={13}/>{/if}</label>{/each}</div></div>
          <div class="setting-group"><h4>DEPTH ENGINE</h4><div class="radio-grid">{#each [['video-depth-anything-small','Neural depth model','Highest quality. Needs PyTorch and a Video Depth Anything Small checkpoint; falls back to image analysis when either is missing.'],['monocular-cues','Image analysis','Built in, no model required. Derives depth from detail, framing, and atmospheric cues. Exports fine; the neural model resolves fine detail better.']] as option}<label class:active={settings.depthEngine===option[0]}><input type="radio" name="depth-engine" value={option[0]} checked={settings.depthEngine===option[0]} on:change={()=>onUpdate({depthEngine:option[0] as AppSettings['depthEngine']})}/><span><strong>{option[1]}</strong><small>{option[2]}</small></span>{#if settings.depthEngine===option[0]}<Check size={13}/>{/if}</label>{/each}</div></div>
          <div class="storage-note"><HardDrive size={14}/><p><strong>Cache location</strong><span>Stored inside each selected project folder for deterministic resume</span></p><em>Project scoped</em></div>
        {:else if tab==='assistant'}
          <div class="page-heading"><h3>AI Assistant</h3><p>Optional shot interpretation. Rendering never depends on it.</p></div>
          <div class="assistant-banner"><span><Bot size={20}/></span><div><strong>Creative advice, bounded by design</strong><p>The assistant can recommend one tested Director preset and explain why. It cannot emit unrestricted disparity values or bypass Comfort Guard.</p></div></div>
          <div class="setting-group"><label class="toggle prominent" class:disabled={!hasLlmKey||!credentialStorageAvailable}><span><strong>Enable AI Assistant</strong><small>{!credentialStorageAvailable?'Available only in the native desktop app':hasLlmKey?'Allow optional OpenAI recommendations':'Configure an API key before enabling recommendations'}</small></span><input type="checkbox" checked={settings.llmEnabled&&hasLlmKey&&credentialStorageAvailable} disabled={!hasLlmKey||!credentialStorageAvailable} on:change={(e)=>onUpdate({llmEnabled:e.currentTarget.checked})}/><i></i></label></div>
          <div class="setting-group"><h4>PROVIDER</h4><label class="select-row"><span><strong>Provider</strong><small>Optional metadata-only recommendations</small></span><select disabled><option>OpenAI</option></select></label><label class="select-row"><span><strong>Model</strong><small>Application release default</small></span><select disabled><option>gpt-5.6-terra</option></select></label></div>
          {#if credentialStorageAvailable}
            <div class="setting-group key-group"><div class="key-header"><h4>API CREDENTIAL</h4>{#if hasLlmKey}<span><ShieldCheck size={11}/> Secured on this device</span>{/if}</div>
            {#if hasLlmKey&&!replacingKey}
              <div class="saved-key"><span><KeyRound size={15}/></span><div><strong>OpenAI key configured</strong><small>Stored by the native credential provider; never written to project files</small></div><button class="replace" on:click={()=>{replacingKey=true;apiKey=''}}>Replace</button><button class="delete" on:click={onDeleteKey} disabled={keyBusy} aria-label="Delete API key"><Trash2 size={13}/></button></div>
            {:else}
              <label class="key-input"><LockKeyhole size={14}/><input type={revealKey?'text':'password'} bind:value={apiKey} autocomplete="off" spellcheck="false" placeholder="sk-proj-…" aria-label="OpenAI API key"/><button on:click={()=>revealKey=!revealKey} aria-label="Show or hide API key"><Eye size={13}/></button></label>
              <div class="key-actions"><span>16–1024 characters; whitespace and control characters are rejected.</span><div>{#if replacingKey}<button class="cancel-key" on:click={()=>{replacingKey=false;apiKey=''}}>Cancel</button>{/if}<button on:click={saveKey} disabled={!keyIsValid||keyBusy}>{#if keyBusy}<RefreshCw class="spin" size={12}/>{:else}<KeyRound size={12}/>{/if} Save securely</button></div></div>
            {/if}
            </div>
            <div class="test-row"><div><strong>Connection test</strong><span>Makes one small API request; provider charges may apply.</span></div><button class:success={testState==='success'} class:error={testState==='error'} on:click={onTestLlm} disabled={!hasLlmKey||testState==='testing'}>{#if testState==='testing'}<RefreshCw class="spin" size={12}/> Testing…{:else if testState==='success'}<Check size={12}/> Connected{:else if testState==='error'}Retry test{:else}Test connection{/if}</button></div>
          {:else}
            <div class="privacy-note demo-key-note"><ShieldCheck size={14}/><p><strong>Desktop credential storage required</strong><span>The browser demo never accepts API keys or contacts OpenAI. Install the native desktop build to store a key in Windows Credential Manager.</span></p></div>
          {/if}
          <div class="privacy-note"><ShieldCheck size={14}/><p><strong>Privacy & cost</strong><span>When you use Ask AI, concise shot metadata—not video frames, audio, paths, or depth maps—is sent to OpenAI. Requests use your account and may incur API charges.</span></p></div>
        {:else}
          <div class="about-page"><BrandMark size={46}/><h3>Parallax Forge</h3><p>AI Stereo Director · Version 0.1.0 prototype</p><div><span>Offline-first</span><i></i><span>Windows 11 x64</span><i></i><span>Production preview</span></div></div>
          <div class="about-copy"><p>Built for deterministic, resumable 2D-to-stereo authoring. Parallax Forge uses an independently testable Director, Comfort Guard, and occlusion-aware rendering pipeline.</p><p>Dependency notices are included with the application distribution.</p></div>
        {/if}
      </main>
    </div>
    <footer><span>Settings are saved automatically on this device.</span><button on:click={onClose}>Done</button></footer>
  </div>
</div>

<style>
  .modal-backdrop{position:fixed;inset:0;z-index:80;display:grid;place-items:center;background:rgba(2,4,7,.74);backdrop-filter:blur(7px);padding:20px}.settings-modal{width:min(780px,94vw);height:min(610px,90vh);display:flex;flex-direction:column;border:1px solid #323945;background:#0d1016;border-radius:12px;box-shadow:0 35px 100px rgba(0,0,0,.65);overflow:hidden}.settings-modal>header{height:59px;display:flex;align-items:center;justify-content:space-between;padding:0 17px;border-bottom:1px solid var(--line-soft);background:#10131a}.settings-modal>header span{font-size:10px;color:#5c6675;letter-spacing:.16em}.settings-modal>header h2{font-size:15px;margin:4px 0 0;color:#dce1e8}.settings-modal>header button{width:28px;height:28px;display:grid;place-items:center;border:0;background:transparent;color:#677180;border-radius:6px}.settings-modal>header button:hover{background:#1b2028;color:#c4cad2}.settings-body{flex:1;min-height:0;display:grid;grid-template-columns:165px 1fr}.settings-body>nav{padding:11px 8px;border-right:1px solid var(--line-soft);background:#0a0d12}.settings-body>nav button{width:100%;height:34px;display:flex;align-items:center;gap:9px;border:0;background:transparent;color:#687382;border-radius:6px;padding:0 9px;font-size:11px;text-align:left;position:relative}.settings-body>nav button:hover{color:#aeb6c1;background:#11161d}.settings-body>nav button.active{color:#d2d8e1;background:#19201f}.settings-body>nav button.active :global(svg){color:#66d1c3}.settings-body>nav button i{position:absolute;right:9px;width:5px;height:5px;border-radius:50%;background:#62c9ad;box-shadow:0 0 6px #62c9ad}.settings-body>main{overflow:auto;padding:22px 26px}.page-heading{margin-bottom:21px}.page-heading h3{margin:0 0 6px;font-size:17px;color:#e0e4ea;font-weight:620}.page-heading p{margin:0;font-size:11px;color:#687281}.setting-group{margin:0 0 19px;padding:13px;border:1px solid #232a33;background:#10141a;border-radius:8px}.setting-group h4,.key-header h4{margin:0 0 10px;font-size:10px;color:#5e6877;letter-spacing:.14em}.toggle,.select-row{min-height:40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1d232b}.toggle:last-child,.select-row:last-child{border-bottom:0}.toggle>span,.select-row>span{display:grid;gap:3px}.toggle strong,.select-row strong{font-size:11px;color:#b9c0ca;font-weight:560}.toggle small,.select-row small{font-size:10px;color:#5e6876}.toggle input{position:absolute;opacity:0}.toggle>i{width:29px;height:16px;border-radius:9px;background:#282f39;position:relative}.toggle>i::after{content:'';position:absolute;width:12px;height:12px;left:2px;top:2px;border-radius:50%;background:#76818f;transition:.18s}.toggle input:checked+i{background:#2b6760}.toggle input:checked+i::after{left:15px;background:#72ddd0}.select-row select{min-width:138px;height:28px;border:1px solid #303844;background:#161b22;color:#aeb6c0;border-radius:6px;padding:0 8px;font-size:11px;outline:0}.hardware-card{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:10px;padding:12px;border:1px solid rgba(86,197,181,.24);background:rgba(51,112,104,.08);border-radius:8px;margin-bottom:18px}.hardware-card>span{width:34px;height:34px;display:grid;place-items:center;border-radius:7px;background:#142420;color:#62cdbc}.hardware-card>div{display:grid;gap:4px}.hardware-card strong{font-size:11px;color:#c6ccd4}.hardware-card small{font-size:10px;color:#648078}.hardware-card em{font-style:normal;font-size:10px;color:#64c9b0;background:rgba(84,192,160,.1);padding:4px 6px;border-radius:8px}.radio-grid{display:grid;gap:6px}.radio-grid label{display:grid;grid-template-columns:1fr auto;align-items:center;padding:9px;border:1px solid #252c35;border-radius:6px}.radio-grid label.active{border-color:#3b706a;background:#14201f}.radio-grid input{position:absolute;opacity:0}.radio-grid span{display:grid;gap:3px}.radio-grid strong{font-size:11px;color:#acb4bf}.radio-grid small{font-size:10px;color:#5a6573}.radio-grid :global(svg){color:#63cfc1}.storage-note{display:grid;grid-template-columns:auto 1fr auto;gap:9px;align-items:center;padding:10px;color:#697483}.storage-note p{display:grid;gap:3px;margin:0}.storage-note strong{font-size:11px;color:#afb6c0}.storage-note span{font-size:10px}.assistant-banner{display:flex;gap:11px;padding:13px;border:1px solid rgba(142,124,235,.25);background:linear-gradient(110deg,rgba(110,87,211,.1),rgba(38,31,67,.04));border-radius:8px;margin-bottom:17px}.assistant-banner>span{width:35px;height:35px;display:grid;place-items:center;border-radius:8px;color:#ad9df3;background:rgba(121,99,218,.12);flex:0 0 auto}.assistant-banner>div{display:grid;gap:5px}.assistant-banner strong{font-size:11px;color:#cbc7df}.assistant-banner p{margin:0;font-size:10px;color:#757a8d;line-height:1.5}.prominent{border:0}.key-header{display:flex;justify-content:space-between}.key-header span{display:flex;align-items:center;gap:4px;color:#62c7a9;font-size:10px}.key-input{height:35px;display:flex;align-items:center;gap:8px;border:1px solid #303844;background:#0c1015;border-radius:6px;padding:0 8px;color:#66717e}.key-input:focus-within{border-color:#7365bd}.key-input input{flex:1;border:0;outline:0;background:transparent;color:#c9cfd7;font-family:var(--font-mono);font-size:11px}.key-input button{border:0;background:none;color:#626c79}.key-actions{display:flex;justify-content:space-between;align-items:center;margin-top:8px}.key-actions>span{font-size:10px;color:#596372}.key-actions button,.test-row button{display:flex;align-items:center;gap:5px;border:1px solid #7769c3;background:#6355aa;color:#edeafa;border-radius:5px;padding:6px 9px;font-size:10px}.key-actions button:disabled{opacity:.4}.saved-key{display:grid;grid-template-columns:auto 1fr auto auto;gap:8px;align-items:center}.saved-key>span{width:30px;height:30px;display:grid;place-items:center;background:#14231f;color:#60c6a6;border-radius:7px}.saved-key>div{display:grid;gap:3px}.saved-key strong{font-size:11px;color:#b9c0c9}.saved-key small{font-size:10px;color:#5f6977}.saved-key button{height:25px;border:1px solid #303743;background:#151a21;color:#88929e;border-radius:5px;font-size:10px;padding:0 7px}.saved-key .delete{width:25px;display:grid;place-items:center;padding:0;color:#ae6c70}.test-row{display:flex;align-items:center;justify-content:space-between;padding:11px 2px 17px}.test-row>div{display:grid;gap:3px}.test-row strong{font-size:11px;color:#acb4bf}.test-row span{font-size:10px;color:#5d6775}.test-row button{border-color:#3b4350;background:#171c23;color:#9ba4b0}.test-row button.success{border-color:#356557;color:#67c5a3}.test-row button.error{border-color:#784047;color:#d8787c}.privacy-note{display:flex;gap:9px;padding:11px;color:#6f7b89;background:#11161d;border-radius:7px}.privacy-note :global(svg){color:#62bca8;flex:0 0 auto}.privacy-note p{display:grid;gap:4px;margin:0}.privacy-note strong{font-size:11px;color:#aab2bd}.privacy-note span{font-size:10px;line-height:1.5}:global(.spin){animation:spin .9s linear infinite}.about-page{text-align:center;display:grid;place-items:center;padding:25px 0 19px}.about-page h3{font-size:18px;margin:12px 0 4px}.about-page p{font-size:11px;color:#687281}.about-page>div{display:flex;align-items:center;gap:7px;color:#596472;font-size:10px}.about-page>div i{width:3px;height:3px;background:#515a67;border-radius:50%}.about-copy{border-top:1px solid #222832;padding-top:16px;color:#697382;font-size:11px;line-height:1.6}.settings-modal>footer{height:49px;display:flex;align-items:center;justify-content:space-between;padding:0 16px;border-top:1px solid var(--line-soft);background:#10131a}.settings-modal>footer span{font-size:10px;color:#535d6b}.settings-modal>footer button{border:0;background:#dce3e5;color:#11161a;border-radius:6px;padding:7px 15px;font-size:11px;font-weight:700}@keyframes spin{to{transform:rotate(360deg)}}
  .read-row{min-height:44px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1d232b}.read-row>span{display:grid;gap:3px}.read-row strong{font-size:11px;color:#b9c0ca;font-weight:560}.read-row small{font-size:10px;color:#667180}.read-row em,.storage-note em{font-style:normal;font-size:10px;color:#7c8795;border:1px solid #303844;border-radius:6px;padding:6px 8px}.toggle.disabled{opacity:.55}.key-actions>div{display:flex;align-items:center;gap:6px}.key-actions .cancel-key{border-color:#3b4350;background:#171c23;color:#9ba4b0}.about-copy p:last-child{color:#7e8998}
  @media(max-width:650px){.settings-body{grid-template-columns:55px 1fr}.settings-body>nav button{justify-content:center}.settings-body>nav button{font-size:0}.settings-body>nav button i{right:5px}.settings-body>main{padding:18px 15px}.modal-backdrop{padding:8px}}
</style>
