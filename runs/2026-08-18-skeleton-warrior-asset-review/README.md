# Field trial — low-poly game asset review against its concept sheet

**Date:** 2026-08-18
**Question:** does the vision-first/measure-second method survive a real production
task — reviewing a Blender character build against the concept art it was made from?
**Verdict:** yes, and it earned its keep in the only way that matters: **four of the
reviewing agent's own confident visual conclusions were wrong, and measurement caught
all four before they reached the defect list.** Two of those would have sent an artist
to fix something that was already correct.

This is the first bundle in which the plugin was used on someone else's problem rather
than on its own validation image.

## Subject

A Templar skeleton-warrior game asset. Two images:

- **A — concept sheet.** Three body views (front, three-quarter, back) plus a full
  disassembled parts sheet, on a flat warm-grey backdrop.
- **B — Blender build.** A single front-view T-pose render on a flat neutral-grey
  backdrop.

Neither is distributed; see *Reproducing* below.

## Method

The method is the one `agents/image-comparison-analyst.md` prescribes, followed to the
letter:

1. Read both images with native vision and **commit to a written description first**,
   so the numbers cannot anchor perception afterwards.
2. Cut eight matched regions from each figure and measure them.
3. Report where vision and measurement **disagree**, rather than silently deferring to
   whichever spoke last.

Step 3 is the whole point, and step 1 is what makes step 3 possible. An agent that
measures first has nothing to be corrected against.

## What the plugin measured, and what it did not

This bundle is capability evidence, so the line is drawn explicitly.

| Artifact | Produced by | Capability exercised |
|---|---|---|
| `01-region-palette-diffs.json` | **plugin** — `pil_palette_diff` ×8 | Base and accent palettes, hue-family census, saturation, entropy, per-region diff verdicts |
| `02-structure-diff-rejected.json` | **plugin** — `pil_structure_diff` | Self-diagnosis: the tool declining a comparison it cannot serve |
| `03-harness-proportions.txt` | **harness** — numpy in `regenerate.py` | Silhouette bounding box, head count, skull aspect, neck thickness |
| Region cutting | **harness** | Matched crops at identical silhouette-bbox fractions |
| Material sampling | **harness** | Backdrop-excluded foreground medians |

**The plugin has no silhouette, proportion, region-cutting or foreground-isolation
capability.** Everything in the third column of that table is harness code written
around it for this task. Crediting the plugin with those measurements would be exactly
the kind of unearned claim the rest of this repository is built to prevent.

## Where measurement overruled vision

The headline result. Four visual conclusions were written down before any tool ran,
and did not survive.

| # | Visual claim, committed before measuring | Measurement | Outcome |
|---|---|---|---|
| 1 | "The build is short and squat with an oversized head" | 6.35 heads tall vs the concept's **6.52** — a 2.7% difference. Legs occupy 28% of figure height vs 26%. | **Withdrawn.** No proportion defect exists. The impression came from a 20%-broader skull and a 50%-thinner neck. |
| 2 | "Concept greaves are polished silver steel; the build's are black" | Clean shin-plate samples: luminance **63.8 vs 57.6** — the same value. | **Withdrawn as a value error.** Re-filed as warmth (R−B +10 → 0) and contrast range (172 → 107 levels). |
| 3 | "The concept shows bare leg bone that the build covers" | The concept's lower-leg band is **0.0%** bone-toned — one pixel. Greaves cover it there too. | **Relocated.** The real difference is 1.9% → 0.6% through the skirt split. |
| 4 | "Structural similarity will quantify the shape deviation" | 0.900 similarity, 0.71 changed area — but `aspect_ratio_mismatch` and `resolution_mismatch` flagged on every pair, and the poses differ (A-pose vs T-pose). | **Discarded.** See below. |

Claims 1 and 2 are the valuable ones. Both are cases where an agent trusting its own
eyes would have filed a defect against correct work.

## The structure tool refusing the job

`02-structure-diff-rejected.json` is kept deliberately, because a tool that knows when
it cannot answer is a feature and needs evidence like any other.

Asked to compare an A-pose concept view against a T-pose render, `pil_structure_diff`
returned confident-looking numbers — `structural_similarity` 0.900, `dhash_distance`
20, `changed_area_fraction` 0.708 — and simultaneously raised
`aspect_ratio_mismatch` and `resolution_mismatch`, with `interpretation_limits`
spelling out what that means. Read together, the payload says: *these numbers describe
framing and pose, not the model.*

They were discarded. Only pose-independent statistics — palette, hue family,
saturation, entropy — were carried into the findings. This is the failure mode
`10-metric-discrimination-matrix.md` predicted in phase 1: a similarity score and a
hash both sounding authoritative while measuring the wrong thing.

## What the plugin found that vision did not

