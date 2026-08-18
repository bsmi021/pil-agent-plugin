---
name: image-measurement
description: Measures images numerically and compares two images with Pillow — exact hex colour palettes with coverage, per-hue census, luminance/saturation/entropy statistics, per-region grid statistics, perceptual hash distance, and changed-region bounding boxes. Use when asked whether two images match, whether a render or screenshot kept a reference's colour scheme or layout, what specifically changed between two versions, what the exact colours of an image are, or to compare a generated image against a reference. Does NOT measure 3D geometry or polygon count.
---

# Image measurement

Native vision already reads images well — text, layout, objects, style. Use these
tools when the answer needs to be a **number**: an exact hex value, a reproducible
similarity score, or coordinates of what changed.

Both tools emit JSON on stdout, are deterministic (repeated runs are
byte-identical), and take one image (analyse) or two (analyse and diff).

## Running the tools

Preferred, using the plugin's own pinned environment:

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" python "${CLAUDE_PLUGIN_ROOT}/scripts/pil_palette_diff.py" "<image>"
```

If `uv` is unavailable, invoke directly — requires Pillow and numpy in the active
environment:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/pil_palette_diff.py" "<image>"
```

Always quote image paths: they frequently contain spaces or parentheses.

## Choosing the right tool and field

| Question | Tool | Field to read |
|---|---|---|
| Is this the same image? | structure | `dhash_distance`, `changed_area_fraction` |
| Same layout/composition? | structure | `structural_similarity` |
| What changed, and where? | structure | `changed_region_bbox_fractional`, `most_divergent_cells` |
| Did the colour scheme change? | palette | `accent_hue_shift_detected`, `hue_family_fraction_deltas` |
| What exact colours are used? | palette | `base_palette`, `accent_palette`, `hue_families` |
| Did it get more/less detailed? | structure | per-cell `edge_mean` (read the caveat below) |

**Consult both tools for any "do these match?" question.** They are blind to
different things. A measured cyan→red recolour scored 0.9990 structural similarity
and 0 hash distance, versus 0.9996 and 0 for a mere rescale — structure and hashes
cannot see hue at all.

## `pil_palette_diff.py`

```bash
... pil_palette_diff.py "<reference>" ["<candidate>"] [--colors 8] [--accent-sat 100] [--accent-val 60]
```

Reports colour three ways, because no single view suffices:

- `base_palette` — which colours dominate **by area**
- `accent_palette` — which colours dominate among **vivid pixels only**
- `hue_families` — which hues are **present at all**, regardless of area

**Read `hue_families` when a colour matters semantically.** Area-weighted
palettes systematically miss small vivid accents: on a dark test image, 75% of
pixels were near-black, so the global palette contained no vivid colours at all,
and the accent palette was itself crowded out by the single dominant accent. A
cyan occupying 0.485% of the frame — which encoded an entire UI state — appeared
only in the hue census. Semantic importance and pixel area are uncorrelated.

For a colour-scheme verdict, read `accent_hue_shift_detected` first, then
`hue_families_diminished` / `_amplified` / `_lost` / `_gained` and
`hue_family_fraction_deltas` for detail. Do **not** rely on
`base_palette_distance`: measured against a real recolour it scored the changed
image as *more* similar than an unchanged rescale.

Direction words (`lost`, `gained`, `diminished`, `amplified`, and the deltas)
describe what the **second** image did relative to the first. The overall verdict
and both palette distances are symmetric under swapping them.

## `pil_structure_diff.py`

```bash
... pil_structure_diff.py "<reference>" ["<candidate>"] [--grid 4x3]
```

Statistics run on a fixed-size working copy over a grid defined as *fractions* of
each image's dimensions, so the same layout at different resolutions compares
equal. Mismatched aspect ratios are reported in `flags` as
`aspect_ratio_mismatch` — when you see it, treat per-cell numbers with suspicion,
since the grids no longer correspond.

`changed_region_bbox_fractional` is usually the most actionable output in a
revision loop: it answers *where* something changed, not merely how much.

## These tools do not measure geometry

`edge_mean` and `entropy` are 2D image-complexity proxies. They are **not**
polygon counts, mesh density, or topology, and must never be used as a proxy for
them. Shading, normal maps, lighting and camera angle all move these numbers
independently of the underlying model — a smooth-shaded low-poly render can score
as more complex than a flat-shaded high-poly one.

For polygon count, mesh density, topology or "is this lower-poly than that",
query the 3D scene's own mesh statistics directly — e.g. the Blender MCP server's
object/mesh summary tools — rather than analysing a rendered image. Analysing a
render to infer geometry produces confident, wrong answers.

Every `pil_structure_diff` payload repeats this under `interpretation_limits`.

## Other limits worth stating to the user

- Palette distance is Euclidean RGB, which is not perceptually uniform. Treat it
  as a relative signal between comparable images, not an absolute perceptual delta.
- Accent membership is a hard HSV threshold, echoed in the output as
  `accent_thresholds`. Colours near the boundary can flip between palettes.
- Hue-shift margins were calibrated against one image and two derived variants.
  On unusual inputs, check `hue_family_fraction_deltas` directly rather than
  relying only on the boolean verdict.
