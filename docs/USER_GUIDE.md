# Parallax Forge user guide

How to take a 2D clip through to a finished red/cyan anaglyph master, and what
to do when a stage refuses to continue.

This guide covers the desktop application. For the command line, run
`aistereo COMMAND --help`, which is the authoritative reference for options.

---

## 1. Before you start

You need three things for real work. The application runs without the last two,
but it will tell you it is running in a reduced mode rather than pretend.

| Requirement | Why | Without it |
| --- | --- | --- |
| FFmpeg + FFprobe 8.1.2 | Decoding, normalising, encoding | Nothing can be imported |
| A depth model | Measuring scene geometry | Falls back to image analysis |
| An NVIDIA GPU | Neural depth speed | Depth runs on CPU, far slower |

Provision the media tools once:

```powershell
.\scripts\bootstrap.ps1 -ProvisionMediaTools
```

This verifies a pinned archive checksum and extracts to `.dev-tools/ffmpeg/8.1.2/`.
The application finds that automatically; you do not need to set anything.

### Depth engines

Two engines are selectable in **Settings → Performance → Depth engine**.

**Neural depth model** — highest quality. Requires PyTorch and a Video Depth
Anything checkpoint. Install PyTorch into the project environment:

```powershell
.\.venv\Scripts\python.exe -m pip install "torch>=2.2,<3" torchvision --index-url https://download.pytorch.org/whl/cu124
.\.venv\Scripts\python.exe -m pip install einops easydict
```

Then place a checkpoint and the upstream source tree in `.dev-tools/depth/`:

```text
.dev-tools/depth/
├── video_depth_anything_vits.pth      ← the checkpoint
└── Video-Depth-Anything/              ← upstream source checkout
```

The desktop host finds these automatically in development builds. To point
elsewhere, set `AISTEREO_DEPTH_MODEL_PATH` and `AISTEREO_DEPTH_MODEL_SOURCE`.

Checkpoint choice matters more than anything else for speed:

| Encoder | Licence | Relative speed | 5-second shot |
| --- | --- | --- | --- |
| Small (`vits`) | Apache-2.0, commercial use permitted | 1× | ~2.5 min |
| Base (`vitb`) | CC-BY-NC-4.0, non-commercial only | 3× slower | ~8 min |
| Large (`vitl`) | CC-BY-NC-4.0, non-commercial only | 10× slower | ~26 min |

Measured on an RTX 2080 in half precision. **Small is the right default.** Only
Small may be used in anything commercial; see
[model and licence policy](MODEL_AND_LICENSE_POLICY.md).

**Image analysis** — built in, no model, no download. It derives depth from
detail falloff, framing, and atmospheric cues. Weaker than the neural model on
fine detail like railings, wires, and hair, but it measures your actual footage
and is fully usable for editing, preview, and export.

---

## 2. Importing

Choose a source video. The application shows the source and the planned project
folder together before creating anything, defaulting to a collision-safe
subfolder under `D:\Parallax Projects`. Your source video is never moved or
copied by this step.

Import then runs automatically through: workspace setup, media inspection, a
normalised working copy, shot detection, and a **quick Director draft**.

The quick draft samples a bounded set of representative frames inside every
shot — not codec keyframes, so every shot is covered and nothing crosses a hard
cut. When it finishes, the Director controls unlock and you can start making
creative decisions immediately, while full-frame analysis is still pending.

---

## 3. Directing

Each shot gets a preset. The Director chooses one automatically; you can
override it per shot, or select several shots and apply one preset to all.

| Preset | Depth strength | Separation at 1280 px | Use for |
| --- | --- | --- | --- |
| Vista Deep | 0.90 | ~12.7 px | Landscapes, establishing shots |
| Action Controlled | 0.68 | ~9.1 px | Movement, tighter comfort limits |
| Dialogue Subtle | 0.55 | ~6.3 px | Faces and speech |
| Neutral | 0.45 | ~3.5 px | Conservative fallback |
| Close-up Flat | 0.38 | ~2.9 px | Close-ups, where strong depth hurts |

**If a shot looks flat, check its preset first.** Close-up Flat and Neutral are
deliberately shallow — around 3 px of separation, which is close to invisible on
a wide scene. That is correct behaviour for a face filling the frame and wrong
for a landscape. Switch it to Vista.

Below the presets, **Stereo Geometry** exposes depth strength, screen plane, and
the background and pop-out limits directly. Everything you set here is bounded:
a non-bypassable Comfort Guard clamps the final values at render time, so you
cannot author an uncomfortable render by dragging a slider.

Edits are versioned. They are written to `director/stereo_script.json`, which is
plain editable JSON, and they survive safe regeneration of upstream stages.

---

## 4. Previewing

Two kinds of preview, because they answer different questions.

**Render preview frame** (`R`) — renders the single frame under the playhead in
about a second. This is the one to use while adjusting depth: change a slider,
press `R`, look at it, repeat.

**Render 3D clip** (`Shift+R`) — renders every frame of the shot and plays it
back. Use this to judge motion and temporal comfort, which a still cannot show.
Pressing play in Anaglyph view also renders a clip if only a still exists.

**Render all** in the shot list footer queues every shot that has no clip yet.
The shot list and timeline both show render state at a glance: green means
rendered, a pulsing dot means rendering, a hollow dot means not yet.

