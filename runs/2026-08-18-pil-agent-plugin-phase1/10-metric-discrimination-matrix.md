# Metric discrimination matrix

The central empirical result of phase 1. Two variants were derived from the
reference image and compared against it:

- **`variant-rescaled.png`** — the reference at 60% scale (1003×565). Content is
  unchanged, so every metric *should* read "same".
- **`variant-cyan-to-red.png`** — the reference with only the cyan hue family
  (7,634 px) rotated to red. Layout, luminance and composition are untouched;
  only the accent colour scheme changed. Metrics *should* read "changed".

A metric is only useful for comparison if it separates these two cases.

| Metric | Rescale (expect same) | Recolour (expect changed) | Separates? |
|---|---|---|---|
| `structural_similarity` | 0.9996 | 0.9990 | **No** |
| `dhash_distance` | 0 | 0 | **No** |
| `ahash_distance` | 0 | 0 | **No** |
| `changed_area_fraction` | 0.0000 | 0.0034 | Weakly |
| `base_palette_distance` | 2.62 | 0.50 | **No — inverted** |
| `accent_palette_distance` | 4.83 | 7.72 | Weakly |
| `hue_families_lost` | `[]` | `[]` | **No** |
| `hue_family_fraction_deltas` | ~0 | cyan −0.060, red +0.071 | **Yes** |
| `hue_families_diminished` | `[]` | `['cyan']` | **Yes** |
| `accent_hue_shift_detected` | `False` | `True` | **Yes** |
| `changed_region_bbox_fractional` | `None` | `[0.73, 0.08, 0.98, 0.62]` | **Yes (localises)** |

## Findings

1. **`base_palette_distance` is actively misleading.** It scored the recolour
   (0.50) as *more similar* than a pure rescale (2.62). A tool that reported only
   a global quantised palette would answer "did the colour scheme change?"
   backwards. This is the flaw that shaped the whole design.

2. **Both perceptual hashes are blind to hue.** dHash and aHash operate on
   luminance, and the rotation was luminance-preserving. They are excellent at
   near-duplicate detection and scale invariance, and useless for colour-scheme
   questions. They must never be the sole similarity signal.

3. **Structural metrics are correctly blind, and that is fine.** Structure did
   not change, so 0.9990 is the right answer — but it means structural
   similarity cannot be read as overall "sameness". The two tools are
   complementary and both must be consulted.

4. **Presence/absence testing is too brittle for real edits.** `hue_families_lost`
   stayed empty because residue pixels near the HSV threshold kept cyan's count
   above zero. Detection has to be magnitude-based: a family counts as shifted
   only when its share of accent pixels moves by ≥0.02 absolute *and* ≥30%
   relative.

5. **`changed_region_bbox_fractional` earns its place.** It localised the
   recolour to x∈[0.73, 0.98], y∈[0.08, 0.62] — the right-hand region holding the
   TESTING column and lane labels, which is exactly where the cyan lived. In a
   revision loop, "what changed and where" is more actionable than "how much".

## Consequence for the tool contract

The headline verdict for a colour-scheme question is `accent_hue_shift_detected`
plus `hue_family_fraction_deltas`. Palette distances are supporting detail only.
Any caller that reads `structural_similarity` or a hash distance alone will miss
accent recolouring entirely.
