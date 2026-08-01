<script lang="ts">
  import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-svelte';
  import type { Toast } from '../types';
  export let toasts: Toast[] = [];
  export let onDismiss: (id: string) => void;
</script>

<div class="toast-stack" aria-live="polite" aria-relevant="additions">
  {#each toasts as toast (toast.id)}
    <div class={`toast ${toast.kind}`} role={toast.kind==='error' ? 'alert' : 'status'}>
      <span class="toast-icon">{#if toast.kind==='success'}<CheckCircle2 size={15}/>{:else if toast.kind==='error'}<XCircle size={15}/>{:else if toast.kind==='warning'}<AlertTriangle size={15}/>{:else}<Info size={15}/>{/if}</span>
      <div><strong>{toast.title}</strong>{#if toast.message}<span>{toast.message}</span>{/if}</div>
      <button on:click={()=>onDismiss(toast.id)} aria-label="Dismiss notification"><X size={12}/></button>
    </div>
  {/each}
</div>

<style>
  .toast-stack{position:fixed;right:14px;top:58px;z-index:100;display:grid;gap:7px;width:min(330px,calc(100vw - 28px));pointer-events:none}.toast{pointer-events:auto;display:grid;grid-template-columns:auto 1fr auto;align-items:start;gap:9px;padding:10px;border:1px solid #313944;background:rgba(18,22,29,.96);backdrop-filter:blur(18px);border-radius:8px;box-shadow:0 13px 35px rgba(0,0,0,.38);animation:arrive .22s ease-out}.toast-icon{width:24px;height:24px;display:grid;place-items:center;border-radius:6px;color:#77cfc4;background:rgba(89,193,180,.1)}.toast>div{display:grid;gap:3px;padding-top:1px}.toast strong{font-size:11px;color:#d1d6de}.toast span{font-size:10px;line-height:1.45;color:#6e7886}.toast button{width:20px;height:20px;display:grid;place-items:center;border:0;background:transparent;color:#5b6572;border-radius:4px}.toast.error .toast-icon{color:#e17979;background:rgba(218,97,97,.1)}.toast.warning .toast-icon{color:#dda06b;background:rgba(214,143,82,.1)}.toast.success .toast-icon{color:#64caa9;background:rgba(77,184,145,.1)}@keyframes arrive{from{opacity:0;transform:translateY(-5px) scale(.98)}}
</style>
