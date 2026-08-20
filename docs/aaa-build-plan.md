# AAA build plan — alpha/coverage fix, foreground recalibration, phase 3 Track A

Status: **design only, awaiting sign-off.** Nothing here has been implemented.
Author role: architect. Every work item below is written to be executed by a
different agent, so the file-ownership register in §1 is a hard constraint, not
advice — this build has already lost work three times to two agents editing one
file.

Target release: **0.4.0** across all manifests and every `TOOL_VERSION`.

Everything in this document inherits the repository's discipline: *never claim
more than you measured*. Where I could not decide without a measurement, the
item is listed in §11 as open, with the specific measurement that closes it —
not guessed at.

**Two findings from the design pass change the shape of the work and must be
read before anything else:**

1. **Only 3 of the 6 `xfail(strict)` tests in `tests/test_alpha_foreground.py`
   can flip on the briefed fix.** One (`test_accent_gate_admits_every_vivid_fringe_pixel`)
   asserts a property of `load_rgb_alpha`, whose contract the fix deliberately
   does not change, so it can never flip as written. Two
   (`TestMaskProvenance`'s pair-agreement tests) compare the alpha path against
   the border-median path, and a composited render carries **no coverage
   information at all** — the arithmetic is in §3.7. Handling this without
   weakening a test is W1's most delicate task.
2. **W2 as briefed re-derives thresholds that W1 does not move.** Every
   foreground threshold in the bundle comes from `thin_object`, which is
   *opaque*, so it exercises the border-median path that W1 leaves untouched.
   Re-deriving after W1 changes those numbers only if the control corpus gains
   alpha scenes. §4 splits W2 accordingly and withdraws the implied prediction
   that the foreground luminance threshold collapses.

---

## Provenance of this document — three limits on what it rests on

Stated first, because the plan inherits the repository's discipline and must not
exempt itself from it.

1. **`tools/synty_asset_index/palette.py` was NOT read while writing this.** The
   task asked whether it could be reached. It could not: the `tms-heim` workspace
   is outside this session's permitted directories and the read was refused by the
   harness. Every claim here about that file's convention — `alpha >= 8`
   membership, `alpha / 255.0` weight per visible pixel, colour never composited
   before weighting, `has_transparency` disabling background subtraction — is
   **second-hand**, taken from `calibration/alpha_truth.py`'s module docstring,
   which pins those claims to a reading made on 2026-08-20 and says so itself.
   W1's owner must re-verify against the real file if their environment can reach
   it, and record the verification — or its impossibility — in the W1 bundle.
   Do not upgrade the claim's confidence without re-reading it. Note also
   `alpha_truth.py`'s own honest limit: palette.py applies that convention to
   *palette extraction only*; extending it to luminance, saturation and accent
   fraction was this repository's step, not a convention it converges on.

2. **No command was executed while writing this.** Test collection and
   `uv run pytest` were permission-blocked in the authoring session. The test
   counts quoted in §10 D1 come from the task brief and from reading the files,
   not from a collection run. The binding criterion everywhere is the stated
   command's exit status, never a count someone remembered.

3. **Every number in this document was read out of a committed file** —
   `tests/test_alpha_foreground.py`'s reason strings, `calibration/alpha_truth.py`,
   the two `runs/` bundles, `scripts/pil_common.py`'s constant comments. Nothing
   here was measured by the author. After W1 and W2 land, these numbers are
   history: W7 must cite the new bundles, never this plan.

---

## Contents

1. [Dependency graph, file ownership, and the collision register](#1-dependency-graph-file-ownership-and-the-collision-register)
2. [W0 — `scripts/pil_region.py`, the shared fractional-bbox contract](#2-w0--scriptspil_regionpy-the-shared-fractional-bbox-contract)
3. [W1 — the alpha/coverage fix](#3-w1--the-alphacoverage-fix)
4. [W2 — re-derive the foreground thresholds honestly](#4-w2--re-derive-the-foreground-thresholds-honestly)
5. [W3 — `pil_crop.py` (phase 3 A1)](#5-w3--pil_croppy-phase-3-a1)
6. [W4 — `pil_annotate.py` (phase 3 A2)](#6-w4--pil_annotatepy-phase-3-a2)
7. [W5 — `pil_image_info.py` (phase 3 A3)](#7-w5--pil_image_infopy-phase-3-a3)
8. [W6 — `--region FRACTIONAL_BBOX` on both existing tools (phase 3 A4)](#8-w6--region-fractional_bbox-on-both-existing-tools-phase-3-a4)
9. [W7 — documentation and release](#9-w7--documentation-and-release)
10. [Definition of DONE for the whole build](#10-definition-of-done-for-the-whole-build)
11. [Open questions](#11-open-questions)

---

## 1. Dependency graph, file ownership, and the collision register

### 1.1 Graph

```
                W0  pil_region.py  (small, first, write-once)
                 |
     +-----------+-----------+-----------+
     |           |           |           |
     v           v           v           |
    W3 crop     W4 annot    W5 info      |     W1  alpha/coverage fix
     |           |           |           |      |
     |           |           |           v      v
     |           |           |          W6  --region on both tools
     |           |           |           |      |
     |           |           |           |      v
     |           |           |           |     W2a widen foreground controls
     |           |           |           |      |
     |           |           |           |      v
     |           |           |           |     W2b RGBA control family
     |           |           |           |      |
     +-----------+-----------+-----------+------+
                             |
                             v
                            W7  docs + release
```

Read as: **W0 and W1 start immediately and in parallel** (disjoint files).
**W3, W4, W5 start as soon as W0's file is committed** and run fully in
parallel with each other and with W1. **W6 starts only after W1 is merged**,
because both edit `pil_common.py`, `pil_palette_diff.py` and
`pil_structure_diff.py`. **W2 starts only after W1 is merged**, because it
measures W1's tools. **W7 is last**, because it bumps `TOOL_VERSION` in files
every other item edits.

The graph's vertical stacking of W6 above W2a is drawing convenience, **not a
dependency**: W6 and W2a share no file (W6 edits `scripts/`, W2a edits
`calibration/` plus the generated `scripts/detection_limits.json`, which W6
never touches) and they run concurrently. Both depend on W1 and nothing else.
Serializing them would waste the build's longest parallel window.

| Item | Starts after | Runs in parallel with | Blocks |
|---|---|---|---|
| W0 | — | W1 | W3, W4, W5, W6 |
| W1 | — | W0, W3, W4, W5 | W6, W2 |
| W3 | W0 merged | W1, W4, W5 | W7 |
| W4 | W0 merged | W1, W3, W5 | W7 |
| W5 | W0 merged | W1, W3, W4 | W7 |
| W6 | W1 merged | W2a, W3–W5 | W7 |
| W2a | W1 merged | W6, W3–W5 | W2b |
| W2b | W2a merged | W6, W3–W5 | W7 |
| W7 | all of the above | — | — |

W2b is the item most likely to be descoped (see §4.3). If it is, W7 proceeds
and the release ships with the alpha path **still uncalibrated but now
correct** — which must be said in exactly those words in `docs/index.md`.

### 1.2 File ownership register

One writer per file per item. A file appearing twice in the *same* column
group is a collision and is called out in §1.3.

| Item | Files it may create or modify (exhaustive) |
|---|---|
| **W0** | `scripts/pil_region.py` (new), `tests/test_region_parsing.py` (new) |
| **W1** | `scripts/pil_common.py`, `scripts/pil_palette_diff.py`, `scripts/pil_structure_diff.py`, `tests/test_alpha_foreground.py`, `tests/test_alpha_weighting.py` (new), `calibration/alpha_truth.py` (**docstrings only**), `runs/2026-08-2X-alpha-coverage-fix/**` (new bundle) |
| **W2a** | `calibration/scenes.py`, `calibration/measure.py`, `calibration/derive.py`, `calibration/run_all.py`, `calibration/distill_detection_limits.py`, `scripts/detection_limits.json` (generated), `runs/2026-08-2X-foreground-recalibration/**` (new bundle), `runs/2026-08-19-phase2-calibration/README.md` (**append a supersession pointer only**) |
| **W2b** | same set as W2a, plus `calibration/perturb.py` |
| **W3** | `scripts/pil_crop.py` (new), `tests/test_crop.py` (new) |
| **W4** | `scripts/pil_annotate.py` (new), `tests/test_annotate.py` (new), `runs/2026-08-2X-annotate-readback/**` (new bundle) |
| **W5** | `scripts/pil_image_info.py` (new), `tests/test_image_info.py` (new) |
| **W6** | `scripts/pil_common.py`, `scripts/pil_palette_diff.py`, `scripts/pil_structure_diff.py`, `tests/test_region_mode.py` (new) |
| **W7** | `README.md`, `skills/image-measurement/SKILL.md`, `agents/image-comparison-analyst.md`, `docs/index.md`, `docs/phase3-scope.md`, `plugin.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `pyproject.toml`, `tests/test_packaging_conformance.py`, and **only** the `TOOL_VERSION = "..."` line in each of `scripts/pil_palette_diff.py`, `scripts/pil_structure_diff.py`, `scripts/pil_contract_verdict.py`, `scripts/pil_crop.py`, `scripts/pil_annotate.py`, `scripts/pil_image_info.py` |

Files **no item may touch**: `scripts/pil_color.py`, `scripts/pil_contract_verdict.py`
(beyond W7's one-line version bump), `tests/test_ciede2000.py`,
`tests/test_contract_verdict.py`, `tests/test_palette_diff.py`,
`tests/test_palette_perceptual.py`, `tests/test_structure_diff.py`,
`tests/test_foreground_mode.py`, `tests/conftest.py`,
`runs/2026-08-20-phase2-real-validation/**`, and every historical run bundle
other than the one supersession line W2a adds. If an item believes it needs to
edit one of these, that is a design escape and goes back to the architect.

### 1.3 Collision register — every place two items would touch one file

These are the failure sites. Each has a stated resolution.

| File | Contending items | Resolution |
|---|---|---|
| `scripts/pil_common.py` | W1 (coverage weighting), W6 (region resolution helpers) | **Serialize: W1 then W6.** W6 must rebase on merged W1, never branch from `main`. W6 adds only new functions; it must not modify any function W1 touched. |
| `scripts/pil_palette_diff.py` | W1, W6, W7 | **Serialize W1 → W6 → W7.** W7 touches only the `TOOL_VERSION` line. |
| `scripts/pil_structure_diff.py` | W1, W6, W7 | Same. |
| `INTERPRETATION_LIMITS` lists inside the two diff tools | W1 (alpha/coverage wording), W6 (region wording), W7 (would like to edit prose) | **W7 may not edit these lists.** The list inside a tool file is owned by whichever item changed that tool's behaviour. W7 owns prose only in `README.md`, `SKILL.md`, the agent file and `docs/`. This is the single most likely accidental collision in the build. |
| Fractional-bbox parsing | W3, W4, W6 (all need it; W5 does not) | **W0 owns `scripts/pil_region.py` and is its only writer.** W3/W4/W6 import it read-only. A consumer that needs a change files it back to W0's owner; it does not edit the file. Rejected alternative: three private parsers. Rejected because A4's gate is *equality with a file pre-cropped by A1*, and two independent parsers make that equality a coincidence rather than a guarantee. |
| `scripts/detection_limits.json` | W2a/W2b generate it; W1's tests read it | **W1 never writes it.** W1 grades against absolute literals derived from `calibration/alpha_truth.py`, not against this file, precisely so W2 cannot loosen a threshold into making W1's tests pass. See §3.6 and §4.5. |
| `calibration/scenes.py` | W2a/W2b (new scenes), W1 (would like new corpus entries) | **W1 adds no scenes.** If W1 needs a scene it does not have, it files it to W2a. `ALPHA_CORPUS` and every existing builder are frozen for the duration; `test_every_corpus_scene_builds_byte_identical_pngs` is the guard. |
| `calibration/alpha_truth.py` | W1 (docstrings), W2b (imports it) | **W1 may edit docstrings only.** `weighted_stats`, `unweighted_stats`, `truth_arrays`, `unpremultiply`, `_floor_sweep` and `_accent_gate_record` are the contract W1 is graded against; changing them to make a test pass is the build's cardinal sin. Reviewer check: `git diff calibration/alpha_truth.py` must contain no change outside a docstring or comment. |
| The four manifests (5 version strings) | W7 only | `plugin.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` (**two** places: `metadata.version` and `plugins[0].version`), `pyproject.toml`. |
| `runs/2026-08-19-phase2-calibration/` | W2a | **Append-only, one pointer line.** The bundle is evidence; it is never regenerated in place. W2a writes a new dated directory. |

---

## 2. W0 — `scripts/pil_region.py`, the shared fractional-bbox contract

### 2.1 Decision and justification

Three items need to turn a fractional bbox into a pixel rect, and phase 3's own
gate for A4 is *"metrics over `--region R` equal metrics over a file
pre-cropped to `R`"*. That gate is only meaningful if `pil_crop` and
`--region` resolve identically **by construction**. One module, one writer, no
behaviour, no I/O — so it can be written, reviewed and frozen in an hour, before
the parallel items begin.

It is deliberately **not** placed in `pil_common.py`: `pil_common` is W1's
working file for the duration, and putting the parser there would recreate the
exact collision this item exists to remove.

### 2.2 The API, frozen

```python
REGION_SPACES = ("frame", "foreground")
DEFAULT_REGION_SPACE = "frame"

def parse_fractional_bbox(text): ...
    """[left, top, right, bottom] as floats, or raise RegionError."""

def resolve_pixel_rect(box, size, origin=(0, 0)): ...
    """(left, top, right, bottom) integer pixel rect, half-open, or raise RegionError."""

def rect_to_fractional(rect, size, origin=(0, 0)): ...
    """The rect re-expressed as fractions of `size`, rounded to 6 dp."""

class RegionError(ValueError): ...
```

Accepted input spellings, both, because the tools emit the first and humans
type the second:

* `"[0.1, 0.4, 0.3, 0.9]"` — a JSON list, exactly what
  `changed_region_bbox_fractional` and `bbox_fractional` emit.
* `"0.1,0.4,0.3,0.9"` — bare comma-separated.

Rejections, all raising `RegionError` with the offending value in the message:
not four values; a non-numeric value; `left >= right` or `top >= bottom`
(inverted or degenerate); any coordinate outside `[0.0, 1.0]`; a resolved rect
with zero width or height.

**Rounding rule, pinned:** each edge independently,
`px = int(math.floor(fraction * extent + 0.5))`, then clamped to `[0, extent]`.
Half-up, not Python's `round()` (banker's rounding sends `0.5` to `0` and `1.5`
to `2`, which is deterministic but asymmetric and impossible to reason about at
a glance). If `right <= left` or `bottom <= top` after resolution, **reject** —
never widen to a minimum 1px rect, because a silently-widened rect is a
measurement over pixels the caller did not ask for.

`origin` exists so `region_space="foreground"` can resolve fractions against a
foreground bbox that is itself offset inside the frame: the caller passes the
bbox's own `(width, height)` as `size` and its `(left, top)` as `origin`, and
gets back a rect in **frame** coordinates.

### 2.3 Deliverables

* `scripts/pil_region.py`, no imports beyond `json` and `math` — in particular
  no Pillow and no numpy, so every tool can import it before opening a file.
* Every public function's docstring states **why it exists**, not what it does:
  `resolve_pixel_rect`'s docstring must say that the rule is half-up and
  independent per edge *so that A1's crop and A4's region resolve to the same
  pixels*, and that rejection is preferred to clamping *because a clamped region
  silently measures something else*.
* `tests/test_region_parsing.py`.

### 2.4 Acceptance criteria

| # | Criterion |
|---|---|
| A0.1 | `python -c "import pil_region"` succeeds with neither Pillow nor numpy importable (verify by `-c` with a stub that raises on those imports, or simply assert `pil_region.__dict__` has no `Image`). |
| A0.2 | `parse_fractional_bbox` returns identical output for `"[0.1,0.4,0.3,0.9]"` and `"0.1, 0.4, 0.3, 0.9"`. |
| A0.3 | Each of the six rejection classes raises `RegionError`; `pytest -k region_parsing` covers all six by name. |
| A0.4 | Enumerated rounding fixtures, hand-computed in the test file, at **odd** extents where rounding decides: at `extent=101`, `0.5 → 51`; at `extent=37`, `0.5 → 19`, `0.333 → 12`; at `extent=3`, `[0.0,0.34) → 0` and `0.34 → 1`. Each expected value is written as a literal with the arithmetic in a comment. |
| A0.5 | `resolve_pixel_rect` with `origin` returns frame coordinates: `resolve_pixel_rect([0,0,1,1], (50,20), origin=(10,5)) == (10,5,60,25)`. |
| A0.6 | `rect_to_fractional(resolve_pixel_rect(b, s), s)` round-trips to within 1/min(s) of `b` for 20 enumerated cases — asserting the *reported* fraction is the one actually cut, not the one requested. |

### 2.5 How a plausible implementation could be wrong

* **Uses `round()`.** Passes casual tests, then disagrees with a hand-computed
  expectation at exactly `.5`, and A4's equality gate fails intermittently by
  one pixel on some sizes only. Detect: fixture A0.4 at `extent=101`.
* **Clamps instead of rejecting.** A region of `[0.1, 0.1, 0.1, 0.9]` becomes a
  1px column and the tool cheerfully measures it. Detect: A0.3.
* **Validates the fractions but not the resolved rect.** `[0.500, 0.5, 0.501, 0.9]`
  on a 100px-wide image resolves to `left == right`. Detect: A0.3's sixth class.
* **Accepts `[0,0,1,1]` as "no region" and returns `None`.** Then `--region 0,0,1,1`
  and no `--region` become indistinguishable in the payload, and W6's
  `test_region_full_frame_equals_no_region` becomes vacuous because it is
  comparing a payload against itself. The full-frame region must resolve to a
  real rect and be echoed as one.

---

## 3. W1 — the alpha/coverage fix

### 3.1 The defect, restated from measurement

`load_rgb_alpha` alpha-composites RGBA onto **black**, and `foreground_mask`
admits every pixel with `alpha >= 8` at **full weight**. Two independent errors
compound: partial-coverage pixels arrive *darkened* by the composite, and they
are then counted like opaque ones.

Measured consequences already in the repository:

* Foreground luminance bias **−4.26 / −12.88 / −27.88 / −42.84** at blade widths
  40 / 12 / 5 / 3 px, tracking partial-coverage share (9.49% / 28.36% / 59.39% /
  58.77%) rather than anything about the object's colour.
* A **sub-pixel** re-render of an unchanged object moves reported foreground
  luminance by up to **21.56** code values while alpha-weighted truth moves
  **3.82** (`TestSubPixelPlacement`).
* On `glass_a64`, whose interior is a flat alpha 64 over 82.13% of the
  foreground with no fringe at all, the tools read luminance 67.696 against
  truth 175.749 — bias **−108.05**.
* The alpha floor of 8 already discards **4.58% / 12.52% / 23.55% / 32.52%** of
  covered pixels at those widths, 47% at width 1, *before any statistic runs*.
* The hard-edge control (`hard_blade_w12`, `hard_blade_w5`) reads **identically**
  through both mask paths, so the alpha path itself is sound: partial coverage
  is the entire defect.

### 3.2 The fix, and where each operation belongs

Two operations, applied at two different resolutions, for two different
reasons. Confusing them is the single most likely implementation error.

**At full resolution — read the straight colour, do not recover it.**
A PNG stores *straight* (non-premultiplied) alpha. The RGB channels of
`Image.open(path).convert("RGBA")` already *are* the object's true colour at
every partial pixel. Reading them costs nothing and is exact.

**At working resolution — un-premultiply, because there is no alternative.**
The working copy is a LANCZOS resample. Resampling the straight RGB directly
averages the object's colour against the transparent region's stored RGB and
bakes a dark halo into the content — the exact failure `scenes._resolve_rgba`
documents. The correct operation is to resample the **premultiplied** colour
(which is what the composited-on-black image already is), resample the
coverage with the same kernel, and divide. That is `_resolve_rgba`'s pattern,
run in the opposite direction, and it is where the un-premultiply guard earns
its place.

**Weighting.** Every statistic that describes *the object's colour* is weighted
by `alpha/255` per visible pixel — the convention `alpha_truth.weighted_stats`
implements and `tools/synty_asset_index/palette.py` applies to palette
extraction. Statistics that describe *where the object is* are not weighted;
see §3.4.

#### 3.2.1 Deviation from the briefed fix, and why

The brief specifies "un-premultiply partial pixels to recover true colour". I
am proposing to un-premultiply **only at working resolution**, and to read the
stored straight RGB at full resolution instead. The justification is in the
repository's own evidence: `alpha_truth._recovery_record` exists precisely to
record what un-premultiplying the flattened buffer *fails* to recover, because
compositing rounds `colour × coverage` to a byte and no division recovers the
discarded fraction — at alpha 8 that is a possible 16-code-value overshoot,
clipped. The straight read has **zero** recovery error. Un-premultiply is
retained where it is genuinely unavoidable.

Honest limit on the deviation, and it must ship as an `interpretation_limits`
entry: the straight read assumes a spec-conformant straight-alpha PNG. A
producer that writes premultiplied data into a PNG (a spec violation, but it
happens) would be read as darkened. The tool cannot detect this, and does not
claim to.

### 3.3 What carries weights cleanly, and what does not

| Statistic | Weighted? | Decision and justification |
|---|---|---|
| `luminance` mean / std | **yes** | Weighted mean `Σwx/Σw`; weighted std `sqrt(Σw(x−μ̄)²/Σw)`. Clean, and it is exactly what `alpha_truth.weighted_stats` computes for the mean. |
| `saturation` mean / std | **yes** | Same construction, on PIL's own `convert("HSV")` channel 1. |
| `entropy` | **yes** | `np.histogram(lum, bins=256, range=(0,256), weights=w)`, then the usual Shannon sum over `p = h/Σh`. The entropy of a coverage-weighted luminance distribution is well defined and is the honest analogue. `entropy_delta` is demoted anyway, so this is a consistency decision, not a load-bearing one — but leaving it unweighted while everything around it is weighted would make one number in the payload mean something different from its neighbours, which is worse. |
| `accent_pixel_fraction` | **yes** | `Σw over accent∩fg / Σw over fg`, matching `weighted_stats["accent_fraction"]`. |
| `hue_families` | **partly** | `fraction_of_accents` and `fraction_of_frame` become coverage-weighted. `pixels` **stays an integer count** of masked pixels, unchanged in meaning, because `HUE_PRESENCE_MIN_PIXELS` exists to stop a handful of anti-aliased edge pixels flipping the verdict and that gate is a *count* question. A new key `coverage` (Σw, float, 6 dp) is added beside `pixels`. |
| `base_palette`, `accent_palette` (MEDIANCUT) | **coverage weighted, centres not** | See §3.3.1. |
| `fractional_cells` luminance / entropy | **yes** | Weighted with working-resolution weights (§3.4). |
| `fractional_cells` `edge_mean` | **no** | Gradients are already taken on the *unmasked* image on purpose, so silhouette edges — real object structure — survive. A fringe pixel's gradient *is* the object's boundary; damping it by coverage would make a thin object read as progressively less edgy the thinner it gets, which is backwards. |
| `foreground_pixels` per cell | **no** | Integer count. `CELL_MIN_SUPPORT_PIXELS = 64` is a sampling-adequacy gate: it asks how many samples the statistic has, not how much object is in them. |
| `foreground_fraction` per cell | **no**, but a weighted twin is added | `foreground_fraction` keeps its meaning (count / cell size). A new `foreground_coverage_fraction` (Σw / cell size) is added, and `cell_similarity`'s occupancy feature switches to it when present. Rationale: total coverage is conserved under a sub-pixel translation while the pixel *count* is not, so the coverage form is what makes cell occupancy placement-stable. On the opaque path the two are identical by construction, so nothing changes there. |
| `dhash`, `ahash`, `symmetry`, `changed_area_fraction`, `changed_region_bbox_fractional` | **no — untouched** | These run on `apply_mask(working, working_mask)`, i.e. the composited-on-black working copy. Compositing onto black *is* premultiplication by coverage, which is exactly the right thing for a pixel-domain appearance metric: it preserves the anti-aliased silhouette that makes a hash stable. Substituting straight RGB here would give every 1-alpha pixel full brightness against black, hardening the silhouette and *destroying* hash stability. This path is left byte-identical. |
| `foreground.fraction_of_frame` | **no**, but a weighted twin is added | Keeps `mask.mean()`. `FOREGROUND_MIN_FRACTION` is calibrated against it. New sibling keys `coverage_fraction_of_frame` (Σw / frame pixels) and `partial_coverage_share` (share of masked pixels with `alpha < 255`) are added as diagnostics. **No flag and no threshold is attached to `partial_coverage_share`** — an uncalibrated flag threshold is exactly the thing this repository refuses. The number is emitted; the reader decides. |
| `foreground_estimate` (default, non-foreground mode) | **no — untouched** | Full-frame behaviour must not change, and this feeds `background_dominant` in full-frame mode only. |
| `estimate_background`, `_border_samples`, `rgb_to_oklab_array`, `DEFAULT_BACKGROUND_DELTA` | **no — untouched** | §3.5. |

#### 3.3.1 MEDIANCUT: the one statistic that cannot take weights

`Image.quantize` accepts no weights, and MEDIANCUT partitions the pixel
population it is given. Three options were considered:

* **Rejected — coverage replication.** Repeat each pixel `round(w·K)` times in
  the 1×N strip. Approximates weighting inside the quantiser itself, but costs
  `K×` memory and time on every palette call (the strip for a 512×512 render is
  already ~30k pixels; `K=16` makes it half a million per palette, per image,
  four palettes per pair), and `K` is a new uncalibrated constant that changes
  the answer. Bad trade.
* **Rejected — drop pixels below a coverage floor.** Simple, and it deletes the
  object: `aa_blade_w3` is 58.77% partial coverage, so a `w >= 0.5` floor throws
  away most of a thin asset. `TestAlphaFloorSweep` already establishes that
  buying accuracy by deleting object pixels is not the fix.
* **Chosen — unweighted centres, weighted coverage.** Quantise the
  **straight-RGB** strip exactly as today (MEDIANCUT, dither off, so the
  *choice* of cluster centres is made from the object's true colours rather
  than from darkened ones — which is already most of the fix). Then read the
  quantiser's own per-pixel index map, `np.asarray(quantized).reshape(-1)`, and
  compute each entry's coverage as `bincount(idx, weights=w) / w.sum()`. Exact,
  one extra vectorised pass, no new constant, and the coverage number that
  `palette_distance` and `palette_distance_de2000` weight by is now the honest
  one.

**The residual, stated rather than hidden:** the cluster *centres* remain
unweighted, so a large low-coverage region can still win a palette slot it
would not win under true weighting. Whether that residual is material is
**open** (§11.3) — it is measured on `glass_a64`/`a128`/`a192` and
`alpha_ladder` and published in W1's evidence bundle, and it becomes an
`interpretation_limits` entry stating the measured magnitude. Back-of-envelope
for `glass_a64`: the interior is 82.13% of the foreground at coverage 0.251, so
its weighted share is ≈53% — it earns a slot either way, and the residual is
expected to be small. That expectation is not a measurement.

Signature change: `quantize_palette(img, n_colors, weights=None)`. **When
`weights is None` the function must take the existing code path verbatim**,
including `getcolors`, so opaque output stays byte-identical rather than
merely numerically equal.

### 3.4 Working-resolution weights, and the constraint that fixes their form

The obvious construction — resample alpha with LANCZOS and threshold at 8 to
get the working mask — **breaks a currently-passing test**. On the hard-edge
control, LANCZOS produces intermediate alpha at silhouette edges, so a `>= 8`
threshold admits more fringe than the border-median path's NEAREST boolean
resize; `apply_mask` output then differs between the two forms and
`test_hard_edged_rgba_matches_its_composited_twin` fails on `dhash_distance == 0`.

The construction that satisfies the control exactly:

```python
def resize_coverage(alpha, size):
    """NEAREST-resample an alpha channel to working size.

    NEAREST, and not an averaging kernel, because membership at working
    resolution must remain identical to resize_mask's -- both select the same
    source pixel, so `resize_coverage(alpha, s) >= ALPHA_FOREGROUND_MIN` is
    provably the same boolean array as `resize_mask(alpha >= ALPHA_FOREGROUND_MIN, s)`.
    That equality is what keeps the alpha and border-median paths selecting the
    same pixels on a hard-edged object.
    """
```

PIL's NEAREST index mapping depends only on source and target size, so for
arrays of identical shape it selects the same source pixel `p` for every target
pixel `q`. Therefore `resize_coverage(alpha)[q] = alpha[p]` and
`resize_mask(alpha >= 8)[q] = (alpha[p] >= 8)`. On hard alpha, interior weights
are **exactly 1.0**, so the hard-edge control's exact-equality assertions hold.
This equality is pinned by an acceptance test (A1.6), not assumed.

Separately, the working-resolution **colour** needs the continuous coverage,
because it must invert the same LANCZOS kernel that produced the resampled
premultiplied colour:

```
working_premultiplied = to_working(composited_subject)         # existing call, unchanged
working_coverage_cont = clip(to_working_L(alpha_subject), 0, 255) / 255.0
working_straight      = clip(working_premultiplied / maximum(working_coverage_cont, UNPREMULTIPLY_COVERAGE_MIN), 0, 255)
```

Two coverage arrays with two jobs — NEAREST for membership and weights,
LANCZOS for inverting the colour resample — and both must be documented as
such in `pil_common`. The clip is required because LANCZOS has negative lobes
and the filtered premultiplied colour can overshoot its own coverage near a
hard edge.

`UNPREMULTIPLY_COVERAGE_MIN = ALPHA_FOREGROUND_MIN / 255.0`, derived rather
than invented: nothing below the membership floor is ever read, so the guard
need only cap amplification at `255/8 ≈ 32×`. Tying it to the existing constant
means the build adds no new magic number.

**Error bound at working resolution, stated precisely.** Compositing rounds
`colour × coverage` to a byte, so the numerator carries up to half a code
value; dividing by `w` amplifies it to `0.5/w` code values, and the pixel then
enters the weighted mean with weight `w`, contributing `0.5` to the numerator
of `Σwx/Σw`. The aggregate bound on a weighted cell mean is therefore
`0.5 / w̄`, where `w̄` is the mean coverage over the cell — about **2 code
values** on `degenerate_hairline` (mean coverage ≈ 0.25) and well under 1
elsewhere. This is *not* zero, and the earlier temptation to claim the weights
"cancel the amplification" is an overclaim. Full-resolution statistics carry no
such term at all, because they never divide.

### 3.5 How the border-median path stays untouched

**Rule:** every weighted code path is conditional on `alpha is not None`. When
alpha is absent, the code takes the *existing branch verbatim* — not a weighted
branch with unit weights. A ones-weighted mean computed as `Σ1·x/Σ1` in float64
can differ from `x.mean()` in the last ULP, which is enough to break
byte-identical JSON. The branch is the guarantee; equality of unit weights is
not.

Concretely untouched: `estimate_background`, `_border_samples`,
`rgb_to_oklab_array`, `DEFAULT_BACKGROUND_DELTA`, the `"background_estimate"`
branch of `foreground_mask`, `foreground_estimate`'s colour path, and every
full-frame (no `--foreground`) invocation regardless of input format.

The gate for this is a **pre/post output diff**, not an argument (A1.1).

One consequence worth stating, because it is the reason D1 is achievable at
all: `tests/test_contract_verdict.py` invokes `--foreground` fifteen times, and
it is on the frozen list. Verified during design — none of its fixtures carries
transparency (`grep -n "rgba" tests/test_contract_verdict.py` is empty; every
fixture comes from `conftest.preview_render`, which is opaque RGB). So the
entire contract-verdict foreground surface takes the border-median branch and is
covered by the §3.5 rule rather than needing a carve-out. The one RGBA fixture
in the frozen tests, `conftest.preview_render_rgba`, is drawn with hard alpha
(0 or 255 only), so weighted and unweighted readings are identical on it by
construction and `test_alpha_is_used_when_the_file_carries_transparency` is
unaffected. If either of those facts turns out to be false at implementation
time, it is a design escape and goes back to the architect, not a test edit.

### 3.6 New loader API

`load_rgb_alpha(path)` keeps its two-tuple signature and its documented
contract — `rgb` stays byte-identical to `load_rgb`. `tests/test_alpha_foreground.py`
unpacks it as a two-tuple in three places and those lines must keep working.

Added:

```python
def load_rgba_straight(path):
    """(composited_rgb, straight_rgb, alpha).

    Exists because the two RGB forms answer different questions and the tools
    need both: the composited form is the rendered appearance that hashes and
    pixel diffs must see, the straight form is the object's own colour that
    every colour statistic must see. load_rgb_alpha remains the composited-only
    view so that full-frame behaviour cannot drift.
    """
```

`load_rgb_alpha` becomes a two-element projection of it. When `alpha is None`,
`straight_rgb is composited_rgb` (the same object, not a copy) so the identity
`straight is composited` can be asserted.

### 3.7 The three xfail tests that do not simply flip

This is the part of W1 most likely to be got wrong, in either direction:
silently rewriting an assertion to go green, or leaving a strict xfail that can
never flip.

**`test_foreground_luminance_tracks_alpha_weighted_truth_at_every_blade_width`,
`test_interior_transparency_luminance_tracks_alpha_weighted_truth`,
`test_a_sub_pixel_re_render_does_not_change_the_reading`** — these three flip.
Action: **delete the `@pytest.mark.xfail` decorator, move the reason string
verbatim into the test's docstring under a heading `Pre-fix measurement`, and
change nothing else.** The evidence must survive the fix; it is what makes the
test's bound legible.

Note that after the fix these three pass with enormous margin (the tool and
`alpha_truth` compute the same thing, so the bias is ≈0 against a bound of
34.129). A bound that loose grades almost nothing. W1 therefore adds tight
absolute assertions alongside them in `tests/test_alpha_weighting.py` — see
A1.2.

**`test_accent_gate_admits_every_vivid_fringe_pixel`** — cannot flip as
written. It asserts a property of `load_rgb_alpha(path)[0]`, a value the fix
deliberately does not change, because full-frame metrics depend on it and a
straight-RGB full-frame read would expose whatever garbage a producer stored
under fully-transparent pixels.

*Action, and it must be reviewed as a re-levelling and not a weakening:* move
the assertion from the loader to the **tool**. New form — same question, same
corpus scene, same pre-fix numbers preserved in the docstring:

```
result = tool("pil_palette_diff.py", corpus["vivid_blade_w5"]["rgba"], "--foreground")
assert result["images"]["a"]["accent_pixel_fraction"] == approx(
    truth["vivid_blade_w5"]["truth"]["accent_fraction"], abs=1e-4)
```

Why this is not vacuous even though both sides gate on the same array: it runs
through a subprocess and the real CLI, and it fails if any of these is wrong —
composited RGB reaches the accent gate; weights are omitted; the `within` mask
is not applied; the accent fraction denominator is pixel count rather than
coverage sum. It is an integration assertion, and those are exactly the wiring
errors §3.9 predicts.

**`test_thin_vivid_object_reads_the_same_through_both_mask_paths`,
`test_interior_transparency_reads_the_same_through_both_mask_paths`** — cannot
pass, and the arithmetic says so unambiguously. Using the numbers already in
their own reason strings:

| Scene | statistic | RGBA today | composited today | truth | RGBA after fix | **delta after fix** | bound |
|---|---|---|---|---|---|---|---|
| `vivid_blade_w5` | saturation | 254.692 | 243.120 | 254.665 | ≈254.665 | **≈11.55** | 4.8875 |
| `glass_a64` | saturation | 212.467 | 168.117 | 144.478 | ≈144.478 | **≈23.64** | 4.8875 |

The composited twin has no alpha channel. Its fringe *genuinely is* a blend of
object and backdrop, and the coverage information that would un-blend it was
destroyed when the render was composited. Recovering it is alpha matting: an
under-determined problem, uncalibratable against this corpus, and a direct
violation of the brief's requirement that the border-median path stay
untouched. The `vivid_blade_w5` gap does not even shrink — the fix moves the
RGBA side by 0.027 code values.

*Action:* **convert both to passing characterisation tests that pin the
residual two-sidedly.** `assert delta == pytest.approx(<measured>, rel=0.05)`,
never `assert delta <= X` — a one-sided bound can be loosened by a later editor
without anyone noticing, a two-sided pin cannot. The docstring records the
pre-fix delta, the post-fix delta, and one sentence on why the residual is
irreducible. The class docstring's current premise ("the answer must not depend
on which file format the two renders happened to arrive in") is **false as
stated** and must be replaced by the true one: the answer depends on the format
because one format carries coverage and the other does not, and the tool's job
is to *say so*, which is what the new flag in §3.8 does.

Leaving these as permanent `xfail(strict=True)` was considered and rejected:
an xfail that is never intended to flip is the "xfail that can never flip"
anti-pattern, and it hides a permanent property behind a marker that reads as
"pending".

### 3.8 New payload fields and the new flag

Per image, inside the existing `foreground` block:

```json
"coverage_weighted": true,
"coverage_fraction_of_frame": 0.008431,
"partial_coverage_share": 0.593853
```

`coverage_weighted` is `true` only when `source == "alpha"` and the mask was
applied. On the border-median path it is `false`, and that is the point.

Per hue family, beside `pixels`: `"coverage": 812.47`.

Per cell, beside `foreground_fraction`: `"foreground_coverage_fraction": 0.4821`.

New diff flag on **both** tools: **`foreground_source_mismatch`** — raised when
`a.foreground.source != b.foreground.source` in `--foreground` mode. Its
meaning, and the `interpretation_limits` entry that accompanies it: one side's
colours are coverage-weighted true object colour, the other side's are blended
toward its own backdrop, and the two are not commensurable. This flag is the
productised form of the finding in §3.7, and it is the deliverable that makes
that unfixable residual *visible to a caller* instead of merely documented.

Note it is distinct from the existing `foreground_mask_mismatch`, which fires
when one side *fell back* to full frame.

### 3.9 Deliverables

* `scripts/pil_common.py`: `load_rgba_straight`, `resize_coverage`,
  `UNPREMULTIPLY_COVERAGE_MIN`, and `weights=`-accepting forms of
  `luminance_stats`, `saturation_stats`, `entropy_of`, `quantize_palette`,
  `accent_subset`, `hue_families`, `fractional_cells`. Every one of them takes
  the existing branch verbatim when `weights is None`.
* `scripts/pil_palette_diff.py`: foreground path switched to straight RGB +
  weights; new payload fields; `foreground_source_mismatch`; two new
  `interpretation_limits` entries (what is and is not weighted; the
  composited-render limitation with its measured magnitude).
* `scripts/pil_structure_diff.py`: same, plus working-resolution weights for
  `fractional_cells`, plus the occupancy feature switch. The
  `interpretation_limits` entry about thin objects degrading across resolutions
  must be **rewritten**, not deleted: it is still true on the border-median
  path and no longer true on the alpha path, and saying so is the honest form.
* `tests/test_alpha_foreground.py`: the six-test surgery of §3.7.
* `tests/test_alpha_weighting.py`: the new tight assertions.
  *No stored golden-output file.* A committed sha-per-invocation baseline was
  considered and dropped: it is Pillow-version-dependent, so it would either
  hard-fail on a different environment or skip itself into vacuity there. A1.1's
  worktree diff is the version-proof form of the same guarantee and is the gate.
* `runs/2026-08-2X-alpha-coverage-fix/README.md` + `measurements.json`: the
  before/after table over every `ALPHA_CORPUS` scene (bias in luminance,
  saturation, accent fraction, against `alpha_truth`), the sub-pixel excursion
  table at all four angles, the MEDIANCUT centre-selection residual (§11.3),
  and the irreducible RGBA-vs-composited residual per scene.
* `calibration/alpha_truth.py`: **docstring only.** The module docstring's
  claim that `tool_path` is "what the bundled tools compute" becomes false the
  moment W1 lands; it must be corrected to say that it is the pre-0.4.0
  unweighted reading, retained as the reference for what the fix removes.

### 3.10 Acceptance criteria

| # | Criterion — objectively checkable |
|---|---|
| **A1.1** | **Full-frame and border-median output is byte-identical to the pre-W1 tree.** `git worktree add ../pre-w1 <commit before W1>`; for every fixture in `tests/conftest.py` (`preview_render` in 4 variants, `preview_render_rgba`, `synthetic_reference`, `detailed_vs_flat`) and for every `ALPHA_CORPUS` scene in both forms, run each of `pil_palette_diff.py` and `pil_structure_diff.py` **without `--foreground`**, plus `--foreground` on the opaque forms only, under both trees and `diff` the stdout. **Every diff must be empty.** This is the primary gate for §3.5 and is version-proof, unlike a stored golden. |
| A1.2 | `pytest tests/test_alpha_weighting.py -k truth` — for every `ALPHA_CORPUS` scene with a non-empty mask, the tool's `--foreground` `luminance.mean`, `saturation.mean` and `accent_pixel_fraction` equal `alpha_truth.weighted_stats` to `abs=0.001`, `abs=0.001`, `abs=1e-4` respectively. Absolute literals, **not** values read from `detection_limits.json`. |
| A1.3 | `pytest tests/test_alpha_foreground.py` reports **0 xfailed and 0 xpassed**; `grep -c "@pytest.mark.xfail" tests/test_alpha_foreground.py` returns **0**. The marker is what must go; the *word* may and should remain in the module docstring, which explains what the markers recorded and why they were removed. This is the whole tree's only file carrying the marker (verified: `grep -rn xfail tests/` hits nothing else). |
| A1.4 | Every removed `xfail` reason string appears verbatim in a docstring in the same file — verified by `grep -F "measured foreground luminance bias -4.261"` and one anchor phrase per removed marker. |
| A1.5 | `git diff calibration/alpha_truth.py` contains **no change outside a docstring or comment**. Reviewer reads the diff; there is no automated substitute and pretending otherwise would be worse. |
| A1.6 | `pytest -k working_membership` — for every `ALPHA_CORPUS` scene and for target sizes (256,256), (256,192), (37,101): `np.array_equal(resize_mask(alpha >= ALPHA_FOREGROUND_MIN, s), resize_coverage(alpha, s) >= ALPHA_FOREGROUND_MIN)`. |
| A1.7 | The sub-pixel excursion, computed exactly as `TestSubPixelPlacement` does, is **≤ 0.5 code values at all four angles** (0, 20, 45, 60). The existing test's bound of 1.243 stays as written; this is the tighter separate assertion. Pre-fix values for reference: 17.74 and 15.91 at the two grid-commensurate angles. |
| A1.8 | `test_partial_coverage_pixels_carry_their_true_colour`: on `glass_a64`, the `--foreground` `base_palette` contains a hex within ΔE2000 3.0 of the interior's true colour `#00ffff`, and contains **no** entry within ΔE2000 3.0 of `#004040` (its coverage-darkened form). This is the assertion that fails if un-premultiply / straight-read is omitted. |
| A1.9 | `test_weighted_statistics_are_insensitive_to_the_alpha_floor`: sweeping `ALPHA_FOREGROUND_MIN` over 8, 16, 32, 64 and recomputing the weighted statistics, `luminance.mean` moves by **≤ 1.243** (the full-frame no-change floor) on every `ALPHA_CORPUS` scene. Pre-fix, the same sweep moves it by tens of code values — that is what `TestAlphaFloorSweep` records. If this fails, the floor is still doing bias control and the fix is incomplete. |
| A1.10 | `foreground_source_mismatch` appears in `diff.flags` for every RGBA-vs-composited pair in the corpus, and appears in **no** same-format pair. |
| A1.11 | Byte-determinism: two consecutive runs of each tool with `--foreground` on `aa_blade_w3` produce identical stdout. |
| A1.12 | Full suite green: `uv run python -m pytest` — 0 failed, 0 error, 0 xpassed. |
| A1.13 | Every new public function in `pil_common.py` has a docstring stating **why it exists**, not what it does. Specifically: `resize_coverage`'s docstring states the NEAREST-equivalence argument of §3.4; `load_rgba_straight`'s states why both RGB forms are needed; `UNPREMULTIPLY_COVERAGE_MIN`'s comment states the derivation from `ALPHA_FOREGROUND_MIN` and the `0.5/w̄` error bound. |
| A1.14 | The evidence bundle exists and its before/after table covers **all 19** `ALPHA_CORPUS` labels. |

### 3.11 How a plausible implementation could be wrong

Each entry names the corpus scene or test that catches it.

* **Weighting without un-premultiplying.** The seductive half-fix: apply
  `alpha/255` weights to the *composited* RGB. The algebra is decisive — the
  composited value is `c·w`, so the weighted mean is `c·Σw²/Σw ≠ c`, and it is
  biased *downward more* than the unweighted mean on low-coverage regions.
  It will look like progress on the blade scenes (where the fringe is a small
  share of a bright object) and be catastrophic on `glass_a64`, reading ≈44
  against truth 175.7. **Caught by:** A1.2 on `glass_a64`, A1.8.
* **Un-premultiplying without weighting.** Recovers the true colour but still
  counts a 3%-covered pixel like an opaque one. Nearly invisible on
  `aa_blade_*`, because a fringe pixel's true colour equals the object's, so
  the bias goes to ≈0 and the fix looks complete. Fails on `glass_*` (the
  interior is over-weighted relative to the bezel) and on the sub-pixel sweep
  (the fringe pixel *count* still changes with placement). **Caught by:** A1.2
  on `glass_a64/128/192`, A1.7.
* **Changing `load_rgb_alpha`'s first return to straight RGB.** Makes
  `test_accent_gate_admits_every_vivid_fringe_pixel` flip "for free", and
  silently changes every full-frame number on every RGBA input, exposing
  whatever RGB a producer left under fully-transparent pixels. **Caught by:**
  A1.1 — the full-frame diff on the RGBA corpus forms will be non-empty.
* **Using LANCZOS for the working-resolution membership mask.** Breaks
  `test_hard_edged_rgba_matches_its_composited_twin` on `dhash_distance == 0`.
  **Caught by:** A1.6, A1.12.
* **Using an averaging kernel (BOX/AREA) for the working-resolution weights.**
  Interior weights are still exactly 1 on hard alpha, but *edge* working pixels
  get fractional weights while the border-median path gives them 1 — so the
  hard-edge control's exact equalities fail on the palette side. **Caught by:**
  A1.6, A1.12.
* **Forgetting to clip the LANCZOS coverage to [0,255].** Negative lobes produce
  weights outside [0,1] and, at working resolution, a division by a negative
  number. Symptom is a NaN or a wild cell luminance on exactly one scene.
  **Caught by:** A1.12 plus an explicit assertion that every emitted weight is
  in [0,1].
* **float32 anywhere in the weight path.** Byte-determinism dies
  non-reproducibly. **Caught by:** A1.11, but only sometimes — so the code
  review must confirm `dtype=np.float64` at every weight construction site.
* **Computing weights from the resized boolean mask rather than from alpha.**
  Every weight becomes 1 and the whole fix is inert while every test that grades
  against a loose threshold still passes. **Caught by:** A1.2 (tight), A1.7,
  A1.9. This is precisely why A1.2's bounds are absolute literals.
* **Grading against `detection_limits.json` instead of `alpha_truth`.** The
  34.129 foreground bound is loose *because of the defect under test* — the
  test would pass while asserting nothing. This is the repository's own recorded
  history (see the calibration bundle's third compounding defect). **Caught by:**
  code review of `tests/test_alpha_weighting.py`; the file must not import
  `detection_limits.json` at all.
* **Editing `alpha_truth.weighted_stats` so the tool matches it.** Turns the
  reference into a mirror. **Caught by:** A1.5.
* **Loosening the two `TestMaskProvenance` characterisation bounds to one-sided
  `<=` and widening them.** **Caught by:** code review; the criterion is that
  the assertion is `approx(..., rel=...)` two-sided.
* **Silently dropping the "thin objects degrade across resolutions"
  interpretation limit** because it now reads as false. It is still true on the
  border-median path, which is most production input. **Caught by:** review
  against A1.14's bundle, which must state which path the caveat now applies to.

---

## 4. W2 — re-derive the foreground thresholds honestly

### 4.1 What is actually wrong with the current numbers

Recorded in the calibration bundle, three compounding defects:

1. **Weaker sampling, not advertised.** Full-frame thresholds are n=400, α=0.01,
   four scenes. **Every** foreground threshold is n=100, α=0.05, from
   `thin_object` **alone**.
2. **One control family sets most of them, and it perturbs placement.**
   `rescale_roundtrip` dominates by `changed_area_fraction` **997×**,
   `accent_fraction_delta_abs` **581×**, `structural_dissimilarity` **29.7×**,
   `luminance_mean_delta_abs` **18.9×** (27.460 against 1.452). Hue metrics
   report an unbounded ratio because no other family moves them at all.
3. **The dominance is a symptom.** A sub-pixel re-render of an unchanged object
   moves foreground luminance by 21.56 while truth moves 3.82. The threshold
   absorbed a measurement defect instead of measuring around it.

### 4.2 The correction the brief assumes, and why it does not hold

The brief asks to re-derive "after W1". **W1 does not move these numbers.**
`thin_object` is an opaque RGB scene on `PREVIEW_BG`; `load_rgb_alpha` returns
`alpha = None`; `foreground_mask` takes the border-median branch; W1 leaves
that branch byte-identical by design (§3.5). Re-running `run_all.py` after W1
on the existing corpus reproduces the existing foreground thresholds exactly —
which is, incidentally, a useful sanity check and is criterion A2.1.

The placement instability on an *opaque* thin object has the same root cause —
partial coverage counted at full weight — but no fix, because an opaque render
carries no coverage channel. So:

**Decision: split the foreground thresholds by mask source.** The bundle and
`scripts/detection_limits.json` gain, per metric:

```json
"threshold_foreground": 34.129,               // kept, = the estimate path, for continuity
"threshold_foreground_estimate": 34.129,      // border-median path
"threshold_foreground_alpha": null,           // alpha path; null until W2b measures it
"threshold_foreground_estimate_no_placement": 1.564,
"provenance": { "foreground_estimate": {...}, "foreground_alpha": {...} }
```

`threshold_foreground` keeps its key and its meaning so `tests/test_alpha_foreground.py`
and any external reader do not break. `..._no_placement` is derived by the same
Neyman–Pearson construction over the control set **excluding** `rescale_roundtrip`,
with its own n recorded — it is not a second method, it is the same method on a
named subset, and it is what lets a reader see both "upper bound on resampling
noise" and "non-placement noise floor" without recomputing anything. The current
bundle already carries the raw material (`by_control_family`).

### 4.3 W2a — widen the opaque foreground control set

`calibration/scenes.py` currently declares `FOREGROUND_SCENES = ("thin_object",)`,
with the honest reason that on the other three scenes "the background is the
subject". That reason holds for `busy` (no background at all) and is arguable
for `dark_accent` (a black base whose border median is black, so the mask would
be "everything that is not near-black" — a chroma mask wearing a foreground
mask's name). It does **not** hold for `structured`, whose `(32,32,36)` margin
is a genuine backdrop.

Two new scenes, both opaque, both objects on `PREVIEW_BG`, added so
`rescale_roundtrip` stops being the only family that moves anything:

* **`blob_object`** — a filled, rounded, *thick* form at ~15% of frame. Low
  perimeter-to-area, so resampling barely moves it. This is the scene that
  tests whether `rescale_roundtrip`'s dominance is about thin objects
  specifically.
* **`multipart_object`** — three separated components at ~8% of frame combined.
  Exercises a disjoint mask and cell-support gating.

`FOREGROUND_SCENES` becomes `("thin_object", "structured", "blob_object",
"multipart_object")` → 4 scenes × 5 control seeds × 20 recipes = **400** units,
so α = 0.01 becomes reachable under the bundle's own `n ≳ 3/α` rule.

**The limitation that survives and must be restated, not dropped:** these are
still 20 recipes crossed with a handful of base scenes, so units within a recipe
are correlated and the bootstrap reports a tighter CI than the design earns.
The bundle already says this. Widening scenes reduces the problem; it does not
remove it.

### 4.4 W2b — an RGBA control family (the item that actually calibrates the alpha path)

This is the work that closes phase 2's one remaining open gate. It is also the
riskiest, and it is scoped so it can be dropped without blocking the release.

Control units built from `ALPHA_CORPUS` scenes in RGBA form, run through
`--foreground`, producing `threshold_foreground_alpha` per metric.

**Design note that is a gate, not a checkbox:** the control recipes must be
alpha-aware or they manufacture the very artefact `scenes._resolve_rgba` warns
about.

* `rescale_roundtrip` on RGBA **must resample premultiplied and un-premultiply
  once**, exactly as `_resolve_rgba` does. Channel-by-channel resampling of
  straight RGBA averages the object's colour against the transparent region's
  stored RGB and bakes a dark halo into the *content*, which would then be
  measured as noise and inflate the very threshold this work exists to tighten.
* `jpeg_reencode` **must be skipped** for RGBA — JPEG carries no alpha. Skipping
  a recipe changes `n` for this family; the skipped recipes must be recorded in
  the bundle rather than silently omitted.
* `png_reencode`, `identical`, `noise`, `exposure`, `saturation`, `hue`, `blur`
  apply to the RGB channels only, leaving alpha untouched. That must be
  asserted, not assumed: a perturbation that moves alpha is a *geometry*
  perturbation wearing a colour perturbation's name.

If premultiplied resampling cannot be added to `perturb.py` without
restructuring it, **descope W2b**, ship `threshold_foreground_alpha: null`, and
state in `docs/index.md` that the alpha path is now *correct but still
uncalibrated*. That is a materially better position than today (uncalibrated
*and* wrong) and it is honest. It is not a silent descope: the null and its
reason are in the payload.

### 4.5 The re-grading coupling with W1, made explicit

`tests/test_alpha_foreground.py` reads `threshold_foreground` from
`scripts/detection_limits.json` at import time. W2 rewrites that file. So W1's
tests **silently re-grade** when W2 lands, and if W2's numbers move the wrong
way a W1 test could start failing without W1 changing.

That is a feature — it is how a threshold change is prevented from quietly
invalidating a fix — but it is only safe because W1's own acceptance (A1.2,
A1.7, A1.8, A1.9) rests on **absolute literals independent of that file**.
Both work items must state this dependency in their PR descriptions, and W2's
acceptance includes "full suite green after distillation" (A2.7).

### 4.6 Deliverables

* `calibration/scenes.py`: two new opaque scenes; `FOREGROUND_SCENES` widened;
  `ALPHA_CORPUS` and every existing builder **unchanged** (byte-identity test
  is the guard).
* `calibration/measure.py`: per-mask-source control accounting; RGBA control
  family (W2b); recorded skip list for inapplicable recipes.
* `calibration/perturb.py` (W2b only): premultiplied resample path.
* `calibration/derive.py`: the `..._no_placement` derivation and per-source
  thresholds.
* `calibration/run_all.py`: `DEFAULT_OUT` repointed to the new bundle
  directory.
* `calibration/distill_detection_limits.py`: the new schema of §4.2, with the
  docstring updated to explain per-source provenance the way it already
  explains per-mode provenance.
* `scripts/detection_limits.json`: regenerated.
* `runs/2026-08-2X-foreground-recalibration/`: new bundle
  (`README.md`, `derived-thresholds.json`, `response-curves.json`,
  `lch-hue-boundaries.json`).
* `runs/2026-08-19-phase2-calibration/README.md`: **one appended line** naming
  the superseding bundle and what it supersedes (foreground thresholds only —
  the full-frame numbers and the LCh work stand).

### 4.7 Acceptance criteria

| # | Criterion |
|---|---|
| A2.1 | **Null-change check first.** Re-running the *unmodified* corpus against post-W1 tools reproduces the existing foreground thresholds to 6 dp. If it does not, W1 changed the border-median path and A1.1 was not enforced. Record this in the bundle. |
| A2.2 | `derived-thresholds.json` → `control_sets.foreground_estimate.n >= 300` and `alpha == 0.01`. |
| A2.3 | `control_sets.foreground_estimate.scenes` has **length ≥ 3**. A threshold with n=400 drawn from one scene is the same provenance defect wearing a bigger number, and this criterion is what forbids it. |
| A2.4 | Every metric emits `threshold_foreground_estimate`, `threshold_foreground_estimate_no_placement`, `threshold_foreground_alpha` (possibly `null`), and `dominant_control_family` **per source**. |
| A2.5 | The bundle README states, per metric, the new `dominant_control_family` ratio next to the old one, in a table. **No pass/fail threshold is set on this ratio** — I cannot defensibly pick one without the measurement — but a ratio that stays above 10× must carry a written sentence saying the threshold remains a statement about that family. |
| A2.6 | Any foreground threshold that **increased** relative to the 2026-08-19 bundle carries a written explanation in the README. Thresholds are expected to fall or hold; a rise means either a genuinely noisier widened corpus or a bug. |
| A2.7 | `uv run python calibration/distill_detection_limits.py` then `uv run python -m pytest` — 0 failed. |
| A2.8 | `uv run python calibration/run_all.py --out runs/2026-08-2X-foreground-recalibration` runs the pipeline twice and asserts byte-identical payloads (the existing behaviour) — and `provenance.tool_sha256` for `pil_common.py`, `pil_palette_diff.py`, `pil_structure_diff.py` **matches `scripts/*.py` at the recalibration commit**, verified by `sha256sum scripts/*.py`. That commit must contain W1; it may also contain W6, since W6 and W2a run concurrently, and a bundle measured after W6 is fine as long as the recorded hashes are the ones that were measured. |
| A2.9 | `git diff runs/2026-08-19-phase2-calibration/` shows **exactly one added line** in `README.md` and no other change anywhere in that directory. |
| A2.10 | W2b only: `threshold_foreground_alpha` is non-null for every metric, and the bundle records which recipes were skipped for RGBA and why. |
| A2.11 | The limitations section of the new README restates, unchanged in substance: synthetic perturbations underestimate real difficulty; controls are not independent draws; sub-threshold perturbations are inside the control set by design; one machine, one Pillow. Dropping an inherited limitation because the new corpus is bigger is not permitted. |

### 4.8 How a plausible implementation could be wrong

* **Re-deriving with pre-W1 tools.** The bundle snapshots `scripts/*.py` before
  measuring; a stale snapshot measures the old behaviour and publishes it as
  the new calibration. **Caught by:** A2.8's sha256 comparison.
* **Raising n by adding seeds or recipes while leaving `FOREGROUND_SCENES` at
  one scene.** n=400 with one scene fixes the number and not the defect, and it
  is the most likely shortcut because it is a one-line change. **Caught by:**
  A2.3.
* **Declaring α=0.01 on a control set whose effective independent sample is 20
  recipes.** **Caught by:** A2.11 — the non-independence limitation must survive
  verbatim.
* **Overwriting `runs/2026-08-19-phase2-calibration/`** because `DEFAULT_OUT`
  still points there and someone ran `run_all.py` with no `--out`. This
  destroys evidence and is not recoverable from the working tree. **Caught by:**
  A2.9. Repoint `DEFAULT_OUT` **first**, as the very first commit of W2a.
* **Adding RGBA controls whose rescale recipe resamples straight alpha.** Bakes
  a halo into the content, inflating `threshold_foreground_alpha` — producing a
  threshold that looks rigorous and encodes an artefact. This is the same class
  of error as the one being fixed. **Caught by:** §4.4's design-note gate; the
  reviewer must read the resample code, and the bundle must show
  `threshold_foreground_alpha` at or below the non-placement estimate floor
  (≈1.5), not near 34. A value near 34 is the fingerprint.
* **Silently dropping JPEG for RGBA without recording it**, so a reader compares
  an n=100 family against an n=80 one. **Caught by:** A2.10.
* **Fitting the threshold to make a W1 test pass.** The temptation exists
  because W1's tests read this file. **Caught by:** W1's tests using absolute
  literals (§4.5), so a fitted threshold buys nothing.
* **Deleting `threshold_foreground`** in favour of the new per-source keys.
  Breaks `tests/test_alpha_foreground.py` at import. **Caught by:** A2.7.

---

## 5. W3 — `pil_crop.py` (phase 3 A1)

### 5.1 Decision and justification

Closes the zoom loop: hand vision back a native-resolution crop of a bbox the
tools already emitted, at a resolution the encoder never received. It adds no
judgment metric, so it carries no calibration dependency.

**Output-file policy, decided here for W3 and W4 both** (phase 3 open question
2): `--out PATH` is **required** — there is no default path, because a tool
that writes to a guessed location is a tool that overwrites something. Writing
refuses if the target exists unless `--overwrite` is passed. `runs/**/*.png` is
already gitignored, and the documentation recommends `runs/` as the destination,
but the tool enforces nothing about location.

**`--region-space` defaults to `frame`** (phase 3 open question 1). Every
fractional coordinate the tools currently emit — `changed_region_bbox_fractional`,
`bounds_fractional`, `bbox_fractional` — is frame-relative, so `frame` is the
space a caller pasting a number from a payload is already in. The field trial's
silhouette-relative cutting is available as `--region-space foreground` and is
never inferred. Silently picking either one would mislead the other caller.

### 5.2 CLI surface

```
pil_crop.py IMAGE --out PATH --region L,T,R,B
            [--region-space frame|foreground]
            [--scale N] [--background-delta F] [--overwrite]
```

* `--region` accepts both spellings from §2.2.
* `--scale N`, integer ≥ 1, default 1. **NEAREST only** — no other resample is
  offered, because magnification must invent no colour.
* `--region-space foreground` derives the foreground bbox with
  `pil_common.foreground_mask` (alpha when present, border-median otherwise) and
  resolves the region against that bbox via `pil_region.resolve_pixel_rect`'s
  `origin` parameter. An empty mask is an **error** (exit 2), not a silent
  fallback to frame — the caller asked for a space that does not exist.
* Exit codes: `0` success; `2` bad region, unreadable image, refused overwrite,
  or empty foreground under `--region-space foreground`. On a non-zero exit,
  **nothing is written to stdout** — a caller must never be able to parse a
  partial answer.

### 5.3 JSON output shape

```json
{
  "tool": "pil_crop",
  "version": "0.4.0",
  "parameters": {"region": [0.1,0.4,0.3,0.9], "region_space": "frame",
                 "scale": 1, "resample": "nearest", "overwrite": false,
                 "background_delta": 0.035},
  "source": {"path": "...", "size": [1672, 941], "mode": "RGBA",
             "has_alpha": true, "sha256": "..."},
  "region": {"requested_fractional": [0.1,0.4,0.3,0.9],
             "resolved_pixel_rect": [167, 376, 502, 847],
             "resolved_fractional": [0.099880, 0.399575, 0.300239, 0.900106],
             "space": "frame",
             "reference_rect": null},
  "output": {"path": "...", "size": [335, 471], "sha256": "...", "bytes": 40213},
  "flags": [],
  "interpretation_limits": ["..."]
}
```

`resolved_fractional` exists so a caller can see the rounding it actually got
rather than the fraction it asked for. `reference_rect` is the foreground bbox
in frame pixels under `--region-space foreground`, `null` otherwise. `sha256` on
both source and output makes byte-determinism checkable from the payload alone.

### 5.4 Determinism requirements

* Two runs on the same input produce a byte-identical PNG and a byte-identical
  payload (the payload includes hashes, so the second implies the first).
* Format from the `--out` suffix, PNG only in 0.4.0; any other suffix is exit 2.
  (JPEG would make the output lossy and encoder-dependent, which defeats the
  purpose.)
* **No metadata is copied** — no EXIF, no ICC, no text chunks. Stated in the
  payload's interpretation limits, because a caller might reasonably expect
  otherwise, and copying an ICC profile onto a crop the tool did not
  colour-manage would be a claim it cannot support.
* Alpha is preserved when the source has it; the crop is **never** composited
  onto a background.

### 5.5 `interpretation_limits` content

1. "A crop is a view, not a measurement. Nothing in this payload asserts that
   the region contains what you believe it contains."
2. "`--scale` is nearest-neighbour: it invents no colour, and it recovers no
   detail the source never had. A 4× upscale of a 3-pixel feature is still a
   3-pixel feature, drawn larger."
3. "Fractional→pixel resolution rounds half up on each edge independently.
   `resolved_pixel_rect` and `resolved_fractional` record exactly what was cut,
   and they will differ from what you asked for. Read them, not `parameters.region`."
4. "`region_space=foreground` resolves against a foreground bounding box derived
   by `pil_common`'s rule — exact on a file with real alpha, an estimate from
   the border-median colour otherwise. The crop inherits that estimate's error;
   `reference_rect` records the box used."
5. "No metadata is carried across: the output has no EXIF, no ICC profile and
   no text chunks, whatever the source had."

**Must NOT be claimed:** that the crop is "the object"; that upscaling adds
detail or resolution; that the region is semantically meaningful; that the
output is colour-managed.

### 5.6 Acceptance criteria

| # | Criterion |
|---|---|
| A3.1 | Byte-identical output across runs: run twice to different paths, compare sha256 of the two files and of both payloads with `output.path` masked. |
| A3.2 | Fractional→pixel rects exact at odd dimensions: enumerate at least 6 (size, region) pairs including `(101, 37)` and `(3, 3)`, each with a hand-computed expected rect written as a literal in the test. |
| A3.3 | `--scale 4` output contains **only** colours present in the source, **and** the same number of distinct colours: `set(out_colours) == set(src_colours)` on a source with ≥ 5 distinct colours. The equality (not subset) is what stops the test passing vacuously on a flat crop. |
| A3.4 | Out-of-range (`[0,0,1.2,1]`), inverted (`[0.9,0,0.1,1]`) and degenerate (`[0.5,0,0.5,1]`) regions all exit 2 with empty stdout. |
| A3.5 | Refusing to overwrite: a second run without `--overwrite` exits 2, stdout empty, **and the existing file's sha256 is unchanged**. |
| A3.6 | `--region-space foreground` on `preview_render_rgba()` with `--region 0,0,1,1` produces a file whose size equals the foreground bbox size reported by `pil_structure_diff --foreground`'s `bbox_fractional` resolved against the same frame. |
| A3.7 | `--region-space foreground` on a uniform frame (empty mask) exits 2. |
| A3.8 | Alpha preserved: cropping an RGBA source yields an RGBA output whose alpha channel equals the corresponding source slice exactly. |
| A3.9 | Every public function's docstring states why it exists. |

### 5.7 How a plausible implementation could be wrong

* **Reimplements the region parser** instead of importing `pil_region`, so A4's
  equality gate becomes a coincidence. **Caught by:** W6's A6.2, which literally
  runs `pil_crop` and then the diff tool and compares — but by then W6 is
  blocked. Reviewer must check the import.
* **`Image.crop` with a fractional or float box.** Pillow accepts floats and
  truncates, disagreeing with the half-up rule. **Caught by:** A3.2.
* **Uses `Image.resize` with the default resample for `--scale`.** The default
  is BICUBIC, which invents colours. **Caught by:** A3.3's set equality.
* **Writes the file before validating the region**, leaving a partial artefact
  on an error path. **Caught by:** A3.4 extended to assert the output path does
  not exist after a rejected run.
* **Emits JSON on stdout *and* exits non-zero.** A caller pipes it to `json.load`
  and gets a plausible-looking answer for a run that failed. **Caught by:**
  A3.4/A3.5's "stdout empty" clause.
* **A vacuous `--scale` test** on a single-colour crop, where "only colours from
  the source" is trivially true. **Caught by:** A3.3's ≥5-colour requirement.
* **Silently clamping a region to the frame** instead of rejecting. **Caught
  by:** A3.4.

---

## 6. W4 — `pil_annotate.py` (phase 3 A2)

### 6.1 Decision and justification

Closes the grounding loop's tool→vision direction: an agent Reads the annotated
copy and can then say "region 3" instead of "the bit near the top left".

**Font decision, and it is the load-bearing one.** Phase 3 requires
byte-identical output "across machines with different fonts installed", and
proposes Pillow's bundled default font or pure geometric markers. I am
proposing a third option and rejecting both:

* **Rejected — `ImageFont.load_default()`.** Bundled, so it is host-font
  independent, but its glyphs have changed between Pillow releases (the default
  font became a scalable face in the Pillow 10 line). Determinism across
  *versions* is not guaranteed, and this repository pins byte-identical output
  as its core contract.
* **Rejected — geometric markers with numbers only in the JSON legend.** The
  overlay then cannot be read back visually, which is the entire purpose.
* **Chosen — a hand-rolled 5×7 bitmap digit table defined as a constant in
  `pil_annotate.py`.** Digits 0–9 only, ~20 lines of literal data, drawn at
  `--label-scale` (default 3, giving 15×21px numerals). Output is byte-identical
  across every Pillow version and every machine, forever, and vision reads block
  numerals reliably. Words never appear in the image; the caller's labels live
  in the JSON legend.

**Occlusion rule.** Boxes are drawn as outlines, never filled. The numeral is
drawn **outside** the box at its top-left when there is room in the frame, and
inside the top-left corner otherwise, with the choice recorded per box as
`glyph_placement`. Glyph colour is chosen deterministically from the mean
luminance under the glyph's own footprint — black on light, white on dark — and
recorded as `glyph_colour`. An overlay that occludes what it labels corrupts the
loop; recording the placement is what lets a reviewer check that it did not.

**Numbering rule.** Boxes are sorted by `(top, left, bottom, right)` and
numbered 1..N in that order, regardless of input order, with
`requested_index` recorded per box. So passing the same set of boxes in a
different order produces a byte-identical image.

### 6.2 CLI surface

```
pil_annotate.py IMAGE --out PATH
                [--box L,T,R,B]...        (repeatable)
                [--label TEXT]...          (repeatable, positional to --box)
                [--grid COLSxROWS]
                [--from-json PATH]
                [--label-scale N] [--thickness N] [--overwrite]
```

`--from-json` reads a `pil_structure_diff` payload and draws exactly two things,
named explicitly so a reader knows what appeared and why:
`diff.most_divergent_cells[*].bounds_fractional` and
`diff.changed_region_bbox_fractional`. Nothing else in that payload is
interpreted. Boxes from `--from-json` and `--box` are merged and then sorted and
numbered together.

`--grid COLSxROWS` draws the fractional grid to scale, so
`most_divergent_cells` becomes visible in context. Grid lines are 1px and drawn
**before** boxes, so a box outline is never overdrawn by a grid line.

### 6.3 JSON output shape

```json
{
  "tool": "pil_annotate", "version": "0.4.0",
  "parameters": {"grid": {"cols":4,"rows":3}, "label_scale": 3, "thickness": 2,
                 "from_json": "…/structure.json", "overwrite": false},
  "source": {"path": "...", "size": [w,h], "sha256": "..."},
  "output": {"path": "...", "size": [w,h], "sha256": "..."},
  "legend": [
    {"number": 1, "requested_index": 2, "source": "--box", "label": "collar",
     "fractional": [0.1,0.1,0.3,0.4], "pixel_rect": [167,94,501,376],
     "glyph_colour": "#ffffff", "glyph_placement": "outside_top_left"}
  ],
  "grid": {"cols": 4, "rows": 3, "lines_drawn": 5},
  "flags": [],
  "interpretation_limits": ["..."]
}
```

### 6.4 `interpretation_limits` content

1. "The overlay is drawn on a **copy**. The source file is not modified; its
   sha256 is recorded here so you can verify that."
2. "**Never measure an annotated image.** The boxes, grid and numerals are
   pixels: feeding this output to `pil_palette_diff` or `pil_structure_diff`
   measures the annotation as well as the content."
3. "Box numbers are geometric glyphs from a table defined in this file, not
   rendered text, so the output is byte-identical on every machine and every
   Pillow version. Only digits exist; your labels appear in `legend`, never in
   the image."
4. "Numbering is by position (top, then left), not by the order you passed the
   boxes. `requested_index` maps each drawn number back to your input."
5. "The boxes are the caller's. This tool asserts nothing about what is inside
   them."

**Must NOT be claimed:** that a numbered region contains anything in
particular; that the overlay is measurable; that the tool found the regions
(it drew what it was given).

### 6.5 Acceptance criteria

| # | Criterion |
|---|---|
| A4.1 | Byte-identical output across runs, and across two runs whose `--box` arguments are in **shuffled order** — the PNG sha256 must match exactly, and `legend[*].requested_index` must differ between the two runs. Both halves are required: the first without the second would pass on an implementation that ignores input order entirely. |
| A4.2 | Source untouched: sha256 of the source file before and after equals, in the same test. |
| A4.3 | Font independence: the module contains no `ImageFont` import at all — `grep -c "ImageFont" scripts/pil_annotate.py` returns 0. This is a stronger and cheaper check than trying to vary installed fonts in CI. |
| A4.4 | Digit legibility, verified by a **model reading the output back**, not by asserting pixels were drawn. Deliverable: `runs/2026-08-2X-annotate-readback/README.md` recording an agent shown the annotated PNG (and not the JSON) transcribing every box number correctly, on at least 3 images spanning light, dark and busy content, with the transcript quoted. Phase 3 names this failure mode explicitly; a unit test cannot cover it. |
| A4.5 | No occlusion of box content: for every box, the glyph footprint does not intersect the box interior when `glyph_placement == "outside_top_left"`, asserted geometrically. |
| A4.6 | `--from-json` on a real `pil_structure_diff` payload draws exactly `len(most_divergent_cells)` + (1 if `changed_region_bbox_fractional` else 0) boxes, and the legend records `source` for each. |
| A4.7 | Grid lines drawn before boxes: on a case where a box edge coincides with a grid line, the box colour is the one present at that pixel. |
| A4.8 | Refuses overwrite without `--overwrite`; exit 2 with empty stdout. |
| A4.9 | Every public function's docstring states why it exists — in particular the digit table's comment must state that it exists to make output byte-identical across Pillow versions, and name the rejected alternative. |

### 6.6 How a plausible implementation could be wrong

* **Uses `ImageFont.load_default()` "because it is bundled".** Output is stable
  on the author's machine and drifts on a different Pillow. **Caught by:** A4.3.
* **Numbers boxes in input order.** Two agents describing the same image
  disagree about which region is "3". **Caught by:** A4.1's shuffle half.
* **Draws filled boxes or a semi-transparent tint.** Occludes exactly what the
  number labels; the model then describes the overlay. **Caught by:** A4.4 —
  and only by A4.4, which is why the read-back is a deliverable and not a
  nicety.
* **Draws the numeral inside the box always.** On a small box the glyph covers
  the content. **Caught by:** A4.5.
* **Modifies the source in place** because `Image.open` was drawn on without
  `.copy()` and then saved to the source path when `--out` was omitted.
  **Caught by:** A4.2, plus `--out` being required.
* **A vacuous read-back**: showing the model the JSON legend alongside the
  image. **Caught by:** A4.4's "and not the JSON" clause; the run bundle must
  record the exact prompt.
* **Glyph colour chosen from a global mean** rather than the local footprint,
  so a white numeral lands on a white highlight. **Caught by:** A4.4 on the
  busy image.

---

## 7. W5 — `pil_image_info.py` (phase 3 A3)

### 7.1 Decision and justification

Reports what vision never receives: images reach a model resampled and stripped
of metadata, so it cannot report even the true dimensions of what it was shown.
Near-free, no judgment, no calibration dependency.

The one design constraint that carries weight: phase 3 requires **explicit nulls
for absent metadata rather than omitted keys**, so "no ICC profile" is
distinguishable from "not checked". I am extending that: `null` alone cannot
distinguish *absent* from *unreadable*, so a probe that fails emits `null` **and**
a flag (`icc_unreadable`, `exif_unreadable`, `frames_unreadable`). Three states,
three signals.

### 7.2 CLI surface

```
pil_image_info.py IMAGE [IMAGE ...]
```

One JSON payload with an `images` array in argument order. A missing or
unreadable file produces an entry with `"readable": false` and a `reason`, and
does **not** abort the run — a caller inspecting 30 files wants the other 29.
Exit code is 0 if every file was readable, 1 if any was not.

### 7.3 JSON output shape (per image)

```json
{
  "path": "...", "readable": true, "reason": null,
  "size": [1672, 941], "width": 1672, "height": 941,
  "mode": "RGBA", "format": "PNG",
  "bands": ["R","G","B","A"], "bit_depth_per_channel": 8,
  "has_alpha_channel": true,
  "uses_transparency": true,
  "alpha_min": 0, "alpha_max": 255,
  "transparency_key": null,
  "palette_size": null,
  "icc_profile_present": false, "icc_profile_bytes": null,
  "icc_profile_description": null,
  "exif_present": false, "exif": null,
  "dpi": null,
  "n_frames": 1, "is_animated": false,
  "file_bytes": 402131, "sha256": "...",
  "flags": []
}
```

Every key is present on every readable image; `null` means absent. `mode`,
`format` and `bands` come from Pillow directly. `bit_depth_per_channel` is
derived from `Image.mode` via an explicit table in the file, not guessed.

`uses_transparency` is the load-bearing field: **it must equal
`pil_common.load_rgb_alpha(path)[1] is not None`**, i.e. the file carries an
alpha channel *and* at least one pixel below 255. A file with an all-255 alpha
channel reports `has_alpha_channel: true, uses_transparency: false`, exactly as
the loader's `alpha = None` branch decides. This is phase 3's stated gate for
A3 and it is the only place this tool can meaningfully be wrong about
something the rest of the plugin depends on.

**EXIF determinism.** EXIF values include byte strings, rationals and undecodable
blobs. Rendering rules, pinned: integers and floats as themselves; rationals as
a two-element `[num, den]` array; strings decoded as UTF-8 when they decode
cleanly; anything else as `{"type": "bytes", "length": N, "sha256": "..."}`.
Never a lossy `errors="replace"` decode, which would silently corrupt a value
and still look like data. Tag names via PIL's own `ExifTags.TAGS`, emitted as
`"Make (271)"` so an unknown tag is still addressable. Keys sorted.

### 7.4 `interpretation_limits` content

1. "`uses_transparency` follows `pil_common.load_rgb_alpha`'s rule exactly: an
   alpha channel that is entirely opaque carries no foreground information, so
   it reports `false`. `has_alpha_channel` reports the channel's mere presence."
2. "An ICC profile is reported as present or absent and by size. This tool does
   **not** validate it, does not interpret it, and converts nothing. Presence
   is not evidence the image is correctly colour-managed."
3. "Absent metadata is `null`; unreadable metadata is `null` **plus** a flag.
   Check `flags` before concluding a field was absent."
4. "Dimensions and DPI describe the file. They say nothing about quality,
   provenance or whether the image was resampled before it reached you."
5. "`sha256` is over the file's bytes, so two visually identical images saved by
   different encoders hash differently. It identifies a file, not an image."

**Must NOT be claimed:** that absence of EXIF implies a synthetic or generated
image; that ICC presence implies correct colour; that DPI implies physical size
of anything.

### 7.5 Acceptance criteria

| # | Criterion |
|---|---|
| A5.1 | Byte-determinism across runs on a fixture set including an EXIF-bearing JPEG. |
| A5.2 | **The alpha rule matches the loader**, parametrised over 5 fixtures: RGB; RGBA with transparency; RGBA all-255 (`scenes.alpha_opaque`); P-mode with a `transparency` key; LA. For each, `uses_transparency == (load_rgb_alpha(path)[1] is not None)`. |
| A5.3 | Every key in §7.3 is present on every readable image — asserted as a set equality against a literal key list in the test, so a future key addition is a deliberate act. |
| A5.4 | A missing file yields `readable: false` with a reason, the other files in the same invocation are still reported, and the exit code is 1. |
| A5.5 | An image with a deliberately corrupt EXIF block yields `exif: null` **and** `"exif_unreadable" in flags` — not a crash and not a silent absent. |
| A5.6 | An animated GIF reports `n_frames > 1` and `is_animated: true`. |
| A5.7 | Undecodable EXIF bytes render as the `{"type":"bytes",...}` form, never as a replacement-character string. |
| A5.8 | Every public function's docstring states why it exists. |

### 7.6 How a plausible implementation could be wrong

* **Computes `uses_transparency` as `"A" in img.getbands()`.** Reports `true`
  for an all-opaque RGBA file, contradicting `load_rgb_alpha` and the whole
  foreground design. **Caught by:** A5.2's `alpha_opaque` fixture.
* **Computes it as `img.info.get("transparency") is not None`.** Correct for
  P-mode, wrong for RGBA. **Caught by:** A5.2's RGBA fixture.
* **Omits keys when metadata is absent** rather than emitting nulls, so a caller
  cannot tell absent from unchecked. **Caught by:** A5.3.
* **Lossy EXIF decode** with `errors="replace"`, producing plausible-looking
  corrupted strings that differ across Python builds. **Caught by:** A5.1 and
  A5.7.
* **Aborting the whole run on one unreadable file.** **Caught by:** A5.4.
* **Reading `n_frames` without `getattr` on formats that lack it**, crashing on
  PNG. **Caught by:** A5.3 over the full fixture set.
* **Loading the full image to get the size**, making the tool slow on large
  files for no reason — `Image.open` is lazy and the header is enough for
  everything except `alpha_min`/`alpha_max`, which should be the only path that
  reads pixels.

---

## 8. W6 — `--region FRACTIONAL_BBOX` on both existing tools (phase 3 A4)

### 8.1 Decision and justification

Moves the field trial's highest-leverage harness capability into the plugin, and
makes its "ad-hoc numpy probes placed by eye" reproducible.

**Composition with `--foreground`, decided and documented rather than left
implicit:** the region is applied **first**, cutting both images at full
resolution; the foreground mask is then derived **within the crop**. A caller
saying "measure this part of the frame" means the object inside that part.

**The background estimate is re-derived from the crop, not inherited from the
frame.** This was the harder call. Inheriting the frame's estimate is arguably
more accurate when the crop contains no backdrop — but it breaks phase 3's
stated A4 gate, *"metrics over `--region R` equal metrics over a file pre-cropped
to R"*, because a pre-cropped file has no frame to inherit from. That gate is
the stronger guarantee and it is the one a reviewer can check in one command, so
re-derivation wins. The hazard it creates is surfaced rather than hidden: a new
flag `region_background_estimate_diverged` fires when the crop's border-median
colour is further than `--background-delta` in OKLab from the whole frame's,
which is exactly the "this crop contains no backdrop" condition.

**`--region-space`** takes the same values and the same default (`frame`) as
W3, from the same `pil_region` module.

### 8.2 Payload changes

`parameters` gains `"region"` (the parsed fractions, or `null`) and
`"region_space"`. Each image block gains:

```json
"region": {"requested_fractional": [...], "resolved_pixel_rect": [...],
           "resolved_fractional": [...], "space": "frame",
           "reference_rect": null}
```
or `null` when no region was given, and `"source_size": [w, h]` **always**.

`size` continues to mean "the pixels that were measured", which is the crop
when a region is present; `source_size` is the file's own size and equals `size`
when no region is given. Adding `source_size` unconditionally is additive: no
existing field changes meaning, which is why it is unconditional rather than
region-only.

### 8.3 Acceptance criteria

| # | Criterion |
|---|---|
| A6.1 | **The existing suite passes unchanged.** `uv run python -m pytest` — 0 failed, and `git diff` touches no test file other than the new `tests/test_region_mode.py`. |
| A6.2 | **Region equals pre-crop**, the gate: for both tools, for at least 4 (image, region) pairs including an odd-sized image and a region whose edges land on `.5` pixels, `pil_structure_diff.py A --region R` equals `pil_crop.py A --region R --out t.png` followed by `pil_structure_diff.py t.png`, comparing the full payload with the `region`, `source_size`, `path` and `parameters.region*` keys removed. **Byte equality**, not approximate. Runs the real `pil_crop`, so it also proves the two share one parser. |
| A6.3 | `--region 0,0,1,1` equals no `--region` on the same image, with the same keys removed. Requires `pil_region` to resolve the unit box to a real rect (see A0.6's rejection of a `None` shortcut). |
| A6.4 | Byte-determinism with and without `--region`; the existing determinism tests still pass. |
| A6.5 | Out-of-range, inverted and degenerate regions exit 2 with empty stdout, on both tools. |
| A6.6 | `--region` × `--foreground` on `preview_render()` with a region containing the sword's hilt reports a foreground fraction ≥ 3× the whole-frame foreground fraction — i.e. the region actually scoped the measurement, rather than being echoed and ignored. |
| A6.7 | `region_background_estimate_diverged` fires on a region wholly inside the object and does **not** fire on a region containing a normal share of backdrop. |
| A6.8 | The region is applied to **both** images: a diff over `--region R` where `R` covers only the changed area reports `changed_area_fraction` substantially higher than the same diff full-frame. |
| A6.9 | `--region` × `--foreground` composition is documented in both tools' `--help` and in a new `interpretation_limits` entry stating the order of operations and the re-derived background estimate. |

### 8.4 How a plausible implementation could be wrong

* **Crops the working copy instead of the full-resolution image.** The region
  then resolves against a 256px long edge and the measurement becomes
  resolution-dependent — a direct violation of the scale-invariance requirement.
  **Caught by:** A6.2 at two different source resolutions.
* **Applies the region to image A only.** The diff is then between a crop and a
  whole frame. **Caught by:** A6.8 and by A6.2's aspect-ratio flags.
* **Echoes the region without applying it.** The payload looks right and the
  numbers are full-frame. This is the single most likely silent failure.
  **Caught by:** A6.6.
* **Rounds differently from `pil_crop`** because it computed the rect inline.
  **Caught by:** A6.2's byte equality.
* **Breaks byte-determinism by echoing the region as parsed floats with
  different repr** across runs, or by echoing an unrounded `resolved_fractional`.
  **Caught by:** A6.4; `resolved_fractional` is rounded to 6 dp by `pil_region`.
* **Clamps an out-of-range region**, so `--region 0,0,1.5,1` silently measures
  the whole frame and reports success. **Caught by:** A6.5.
* **A vacuous A6.2** that compares only a handful of scalar fields rather than
  the full payload. **Caught by:** review — the criterion says full payload with
  a named key list removed, and that list must be a literal in the test.
* **Rebasing W6 on `main` rather than on merged W1**, silently reverting W1's
  changes to the same three files. This is the exact failure that has already
  cost this build three times. **Caught by:** A6.1 plus re-running W1's A1.2 and
  A1.7 after W6 merges — which is criterion D6 in §10.

---

## 9. W7 — documentation and release

### 9.1 Decision and justification

Version **0.4.0**. It is not a patch: foreground-mode numbers change on RGBA
input, the payload gains fields on every tool, three new tools ship, and
`--region` is a new surface. Under 0.x, that is a minor bump.

Version strings live in **eleven** places — five in the manifests and
`pyproject.toml` (note `marketplace.json` carries it **twice**), six as
`TOOL_VERSION` once the three new tools land — and there is currently **no
test** that they agree with the tools. The manifest trio is guarded by
`tests/test_packaging_conformance.py`; `TOOL_VERSION` is not guarded at all.
W7 closes that gap — it is the same class of drift the repository already
decided to test for, left half-done.

### 9.2 Deliverables

* **Version bump to 0.4.0** in: `plugin.json`, `.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json` (**both** `metadata.version` and
  `plugins[0].version`), `pyproject.toml`, and `TOOL_VERSION` in
  `pil_palette_diff.py`, `pil_structure_diff.py`, `pil_contract_verdict.py`,
  `pil_crop.py`, `pil_annotate.py`, `pil_image_info.py`.
* **`tests/test_packaging_conformance.py`**: a new
  `test_tool_versions_match_the_manifest`, parametrised over every
  `scripts/pil_*.py` that defines `TOOL_VERSION`, extracting it by regex and
  asserting equality with `plugin.json`'s `version`. It must **discover** the
  scripts by glob, not list them, so a new tool cannot be added without being
  covered.
* **`skills/image-measurement/SKILL.md`**: the three new tools in the
  "Choosing the right tool and field" table; a `--region` row; the foreground
  section rewritten for coverage weighting; the "thin objects lose edge
  fidelity" caveat corrected to say it applies to the border-median path and no
  longer to the alpha path; the new `foreground_source_mismatch` flag added to
  the flags list.
* **`README.md`**: tool reference for the three new tools; `--region`; the
  0.4.0 behaviour change stated as a behaviour change, so a caller with stored
  numbers knows they moved.
* **`agents/image-comparison-analyst.md`**: the new tools and, specifically,
  when the analyst should reach for `pil_crop` (a suspicious region at native
  resolution) and `pil_annotate` (grounding a described region) — the loops
  they exist to close, not a feature list.
* **`docs/index.md`**: contents entries; the Status section for 0.4.0; the Open
  items list corrected — "the alpha foreground path is uncalibrated" becomes
  either "calibrated as of \<bundle\>" (if W2b landed) or "**correct but still
  uncalibrated**" (if W2b was descoped), which is a materially different and
  better position than today.
* **`docs/phase3-scope.md`**: A1–A4 marked implemented with their bundle links;
  open questions 1 and 2 marked **decided** with the decisions from §5.1;
  A5 and Track B untouched.

### 9.3 Acceptance criteria

| # | Criterion |
|---|---|
| A7.1 | `grep -rn '"version"\|TOOL_VERSION\|^version' plugin.json .claude-plugin/ pyproject.toml scripts/` shows `0.4.0` in all **eleven** places and nothing else. |
| A7.2 | `pytest tests/test_packaging_conformance.py` passes, including the new version test, and the new test **fails** when `TOOL_VERSION` in any one tool is manually reverted (verify once, by hand, before committing — a version test that cannot fail is worse than none). |
| A7.3 | Every tool named in `SKILL.md`'s table exists in `scripts/`, and every `scripts/pil_*.py` with a `main()` appears in the table. Assert both directions in a test — a docs drift check that only runs one way misses the new tool nobody documented. |
| A7.4 | `docs/index.md`'s open-items list contains no claim contradicted by a shipped bundle. Specifically the phrase "the alpha foreground path is uncalibrated" must be gone or qualified. |
| A7.5 | `claude plugin validate --strict` passes (the phase-1 gate, still in force). |
| A7.6 | No file outside W7's ownership list is modified by W7's commit. |

### 9.4 How a plausible implementation could be wrong

* **Bumping three of the four manifests.** `marketplace.json` holds the version
  twice and the second is easy to miss; `claude plugin tag` refuses to tag when
  they disagree. **Caught by:** the existing marketplace tests plus A7.1.
* **Editing `INTERPRETATION_LIMITS` in a tool file** while "updating the docs".
  This is the collision flagged in §1.3 and it will silently revert W1's or
  W6's wording. **Caught by:** A7.6.
* **A version test that lists tools explicitly**, so the three new ones are
  never checked. **Caught by:** A7.3's bidirectional assertion and the glob
  requirement in §9.2.
* **Describing 0.4.0 as a bug fix** and not warning that foreground numbers on
  RGBA input have moved. A caller diffing against stored output will read the
  change as a regression. **Caught by:** review of `README.md` against A7.4.
* **Claiming the alpha path is calibrated when W2b was descoped.** **Caught by:**
  A7.4, and it is the exact failure mode this repository names as "provenance
  claimed but not verified".

---

## 10. Definition of DONE for the whole build

Gradeable without asking me. Every line is a command, a file, or a number.

**D1 — the suite.** `uv run python -m pytest` reports **0 failed, 0 errors,
0 xpassed, 0 xfailed**. The 164 test functions currently in the tree (213
collected cases) all still pass, and none was deleted or had an assertion
weakened without a written justification in its docstring.

**D2 — full-frame is untouched.** The A1.1 worktree diff is empty for every
fixture, in both tools, without `--foreground`, including on RGBA inputs.
Re-run after W6 and after W7, not only after W1.

> **Amended 2026-08-20, after W6 landed.** As written, D2 became unsatisfiable
> the moment §8.2's payload additions shipped, and the contradiction is
> internal to this plan rather than a defect in anyone's work. §8.2 mandates
> `source_size`, `parameters.region`, `parameters.region_space`, the per-image
> `region` block and a region `interpretation_limits` entry **unconditionally**
> — verified present on a plain invocation with no `--region` — so a byte diff
> against the pre-W6 payload is necessarily non-empty for *every* invocation,
> region-using or not. Those fields are load-bearing: A6.2 and A6.3 compare
> full payloads between a `--region` run and a pre-cropped-file run, and a
> field that appears only when `--region` is passed would make that equality
> impossible. W6 raised this rather than quietly weakening §8.2 or ignoring D2,
> which is the correct handling.
>
> D2's intent is *"the alpha fix did not move full-frame numbers"*, and that
> intent survives intact. The criterion is therefore restated as: **no
> pre-existing payload field changes its value** on a full-frame invocation —
> new keys may be added, but every key that existed before must carry the same
> value. Grade it by comparing the intersection of the two payloads' keys, not
> by byte-diffing the whole document.

**D3 — the alpha fix is graded against truth, not against a threshold.**
`tests/test_alpha_weighting.py` does not read `scripts/detection_limits.json`
(`grep -c detection_limits tests/test_alpha_weighting.py` == 0), and its bounds
are absolute literals: luminance and saturation within 0.001 of
`alpha_truth.weighted_stats`, accent fraction within 1e-4, sub-pixel excursion
≤ 0.5, floor-insensitivity ≤ 1.243.

**D4 — no xfail was rewritten into vacuity.** `grep -c xfail
tests/test_alpha_foreground.py` == 0; every removed reason string appears
verbatim in a docstring in the same file; the two `TestMaskProvenance` tests
assert their residual **two-sidedly** (`approx(..., rel=)`), never
`<=`; `git diff calibration/alpha_truth.py` contains no non-docstring change.

**D5 — calibration provenance is repaired.**
`scripts/detection_limits.json` → for every metric,
`provenance.foreground_estimate.n >= 300`, `.alpha == 0.01`, and
`len(.scenes) >= 3`. Every metric carries `threshold_foreground_estimate`,
`threshold_foreground_estimate_no_placement` and `threshold_foreground_alpha`
(non-null if W2b landed, `null` with a stated reason if not). The 2026-08-19
bundle has exactly one added line and no other change.

**D6 — nothing was silently reverted.** After W6 and again after W7,
re-run A1.2, A1.7 and A1.8. All pass. `git log --oneline --name-only` shows no
file in §1.2 modified by an item that does not own it.

**D7 — the three new tools exist and are honest.**
`scripts/pil_crop.py`, `scripts/pil_annotate.py`, `scripts/pil_image_info.py`
each: emit JSON on stdout with `tool`, `version`, `parameters`,
`interpretation_limits`; produce byte-identical output across runs; exit 2 with
**empty stdout** on every rejection path; contain no `ImageFont` import
(annotate); pass their own acceptance table.

**D8 — the grounding loop was verified by a model, not by an assertion.**
`runs/2026-08-2X-annotate-readback/README.md` exists and quotes a transcript in
which an agent shown only the annotated image transcribes every box number
correctly on 3 images.

**D9 — region equals pre-crop, byte for byte.** A6.2 passes for both tools at
two source resolutions, running the real `pil_crop` binary.

**D10 — the release is consistent.** `0.4.0` in all eleven places;
`test_tool_versions_match_the_manifest` present, glob-driven, and demonstrated
to fail when a version is reverted; `claude plugin validate --strict` passes;
`SKILL.md`'s tool table and `scripts/` agree in both directions.

**D11 — every claim in the shipped docs is backed by a bundle.**
`docs/index.md`'s open-items list contains no statement contradicted by a
committed run bundle, and every new capability claim points at the bundle that
established it. In particular: the plugin claims region-cutting capability only
after W6's gate passed, per phase 3's own "crediting the plugin with harness
capabilities" failure mode.

**D12 — the residuals are published, not buried.** The W1 bundle states, with
numbers: the irreducible RGBA-vs-composited delta per corpus scene; the
MEDIANCUT centre-selection residual; the `0.5/w̄` working-resolution
un-premultiply bound. If any of these is absent, the build is not done, however
green the suite is.

---

## 11. Open questions

Marked open because they need a measurement, not a decision. Each carries the
measurement that closes it. I have deliberately not guessed at any of them.

**11.1 — What the alpha-path foreground thresholds actually are.**
I predicted `luminance_mean_delta_abs` would fall below ~2.0 on the alpha path
after W1, on the basis that the weighted reading is placement-stable (A1.7
targets ≤0.5 excursion) while the current 34.129 is set entirely by placement
perturbation. That is a **prediction with a stated basis, not a measurement**.
*Closes with:* W2b. If the measured value lands near 34, either W1 is incomplete
or resampling a thin RGBA object genuinely moves its coverage-weighted colour —
and which of those it is must be determined before the number is published.

**11.2 — Whether `ALPHA_FOREGROUND_MIN` should remain 8.**
Two forces pull against each other. The cross-repo convergence claim in
`pil_common`'s docstring — the same definition of "visible pixel" as
`synty_asset_index` — pins it at 8. But the floor now governs mask *extent*
(bbox, occupancy, `foreground_too_small`) rather than bias, and the right extent
floor is an empirical question the corpus can answer.
*Closes with:* A1.9's sweep. If weighted statistics are insensitive across
8–64 (they should be, since a pixel's influence is its own weight), the floor is
a pure extent decision and 8 stands on the convergence argument alone. If they
are **not** insensitive, the fix has a residual and A1.9 fails, which is the
signal to look again rather than to raise the floor.

**11.3 — Whether MEDIANCUT's unweighted centre selection leaves a material
residual.**
The chosen palette approach weights coverage but not the choice of cluster
centres (§3.3.1). A rough estimate says the residual is small on `glass_a64`,
but an estimate is not a measurement, and `alpha_ladder` — 18 bands of one
colour at 18 alphas — is the scene designed to expose it.
*Closes with:* a comparison in W1's bundle between the shipped approach and a
one-off coverage-replicated quantisation (`K=16`) on `glass_a64/128/192` and
`alpha_ladder`, reporting per-entry coverage deltas and any change in which
colours win slots. If the residual is material, the `interpretation_limits`
entry must state its measured magnitude; if it is not, the entry says so.
The replicated version is a measurement instrument, not a shipping candidate.

**11.4 — Whether W2b is feasible without restructuring `perturb.py`.**
The RGBA control family needs premultiplied resampling inside the perturbation
machinery, and needs JPEG excluded. Whether that is a contained change or a
redesign, I cannot tell from reading `measure.py`'s recipe table alone.
*Closes with:* a 30-minute spike by W2b's owner before committing to the item.
If it is a redesign, descope per §4.4 — the release does not depend on it.

**11.5 — Whether `structured` is a legitimate foreground scene.**
W2a proposes widening `FOREGROUND_SCENES` to include it, on the argument that
its `(32,32,36)` margin is a genuine backdrop. `scenes.py`'s existing comment
says the opposite about the non-`thin_object` scenes generally. I think the
comment is right about `busy` and `dark_accent` and over-broad about
`structured`, but that is a judgement about an image, not a measurement.
*Closes with:* running `pil_structure_diff --foreground` on one `structured`
scene and reading `foreground.fraction_of_frame` and the mask bbox. If the mask
is "everything except a 3px margin", it is not a foreground and the scene is
dropped in favour of a third purpose-built object scene.

**11.6 — What `partial_coverage_share` should make a caller do.**
The number is emitted (§3.3) with no flag and no threshold, deliberately.
Whether there is a share above which foreground colour statistics should be
disqualified — the way `accent_area_very_small` disqualifies hue statistics —
is a real question, and answering it needs the coverage-weighted error
distribution across the corpus, which does not exist yet.
*Closes with:* W1's bundle plotting weighted-statistic error against
`partial_coverage_share` over all 19 scenes. If there is a knee, a flag can be
proposed in a later phase with that measurement behind it. Until then, no flag —
an uncalibrated flag threshold is precisely what this repository refuses.
