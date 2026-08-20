# Phase 2 WP2 — threshold calibration

**Date:** 2026-08-20
**Question:** are the tools' numeric constants defensible, and what is the
smallest real change each metric can actually resolve?
**Method:** Neyman–Pearson with a fixed false-alarm budget, over synthetic pairs
with exact ground truth. Every threshold is the **upper bound of a
2000-resample bootstrap CI on Q(1−α)** of the
no-change control distribution, never the point estimate, and every threshold is
recorded with its `n` and its α.
**Scope:** derivation only. No constant here has been applied to `scripts/` —
that is the integrator's step, from this bundle.

## Why this method and not ROC

Fixed by [`docs/phase2-scope.md`](../../docs/phase2-scope.md) and restated here
because it is the part most likely to be "improved" back into a mistake:

- **Not Youden's J.** It needs a positive class that matches deployment. Ours is
  fabricated by our own perturbation generator, so maximising J optimises for the
  fabrication.
- **Not equal-error rate.** EER asserts symmetric costs. False negatives cost
  more here — an invariant wrongly reported `SATISFIED` is the failure WP3 exists
  to prevent.
- **Neyman–Pearson fits** because it only needs the *negative* class
  characterised densely, and that is the class synthetic controls can supply.

α is not chosen by taste. The bootstrap resamples with replacement and cannot
extrapolate past the observed maximum, so an extreme quantile from a small
control set is the maximum wearing a disguise. Working rule `n ≳ 3/α`:
α = 0.01 needs ~300 controls, α = 0.05 needs ~60. This run measured
**400 full-frame controls** and
**400 foreground-mode controls**, so the two modes
carry different α — recorded per metric, per mode, in
`derived-thresholds.json`.

## Corpus

| Block | Units | What it establishes |
|---|---|---|
| No-change controls | 800 | the noise floor: identical re-save, PNG re-encode, rescale round trip, sub-threshold perturbation |
| Perturbation grid | 792 | 11 families × magnitude × extent, 66 levels total |
| Foreground-fraction sweep | 144 | BACKGROUND_DOMINANT_MAX and FOREGROUND_MIN_FRACTION |
| Accent-area sweep | 98 | ACCENT_AREA_SMALL_FRACTION |

Four base scenes at 384×288 — dark base with vivid accents,
blocks-and-stripes layout, a thin object on a flat preview backdrop (~2% of
frame), and full-frame busy content — each built from
5 seeds for controls and 2 for the grid. Every
image is a pure function of its parameters; the only stochastic perturbation is
additive noise, drawn from a `PCG64` generator whose seed is derived up front.

## W2 foreground control scenes

The opaque foreground estimate uses exactly `thin_object`, `structured`,
`blob_object`, and `multipart_object`. `structured` qualifies because its
(32,32,36) margin is a genuine backdrop; `busy` is excluded because the
background is the subject, and `dark_accent` is excluded because its black base
would make the foreground mask a chroma mask rather than a backdrop estimate.

- `blob_object`: a filled rounded form at approximately 15% of the frame with
  low perimeter-to-area, testing whether rescale noise is specifically a thin-
  object effect.
- `multipart_object`: three separated rounded components at approximately 8%
  combined, testing a disjoint mask and cell-support gating.

The existing `ALPHA_CORPUS` supplies the RGBA source family. The A2.1
null-change preflight is recorded in `derived-thresholds.json`; it reports the
exact per-metric six-decimal comparisons rather than treating a mismatch as a
pass.

## A2.1 null-change check

Status: **FAIL**. The unmodified opaque `thin_object` corpus was re-run against the post-W1 tool snapshot; equality is tested to six decimal places.

| Metric | 2026-08-19 | Re-run | Match to 6 dp |
|---|---|---|---|
| `accent_fraction_delta_abs` | 0.089222 | 0.089222 | yes |
| `accent_palette_distance` | 3.57495 | 3.57495 | yes |
| `accent_palette_distance_de2000` | 1.01327 | 1.01327 | yes |
| `ahash_distance` | 1 | 1 | yes |
| `base_palette_distance` | 28.4093 | 28.4093 | yes |
| `base_palette_distance_de2000` | 6.22336 | 6.22336 | yes |
| `changed_area_fraction` | 0.024201 | 0.01622 | NO |
| `dhash_distance` | 1 | 1 | yes |
| `entropy_delta_abs` | 5.28453 | 5.28453 | yes |
| `hue_family_delta_l1` | 0.061974 | 0.061974 | yes |
| `hue_family_delta_max` | 0.030987 | 0.030987 | yes |
| `luminance_mean_delta_abs` | 34.129 | 34.129 | yes |
| `saturation_mean_delta_abs` | 4.8875 | 4.8875 | yes |
| `structural_dissimilarity` | 0.206228 | 0.206228 | yes |

