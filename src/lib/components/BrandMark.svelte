<script lang="ts">
  export let compact = false;
  export let size = 28;
</script>

<div class:compact class="brand" aria-label="Parallax Forge">
  <svg
    class="mark"
    style:width={`${size}px`}
    style:height={`${size}px`}
    viewBox="0 0 32 32"
    fill="none"
    aria-hidden="true"
  >
    <defs>
      <linearGradient id="brand-a" x1="4" y1="4" x2="27" y2="29" gradientUnits="userSpaceOnUse">
        <stop stop-color="#69E0D0" />
        <stop offset="1" stop-color="#7771F6" />
      </linearGradient>
      <linearGradient id="brand-b" x1="24" y1="4" x2="10" y2="28" gradientUnits="userSpaceOnUse">
        <stop stop-color="#F16F8C" />
        <stop offset="1" stop-color="#6ADCCD" />
      </linearGradient>
    </defs>
    <path d="M4.5 7.5 13.2 3v19.1l-8.7 5.4v-20Z" fill="url(#brand-a)" />
    <path d="m18.7 9.8 8.8-5.3v20l-8.8 4.5V9.8Z" fill="url(#brand-b)" opacity=".92" />
    <path d="m13.2 8.1 5.5 1.7V29l-5.5-6.9v-14Z" fill="#B9FFF5" opacity=".66" />
  </svg>
  {#if !compact}
    <div class="wordmark">
      <strong>PARALLAX <span>FORGE</span></strong>
      <small>AI STEREO DIRECTOR</small>
    </div>
  {/if}
</div>

<style>
  .brand {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    min-width: max-content;
    user-select: none;
  }
  .mark { flex: 0 0 auto; filter: drop-shadow(0 0 12px rgba(83, 214, 200, 0.18)); }
  .wordmark { display: grid; line-height: 1; gap: 4px; }
  strong { color: #f0f3fa; font-size: 11px; letter-spacing: .11em; font-weight: 720; }
  strong span { color: #73d6c9; }
  small { color: #70798a; font-size:10px; letter-spacing: .24em; font-weight: 650; }
</style>
