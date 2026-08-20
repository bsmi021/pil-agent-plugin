# Phase 2 WP2 — threshold calibration

**Date:** 2026-08-19
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
**100 foreground-mode controls**, so the two modes
carry different α — recorded per metric, per mode, in
`derived-thresholds.json`.

## Corpus

| Block | Units | What it establishes |
|---|---|---|
| No-change controls | 500 | the noise floor: identical re-save, PNG re-encode, rescale round trip, sub-threshold perturbation |
| Perturbation grid | 660 | 11 families × magnitude × extent, 66 levels total |
| Foreground-fraction sweep | 144 | BACKGROUND_DOMINANT_MAX and FOREGROUND_MIN_FRACTION |
| Accent-area sweep | 98 | ACCENT_AREA_SMALL_FRACTION |

Four base scenes at 384×288 — dark base with vivid accents,
blocks-and-stripes layout, a thin object on a flat preview backdrop (~2% of
frame), and full-frame busy content — each built from
5 seeds for controls and 2 for the grid. Every
image is a pure function of its parameters; the only stochastic perturbation is
additive noise, drawn from a `PCG64` generator whose seed is derived up front.

## Control thresholds — full frame

| Metric | n | α | Q(1−α) | CI upper = **threshold** | control max | quantile == max? |
|---|---|---|---|---|---|---|
| `accent_fraction_delta_abs` | 400 | 0.01 | 0.012685 | **0.016402** | 0.039261 | no |
| `accent_palette_distance` | 400 | 0.01 | 56.5255 | **56.6137** | 56.7454 | no |
| `accent_palette_distance_de2000` | 400 | 0.01 | 8.68195 | **9.8104** | 9.8659 | no |
| `ahash_distance` | 400 | 0.01 | 1 | **1** | 1 | yes |
| `base_palette_distance` | 400 | 0.01 | 31.7084 | **36.0039** | 36.0811 | no |
| `base_palette_distance_de2000` | 400 | 0.01 | 8.93015 | **10.8691** | 10.8895 | no |
| `changed_area_fraction` | 400 | 0.01 | 0.01071 | **0.012533** | 0.013713 | no |
| `dhash_distance` | 400 | 0.01 | 4.01 | **5** | 7 | no |
| `entropy_delta_abs` | 400 | 0.01 | 2.27012 | **2.3772** | 2.4569 | no |
| `hue_family_delta_l1` | 400 | 0.01 | 0.069117 | **0.097987** | 0.13958 | no |
| `hue_family_delta_max` | 400 | 0.01 | 0.034558 | **0.041354** | 0.048993 | no |
| `luminance_mean_delta_abs` | 400 | 0.01 | 1.01216 | **1.243** | 1.271 | no |
| `saturation_mean_delta_abs` | 400 | 0.01 | 8.12276 | **8.679** | 8.748 | no |
| `structural_dissimilarity` | 400 | 0.01 | 0.037229 | **0.037263** | 0.0374 | no |

A `yes` in the last column means Q(1−α) has reached the observed maximum: the bootstrap cannot extrapolate past it, so that threshold is the control maximum and its CI is not a statement about the true quantile. Read those as floors, not estimates.

## Control thresholds — foreground mode

| Metric | n | α | Q(1−α) | CI upper = **threshold** | control max |
|---|---|---|---|---|---|
| `accent_fraction_delta_abs` | 100 | 0.05 | 0.07738 | **0.089222** | 0.093193 |
| `accent_palette_distance` | 100 | 0.05 | 2.57115 | **3.57495** | 8.0884 |
| `accent_palette_distance_de2000` | 100 | 0.05 | 0.881835 | **1.01327** | 1.2923 |
| `ahash_distance` | 100 | 0.05 | 0 | **1** | 1 |
| `base_palette_distance` | 100 | 0.05 | 22.2525 | **28.4093** | 29.7848 |
| `base_palette_distance_de2000` | 100 | 0.05 | 5.01804 | **6.22336** | 7.3855 |
| `changed_area_fraction` | 100 | 0.05 | 0.019714 | **0.024201** | 0.02541 |
| `dhash_distance` | 100 | 0.05 | 0 | **1** | 1 |
| `entropy_delta_abs` | 100 | 0.05 | 4.10543 | **5.28453** | 5.3627 |
| `hue_family_delta_l1` | 100 | 0.05 | 0.029947 | **0.061974** | 0.097987 |
| `hue_family_delta_max` | 100 | 0.05 | 0.014973 | **0.030987** | 0.048993 |
| `luminance_mean_delta_abs` | 100 | 0.05 | 28.3809 | **34.129** | 34.998 |
| `saturation_mean_delta_abs` | 100 | 0.05 | 4.68505 | **4.8875** | 5.13 |
| `structural_dissimilarity` | 100 | 0.05 | 0.176348 | **0.206228** | 0.207354 |

## Constants — current guess vs derived vs verdict

