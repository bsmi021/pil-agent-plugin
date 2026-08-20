# Phase 2 scope — trustworthy verdicts

Last updated: 2026-08-19
Status: **implemented** (0.3.0). WP1 → `scripts/pil_color.py` + the `*_de2000`
fields and `--accent-space lch`; WP2 → `calibration/` and the
[calibration bundle](../runs/2026-08-19-phase2-calibration/README.md), with
verdict-by-verdict application recorded in `scripts/pil_common.py`'s constant
comments (one derivation rejected, with the reason recorded there); WP3+WP4 →
`scripts/pil_contract_verdict.py`. WP2's real-image validation gate **closed
2026-08-20** — see
[`runs/2026-08-20-phase2-real-validation/README.md`](../runs/2026-08-20-phase2-real-validation/README.md).
One gate remains open and is stated in the bundle: the alpha foreground path is
uncalibrated (every calibration and validation image is opaque).

Originally proposed 2026-08-18 and revised once against
[phase 2 research findings](research-phase2-colour-and-calibration.md) — WP1's
verification gate passed before implementation, and WP2's threshold methodology
changed as a result of that research.

## Premise

Phase 1 built primitives and proved they work. It also proved two things that
block using them for real decisions:

1. **Raw metrics answer the wrong question.** A caller never asks "how similar are
   these" — they ask "did my requested change land, and did anything else drift?"
   Nothing currently distinguishes intended change from regression.
2. **The thresholds are unproven.** Every numeric boundary was validated against
   one image and two variants derived from it. They are guesses that happened to
   work once.

Phase 2 fixes both, staying domain-agnostic. The Blender character-sheet loop is
phase 3 and builds on this.

## In scope

### WP1 — Perceptual colour distance

Replace Euclidean RGB palette distance with CIELAB + CIEDE2000, computed in numpy
against the existing dependency set — no new dependencies for a plugin users
install.

RGB distance is not perceptually uniform, which is why phase 1 demoted it to
supporting detail after it scored a genuinely recoloured image as *more* similar
than an unchanged rescale.

**Gate: PASSED.** The Sharma, Wu & Dalal (2005) verification dataset was located
and downloaded byte-exact from the first author's server (34 pairs, 1830 bytes),
the formula was transcribed from the authors' own reference MATLAB implementation,
and an independent numpy implementation reproduced **all 34 published values with
maximum absolute error 0.0000** at the published 4-decimal precision. The
transcribed table in the research document was additionally machine-checked
float-for-float against the primary file, because that table — not the scratch
implementation — is what becomes the RED tests. Details and the full dataset:
[`research-phase2-colour-and-calibration.md`](research-phase2-colour-and-calibration.md).

WP1 therefore ships. Its 34 RED tests are already specified.

**Correction to an earlier assumption in this document.** ΔE2000 does *not* come
with authoritative interpretation bands. The research found that the widely-cited
0–1/1–2/2–10 banding table is disclaimed by its own author, that "ΔE 1.0 = just
noticeable difference" has no traceable primary source, and that no Sharma or
Huang paper on ΔE00 perceptibility thresholds exists to cite. What ΔE2000 gives
is a *verified, perceptually-uniform* number — the decision threshold on that
number must still come from WP2 measurement. Any verbal band the tool emits must
be labelled a literature heuristic with its source, not presented as measurement.

**Design constraints established by measurement:**

- **Never normalise ΔE00 by 100 or present it as a percentage.** It is not bounded
  at 100. Black-versus-white happens to give exactly 100.000000, which invites the
  error, but the maximum over the sRGB cube corners is **111.41** and over a
  216-colour sRGB grid **119.22**. Black-vs-white is a landmark, not a maximum.
- **Do not use Pillow's `convert("LAB")`.** It works in Pillow 12.3.0 (contrary to
  an open Pillow issue, which the research falsified), but it is D50-referenced and
  8-bit quantised, and its byte encoding is undocumented — established only by
  experiment. Hand-roll the D65 chain in numpy.
- Report raw ΔE00 unrounded beyond 4 dp.

**Sub-item promoted from "possible" to "in scope": move the accent mask to LCh.**
The research measured concrete failure modes in the current `HSV_S > 100 and
HSV_V > 60` rule and rates the LCh chroma/lightness gate a clear, large win.

**Sub-item moved out of WP1 into WP2: hue-family boundaries.** LCh is also the
better space for hue naming, but the research found **no authoritative CIELAB
hue-angle boundary set for basic colour names** — the two application-specific
range sets it located are mutually inconsistent. Porting the current 0–255 HSV
bounds into degrees would be worse than doing nothing. The boundaries must be
derived by measurement, which makes them WP2's problem, not WP1's.

### WP2 — Threshold calibration

Replace guessed constants with derived ones: `HUE_SHIFT_MIN_ABSOLUTE`,
`HUE_SHIFT_MIN_RELATIVE`, `DEFAULT_ACCENT_SAT_MIN`, `DEFAULT_ACCENT_VAL_MIN`,
`CHANGE_THRESHOLD`.