**Diagnosis of the single mismatch, added by the integrator 2026-08-20.** The
`changed_area_fraction` difference is *not* a W1 regression on the opaque path,
and A2.1's underlying claim — that W1 left the border-median branch untouched —
survives. The cause is that the 2026-08-19 bundle and the application of its own
recommendation landed in the **same commit** (`3453724`): that bundle's controls
were measured while `CHANGE_THRESHOLD` was still **10**, and it recommended
raising it to **20**, which was then applied. Today's re-run measures at 20.

`changed_area_fraction` is defined as the share of pixels whose per-pixel
luminance delta exceeds `CHANGE_THRESHOLD`, so raising that gate can only
*weakly decrease* it — and 0.024201 → 0.016220 is a decrease. Decisively, it is
the **only one of the fourteen metrics that reads that constant at all**, and it
is the only one that moved; the other thirteen match to six decimal places.

So the correct reading is that A2.1 passes on its intent and fails on its
letter, because its baseline is a bundle that was superseded by its own verdict
in the same commit. The comparison is only meaningful for metrics whose
constants have not changed since the baseline was measured. Anyone re-running
this check in future should compare against a baseline taken *after* all
constant applications, or exclude metrics whose constants moved in between.

## Control thresholds — full frame

| Metric | n | α | Q(1−α) | CI upper = **threshold** | control max | quantile == max? |
|---|---|---|---|---|---|---|
| `accent_fraction_delta_abs` | 400 | 0.01 | 0.012685 | **0.016402** | 0.039261 | no |
| `accent_palette_distance` | 400 | 0.01 | 56.5255 | **56.6137** | 56.7454 | no |
| `accent_palette_distance_de2000` | 400 | 0.01 | 8.68195 | **9.8104** | 9.8659 | no |
| `ahash_distance` | 400 | 0.01 | 1 | **1** | 1 | yes |
| `base_palette_distance` | 400 | 0.01 | 31.7084 | **36.0039** | 36.0811 | no |
| `base_palette_distance_de2000` | 400 | 0.01 | 8.93015 | **10.8691** | 10.8895 | no |
| `changed_area_fraction` | 400 | 0.01 | 0.000327 | **0.000671** | 0.000916 | no |
| `dhash_distance` | 400 | 0.01 | 4.01 | **5** | 7 | no |
| `entropy_delta_abs` | 400 | 0.01 | 2.27012 | **2.3772** | 2.4569 | no |
| `hue_family_delta_l1` | 400 | 0.01 | 0.069117 | **0.097987** | 0.13958 | no |
| `hue_family_delta_max` | 400 | 0.01 | 0.034558 | **0.041354** | 0.048993 | no |
| `luminance_mean_delta_abs` | 400 | 0.01 | 1.01216 | **1.243** | 1.271 | no |
| `saturation_mean_delta_abs` | 400 | 0.01 | 8.12276 | **8.679** | 8.748 | no |
| `structural_dissimilarity` | 400 | 0.01 | 0.037229 | **0.037263** | 0.0374 | no |

A `yes` in the last column means Q(1−α) has reached the observed maximum: the bootstrap cannot extrapolate past it, so that threshold is the control maximum and its CI is not a statement about the true quantile. Read those as floors, not estimates.

## Control thresholds — foreground estimate (border-median)