Preview clips render at reduced width for speed, configurable in
**Settings → Preview clip quality** (Fast 640 px through Full working-copy
width). Final export is never scaled.

### Choosing a colour matrix

**Settings → Anaglyph matrix** changes how the two eyes are combined.

- **Calibrated (Dubois)** — preserves full colour and minimises ghosting. It
  deliberately does *not* look like a classic garish anaglyph. A warm night
  scene stays warm.
- **Basic red/cyan channels** — red from the left eye, green and blue from the
  right. Around 37% more red/cyan separation. This is the strong classic look,
  and the better choice for cheap red/blue glasses.

**Swap eyes** is there for glasses with reversed filters. If depth looks
inside-out, turn it on.

---

## 5. Exporting

Export requires two things: a versioned Director script, and depth that actually
measures your footage.

| Depth tier | Badge | Export |
| --- | --- | --- |
| Certified neural model | none | Allowed |
| Image analysis | `IMAGE DEPTH` (blue) | Allowed |
| Synthetic test pattern | `TEST DEPTH` (orange) | **Blocked** |

Synthetic depth is a fixed pattern with no scene in it — an export made from it
would be 2D in a stereo container, so it is refused. Image-analysis depth is
weaker than the neural model but real, and is allowed to ship.

The export dialog states which tier will be used, and the QC report records the
depth provenance alongside the render.

---

## 6. When something fails

Every analysis stage reports its own reason. These are the ones you are most
likely to meet.

### "Shot exceeds the bounded-memory inference limit"

A shot is longer than the memory budget allows — currently about **2157 frames,
90 seconds** at the default depth grid. The limit exists because a whole shot is
held in memory while its depth is computed.

Split the shot, or lower the depth resolution in the project configuration. The
error reports your longest shot's frame count so you know by how much.

### "Depth artifact exceeds the bounded allocation limit"

The depth file is too large to read back. All depth limits derive from one
budget, so this should not occur for a shot that passed the frame limit; if it
does, the depth grid was changed between stages.

### "This project uses test or unverified depth"

The neural model was unavailable and depth fell back. Check that PyTorch is
installed and a checkpoint exists in `.dev-tools/depth/`. You can also switch to
the image-analysis engine deliberately in Settings, which exports fine.

### "Depth stage is stale or its outputs changed"

Depth was computed under a different configuration and no longer matches. Re-run
depth analysis once; it will then stay current. This also appears after an
engine upgrade that changes how stage fingerprints are computed.

### "Preview could not start"

The request was rejected by the native host, not by the engine. Almost always
means the application was updated while running — restart it so the host reloads.

### The picture looks yellow, or has no visible depth

Work through these in order:

1. **Are you in Anaglyph view?** The Original tab shows the untouched source.
2. **What preset is the shot on?** Close-up Flat and Neutral are near-flat by
   design. See the table in section 3.
3. **Which colour matrix?** Calibrated preserves source colour, so a warm scene
   stays warm. Switch to Basic red/cyan for obvious separation.
4. **What does the depth badge say?** `TEST DEPTH` means synthetic depth, which
   has no geometry at all and will show no 3D whatsoever.

---

## 7. Aspect ratios and portrait video

Portrait and square sources are supported. The viewer, thumbnails, and timeline
all follow the source aspect rather than assuming 16:9.

Comfort budgets are expressed as a fraction of the screen width the picture
occupies. Because a portrait clip shown at full height covers less screen width
than a landscape one, the budget is measured against the landscape-equivalent
width. This gives a 9:16 clip the same *perceived* depth as a 16:9 clip at the
same preset, rather than a third of it. Sources 16:9 and wider are unaffected.

---

## 8. Where your data lives

Everything stays on your machine. Nothing is uploaded unless you explicitly
enable the optional LLM Assistant, which sends compact numerical shot statistics
only — never frames, never audio. See [PRIVACY.md](../PRIVACY.md).

```text
MyStereoProject/
├── project.json          project identity and source path
├── config.json           engine configuration for this project
├── pipeline_state.json   stage fingerprints for safe resume
├── source/               normalised working copy and audio
├── shots/                detected cut boundaries
├── depth/                per-shot depth arrays
├── features/             motion, speech, brightness per shot
├── director/             stereo_script.json  ← your edits live here
├── previews/             preview stills and clips
├── renders/              final masters
└── qc/                   report.json and report.html
```

Completed stages record an input fingerprint, so reopening a project resumes
rather than recomputing. Manual shot overrides are carried across regeneration
and are never silently discarded.

---

## 9. Speed

For a typical clip, depth dominates everything else. Measured on a 2:15 video of
3,249 frames:

| Stage | Time |
| --- | --- |
| Depth (neural, Small) | ~35–40 min |
| Feature extraction | seconds |
| Director | seconds |
| Preview clip render | ~8 s per 3-second shot |

If conversion feels slow, depth is why. In order of effect:

1. **Use the Small checkpoint** — 10× faster than Large.
2. **Lower the depth grid** in the project configuration; depth is upscaled to
   the frame anyway.
3. **Raise `chunk_frames`** to keep the GPU busier with fewer round trips.

Rendering already uses all your cores: frames render in parallel and are encoded
in strict order, so output is identical regardless of core count.
