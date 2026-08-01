<script lang="ts">
  export let variant = 1;
  export let mode: 'original' | 'anaglyph' | 'depth' = 'original';
  export let label = '';

  const uid = `scene-${Math.random().toString(36).slice(2)}`;
  $: hue = ((variant - 1) * 19) % 80;
</script>

<svg
  class:depth={mode === 'depth'}
  class="scene"
  viewBox="0 0 960 540"
  preserveAspectRatio="xMidYMid slice"
  role="img"
  aria-label={label || 'Stylized source-frame preview'}
  style={`--hue:${hue}deg`}
>
  <defs>
    <linearGradient id={`${uid}-sky`} x1="0" y1="0" x2="0" y2="1">
      <stop stop-color={mode === 'depth' ? '#26155d' : '#303742'} />
      <stop offset=".52" stop-color={mode === 'depth' ? '#cc3977' : '#a86f60'} />
      <stop offset="1" stop-color={mode === 'depth' ? '#f6d55c' : '#d5ae7c'} />
    </linearGradient>
    <linearGradient id={`${uid}-ground`} x1=".2" y1="0" x2=".75" y2="1">
      <stop stop-color={mode === 'depth' ? '#44d1b0' : '#786758'} />
      <stop offset="1" stop-color={mode === 'depth' ? '#162a61' : '#24272c'} />
    </linearGradient>
    <radialGradient id={`${uid}-sun`}>
      <stop stop-color="#fff7d1" />
      <stop offset="1" stop-color="#ecb97d" />
    </radialGradient>
    <filter id={`${uid}-red`} color-interpolation-filters="sRGB">
      <feColorMatrix values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .52 0" />
    </filter>
    <filter id={`${uid}-cyan`} color-interpolation-filters="sRGB">
      <feColorMatrix values="0 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 .52 0" />
    </filter>
    <filter id={`${uid}-grain`}>
      <feTurbulence baseFrequency=".7" numOctaves="2" seed={variant * 3} stitchTiles="stitch" />
      <feColorMatrix type="saturate" values="0" />
      <feComponentTransfer><feFuncA type="table" tableValues="0 .1" /></feComponentTransfer>
    </filter>
    <symbol id={`${uid}-art`} viewBox="0 0 960 540">
      <rect width="960" height="540" fill={`url(#${uid}-sky)`} />
      <circle cx={720 - variant * 6} cy={135 + variant * 2} r="55" fill={`url(#${uid}-sun)`} opacity=".76" />
      <path d="M0 320 122 208l82 82 94-150 128 165 120-103 138 100 98-116 178 133v221H0Z" fill="#3b4145" opacity=".58" />
      <path d="M0 354 155 252l78 64 93-120 127 135 94-74 130 69 108-82 175 116v180H0Z" fill="#4e4c48" opacity=".86" />
      <path d="M0 383c159-44 224 9 353-26 122-33 212-1 306-2 115-1 192-31 301-1v186H0Z" fill={`url(#${uid}-ground)`} />
      <path d="M350 540c21-75 75-139 165-177 73-31 141-39 216-34-118 44-214 108-275 211Z" fill="#d5b18a" opacity=".48" />
      <path d="M0 394c76-64 159-76 255-46l-25 192H0Z" fill="#202326" />
      <path d="M775 337c71-6 135 17 185 55v148H803Z" fill="#242529" opacity=".95" />
      <g transform={`translate(${470 + (variant % 3) * 16} 284)`}>
        <circle cx="0" cy="0" r="20" fill="#1c1c20" />
        <path d="m-18 24-19 120h77L22 24 0 13Z" fill="#24252a" />
        <path d="m-8 43-5 78 23-4 4-75Z" fill="#8c715d" opacity=".55" />
      </g>
      <g opacity=".62" fill="#111418">
        <path d="m114 353 7-77 7 77Z" />
        <path d="m145 353 9-105 9 105Z" />
        <path d="m691 348 6-61 6 61Z" />
      </g>
      <rect width="960" height="540" fill="#fff" filter={`url(#${uid}-grain)`} opacity=".18" />
    </symbol>
  </defs>

  {#if mode === 'anaglyph'}
    <rect width="960" height="540" fill="#101316" />
    <use href={`#${uid}-art`} x="-4" filter={`url(#${uid}-red)`} style="mix-blend-mode:screen" />
    <use href={`#${uid}-art`} x="4" filter={`url(#${uid}-cyan)`} style="mix-blend-mode:screen" />
  {:else}
    <use href={`#${uid}-art`} />
  {/if}
  <rect width="960" height="540" fill="url(#vignette)" opacity=".18" />
</svg>

<style>
  .scene { width: 100%; height: 100%; display: block; filter: hue-rotate(var(--hue)) saturate(.82) contrast(1.03); }
  .scene.depth { filter: saturate(1.35) contrast(1.04); }
</style>