**Method: synthetic pairs with known ground truth.** A corpus of real images
cannot calibrate anything, because it carries no labels — there is no ground truth
for "how different" two arbitrary images are. Synthetic perturbation supplies
exact ground truth: rotate hue by a known number of degrees across a known
fraction of pixels, then observe what the metric reports.

Also absorbs the LCh **hue-family boundaries** displaced from WP1, since those
must be derived rather than ported.

**Threshold rule: Neyman–Pearson with a fixed false-alarm budget**, per the
research's recommendation, which explicitly argues against the ROC approaches this
document originally assumed:

- **Not Youden's J** — it requires a well-sampled positive class matching
  deployment. Here the positive class is fabricated by our own perturbation
  generator, so maximising J optimises for the fabrication.
- **Not equal-error rate** — EER asserts symmetric costs, and we have stated that
  false negatives cost more. Wrong shape of answer.
- **Neyman–Pearson fits** because it only needs the *negative* class
  well-characterised — which is the class we can generate densely — and because
  fixing α and maximising power is exactly the "lowest threshold whose false-alarm
  rate is tolerable" construction the cost asymmetry calls for.

Procedure per metric: measure the metric across the no-change control set, set
`threshold = Q(1−α)` taking the **upper bound of a bootstrap CI** on that quantile
(≥1000 resamples) rather than the point estimate.

**α is constrained by control-set size, not chosen by taste.** Bootstrap resamples
with replacement and so cannot extrapolate past the observed maximum; at n=20,
`Q(0.99)` degenerates to the maximum and its confidence interval is a fiction.
Working rule `n ≳ 3/α`:

| α | controls needed |
|---|---|
| 0.01 | ~300 |
| 0.05 | ~60 |
| 0.10 | ~30 |

Synthetic controls can reach 300; a captured-twice control cannot. If the union
control set falls short, use α=0.05 and say so in the ledger rather than quoting a
99th percentile that is really just the maximum. **Record n, α, the point estimate
and the CI upper bound together — a threshold without its n is not
interpretable.**

Deliverables:

- A perturbation generator covering hue rotation, saturation shift, exposure
  shift, blur/detail loss, noise, translation, scale, compression artefacts, and
  partial-region edits — each parameterised by magnitude, over an extent ×
  intensity grid.
- No-change controls establishing the noise floor: identical file, re-encode,
  rescale round-trip, and sub-threshold perturbation.
- Response curves per metric per perturbation type, checking monotonicity in
  magnitude.
- Derived thresholds, each recorded with its n, α and bootstrap CI.
- **A published detection limit per metric per perturbation type** — the smallest
  ground-truth perturbation that exceeds the threshold. This is the most valuable
  output of WP2: it is what tells a calling agent how to interpret a *null* result,
  and it feeds directly into WP3 (see below).
- Derived LCh hue-family boundaries and the vivid-accent chroma/lightness gate.
  The research's suggested `C_MIN = 20`, `L_MIN = 20` are *reasoned from measured
  sRGB values, not calibrated*, and are inputs to this work rather than answers.
- A small real-image validation set, to check synthetic-derived thresholds do not
  fall apart on genuine input.
- **Demotion is an acceptable outcome.** If a metric's detection limit is worse
  than the smallest change anyone cares about, demote it, exactly as phase 1 did
  with palette distance. Phase 1's discrimination matrix was this analysis done
  informally; WP2 makes it rigorous.

**Known limitations to document, not solve:**

- Synthetic perturbations are independent and uniform; real revisions are
  correlated and semantic. Calibration will systematically underestimate difficulty
  on real inputs. The validation set bounds that error rather than eliminating it.
- **No academic precedent was found** for visual-regression or screenshot-diff
  threshold calibration specifically. WP2 applies general Neyman–Pearson practice
  to this problem; it is not a validated domain methodology, and should not be
  described as one.

### WP3 — Contract-driven verdicts

The headline deliverable. A caller declares intent; the tool returns a per-item
verdict.

```
INPUT contract:
  expect_change:  palette.warmer
  expect_change:  geometry.poly_count.decrease
  invariant:      layout.composition
  invariant:      identity.silhouette

OUTPUT verdict:
  palette.warmer            SATISFIED     hue: orange +0.14, cyan -0.02
  geometry.poly_count       UNMEASURABLE  needs scene mesh stats
  layout.composition        SATISFIED     structural_similarity 0.94
  identity.silhouette       VIOLATED      bbox [0.1,0.4,0.3,0.9] diverged
```

Three verdict states, and the third carries the design's weight:

- `SATISFIED` / `VIOLATED` — measured, with the supporting field cited.
- `UNMEASURABLE` — the predicate cannot be evaluated from the available evidence.

**New requirement from WP2 research: every negative finding must carry its
detection limit.** "Invariant satisfied" and "no change detected" are not the same
claim. A metric that cannot resolve changes below some magnitude will report an
invariant as `SATISFIED` when a smaller-but-real change occurred. So a `SATISFIED`
verdict on an invariant must report the detection limit of the metric that decided
it — e.g. *"layout.composition SATISFIED; detection limit: translations below 1.5%
of frame width are not resolvable"*. Without that, a null result reads as a
guarantee it cannot support. This is the same class of honesty failure as
approximating `UNMEASURABLE`, and it is easier to miss.

