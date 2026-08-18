# Design rationale

Last updated: 2026-08-18

Why these tools measure what they measure. Every constraint below came from a
measurement on a real image, not from anticipation — the sequence is recorded in
the [phase 1 evidence bundle](../runs/2026-08-18-pil-agent-plugin-phase1/README.md).

## The question

Does wrapping Pillow in scripted tools give a coding agent genuinely new signal
beyond its native multimodal vision?

Two subagents analysed the same image — a dense marketing graphic combining
stylised text, illustration and a dashboard-style pipeline visualisation. One was
restricted to native vision; the other was permitted Pillow and numpy scripting.

## What the experiment established

**Native vision, unassisted, produced:** complete text transcription; semantic
layout description; an object and icon inventory; correct genre identification;
and a correct reading of the graphic's colour-coded pipeline states. It also
volunteered its own uncertainty, flagging which small icons it could not resolve.

**Pillow added, non-redundantly:** exact hex values with area coverage;
luminance, saturation and entropy statistics; per-cell structural statistics;
perceptual hashes; and changed-region bounding boxes.

**Pillow got wrong on its own:** a global 8-colour quantisation of the image
returned *no vivid entries at all*. Roughly 75% of pixels are near-black, so
area-weighted extraction spent its entire budget on dark tones. Native vision's
qualitative read — "dominated by red and cyan accents" — was perceptually correct
where the naive measurement was not.

The conclusion is that neither approach subsumes the other. The naive framing
("Pillow helps the agent see more") is false. The defensible framing is that
Pillow makes perception **quantitative, reproducible and diffable**, which is a
different capability rather than a larger one.

## Why colour is reported three ways

The area-weighting problem recurred twice, one level apart.

1. **Globally.** Quantising all pixels surfaced only near-blacks. Fixed by
   masking for chroma (HSV saturation and value thresholds) and quantising the
   vivid subset separately → `accent_palette`.

2. **Within the accents.** Measuring the real image showed the accent palette was
   *also* area-weighted, so the dominant red accent (56% of accent pixels) crowded
   out cyan, green, blue and purple entirely — despite those four hues encoding
   the graphic's pipeline-state semantics. Cyan is 0.485% of the frame.

The second failure cannot be fixed by better quantisation, because the problem is
not resolution — it is that **semantic importance and pixel area are
uncorrelated**. A status colour occupying half a percent of a frame can carry as
much meaning as the background carries pixels. The fix is a per-hue-family census
that enumerates presence independently of area → `hue_families`.

All three views are retained because each answers a different question, and the
tool cannot know in advance which one the caller needs.

## Why hue-shift detection is magnitude-based

The first implementation reported `hue_families_lost` as a set difference on
presence. Testing it against a real cyan→red recolour, it returned empty: pixels
near the saturation threshold survived the rotation, keeping cyan's count above
zero. Presence-based detection is too brittle for real edits, which are rarely
total.

Detection is therefore magnitude-based. A family counts as shifted only when its
share of accent pixels moves by both an absolute margin (≥0.02) and a relative one
(≥30%), the second condition preventing an already-dominant hue from being flagged
for a proportionally minor change.

## Why so few metrics actually discriminate

The [discrimination matrix](../runs/2026-08-18-pil-agent-plugin-phase1/10-metric-discrimination-matrix.md)
compares each metric's response to a pure rescale (content unchanged) against a
cyan→red recolour (colour scheme changed). Only 4 of 11 separate the two cases.

Most instructive is that `base_palette_distance` scored the recolour as *more
similar* (0.50) than the rescale (2.62) — it answers the colour-scheme question
backwards. Both perceptual hashes were blind, being luminance-based against a
luminance-preserving rotation. Structural similarity was blind, correctly, since
structure genuinely did not change.

This is why the documentation directs callers to specific fields per question
rather than offering a single similarity number. A single score would have to
weight colour against structure against detail, and any fixed weighting is wrong
for some caller.

## Why the geometry disclaimer is in the payload

Edge density is an attractive proxy for "how detailed is this" and therefore an
attractive proxy for polygon count when comparing 3D renders. It is not a valid
one: shading model, normal maps, lighting and camera angle all move edge density
independently of mesh topology, and a smooth-shaded low-poly render can register
as less complex than a flat-shaded high-poly one.

Because the tool cannot detect this misuse, the limitation is emitted in every
payload under `interpretation_limits` and pinned by a test, so it reaches an agent
reading only the JSON. Polygon and topology questions belong to the 3D scene's own
mesh statistics.

## Determinism as a requirement

The entire value proposition is reproducible, diffable output. Quantisation is
pinned to MEDIANCUT with dithering disabled, palettes are sorted by coverage with
hex as tiebreak, and JSON is emitted with sorted keys. A test asserts
byte-identical output across runs — without it, committed output would churn and
the tools would be useless for tracking change over time.

## Scale invariance as a requirement

A render will rarely match a reference at the same resolution. Structural
statistics are therefore computed on a fixed-size working copy over a grid defined
as *fractions* of each image's own dimensions, so cells correspond regardless of
pixel size. Edge extraction is preceded by a small blur, without which edge
density is dominated by resampling aliasing rather than image structure and scale
invariance fails for fine repeating detail.

Mismatched aspect ratios are flagged rather than resampled to match, because
squashing makes grid cells non-corresponding and silently invalidates every
per-cell number.