| Constant | Current | Derived | Verdict | n | α |
|---|---|---|---|---|---|
| `CHANGE_THRESHOLD` | 10 | 20 | **REPLACE-WITH-20** | 400 | 0.01 |
| `HUE_SHIFT_MIN_ABSOLUTE` | 0.02 | 0.02 | **KEEP** | 400 | 0.01 |
| `HUE_SHIFT_MIN_RELATIVE` | 0.3 | 0.3 | **KEEP** | 400 | 0.01 |
| `HUE_PRESENCE_MIN_FRACTION` | 0.0002 | 0.0002 | **KEEP** | 400 | 0.01 |
| `HUE_PRESENCE_MIN_PIXELS` | 10 | 1 | **REPLACE-WITH-1** | 400 | 0.01 |
| `CELL_MIN_SUPPORT_PIXELS` | 16 | 64 | **REPLACE-WITH-64** | 904 | — |
| `BACKGROUND_DOMINANT_MAX` | 0.1 | — | **KEEP** | 6 | — |
| `FOREGROUND_MIN_FRACTION` | 0.02 | 0.022588 | **REPLACE-WITH-0.022588** | 6 | — |
| `ACCENT_AREA_SMALL_FRACTION` | 0.005 | 0.000479 | **KEEP** | 7 | — |
| `DEFAULT_ACCENT_SAT_MIN / DEFAULT_ACCENT_VAL_MIN (HSV accent gate)` | HSV S>100, V>60 | LCh C>=20.0, L>=20.0 | **KEEP** | — | — |

**`CHANGE_THRESHOLD`** — Swept the per-pixel luminance gate over 400 full-frame controls. Smallest gate whose bootstrap CI upper bound on Q(1-alpha) of changed-area stays below 0.001: 20. Gate that also keeps the 'any changed pixel' rate within alpha: None. See change_threshold.sweep for the full table and change_threshold.sensitivity_at_smallest_perturbation for what the choice costs.

**`HUE_SHIFT_MIN_ABSOLUTE`** — Neyman-Pearson sweep of the compound gate over 400 controls and the hue-rotation grid. Current constants: false-alarm rate 0.01 (budget 0.01), detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg. Chosen pair: false-alarm rate 0.01, detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg. Offline rule reproduced the tool's own verdict on 1.0 of checked pairs.

**`HUE_SHIFT_MIN_RELATIVE`** — Neyman-Pearson sweep of the compound gate over 400 controls and the hue-rotation grid. Current constants: false-alarm rate 0.01 (budget 0.01), detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg. Chosen pair: false-alarm rate 0.01, detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg. Offline rule reproduced the tool's own verdict on 1.0 of checked pairs.

**`HUE_PRESENCE_MIN_FRACTION`** — The presence floors were swept jointly with the shift gates -- they can fire the rule on their own, so pinning them would have searched the wrong space. Floors chosen by the joint search: pixels=1, fraction=0.0002, at false-alarm rate 0.01 and detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg.

**`HUE_PRESENCE_MIN_PIXELS`** — The presence floors were swept jointly with the shift gates -- they can fire the rule on their own, so pinning them would have searched the wrong space. Floors chosen by the joint search: pixels=1, fraction=0.0002, at false-alarm rate 0.01 and detection limit extent=0.05: 60.0deg, extent=0.25: 15.0deg, extent=1: 15.0deg.

**`CELL_MIN_SUPPORT_PIXELS`** — 904 cell pairs from foreground-mode control comparisons, bucketed by foreground support. Rule: smallest support bucket whose p99 divergence, and every larger bucket's, is <= 2x the best-supported bucket's p99. Reference p99 divergence at best support: 0.192025.

**`BACKGROUND_DOMINANT_MAX`** — Foreground-fraction sweep: the smallest measured foreground share at which a *total* object recolour still clears the full-frame control thresholds on all of ['changed_area_fraction', 'structural_dissimilarity', 'hue_family_delta_max'] is None. The flag must fire at or below that share, so the constant is sound only if it sits at or above it.