**`UNMEASURABLE` must never silently degrade into an approximation.** This is
phase 1's central lesson generalised. Edge density looks like a polygon-count
proxy and is not one; a tool that quietly substitutes it produces confident wrong
answers. The contract layer therefore carries an explicit registry of predicates
it *cannot* evaluate, and returns `UNMEASURABLE` with a pointer to what evidence
would be needed.

Predicate vocabulary, to be finalised as part of WP3 design:

| Family | Measurable now | Notes |
|---|---|---|
| `exact.unchanged` | yes | hash distance, changed-area fraction |
| `palette.scheme_preserved` | yes | hue census + ΔE2000 |
| `palette.warmer` / `.cooler` | yes | directional hue-family mass shift |
| `palette.hue_present(x)` | yes | hue census |
| `layout.composition_preserved` | yes | structural similarity, fractional grid |
| `layout.region_changed(bbox)` | yes | changed-region bbox |
| `detail.increased` / `.decreased` | partially | 2D proxy only; must state the caveat |
| `identity.silhouette_preserved` | partially | needs design; likely alpha/luma-threshold outline comparison |
| `geometry.*` | **no** | always `UNMEASURABLE` in phase 2 — no scene access |
| `style.*`, `identity.same_character` | **no** | not reducible to pixel statistics; must refuse |

The last row matters most. Predicates that *sound* measurable but are not
("more stylised", "same character") are exactly where an eager tool does damage.

### WP4 — Multi-pair comparison and aggregation

The domain-agnostic half of multi-view support: accept N image pairs, evaluate a
contract across all of them, and aggregate without letting one diverging pair be
diluted by the rest. Aggregation reports worst-case per contract item alongside
the per-pair detail, since a single broken view is a real failure even when the
mean looks healthy.

View *rendering* and view *matching* are phase 3 — they need Blender.

## Explicitly out of scope (phase 3)

Recorded as decided, not open:

- **Optional Blender mesh-statistics tool.** Agreed it belongs in this plugin as a
  standalone script that runs only when scene data is available, so
  `geometry.poly_count.decrease` can resolve to a real verdict instead of
  `UNMEASURABLE`. Deferred because phase 2 is domain-agnostic.
- **Render-matching-views orchestration.** Agreed approach: render front/side/back
  to match the reference's views and compare 1:1, rather than segmenting a sheet
  heuristically. Deferred for the same reason.
- **The character-sheet revision loop itself**, which composes all of the above.

## Sequencing and gates

| WP | Depends on | Gate to proceed |
|---|---|---|
| WP1 colour | ~~Sharma dataset verified~~ **gate passed** | All 34 published ΔE00 values reproduced to 4 dp |
| WP2 calibration | WP1 (ΔE2000 feeds palette thresholds) | Response curves monotonic; every threshold recorded with n, α and bootstrap CI; detection limits published; thresholds beat current guesses on the real validation set |
| WP3 contracts | WP2 (verdicts need trustworthy thresholds **and** detection limits) | `UNMEASURABLE` correctly returned for every refuse-list predicate; every negative finding carries its detection limit |
| WP4 aggregation | WP3 | A single diverging pair cannot be averaged away |

WP1 and WP2 can run partly in parallel: only palette thresholds depend on ΔE2000,
while structural and change thresholds do not. The LCh accent-mask migration sits
in WP1; the LCh hue *boundaries* sit in WP2, so the two must be sequenced with the
mask first.

## What would make phase 2 a failure

Stated up front so it can be checked honestly at the end:

- ~~Shipping ΔE2000 without verifying it against published values.~~ Averted:
  gate passed before implementation began.
- Thresholds that are merely *differently* arbitrary — derived from synthetic data
  that does not transfer, with no real-image validation to catch it.
- A threshold quoted without the `n` it was estimated from, or an extreme quantile
  reported from a control set too small to estimate it.
- A contract layer that approximates `UNMEASURABLE` predicates rather than
  refusing them, reintroducing the exact failure phase 1 was built to avoid.
- A `SATISFIED` invariant presented as a guarantee, with no detection limit
  attached to bound what it actually rules out.
- Normalising ΔE00 to a 0–100 scale or a percentage, which the measured maximum of
  119.22 makes wrong.
- Verdicts confident enough to be trusted but resting on uncalibrated primitives.

## Open questions

1. **`identity.silhouette_preserved`** — needs a concrete definition. Outline
   extraction from an alpha channel is straightforward; from a photograph or a
   render on a busy background it is not. Possibly restrict to
   transparent-background renders and return `UNMEASURABLE` otherwise.
2. **Contract input format** — JSON file, CLI flags, or both. JSON is better for
   reproducibility and for an agent composing it; CLI is better for quick manual
   use.
3. **Whether `detail.*` should exist at all**, given it can only ever be a 2D
   proxy. The alternative is refusing it and routing every detail question to
   geometry in phase 3. Leaning toward keeping it with a mandatory caveat in the
   output, since it is legitimately useful for 2D-only comparisons where no
   geometry exists.