| Metric | n | α | threshold | no-placement threshold | delta | factor | 2026-08-19 | new dom./next | old dom./next |
|---|---|---|---|---|---|---|---|---|---|
| `accent_fraction_delta_abs` | 400 | 0.01 | **0.092181** | 0.047179 | 0.045002 | 1.95386 | 0.089222 | 9.44234 | 580.765 |
| `accent_palette_distance` | 300 | 0.01 | **18.9705** | 16.01 | 2.96054 | 1.18492 | 3.57495 | 1.18449 | 1.31074 |
| `accent_palette_distance_de2000` | 300 | 0.01 | **5.30879** | 4.7169 | 0.59189 | 1.12548 | 1.01327 | 1.34134 | 1.52693 |
| `ahash_distance` | 400 | 0.01 | **10** | 1 | 9 | 10 | 1 | — | — |
| `base_palette_distance` | 400 | 0.01 | **30.5506** | 27.5835 | 2.9671 | 1.10757 | 28.4093 | 3.22979 | 3.19987 |
| `base_palette_distance_de2000` | 400 | 0.01 | **8.8515** | 8.3067 | 0.5448 | 1.06559 | 6.22336 | 4.65168 | 4.45852 |
| `changed_area_fraction` | 400 | 0.01 | **0.28713** | 0.00451 | 0.28262 | 63.6652 | 0.024201 | 56.988 | 997.237 |
| `dhash_distance` | 400 | 0.01 | **14** | 3.81 | 10.19 | 3.67454 | 1 | — | — |
| `entropy_delta_abs` | 400 | 0.01 | **5.304** | 1.6247 | 3.6793 | 3.2646 | 5.28453 | 1.20428 | 2.47378 |
| `hue_family_delta_l1` | 400 | 0.01 | **0.140812** | 0.140812 | 0 | 1 | 0.061974 | 2.02087 | — |
| `hue_family_delta_max` | 400 | 0.01 | **0.070406** | 0.070406 | 0 | 1 | 0.030987 | 2.02106 | — |
| `luminance_mean_delta_abs` | 400 | 0.01 | **34.166** | 1.452 | 32.714 | 23.5303 | 34.129 | 2.9785 | 18.9118 |
| `saturation_mean_delta_abs` | 400 | 0.01 | **5.087** | 3 | 2.087 | 1.69567 | 4.8875 | 2.08809 | 4.50209 |
| `structural_dissimilarity` | 400 | 0.01 | **0.206301** | 0.039831 | 0.16647 | 5.17941 | 0.206228 | 2.89365 | 29.654 |

The no-placement column uses the same Neyman–Pearson bootstrap construction after excluding only `rescale_roundtrip`; it is not a second method. A ratio above 10× remains a statement about the dominant family, not a general noise floor.

## Increased foreground thresholds — explanations

| Metric | Old | New | Explanation |
|---|---|---|---|
| `accent_fraction_delta_abs` | 0.089222 | 0.092181 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `accent_palette_distance` | 3.57495 | 18.9705 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `accent_palette_distance_de2000` | 1.01327 | 5.30879 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `ahash_distance` | 1 | 10 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `base_palette_distance` | 28.4093 | 30.5506 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `base_palette_distance_de2000` | 6.22336 | 8.8515 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `changed_area_fraction` | 0.024201 | 0.28713 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `dhash_distance` | 1 | 14 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `entropy_delta_abs` | 5.28453 | 5.304 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `hue_family_delta_l1` | 0.061974 | 0.140812 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `hue_family_delta_max` | 0.030987 | 0.070406 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `luminance_mean_delta_abs` | 34.129 | 34.166 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `saturation_mean_delta_abs` | 4.8875 | 5.087 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |
| `structural_dissimilarity` | 0.206228 | 0.206301 | The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led. |

## Control thresholds — foreground alpha source

| Metric | n | α | threshold | no-placement threshold | dominant family |
|---|---|---|---|---|---|
| `accent_fraction_delta_abs` | 380 | 0.01 | **0.003494** | 6.910e-06 | rescale_roundtrip |
| `accent_palette_distance` | 240 | 0.05 | **8.5676** | 6.9138 | subthreshold_saturation |
| `accent_palette_distance_de2000` | 240 | 0.05 | **1.53919** | 1.5119 | subthreshold_hue |
| `ahash_distance` | 380 | 0.01 | **1** | 0 | hsv_roundtrip |
| `base_palette_distance` | 380 | 0.01 | **4.0135** | 3.6876 | rescale_roundtrip |
| `base_palette_distance_de2000` | 380 | 0.01 | **1.1536** | 1.1536 | rescale_roundtrip |
| `changed_area_fraction` | 380 | 0.01 | **0** | 0 | hsv_roundtrip |
| `dhash_distance` | 380 | 0.01 | **2** | 2 | hsv_roundtrip |
| `entropy_delta_abs` | 380 | 0.01 | **1.632** | 1.632 | rescale_roundtrip |
| `hue_family_delta_l1` | 380 | 0.01 | **0.034188** | 0.034188 | hsv_roundtrip |
| `hue_family_delta_max` | 380 | 0.01 | **0.017094** | 0.017094 | hsv_roundtrip |
| `luminance_mean_delta_abs` | 380 | 0.01 | **0.997** | 0.997 | rescale_roundtrip |
| `saturation_mean_delta_abs` | 380 | 0.01 | **1.232** | 1.232 | rescale_roundtrip |
| `structural_dissimilarity` | 380 | 0.01 | **0.027778** | 0.027778 | rescale_roundtrip |

