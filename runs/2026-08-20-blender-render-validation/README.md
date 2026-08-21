# Blender matched-view render validation — Track B2

Bundle date: 2026-08-20. Corresponds to `scripts/pil_blender_render.py`
0.4.0 landing on branch `feat/phase3-b2`, per
[docs/phase3-b2-b3-build-plan.md](../../docs/phase3-b2-b3-build-plan.md) §4.1.

**These files are numbers. Renders themselves are not committed** —
`runs/**/*.png` is gitignored, and the reference sheet quadrants used here
are derived from `C:/Projects/tms-heim/art/skeleton-crusaders/brute/references/`
at test time, in memory or in a pytest tempdir. Never copied back into
`tms-heim`, never committed here.

## Camera-convention verification (how I chose front / side / back)

The `.blend` file `C:/Projects/tms-heim/art/skeleton-crusaders/brute/source/SM_Chr_Skeleton_CrusaderBrute_01.blend`
contains 49 mesh objects. **33 of them have `hide_render=True`** —
donor/reference geometry (`Chr_ArmUpperLeft_Male_08_Static`, `Chr_Hips_Male_13_Static`, …)
sitting at 100× the visible character's scale.

Filtering to `hide_render=False` reveals the render-visible character (16
meshes: `SKC_Anatomy_*`, `SKC_Armor_*Brute*`, `SKC_Donor_*`, `SKC_Garment_*`)
whose depsgraph-evaluated world-space bounding box is:

| axis | range | interpretation |
|---|---|---|
| X | 2.365 | shoulder-to-shoulder (T-pose arms) |
| Y | 0.940 | front-to-back depth (cape extends to +Y) |
| Z | 2.144 | feet at Z=0 up to head at Z=2.144 |

Character is **standard Z-up** (Blender default), **faces −Y** (cape hangs
behind at +Y), **right hand extends to +X**. This is verified by two
independent signals:

1. **Bounding-box asymmetry.** Y range is asymmetric (min −0.24, max +0.70),
   consistent with a cape hanging behind the character. If facing +Y, the
   cape would extend to −Y and Y_min would be more negative than Y_max.
2. **Visual comparison of the rendered `front` view** against
   `C:/Projects/tms-heim/art/skeleton-crusaders/brute/runs/compare-reference-2026-08-16/wb_front.png`
   (prior manual Workbench render, known-good front): both show helmet slit
   centred with the skull face visible, cross tabard on chest, T-pose arms
   extended horizontally.

The `back` view (mirror across the facing axis) renders correctly — the
cape covers most of the frame with the small back-of-cross visible on the
cape, matching the bottom-left quadrant of the reference sheet.

The `side` view is the wearer's-right profile: camera positioned at world
+X (wearer's right side, computed via right-hand rule with facing = −Y and
up = +Z), looking toward −X across the body. Structural discrimination
(see below) confirms this: side render matches the sheet's top-right
quadrant markedly better than either front or back.

## Real-corpus comparison numbers

Reference crops are the four quadrants of the 1536×1024 turnaround sheet
`skeletal-brute-tpose-turnaround-lowpoly-2026-08-15.png`, each 768×512:
top-left = front, top-right = side, bottom-left = back, bottom-right =
three-quarter (unused). Cropped at test time; never persisted here.

Renders were performed by `pil_blender_render.py` with `--reference` set
to the matching quadrant crop (so render dimensions were pinned to 768×512
to match). See `front-comparison.json`, `side-comparison.json`,
`back-comparison.json` for the full JSON payloads.

Headlines:

| view | ssim vs matching ref | dhash | diff flags | refused? |
|---|---|---|---|---|
| front | 0.8103 | 16 | `foreground_source_mismatch` | no |
| side  | 0.8061 | 15 | `foreground_source_mismatch`, `foreground_aspect_mismatch` | no |
| back  | 0.7665 | 12 | `foreground_source_mismatch` | no |

**None of the three fire `aspect_ratio_mismatch` or `resolution_mismatch`.**
This is the primary acceptance criterion from §4.1: the bbox-registration
approach closes the framing-related failure the field trial documented
(T-pose render vs A-pose reference → `structural_similarity 0.900` +
`aspect_ratio_mismatch` + `resolution_mismatch`), on the real character it
mattered for.

`foreground_source_mismatch` fires on every comparison because renders
carry a true alpha channel (`film_transparent=True`, PNG RGBA) while the
reference-sheet crops are opaque JPEG-sourced. The flag is honest reporting
of that one-sided difference; it does not refuse, and comparison metrics
remain deterministic.

`foreground_aspect_mismatch` fires on `side` because the reference-sheet
quadrant has the character in a narrow profile *with cape blocking the
whole back*, while the render's cape is thinner in the sagittal plane. The
foreground bbox aspects differ >10%. This is a real content difference,
not a tool defect, and the flag surfaces it correctly.

## Discrimination matrix (each rendered view vs each reference crop)

See `discrimination-matrix.json` for the full 3×3.

| render \ ref | front | side | back |
|---|---|---|---|
| **front** | **ssim 0.810 · dhash 16** | 0.738 · 32 | 0.793 · 13 |
| **side**  | 0.701 · 23 | **0.806 · 15** | 0.735 · 28 |
| **back**  | 0.788 · 13 | 0.711 · 29 | **0.767 · 12** |

