# Briefing: phase 2 research — perceptual colour distance and threshold calibration

## Context

Repo: `C:\Projects\pil-agent-plugin`. A Claude Code plugin providing quantitative
image measurement to coding agents, complementing native multimodal vision.
Phase 1 is complete: 37 tests green, `claude plugin validate --strict` passing.

Read these first for full context:

- `README.md` — tool overview and field semantics
- `docs/design-rationale.md` — why each metric exists, and what failed
- `runs/2026-08-18-pil-agent-plugin-phase1/10-metric-discrimination-matrix.md`
  — the measured finding that only 4 of 11 metrics discriminate a real change
  from a no-op
- `scripts/pil_common.py` — current implementation of the primitives

Environment: Windows 11, Python 3.13.7, uv 0.8.22, Pillow 12.0.0, numpy 2.4.4,
pytest 9.0.3. Invoke Python as `uv run python`, never bare `python3`. The project
uses strict TDD (RED before GREEN, AAA pattern).

## Two known weaknesses this research must address

1. **Palette distance is Euclidean RGB**, which is not perceptually uniform. It is
   currently demoted to "supporting detail only" because it is untrustworthy — it
   once scored a genuinely recoloured image as *more* similar than an unchanged
   rescale. Phase 2 intends to replace it with CIELAB + ΔE2000.

2. **Every threshold rests on one image and two derived variants.** Specifically
   `HUE_SHIFT_MIN_ABSOLUTE = 0.02`, `HUE_SHIFT_MIN_RELATIVE = 0.30`,
   `DEFAULT_ACCENT_SAT_MIN = 100`, `DEFAULT_ACCENT_VAL_MIN = 60`,
   `CHANGE_THRESHOLD = 10`. These are guesses that happened to work once.

## Questions to answer

### A. CIELAB and ΔE2000 without new dependencies

Current deps are Pillow and numpy only. Adding scikit-image or colormath is
undesirable for a plugin users install.

1. Full, precise **sRGB → linear RGB → XYZ → CIELAB** conversion chain. Give the
   exact matrices and the correct sRGB companding function (the piecewise one, not
   a plain 2.2 gamma), plus the D65 white point values. State which illuminant and
   observer you are assuming.
2. The complete **CIEDE2000 (ΔE00)** formula, every term, including the
   hue-rotation term R_T, the arctangent quadrant handling, and the hue-difference
   mean H̄' special cases. This formula is notorious for subtle errors — be exact
   and flag every place an implementation typically goes wrong.
3. **CRITICAL — verification data.** There is a standard test set from Sharma,
   Wu & Dalal (2005) of ~34 Lab pairs with published expected ΔE00 values,
   designed specifically to exercise the formula's discontinuities. Provide as
   many of those pairs as you can find, as concrete numbers
   (`L1,a1,b1, L2,a2,b2, expected_dE00`). These become the RED tests. If you can
   only find some, say exactly how many and which source.
4. Does **Pillow have a usable built-in `LAB` mode conversion**, and if so is it
   accurate enough to trust, or does it quantise/clip in ways that make a
   hand-rolled numpy conversion preferable? Be specific about Pillow 12.
5. Practical ΔE00 interpretation thresholds — what value is "just noticeable
   difference", what is "obviously different"? Cite sources; these will inform
   default thresholds.

### B. Should hue bucketing move from HSV to LCh?

Current hue families use PIL's HSV H channel (0–255) with hand-tuned bucket
bounds, and the "accent" mask is an HSV saturation/value threshold.

1. Is **LCh(ab)** (cylindrical CIELAB) materially better for (a) deciding whether
   a pixel is a vivid accent vs a dark neutral, and (b) assigning a pixel to a
   named hue family? Explain the concrete failure modes of the HSV approach.
2. If yes, give sensible **chroma (C*) and lightness (L*) thresholds** for
   "vivid accent", and hue-angle ranges in degrees for named families
   (red/orange/yellow/green/cyan/blue/purple/magenta).
3. Note any hue-angle **wraparound** hazards, and whether perceptual hue families
   are evenly spaced in LCh hue angle (they are not in HSV — is LCh better or
   just differently uneven?).

### C. Threshold calibration methodology

The plan is to generate **synthetic image pairs with known ground-truth
perturbation magnitudes** (a known hue rotation of known degrees applied to a
known fraction of pixels; a known layout shift; a known blur/detail reduction),
measure each metric's response, and derive thresholds from where the metric
reliably separates real change from no-change noise.

1. Is this a sound approach, and what are its **specific weaknesses**? What will
   synthetic calibration systematically get wrong about real images?
2. What is standard practice for **picking a threshold from a response curve** —
   ROC with Youden's J, equal-error rate, fixed false-positive budget, something
   else? Recommend one for this case (small sample, cost of false-negative
   arguably higher than false-positive, since missing a real regression is worse
   than a spurious warning) and justify it.
3. What **perturbation types** should the synthetic corpus cover to be
   representative? Consider at minimum: hue rotation, saturation shift,
   lightness/exposure shift, blur (detail loss), noise addition, geometric
   translation/scale, JPEG-style compression artefacts, and partial-region edits.
4. How should the **no-change control** be constructed? Candidates: identical
   file, re-encode, rescale round-trip, tiny sub-threshold perturbation. Which
   controls matter for establishing a noise floor?
5. Any **published work on calibrating image-similarity thresholds** for
   render-vs-reference comparison specifically, or perceptual-metric validation
   generally (e.g. how LPIPS/SSIM/DISTS papers establish operating points)? Cite
   what you find.

## Output requirements

Write your findings to `docs/research-phase2-colour-and-calibration.md` in the
repo. Structure it with the same A/B/C headings so it can be cross-referenced.

For every formula, give it in a form directly translatable to numpy. For every
numeric claim, cite the source. **Explicitly separate what you verified from
authoritative sources from what you are recalling** — a wrong ΔE2000 constant
produces plausible-looking but incorrect numbers, which is the worst outcome
here. If you cannot verify the Sharma test data, say so prominently rather than
reconstructing values from memory.

Do not write any implementation code and do not modify any file other than the
research document you are creating.