**`FOREGROUND_MIN_FRACTION`** — Same sweep, foreground mode: the smallest measured foreground share at which foreground-mode control noise stays under its own thresholds is 0.022588. Below that the mask is unstable and foreground statistics are noise, so foreground_too_small must fire.

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
| `exposure_down` | code values (-) | 16 | 64 | —† | 64 | 64 | —† | 4 | 8 | — |
| `exposure_up` | code values (+) | 16 | 64 | — | 64 | 64 | 32 | 4 | 8 | — |
| `gaussian_blur` | radius px | 1 | 8 | — | 8 | 2 | 2 | —† | — | — |
| `hue_rotation @ extent 0.05` | degrees | 60 | —† | —† | — | 120 | — | —† | — | —† |
| `hue_rotation @ extent 0.25` | degrees | 15† | —† | —† | 30 | 15 | — | 120 | — | —† |
| `hue_rotation` | degrees | 15 | —† | 30 | 15† | 15 | — | 15† | — | —† |
| `jpeg_reencode` | quality loss (100 - quality) | — | — | — | — | —† | —† | —† | 70† | — |
| `region_recolour` | region fraction of frame | 0.02 | — | 0.05 | 0.02 | 0.02 | 0.02 | 0.02 | 0.05 | — |
| `rescale_roundtrip` | downscale loss (1 - factor) | 0.5 | 0.92 | — | — | 0.75 | 0.92 | —† | —† | — |
| `saturation_down` | saturation_loss (1 - factor) | 0.1 | 0.75 | 0.75 | 0.25 | 0.5 | 0.25 | 0.1 | 0.1 | —† |
| `saturation_up` | saturation_gain (factor - 1) | 0.1 | — | 2 | 0.1 | — | 0.25 | 0.1 | 0.25 | —† |
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
| `accent_fraction_delta_abs` | 26 | 13 | 0.5 | 4 | keep |
| `accent_palette_distance` | 26 | 12 | 0.461538 | 3 | keep |
| `accent_palette_distance_de2000` | 26 | 10 | 0.384615 | 3 | keep |
| `ahash_distance` | 26 | 13 | 0.5 | 6 | keep |
| `base_palette_distance` | 26 | 15 | 0.576923 | 3 | keep |
| `base_palette_distance_de2000` | 26 | 14 | 0.538462 | 2 | keep |
| `changed_area_fraction` | 26 | 6 | 0.230769 | 3 | keep |
| `dhash_distance` | 26 | 16 | 0.615385 | 5 | keep |
| `entropy_delta_abs` | 26 | 24 | 0.923077 | 9 | **DEMOTE** |
| `hue_family_delta_l1` | 26 | 8 | 0.307692 | 5 | keep |
| `hue_family_delta_max` | 26 | 8 | 0.307692 | 2 | keep |
| `luminance_mean_delta_abs` | 26 | 11 | 0.423077 | 10 | keep |
| `saturation_mean_delta_abs` | 26 | 10 | 0.384615 | 5 | keep |
| `structural_dissimilarity` | 26 | 15 | 0.576923 | 5 | keep |

**Perceptual colour (ΔE2000): derived** for `accent_palette_distance_de2000`, `base_palette_distance_de2000`.

## Limitations — documented, not solved

- **Synthetic perturbations are independent and uniform; real revisions are
  correlated and semantic.** A hue rotation over a band of rows is not what a
  re-render does. This calibration will systematically *underestimate* difficulty
  on real input, so every detection limit here is a best case.
- ~~**No real-image validation set is included.**~~ **Gate closed 2026-08-20.**
  The thresholds derived here were validated against a real production corpus —
  the Black Order Swordsman iteration renders — and passed: zero full-frame
  false alarms over 160 real no-change controls, 21 of 24 real revision pairs
  detected in both modes, and the three undetected ones sub-limit exactly as the
  published `region_recolour` detection limit (2% of frame) predicts. See
  [`runs/2026-08-20-phase2-real-validation/README.md`](../2026-08-20-phase2-real-validation/README.md),
  including two gate criteria that were refined mid-run with the strict results
  preserved. The corpus is one asset from one pipeline, so this demonstrates
  *transfer*, not generalisation.
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
- **The alpha foreground path is uncalibrated.** Every calibration scene is
  opaque, so `ALPHA_FOREGROUND_MIN` and the alpha branch of `foreground_mask`
  were never exercised. Only the border-median colour path is measured here.
  The RGBA corpus added on 2026-08-20 (`calibration/scenes.py` `ALPHA_CORPUS`,
  `tests/test_alpha_foreground.py`) exercises it and documents what it gets
  wrong; the constants here are still derived from opaque input only.
- **The foreground luminance threshold is set by one control family, and that
  family perturbs placement.** Read the by-family breakdown for
  `luminance_mean_delta_abs` in foreground mode: `rescale_roundtrip` has median
  **27.460** and max 34.998, while *every* other family sits at or under
  **1.564** (`subthreshold_blur` 1.452, exposure 1.000, saturation 0.330, noise
  0.237, hue 0.125, identical and re-encode 0.000). The published threshold of
  **34.12895 is therefore a statement about resampling a thin object, not about
  foreground measurement noise in general**, and it is roughly 20x looser than
  the same metric's floor for any change that does not move the object on the
  pixel grid. Two consequences, both open:
  1. A foreground luminance verdict that clears 34.129 today has been graded
     against the most permissive possible bound. Non-placement changes below
     ~20 code values are inside the noise floor of one family only.
  2. That looseness is not merely conservative — the 2026-08-20 corpus shows a
     *sub-pixel* re-render of an unchanged object moving the reported foreground
     luminance by up to 21.56 code values while the object itself moves 3.82.
     The threshold appears to have absorbed a measurement defect rather than
     measured around it. Re-deriving it after the alpha/coverage fix should
     tighten it substantially; until then, treat 34.129 as a placement-noise
     bound and not as a sensitivity claim.
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
