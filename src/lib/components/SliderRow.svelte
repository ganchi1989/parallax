<script lang="ts">
  import { RotateCcw } from 'lucide-svelte';
  export let label: string;
  export let value: number;
  export let min: number;
  export let max: number;
  export let step: number;
  export let unit = '';
  export let hint = '';
  export let disabled = false;
  export let onChange: (value: number) => void;
  export let onReset: (() => void) | undefined = undefined;
  $: progress = Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
  $: display = unit === '%' ? `${Math.round(value * 100)}` : unit === 'px' ? value.toFixed(3) : step < .01 ? value.toFixed(3) : value.toFixed(2);
  function commit(next:number){if(Number.isFinite(next))onChange(next)}
</script>

<div class:disabled class="slider-row">
  <div class="slider-head">
    <span class="control-label">{label}{#if hint}<span title={hint}>?</span>{/if}</span>
    <div class="value"><input type="number" {min} {max} {step} {value} {disabled} on:change={(e) => commit(e.currentTarget.valueAsNumber)} aria-label={`${label} value`} /><i>{unit}</i>{#if onReset}<button {disabled} on:click={onReset} title={`Reset ${label}`} aria-label={`Reset ${label}`}><RotateCcw size={10}/></button>{/if}</div>
  </div>
  <input class="range" style={`--progress:${progress}%`} type="range" {min} {max} {step} {value} {disabled} on:input={(e) => commit(e.currentTarget.valueAsNumber)} aria-label={label} />
</div>

<style>
  .slider-row{display:grid;gap:7px}.slider-row.disabled{opacity:.48}.slider-head{display:flex;align-items:center;justify-content:space-between}.control-label{font-size:11px;color:#969fad;display:flex;align-items:center;gap:5px}.control-label span{width:11px;height:11px;display:grid;place-items:center;border:1px solid #39414e;border-radius:50%;font-size:10px;color:#596474}.value{display:flex;align-items:center;height:20px;border:1px solid #242b35;background:#10141a;border-radius:5px;overflow:hidden}.value input{width:42px;border:0;outline:0;background:transparent;text-align:right;color:#c9d0d9;font-size:11px;font-variant-numeric:tabular-nums;padding:0 2px;appearance:textfield;-moz-appearance:textfield}.value input::-webkit-inner-spin-button{display:none}.value i{font-style:normal;font-size:10px;color:#596372;padding:0 5px 0 2px}.value button{height:100%;width:21px;border:0;border-left:1px solid #252c35;background:transparent;color:#4d5866;display:grid;place-items:center}.value button:hover{color:#92a0ad;background:#171c23}
  .range{appearance:none;width:100%;height:3px;border-radius:4px;outline:0;background:linear-gradient(to right,#62d1c4 0 var(--progress),#262d37 var(--progress) 100%)}.range::-webkit-slider-thumb{appearance:none;width:11px;height:11px;border-radius:50%;background:#dce5e6;border:2px solid #14201f;box-shadow:0 0 0 1px #65d3c6,0 1px 4px #000;cursor:ew-resize}.range::-moz-range-thumb{width:9px;height:9px;border-radius:50%;background:#dce5e6;border:2px solid #65d3c6;cursor:ew-resize}.range:disabled::-webkit-slider-thumb,.range:disabled::-moz-range-thumb{cursor:not-allowed;filter:saturate(.2)}
</style>
