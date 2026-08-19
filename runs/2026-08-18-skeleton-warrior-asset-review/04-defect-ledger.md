# Defect ledger — skeleton warrior build vs. concept sheet

Sixteen deviations, grouped by what has to happen to fix them. Each carries its
evidence provenance: **vision** (native multimodal read), **measured** (plugin or
harness output), or both. Numbers are from `01-region-palette-diffs.json` and
`03-harness-proportions.txt` unless stated.

Severity: 1 critical · 4 high · 8 medium · 3 low.

## A — Geometry never modelled

Absent meshes. No texture or shader work substitutes for these.

### A1 · Both hands are missing entirely — **critical** · vision + measured

Each arm terminates at the leather bracer cuff. The concept treats hands as a hero
feature: three of the sheet's part slots are bracer-and-hand variants, and articulated
finger bones appear in all three body views. Nothing downstream of the wrist exists.

```
distal arm (outer 12% of span), bone-toned pixel share
  concept   11.13% left · 7.62% right
  build      2.85% left · 2.74% right
```

The residual is the bracer's cream top face, not bone — confirmed at 4× zoom. A1
blocks rigging, animation and weapon attachment, which is why it outranks everything
else here.

### A2 · Sword has no crossguard, and the pommel is wrong — **high** · vision

The concept weapon is a cruciform arming sword: long straight steel crossguard,
faceted steel pommel, steel chape on the scabbard. The build has a bare grip with a
gold ball pommel, no guard, and a flat plank scabbard. The cruciform silhouette is
Templar iconography — losing the guard costs the design its second cross.

### A3 · Diagonal sword belt absent; scabbard hangs unsupported — **medium** · vision

The sheet shows two belts: a horizontal waist belt and a diagonal baldric crossing the
hip with its own buckles and strap ends. Only the horizontal belt was built.

### A4 · Feet are solid clogs — no toe bones, no sabatons — **medium** · vision + measured

The concept foot is separated metatarsals and toe bones under a steel sabaton plate.
The build is one cream block with shallow grooves scored across the toe line.

```
feet region   entropy 7.025 -> 4.444   (-2.581 bits, largest drop in the asset)
              saturation 33.08 -> 15.95  (-52%)
              hue family: yellow lost   (the sabaton brass)
```

### A5 · Greaves have no knee cop — **medium** · vision

Concept greaves top out in a pointed V-shaped poleyn with a strap and side buckles.
The build's is a plain rounded cuff, which is why the leg reads as a boot rather than
as plate armour.

### A6 · Mail runs to the wrist with no forearm transition — **low** · vision

The sheet sequences three materials down the arm: scale mail to mid-forearm, leather
bracer over the forearm, bone hand emerging. The build is one uniform mail tube ending
in a small cuff.

## B — Textures never applied

One root cause: the asset wears flat shader colours where the sheet specifies painted
maps.

### B1 · Skirt inner panel is an untextured grey slab — **high** · vision + measured

The concept's front split reveals a dark red inner tabard with red trim running the
full length of both split edges. The build shows a flat light-grey rectangle with a
hard geometric edge, plus two red trim strips that begin and stop mid-panel — an
unfinished UV island.

```
mid-skirt, four largest base colours
  concept  #2b2a27  #1e1d1a  #22211e     warm blacks, fabric grain
  build    #3f3f3f  #343435  #414141  #444343
           four near-identical neutral greys within six luminance levels
  saturation -82%   entropy 6.355 -> 4.963  (-1.391 bits)
```

### B2 · Surface detail collapsed across the whole asset — **high** · measured

Entropy falls in seven of the eight measured regions. Same defect as B1, asset-wide:
the concept's painted weathering, panel lines, fabric grain and edge wear are absent.

```
entropy delta by region (bits)
  feet -2.581 · skirt -1.391 · greaves -1.062 · torso -0.775
  belt -0.726 · hem -0.516 · skull -0.136 · gorget +0.191
```

The gorget's *increase* is stair-step aliasing on the scale silhouette — noise, not
detail. Read the number with the image beside it.

### B3 · Surcoat has no true black and no fabric grain — **medium** · measured

```
torso base tones
  concept  #32302c  #171613    warm, floor at luminance 22
  build    #3e3c3c  #353535    neutral, floor at luminance 53
  entropy 6.896 -> 6.121  (-0.775 bits)
```

The garment never reaches black anywhere, which is what makes it read as grey plastic
rather than dyed wool.

### B4 · Hem cross motifs reduced and simplified — **low** · vision