The RGBA source is measured over the existing `ALPHA_CORPUS` with premultiplied resampling for `rescale_roundtrip`.

## RGBA control-family accounting

Declared recipes: 21; measured recipes: 20.

| Skipped recipe | Reason |
|---|---|
| `jpeg_reencode_q85` | JPEG has no alpha channel; RGBA controls skip it by design. |

RGB-only recipes assert byte-identical alpha; rescale uses premultiplied resampling.

## Constants — current guess vs derived vs verdict

| Constant | Current | Derived | Verdict | n | α |
|---|---|---|---|---|---|
| `CHANGE_THRESHOLD` | 10 | 20 | **REPLACE-WITH-20** | 400 | 0.01 |
| `HUE_SHIFT_MIN_ABSOLUTE` | 0.02 | 0.02 | **KEEP** | 400 | 0.01 |
| `HUE_SHIFT_MIN_RELATIVE` | 0.3 | 0.3 | **KEEP** | 400 | 0.01 |
| `HUE_PRESENCE_MIN_FRACTION` | 0.0002 | 0.0002 | **KEEP** | 400 | 0.01 |
| `HUE_PRESENCE_MIN_PIXELS` | 10 | 1 | **REPLACE-WITH-1** | 400 | 0.01 |
| `CELL_MIN_SUPPORT_PIXELS` | 16 | 64 | **REPLACE-WITH-64** | 4504 | — |
| `BACKGROUND_DOMINANT_MAX` | 0.1 | — | **KEEP** | 6 | — |
| `FOREGROUND_MIN_FRACTION` | 0.02 | 0.006239 | **KEEP** | 6 | — |
| `ACCENT_AREA_SMALL_FRACTION` | 0.005 | 0.000479 | **KEEP** | 7 | — |
| `DEFAULT_ACCENT_SAT_MIN / DEFAULT_ACCENT_VAL_MIN (HSV accent gate)` | HSV S>100, V>60 | LCh C>=20.0, L>=20.0 | **KEEP** | — | — |

**`CHANGE_THRESHOLD`** — Swept the per-pixel luminance gate over 400 full-frame controls. Smallest gate whose bootstrap CI upper bound on Q(1-alpha) of changed-area stays below 0.001: 20. Gate that also keeps the 'any changed pixel' rate within alpha: None. See change_threshold.sweep for the full table and change_threshold.sensitivity_at_smallest_perturbation for what the choice costs.

**`HUE_SHIFT_MIN_ABSOLUTE`** — Neyman-Pearson sweep of the compound gate over 400 controls and the hue-rotation grid. Current constants: false-alarm rate 0.01 (budget 0.01), detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg. Chosen pair: false-alarm rate 0.01, detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg. Offline rule reproduced the tool's own verdict on 1.0 of checked pairs.

**`HUE_SHIFT_MIN_RELATIVE`** — Neyman-Pearson sweep of the compound gate over 400 controls and the hue-rotation grid. Current constants: false-alarm rate 0.01 (budget 0.01), detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg. Chosen pair: false-alarm rate 0.01, detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg. Offline rule reproduced the tool's own verdict on 1.0 of checked pairs.

**`HUE_PRESENCE_MIN_FRACTION`** — The presence floors were swept jointly with the shift gates -- they can fire the rule on their own, so pinning them would have searched the wrong space. Floors chosen by the joint search: pixels=1, fraction=0.0002, at false-alarm rate 0.01 and detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg.

**`HUE_PRESENCE_MIN_PIXELS`** — The presence floors were swept jointly with the shift gates -- they can fire the rule on their own, so pinning them would have searched the wrong space. Floors chosen by the joint search: pixels=1, fraction=0.0002, at false-alarm rate 0.01 and detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg.