Vision saw "the skirt panel looks washed out". The measurement named the cause: the
region's four largest base colours are `#3f3f3f`, `#343435`, `#414141`, `#444343` —
four near-identical **neutral** greys, against the concept's warm `#2b2a27` / `#1e1d1a`
/ `#22211e`. Four flat neutrals clustered inside six luminance levels is the signature
of an unshaded panel, not of a dark texture. Saturation −82%, entropy −1.391 bits.

Two further findings vision could not have produced:

- **Entropy fell in seven of eight regions**, quantifying "detail loss" as a single
  comparable number per part: feet −2.581 bits, skirt −1.391, greaves −1.062, torso
  −0.775, belt −0.726, hem −0.516, skull −0.136. The eighth, the gorget at +0.191, is
  stair-step aliasing — noise reading as detail, which is why the number needs the
  visual read beside it.
- **The `yellow` hue family is lost in all eight regions.** In the concept it is the
  aged brass and the warm ochre in every crevice; in the build the only warm metal
  left classifies as `orange`, because it is saturated gold. One census column
  explained four separately-observed fittings as a single material error.

The hue census earning its place here is the same result phase 1 found on a different
image: area-weighted palettes miss small semantic colour, and only an explicit
per-hue census recovers it.

## A confound the tools surfaced but could not resolve

The greave metal lost its warmth: concept `#413d37` at R−B +10, build `#383838` at
R−B 0. But the two backdrops differ by almost exactly the same amount — concept
`#a5a09d` (R−B +8) against build `#959597` (R−B −2). Foreground-only sampling removed
backdrop *contamination*; it cannot remove warm *lighting* falling on the subject.

So the neutrality is real in the delivered image, but its cause is ambiguous between
material and scene white balance. The finding was filed with that caveat attached and
an instruction to fix the scene first. The saturation collapses (−77% to −86%) and the
entropy drops are far too large to be white balance and stand on their own.

Recorded because a bundle that only lists what the tools resolved would misrepresent
them.

## What this exercise wants from phase 2

Three gaps, in the order they hurt:

1. **Region cutting should be a tool.** Cutting matched regions at identical fractions
   of each figure's silhouette bounding box is what made a 1254×1254 multi-view sheet
   comparable to a 900×1395 single-view render, part by part. It is ~30 lines, it is
   the single highest-leverage thing in the harness, and every caller comparing a
   render to a reference will need it. Candidate: `pil_region_diff`.
2. **Foreground isolation.** Backdrop-excluded material sampling changed a headline
   conclusion (claim 2 above). Without it, a flat backdrop dominates the base palette
   of every crop and material colours cannot be read at all.
3. **Perceptual colour distance.** Already tracked as phase 2 WP1. This trial adds a
   concrete case: the red shift `#6d3428` → `#a04d3e` is the defect an artist most
   wants quantified, and Euclidean RGB is the wrong instrument for it.

Silhouette proportion measurement — head count, skull aspect — is deliberately *not*
proposed as a tool. It worked here because both subjects are single figures on flat
backdrops, and it would mislead on anything else.

## Scope boundary, restated

`interpretation_limits` was honoured: nothing in the review claims anything about the
build's polygon count, mesh density or topology. "Blocky" in the findings describes
silhouette and shading only. The asset is a low-poly model where a poly-count question
is the obvious one to ask, and the tools correctly cannot answer it — that belongs to
the deferred Blender mesh-statistics path.

The review also covers **one front view**. The concept sheet's back panel, profile and
parts sheet are unverified, and absence of a defect is not evidence of correctness
there.

## Findings

`04-defect-ledger.md` — sixteen defects, grouped by root cause, each carrying whether
it was found by vision, by measurement, or by both.

A formatted version was published as a private Claude artifact:
<https://claude.ai/code/artifact/a8e91582-183f-429a-8a1b-9615a53e1e98> — private to
its owner unless shared. The markdown ledger is the durable copy; the artifact is a
convenience.

## Reproducing

```
uv sync
uv run python runs/2026-08-18-skeleton-warrior-asset-review/regenerate.py \
    --concept "<your-concept-sheet.png>" --render "<your-render.png>"
```

`--concept-front` overrides the sub-crop that isolates the front view on a multi-view
sheet; it defaults to the fractions used here.

`regenerate.py` reproduces artifacts `01`–`03` only. The tight material samples
quoted in the ledger — the greave shin plate and the distal-arm bone share — were
ad-hoc numpy probes placed by eye against these two specific images, and are recorded
as values rather than as reproducible steps. Promoting foreground sampling to a tool
(see above) is what would make them reproducible.

**The images are not distributed.** They are a third party's concept art and the
user's own build. `runs/**/*.png` is gitignored, `path` fields in the JSON evidence
were replaced with placeholders, and the intermediate crops under `_regions/` stay
local — the same treatment the phase 1 bundle gave its reference image.

Environment: Windows 11, Python 3.13, Pillow, numpy, Claude Code 2.1.235.
