<script lang="ts">
  import { Check, ChevronDown, ChevronUp, CircleStop, Cpu, ListChecks, RotateCw, X } from 'lucide-svelte';
  import type { QueueItem } from '../types';

  export let queue: QueueItem[] = [];
  export let onCancel: (id: string) => void;
  export let onDismiss: (id: string) => void;
  let open = false;
  $: active = queue.find((item) => item.status === 'processing') ?? queue[0];
  $: working = queue.filter((item)=>item.status==='processing'||item.status==='waiting').length;
</script>

{#if queue.length}
  <div class:open class="queue-shell" aria-live="polite">
    <button class="queue-summary" on:click={() => (open=!open)} aria-expanded={open}>
      <span class:complete={active?.status==='complete'} class:failed={active?.status==='failed'} class="status-icon">
        {#if active?.status==='processing'}<RotateCw size={13}/>{:else if active?.status==='complete'}<Check size={13}/>{:else}<ListChecks size={13}/>{/if}
      </span>
      <span class="summary-copy"><strong>{active?.status==='processing' ? active.stage : active?.title}</strong><small>{working ? `${working} active job${working>1?'s':''}` : `${queue.length} recent job${queue.length>1?'s':''}`}</small></span>
      {#if active?.status==='processing'}<span class="summary-progress">{Math.round(active.progress*100)}%</span>{/if}
      {#if open}<ChevronDown size={13}/>{:else}<ChevronUp size={13}/>{/if}
      {#if active?.status==='processing'}<i class="progress" style={`width:${active.progress*100}%`}></i>{/if}
    </button>
    {#if open}
      <div class="queue-list">
        <header><span><Cpu size={12}/> Processing queue</span><em>{working ? 'Worker active' : 'Idle'}</em></header>
        {#each queue as item}
          <div class="queue-item">
            <span class:spinning={item.status==='processing'} class="item-state">{#if item.status==='complete'}<Check size={12}/>{:else if item.status==='failed'}<X size={12}/>{:else}<RotateCw size={12}/>{/if}</span>
            <div><strong>{item.title}</strong><span>{item.stage} · {item.status==='processing' ? `${Math.round(item.progress*100)}%` : item.detail}</span>{#if item.status==='processing'}<i><b style={`width:${item.progress*100}%`}></b></i>{/if}</div>
            {#if item.status==='processing'}<button on:click={()=>onCancel(item.id)} title="Cancel job" aria-label="Cancel job"><CircleStop size={13}/></button>{:else}<button on:click={()=>onDismiss(item.id)} title="Dismiss job" aria-label="Dismiss job"><X size={12}/></button>{/if}
          </div>
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .queue-shell{position:fixed;right:14px;bottom:13px;width:275px;z-index:50;filter:drop-shadow(0 16px 35px rgba(0,0,0,.46))}.queue-summary{width:100%;height:45px;display:grid;grid-template-columns:auto 1fr auto auto;align-items:center;gap:8px;text-align:left;border:1px solid #2d3540;background:rgba(18,22,29,.96);backdrop-filter:blur(16px);color:#7d8795;border-radius:9px;padding:6px 9px;position:relative;overflow:hidden}.queue-shell.open .queue-summary{border-radius:0 0 9px 9px}.status-icon{width:27px;height:27px;display:grid;place-items:center;border-radius:7px;background:rgba(90,201,188,.1);color:#67d1c3}.status-icon :global(svg){animation:spin 1.6s linear infinite}.status-icon.complete{color:#5cc7a5}.status-icon.complete :global(svg),.status-icon.failed :global(svg){animation:none}.status-icon.failed{color:#e27777;background:rgba(215,92,92,.1)}.summary-copy{display:grid;gap:3px;min-width:0}.summary-copy strong{font-size:11px;color:#cbd1da;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.summary-copy small{font-size:10px;color:#5a6573}.summary-progress{font-size:11px;color:#8b96a4;font-variant-numeric:tabular-nums}.progress{position:absolute;left:0;bottom:0;height:2px;background:#62d1c4}.queue-list{border:1px solid #303844;border-bottom:0;background:rgba(13,16,22,.98);border-radius:9px 9px 0 0;overflow:hidden}.queue-list header{height:34px;display:flex;align-items:center;justify-content:space-between;padding:0 10px;border-bottom:1px solid #222832}.queue-list header span{display:flex;align-items:center;gap:6px;color:#9aa3af;font-size:11px}.queue-list header em{font-style:normal;color:#58bcae;font-size:10px;text-transform:uppercase;letter-spacing:.08em}.queue-item{display:grid;grid-template-columns:auto 1fr auto;gap:8px;padding:9px;border-bottom:1px solid #1d222a}.item-state{width:22px;height:22px;display:grid;place-items:center;color:#63cabb;background:#172320;border-radius:6px}.item-state.spinning :global(svg){animation:spin 1.5s linear infinite}.queue-item>div{display:grid;gap:3px}.queue-item strong{font-size:11px;color:#bac1cb}.queue-item span{font-size:10px;color:#5d6775}.queue-item i{height:2px;background:#282f38;border-radius:2px;overflow:hidden}.queue-item i b{display:block;height:100%;background:#61cfc1}.queue-item button{width:23px;height:23px;display:grid;place-items:center;border:0;background:transparent;color:#5b6573;border-radius:4px}.queue-item button:hover{color:#d77777;background:#25191c}@keyframes spin{to{transform:rotate(360deg)}}
</style>