**`CELL_MIN_SUPPORT_PIXELS`** — 4504 cell pairs from foreground-mode control comparisons, bucketed by foreground support. Rule: smallest support bucket whose p99 divergence, and every larger bucket's, is <= 2x the best-supported bucket's p99. Reference p99 divergence at best support: 0.18422.

**`BACKGROUND_DOMINANT_MAX`** — Foreground-fraction sweep: the smallest measured foreground share at which a *total* object recolour still clears the full-frame control thresholds on all of ['changed_area_fraction', 'structural_dissimilarity', 'hue_family_delta_max'] is None. The flag must fire at or below that share, so the constant is sound only if it sits at or above it.

**`FOREGROUND_MIN_FRACTION`** — Same sweep, foreground mode: the smallest measured foreground share at which foreground-mode control noise stays under its own thresholds is 0.006239. Below that the mask is unstable and foreground statistics are noise, so foreground_too_small must fire.

**`ACCENT_AREA_SMALL_FRACTION`** — Accent-area sweep: the smallest measured accent pixel fraction at which no-change controls produce no hue-shift false alarms and stay under the absolute gate is 0.000479. accent_area_very_small must fire at or below that fraction.

**`DEFAULT_ACCENT_SAT_MIN / DEFAULT_ACCENT_VAL_MIN (HSV accent gate)`** — Over a 64^3 sRGB grid the HSV gate admits 0.835602 of colours and the LCh gate 0.899784; they agree on 0.878201. 0.026528 of HSV-admitted colours have CIELAB L < 20.0 (near-black yet counted as a vivid accent) and 0.008866 have C < 20.0 (near-neutral yet counted as vivid). The LCh gate is a candidate, not a calibrated answer: no perturbation experiment here can rank two gates without a ground-truth notion of 'is an accent', which this corpus does not supply.

## Detection limits — full frame

The smallest ground-truth magnitude whose **median** response clears that
metric's threshold *and stays clear at every larger magnitude tested*. A dash
means the metric never resolved that perturbation at any magnitude in the grid.
`†` marks a curve that was not monotonically non-decreasing in magnitude.

This table is the most valuable output of WP2: it is what tells a calling agent
how to read a *null* result. "Invariant satisfied" and "no change detected" are
not the same claim, and a `SATISFIED` verdict that does not carry the deciding
metric's detection limit is a guarantee the measurement cannot support.

| Perturbation | Magnitude unit | `changed_area_fraction` | `structural_dissimilarity` | `dhash_distance` | `ahash_distance` | `hue_family_delta_max` | `accent_fraction_delta_abs` | `luminance_mean_delta_abs` | `saturation_mean_delta_abs` | `entropy_delta_abs` |
|---|---|---|---|---|---|---|---|---|---|---|
| `additive_noise` | sigma code values | 16 | 4 | — | — | 16 | 16 | 32† | 4 | 16 |
| `exposure_down` | code values (-) | 32 | 64 | —† | 64 | 64 | —† | 4 | 8 | — |
| `exposure_up` | code values (+) | 32 | 64 | — | 64 | 64 | 32 | 4 | 8 | — |
| `gaussian_blur` | radius px | 1 | 8 | — | 8 | 2 | 2 | —† | — | — |
| `hue_rotation @ extent 0.05` | degrees | 30 | —† | —† | — | 120 | — | —† | — | —† |
| `hue_rotation @ extent 0.25` | degrees | 15† | —† | —† | 30 | 15 | — | 120 | — | —† |
| `hue_rotation` | degrees | 15 | —† | 30 | 15† | 15 | — | 15† | — | —† |
| `jpeg_reencode` | quality loss (100 - quality) | 70 | — | — | — | —† | —† | —† | 70† | — |
| `region_recolour` | region fraction of frame | 0.001 | — | 0.05 | 0.02 | 0.02 | 0.02 | 0.02 | 0.05 | — |
| `rescale_roundtrip` | downscale loss (1 - factor) | 0.5 | 0.92 | — | — | 0.75 | 0.92 | —† | —† | — |
| `saturation_down` | saturation_loss (1 - factor) | 0.25 | 0.75 | 0.75 | 0.25 | 0.5 | 0.25 | 0.1 | 0.1 | —† |
| `saturation_up` | saturation_gain (factor - 1) | 0.25 | — | 2 | 0.1 | — | 0.25 | 0.1 | 0.25 | —† |
| `translation` | fraction of width | 0.002604 | — | 0.020833 | 0.041667 | — | —† | —† | —† | — |

