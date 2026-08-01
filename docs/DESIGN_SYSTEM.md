# Product Design System

Parallax Forge should feel like a focused finishing suite: cinematic, precise, calm under load, and trustworthy enough for long renders. It is not a generic dashboard and it does not imitate another editor's brand. This document combines the implemented design system with pre-release quality targets; the final quality gate remains open.

## Reference patterns

The layout borrows proven interaction patterns from open-source editing tools:

- **Kdenlive:** a legible workspace/monitor/timeline/status hierarchy, task-oriented layouts, and a monitor ruler closely tied to the playhead.
- **Shotcut:** persistent jobs feedback and compact property/filter/export panels that keep long operations visible.
- **LosslessCut:** direct zoomable range interaction with minimal ceremony for clip-focused work.
- **Olive:** a restrained, precision-oriented inspector that avoids burying the canvas in cards.

References: <https://docs.kdenlive.org/en/user_interface.html>, <https://www.shotcut.org/>, <https://github.com/mifi/lossless-cut>, and <https://github.com/olive-editor/olive>.

These are workflow references, not visual assets or code sources. Parallax Forge's structure, styling, iconography, copy, and stereo-specific interactions remain original.

## Workspace hierarchy

1. **Title/command bar:** project identity, save state, processing state, shortcuts, settings, and export. Undo/redo and help remain available through documented keyboard shortcuts.
2. **Shot bin:** scan-friendly preview or honest numbered placeholder, duration, preset, confidence, and warning state.
3. **Monitor:** the largest visual mass, with Original/Anaglyph/Split/Depth modes, safe-zone overlay, transport, and unambiguous timecode.
4. **Stereo Director:** selected-shot preset, bounded parameters, AI recommendation, explanation, and Comfort Guard state.
5. **Shot timeline:** duration-proportional shots, playhead, zoom, selection, and warning/emphasis encoding.
6. **Queue/status:** persistent stage, measured progress, cancellation, and recent completion/failure affordances.

The monitor and Director are the product's center. General media-management controls never compete with them.

## Visual language

- Near-black graphite surfaces with small luminance steps establish panel depth.
- Cyan and warm red echo anaglyph channels but are used sparingly; warning semantics remain distinguishable without color.
- One high-energy violet/blue action accent identifies the Director and primary actions.
- Thin borders, selective bloom, and fine grid/ruler detail create technical precision without decorative noise.
- Typography uses a neutral UI face; tabular numerals are mandatory for timecode and metrics.
- Corners are controlled and relatively tight. Avoid a page made of oversized rounded cards.
- Empty states use code-native stereo artwork, never generic stock photos.

## Interaction rules

- Selection updates the shot bin, timeline, monitor range, and inspector as one atomic state change.
- Sliders always pair with an editable value and explicit safe range; keyboard arrows make fine adjustments.
- Manual changes are visibly marked and offer reset-to-directed behavior.
- The Comfort Guard explains clamps in plain language next to the affected control.
- “Ask AI” is explicit, never automatic, and previews a recommendation before applying it.
- Long work is cancellable, survives project/home navigation during the current app session, and remains visible in the queue.
- Every icon-only control has a tooltip and accessible name.
- Destructive or cost-incurring actions require clear language and an appropriate confirmation.

## Motion

Use motion to explain state: panel entrance, playhead movement, progress, compare-divider response, and saved/guarded feedback. Keep transitions near 120–220 ms and disable nonessential motion under `prefers-reduced-motion` or the app setting. Avoid looping decoration outside active processing.

## Responsive behavior

- **1440×900 and above:** full four-zone workstation.
- **1280×720:** narrower shot bin and inspector, compact labels, timeline preserved.
- **Below the supported desktop minimum:** the shot bin is hidden first and the monitor/Director remain prioritized; these sizes are not a certified layout.

## Quality gate

Before release, capture the welcome, imported/analyzing, editor, LLM recommendation, guarded override, export, active queue, completion, and failure states at 1440×900 and 1280×720. Check clipping, focus order, contrast, hover/focus/disabled states, reduced motion, 125–200% Windows scaling, and keyboard-only operation.