Bold cells are the "correct" pairings. **Side profile discriminates
strongly** — best ssim (0.806) is against the side reference, well above
front (0.701) or back (0.735). This is what the test
`test_side_render_beats_wrong_view_references_by_ssim` pins.

**Front and back do not strictly discriminate by ssim.** front-render vs
back-ref (0.793) beats back-render vs back-ref (0.767); back-render vs
front-ref (0.788) beats back-render vs back-ref (0.767). This is a
**published residual**, not a hidden bug: the front and back references
both show a T-pose silhouette dominated by an outstretched arm span and a
downward-hanging cape, and at the coarse 4×3 grid pil_structure_diff uses
by default, those two silhouettes are structurally similar. The axis
convention is correct — front visibly shows the skull face and cross
tabard, back visibly shows the back of the cape with cross emblem — and
the visual identification is the ground truth, not the ssim ranking.
Callers relying on ssim alone to distinguish front from back should not
do so; the intended use is caller-supplied per-view references, one per
named view, exactly as the plan specifies.

## Determinism claim, as scoped

Two independent CLI invocations of
`pil_blender_render.py <BRUTE.blend> --view front --out X.png` on this
machine (Windows 11, Blender 5.1.2 at
`C:/Program Files/Blender Foundation/Blender 5.1/blender.exe`) produce
**byte-identical PNG output**. Verified by `test_two_renders_of_the_same_view_are_byte_identical_pngs`
in `tests/test_blender_render.py`.

Blender's PNG writer embeds `tEXt` metadata chunks (`Date`, `Time`,
`RenderTime`, `Frame`, `Camera`, `Scene`, dpi) that vary per render even
when pixels are identical. `pil_blender_render.py` post-processes the
output with Pillow to strip these chunks (`_strip_png_metadata`), which
is what makes byte-identity hold. Pre-strip, the two renders' full-file
sha256 differ; the raw pixel-array sha256 matches. Both facts were
measured during the build (build log entries at
`front3.png` / `det_a.png` / `det_b.png` / `det2_a.png` / `det2_b.png` in
the build tempdir).

Cross-machine or cross-install byte-identical rendering is **not claimed**
— Blender version, OS PNG codec, and Workbench studio-light bundling can
all shift bytes across installs. This is stated in the tool's
`interpretation_limits` on every payload.

## Regeneration

From this worktree:

```
# 1. Crop the turnaround sheet into three quadrants (in a tempdir):
python -c "from PIL import Image; \
  im=Image.open(r'C:/Projects/tms-heim/art/skeleton-crusaders/brute/references/skeletal-brute-tpose-turnaround-lowpoly-2026-08-15.png'); \
  W,H=im.size; w,h=W//2,H//2; \
  im.crop((0,0,w,h)).save('front_ref.png'); \
  im.crop((w,0,W,h)).save('side_ref.png'); \
  im.crop((0,h,w,H)).save('back_ref.png')"

# 2. Render each view against its reference:
for VIEW in front side back; do
  python scripts/pil_blender_render.py \
    C:/Projects/tms-heim/art/skeleton-crusaders/brute/source/SM_Chr_Skeleton_CrusaderBrute_01.blend \
    --view "$VIEW" --out "${VIEW}.png" \
    --reference "${VIEW}_ref.png" > "${VIEW}-comparison.json"
done

# 3. Verify the full test suite:
uv run pytest tests/test_blender_render.py -v
```

## Residuals (per docs/phase3-handoff.md §9 D12)

1. **Front/back are not ssim-distinguishable at the shipped default grid.**
   Not a defect; documented in the discrimination-matrix table above.
   Callers relying on ssim ranking to route "which view was rendered"
   should not — the intended workflow supplies per-view references
   explicitly.

2. **`foreground_source_mismatch` fires unconditionally with opaque
   references.** Documented in `interpretation_limits` on every payload;
   flag surfaces real one-sided source difference (alpha render vs
   border-median reference), does not refuse, does not affect metric
   determinism.

3. **`foreground_aspect_mismatch` on `side`.** The reference-sheet side
   quadrant shows a bulkier cape profile than the current asset renders.
   Real content difference, not a tool defect.

4. **Cross-machine render byte-determinism is not claimed.** Comparison
   metrics on a fixed image pair remain deterministic everywhere; the
   render step alone is scoped to same-machine, same-install.

## What the test suite pins (as of this bundle)

`tests/test_blender_render.py` — 23 tests, all green:

- 3 hermetic rejection paths (missing Blender / missing .blend /
  missing reference) — exit 2, empty stdout, no traceback
- 5 hermetic probe-parser tests (sentinel-delimited JSON extraction)
- 4 hermetic `build_comparison` refusal tests (correctly reads
  images.a.flags / images.b.flags, not diff.flags)
- 8 corpus-gated real-render tests (framing flags absent on all three
  views, resolution pinning, byte-determinism across two invocations,
  side-view ssim discrimination, visible mesh count guard)
- 2 corpus-gated empty-scene refusal tests (with and without
  --reference)
- 1 corpus-gated background-dominant reference refusal test

Full-suite regression: 632 passed (609 baseline + 23 new), 6 skipped
(unchanged from baseline).