Foreground-mode limits, per-scene breakdowns and the full response curves are in
`response-curves.json`.

## LCh hue-family boundaries

**Derived by measurement against the current HSV behaviour; no authoritative set
exists to port.** The phase 2 research looked for a CIELAB hue-angle boundary set
for basic colour names and found two mutually inconsistent application-specific
sets — porting the current 0–255 HSV bounds into degrees would have been worse
than doing nothing.

So: a 64³ sRGB grid (262144 colours, full, not
subsampled) is classified by the shipping rule — through PIL's own
`convert("HSV")`, not a reimplementation, so the classifier under test is exactly
the one the palette tool runs — every gated point is converted to LCh (D65,
hand-rolled in numpy, *not* Pillow's D50 `convert("LAB")`), and the interval set
that best reproduces that classification is fitted by minimising misclassification
at each boundary.

These intervals describe **what the tool does today**, in a perceptually uniform
space. They are not a colour-naming standard and must not be cited as one.

| Family | HSV H (0–255) | Gated points | LCh mean | Derived interval | 1st–99th pct |
|---|---|---|---|---|---|
| `red` | 0–10, 246–255 | 17142 | 32.356 | 16.318° – 45.856° | 10.938° – 45.333° |
| `orange` | 11–25 | 12897 | 59.946 | 45.856° – 75.696° | 45.734° – 78.467° |
| `yellow` | 26–42 | 14769 | 91.613 | 75.696° – 104.363° | 73.061° – 105.762° |
| `green` | 43–95 | 45350 | 128.389 | 104.363° – 144.389° | 105.124° – 146.259° |
| `cyan` | 96–130 | 30083 | 168.169 | 144.389° – 207.919° | 141.687° – 206.959° |
| `blue` | 131–165 | 30050 | 266.385 | 207.919° – 298.885° | 209.061° – 303.635° |
| `purple` | 166–200 | 30079 | 308.44 | 298.885° – 318.94° | 295.308° – 318.643° |
| `magenta` | 201–245 | 38678 | 342.318 | 318.94° – 16.318° | 319.189° – 22.802° |

Reproduction of the HSV classification by the derived intervals:
**0.947879** agreement over 219048
gated points — disagreement rate **0.052121**. The
residual is where HSV hue and LCh hue genuinely disorder each other; no contiguous
interval set can remove it.

### Accent gate — HSV vs LCh

| Measurement | Value |
|---|---|
| HSV gate pass fraction (whole sRGB grid) | 0.835602 |
| LCh gate pass fraction | 0.899784 |
| Gates agree | 0.878201 |
| HSV-admitted with CIELAB L < 20 (near-black, called vivid) | 0.026528 |
| HSV-admitted with C < 20 (near-neutral, called vivid) | 0.008866 |
| Median L of HSV-only colours | 17.501 |
| Median C of HSV-only colours | 38.423 |
| Median L of LCh-only colours | 73.369 |
| Median C of LCh-only colours | 30.231 |

The research's suggested `C_MIN = 20`, `L_MIN = 20` are *reasoned from measured
sRGB values, not calibrated*, and enter this work as an input. What is measured
above is what each gate admits and how far they disagree — not which is right.
Ranking them needs a ground-truth notion of "is this pixel an accent", which a
synthetic corpus cannot supply.

## Metric demotion

Demotion is an acceptable outcome, and phase 1 already did it once informally to
RGB palette distance. A metric whose detection limit is worse than the smallest
change anyone cares about is worse than useless: a null from it reads as
reassurance. A metric is flagged here when it fails to resolve ≥70% of the
perturbation curves at *any* tested magnitude.

| Metric | Curves | Never detected | Rate | Non-monotonic | Recommendation |
|---|---|---|---|---|---|
| `accent_fraction_delta_abs` | 26 | 12 | 0.461538 | 5 | keep |
| `accent_palette_distance` | 26 | 13 | 0.5 | 7 | keep |
| `accent_palette_distance_de2000` | 26 | 12 | 0.461538 | 6 | keep |
| `ahash_distance` | 26 | 16 | 0.615385 | 5 | keep |
| `base_palette_distance` | 26 | 15 | 0.576923 | 5 | keep |
| `base_palette_distance_de2000` | 26 | 15 | 0.576923 | 3 | keep |
| `changed_area_fraction` | 26 | 7 | 0.269231 | 4 | keep |
| `dhash_distance` | 26 | 20 | 0.769231 | 5 | **DEMOTE** |
| `entropy_delta_abs` | 26 | 25 | 0.961538 | 12 | **DEMOTE** |
| `hue_family_delta_l1` | 26 | 7 | 0.269231 | 7 | keep |
| `hue_family_delta_max` | 26 | 7 | 0.269231 | 3 | keep |
| `luminance_mean_delta_abs` | 26 | 13 | 0.5 | 10 | keep |
| `saturation_mean_delta_abs` | 26 | 11 | 0.423077 | 5 | keep |
| `structural_dissimilarity` | 26 | 16 | 0.615385 | 5 | keep |

**Perceptual colour (ΔE2000): derived** for `accent_palette_distance_de2000`, `base_palette_distance_de2000`.

## Limitations — documented, not solved

- **Synthetic perturbations are independent and uniform; real revisions are
  correlated and semantic.** A hue rotation over a band of rows is not what a
  re-render does. This calibration will systematically *underestimate* difficulty
  on real input, so every detection limit here is a best case.
- **No real-image validation set is included.** The scope asks for one; this repo
  distributes no images (the phase 1 reference is private and gated behind
  `PIL_AGENT_REFERENCE_IMAGE`), so the check that synthetic-derived thresholds do
  not fall apart on genuine input has *not* been performed. That gate is still
  open, and the thresholds here should be treated as provisional until it closes.
- **No academic precedent was found** for visual-regression or screenshot-diff
  threshold calibration specifically. This applies general Neyman–Pearson
  practice to the problem. It is not a validated domain methodology and must not
  be described as one.
- **The controls are not independent draws.** They are a small number of base
  scenes crossed with a fixed recipe list, so values within a recipe are
  correlated. The bootstrap treats them as exchangeable and therefore reports a
  tighter CI than the design earns. Thresholds are consequently slightly
  optimistic.
- **Sub-threshold perturbations are inside the control set by design**, per the
  scope. That deliberately raises the noise floor above pure re-encode noise, and
  it means every detection limit is quoted relative to a floor that already
  tolerates ±1 code value of exposure, σ≈1 of noise and a 1° hue rotation.
- **The control units are not independent draws.** The opaque foreground set is
  four scenes crossed with a fixed recipe list, and the alpha set is the existing
  `ALPHA_CORPUS` crossed with that list. Values within a recipe remain
  correlated, so the bootstrap CI is tighter than the design earns.
- **The alpha foreground path is calibrated here, but only for this synthetic
  corpus and one Pillow build.** RGBA `rescale_roundtrip` uses premultiplied
  resampling and one un-premultiply; RGB-only recipes assert unchanged alpha.
- **Sub-threshold perturbations are inside the control set by design**, so the
  detection limits describe a floor that already tolerates those perturbations.
- **The LCh accent gate is compared, not ranked.** Deciding between the HSV and
  LCh gates needs a ground-truth notion of "is this pixel an accent", which no
  synthetic corpus supplies. What is measured is what each gate admits and how
  much they disagree.
- **One machine, one Pillow.** JPEG and LANCZOS results are encoder-dependent;
  the provenance block records the exact versions.

## Reproducing

```
uv run python calibration/run_all.py
```

Runs the whole pipeline twice and asserts the three JSON payloads are
byte-identical between passes. `--once` skips the second pass, `--quick` runs a
reduced grid, `--jobs N` sets the worker count.

The tools are **snapshotted** before measurement: `scripts/*.py` is copied to a
scratch directory and every measurement runs against that copy, with each file's
sha256 recorded in `provenance.tool_sha256`. Other work happens in this tree
concurrently, and without the snapshot one run could silently measure two
different versions of a tool.

Environment for this bundle: Python 3.12.0, Pillow
12.3.0, numpy 2.5.2.

Files:

- `derived-thresholds.json` — control sets, thresholds with n/α/point
  estimate/CI, detection-limit table, the four constant-specific derivations,
  metric demotion analysis, and the constant verdicts.
- `response-curves.json` — every metric's response over every perturbation
  family's magnitude × extent grid, with monotonicity checks.
- `lch-hue-boundaries.json` — derived LCh hue-angle intervals and the accent-gate
  comparison.
- `README.md` — this ledger.

Sources: `calibration/scenes.py`, `perturb.py`, `measure.py`, `derive.py`,
`lch.py`, `run_all.py`.