The sheet repeats a small cross pattée along the red hem band on every skirt panel.
The build carries fewer, blockier marks, and the red band itself is proportionally
wider.

## C — Colour calibration

Diagnose the scene before the materials — see C3.

### C1 · Heraldic red shifted lighter and toward orange — **medium** · measured

```
cross and trim reds
  concept  #6d3428  #7e4032  #914638     desaturated oxblood
  build    #954739  #a04d3e  #a65041     brighter brick-salmon
  torso hue shift   orange +0.205 · red -0.199
  hem band luminance +16.8
```

Consistent across cross, hem band and skirt trim, so it is one material value, not
three mistakes. Part of the shift may be the render's colour pass — resolve C3 and
re-measure before regrading the texture.

### C2 · Bright gold fittings where the sheet has dark bronze — **medium** · vision + measured

The build adds saturated gold to greave rivets, bracer studs, sword pommel and a large
square belt buckle. The concept's fittings are small, dark and bronze-toned. This is
the one place the build is *louder* than the concept, and it is why the eye goes to
the buckle instead of the cross.

```
belt region accent palette
  concept  #483829  #3f3124  #7c503d     dark leather
  build    #bf915d  #a47e54  #9b7851     gold dominates
  belt      orange +0.319 · red -0.298 · yellow lost
  greaves   orange +0.668 · red lost · yellow lost
```

The concept's brass classifies as hue family **yellow**; the build's gold classifies
as **orange**. It is not the same metal.

### C3 · Metals read neutral where the concept's read warm — **medium** · measured, confounded

**Fix the scene before the material.**

```
greave shin plate, backdrop-excluded sample
  concept  #413d37   luminance 63.8   R-B +10
  build    #383838   luminance 57.6   R-B   0
backdrops
  concept  #a5a09d (R-B +8)   build #959597 (R-B -2)
```

The scene-wide white-balance difference is roughly the same magnitude as the measured
warmth loss. Foreground sampling removes backdrop contamination but not warm lighting
falling on the subject. The neutrality is real in the delivered image; its cause is
ambiguous between material and world lighting. Re-render with matched white balance,
then re-measure.

## D — Proportion and silhouette

Body proportions themselves are correct — see the withdrawn claims in the bundle
README.

### D1 · Gorget flares like a lampshade instead of draping — **high** · vision + measured

The concept gorget is a layered scale collar following the shoulders and tucking under
the surcoat. The build's flares outward into a near-horizontal cone with a scalloped
rim, and sits far too light. Loudest silhouette error in the asset, and the reason the
figure reads top-heavy.

```
gorget base tone
  concept  #39322f  #2f2e2c    dark, warm
  build    #5d5c5d  #4b4949    approx +30 luminance, neutral
  saturation 29.78 -> 6.82  (-77%)
```

### D2 · Skull 20% too broad, neck half as thick — **medium** · measured

```
skull width / height    0.659 -> 0.789   (+19.8%)
neck width / head h     0.268 -> 0.133   (-50.3%)
accent palette          concept: 3 warm ochres  ->  build: EMPTY
                        orange -0.693 · yellow -0.307  = 100% of accents lost
```

Anatomy is also simplified past spec: eye sockets became angular slits under a heavy
brow where the sheet has large rounded orbits, the nasal aperture is gone, teeth are
reduced to a single band with a front gap, and the mandible is fused into the cranium.
The skull is the only region in the asset whose accent palette comes back empty — it
carries no warm crevice shading at all.

### D3 · Belt pouches oversized and protruding — **low** · vision

The sheet's pouches are small, flat and sit tight against the belt line. The build's
are larger, hang lower, and stick out from the hip — adding width exactly where the
silhouette should narrow into the skirt.

## Suggested order of work

1. **The scene.** C3 is confounded with world lighting. Resolve and re-render before
   touching any material, or the textures get graded to compensate for a light rig.
2. **Missing meshes.** A1 blocks everything downstream. A2 next — the crossguard
   carries the Templar read.
3. **Texture pass.** B1 and B2 are one job. Finishing the skirt panel's UV work
   resolves B1 and a large share of B2 together.
4. **Silhouette.** D1 is a modelling fix worth doing *before* the texture pass if the
   gorget is due for retopo anyway, since it needs new UVs either way.

## Not claimed

Nothing above speaks to the build's polygon count, mesh density or topology; both
tools' `interpretation_limits` forbid it and it was honoured. "Blocky" describes
silhouette and shading only. For geometry, query the Blender scene's own mesh
statistics.

Coverage is **one front view**. The concept sheet's back panel, three-quarter profile
and disassembled parts are unverified.
