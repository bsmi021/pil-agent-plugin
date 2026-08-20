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

`${CLAUDE_PLUGIN_ROOT}` is Claude Code's name for the plugin root. Under the
[Agent Plugins](https://agent-plugins.org/) standard the equivalent variable is
`${PLUGIN_ROOT}`; substitute it if that is what the host provides.

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
| Same **object**, ignoring the backdrop? | both, with `--foreground` | same fields, foreground-masked |
| Did my *intended* change land, and did anything else drift? | contract | per-predicate `verdict` + `detection_limit` |
| Did the colour change *perceptually*? | palette | `base_palette_distance_de2000`, `accent_palette_distance_de2000` |
| Measure only **part** of the frame | both, with `--region L,T,R,B` | same fields, scoped to the region |
| Let me **see** that region at full resolution | `pil_crop` | native-resolution crop, optional integer upscale |
| Let me **point** at something so a model understands | `pil_annotate` | numbered boxes drawn on a copy |
| What does the **file** say — dimensions, alpha, EXIF, ICC? | `pil_image_info` | file facts vision cannot see |

**Consult both tools for any "do these match?" question.** They are blind to
different things. A measured cyan→red recolour scored 0.9990 structural similarity
and 0 hash distance, versus 0.9996 and 0 for a mere rescale — structure and hashes
cannot see hue at all.

## Foreground mode — required for object renders

Full-frame metrics include the background. On an asset render (a model on a
preview backdrop), the shared background can be ~98% of both frames, so two
**different** objects score near-identical: a measured sword pair read 0.991
structural similarity while the sword itself occupied 1.5% of the frame. The
flags tell you when this is happening — **check `flags` before trusting any
score**:

- `background_dominant` — the frame is mostly background (estimated foreground
  under 10%). Full-frame similarity and palettes describe the backdrop. Re-run
  both tools with `--foreground`.
- `foreground_too_small` — foreground mode is on, but the object covers under
  2% of the frame; regional and hue statistics carry few pixels.
- `accent_support_low` / `accent_area_very_small` — too few vivid pixels for
  the hue-shift verdict to rank anything. Do not let
  `accent_hue_shift_detected` influence a decision while these are present.
- `foreground_mask_empty` — no foreground found; the tool fell back to
  full-frame analysis.

`--foreground` masks the background out: from the **alpha channel** when the
file carries real transparency, otherwise by removing pixels near the
**border-median colour** in OKLab (`--background-delta`, default 0.035 — the
same definition of a visible pixel as the Synty asset index). The structure
tool additionally crops to the foreground bounding box, so the comparison is
position-independent, and only grid cells with real foreground support are
scored (`cells_compared` / `cells_skipped_low_support` report the split). In
this mode every fraction (accent share, hue `fraction_of_frame`) is relative to
the **foreground**, and the `foreground` block in each image's output records
how the mask was derived.

One caveat: thin objects lose edge fidelity across resolutions (most of their
pixels are edge-blended), so compare like-resolution renders where possible and
rely on relative ranking otherwise.

## `pil_palette_diff.py`

```bash
... pil_palette_diff.py "<reference>" ["<candidate>"] [--colors 8] [--accent-sat 100] [--accent-val 60] [--foreground] [--background-delta 0.035]
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
`hue_family_fraction_deltas` for detail. For colour *distance*, read the
`*_de2000` fields (CIEDE2000 over D65 CIELAB — raw values, never a percentage,
no verbal bands). Do **not** rely on the RGB `base_palette_distance`: measured
against a real recolour it scored the changed image as *more* similar than an
unchanged rescale; it is kept only for continuity. `--accent-space lch` swaps
the accent gate to perceptual chroma/lightness floors (HSV remains the default;
calibration measured what each gate admits but cannot rank them without accent
ground truth).

Direction words (`lost`, `gained`, `diminished`, `amplified`, and the deltas)
describe what the **second** image did relative to the first. The overall verdict
and both palette distances are symmetric under swapping them.

## `pil_structure_diff.py`

```bash
... pil_structure_diff.py "<reference>" ["<candidate>"] [--grid 4x3] [--foreground] [--background-delta 0.035]
```

Statistics run on a fixed-size working copy over a grid defined as *fractions* of
each image's dimensions, so the same layout at different resolutions compares
equal. Mismatched aspect ratios are reported in `flags` as
`aspect_ratio_mismatch` — when you see it, treat per-cell numbers with suspicion,
since the grids no longer correspond.

`changed_region_bbox_fractional` is usually the most actionable output in a
revision loop: it answers *where* something changed, not merely how much.

## `pil_contract_verdict.py`

```bash
... pil_contract_verdict.py "<before>" "<after>" --contract contract.json [--foreground] [--pairs manifest.json]
```

Answers the question a caller actually has — *"did my requested change land, and
did anything else drift?"* — instead of a bare similarity number. The contract
declares intent:

```json
{"expect_change": ["palette.warmer"], "invariant": ["layout.composition_preserved", "identity.silhouette_preserved"]}
```

and every predicate returns `SATISFIED`, `VIOLATED`, or `UNMEASURABLE`, citing
the deciding fields. Three rules make the output trustworthy:

- **Every null result carries its detection limit.** "Invariant satisfied" only
  means "no change bigger than X detected" — X comes from the WP2 calibration
  bundle (`scripts/detection_limits.json`, regenerable via
  `calibration/distill_detection_limits.py`).
- **`UNMEASURABLE` is never approximated.** `style.*` and `identity.same_character`
  (not reducible to pixel statistics) always refuse rather than guess.
  `geometry.*` refuses the same way **unless** the caller supplies real scene
  statistics via `--scene-stats-a`/`--scene-stats-b` (see `pil_blender_mesh.py`
  below) — with no scene stats supplied, the default is still unconditional
  refusal, never an approximation from pixels.
- **Multi-pair aggregation is worst-case.** With `--pairs`, one diverging view
  fails the item; it cannot be averaged away.

## Closing the loop: crop, annotate, and file facts

These three exist because measurement and vision are blind to different things,
and the fastest way to an answer is usually to hand one what the other found.

### `pil_crop.py` — see the region at native resolution

```bash
... pil_crop.py "<image>" --region "0.1,0.4,0.3,0.9" --out crop.png [--scale 4] [--region-space foreground]
```

An image is resampled to fit a vision encoder, so detail below that resolution
is simply gone, and the model does not even know the source's true pixel
dimensions. The measurement tools already tell you *where* to look —
`changed_region_bbox_fractional`, `most_divergent_cells`, the foreground
`bbox_fractional`. Feed one of those here and read the crop.

`--scale` is integer nearest-neighbour only. It magnifies; it never invents
detail, and an upscaled crop contains no more real information than the source
did. High-bit-depth `I`/`F`/`I;*` sources are **refused** rather than converted.

### `pil_annotate.py` — point at something so a model understands you

```bash
... pil_annotate.py "<image>" --out marked.png --box "0.1,0.4,0.3,0.9" [--box ...] [--grid 4x3] [--from-json structure.json]
```

Draws numbered boxes and gridlines onto a **copy**, so a model shown the result
can say "box 3" and be understood. `--from-json` takes a `pil_structure_diff`
payload directly and draws what it found.

Two things to know. **Numbering is by position** (top, then left), not the order
you passed the boxes — `legend[].requested_index` maps each drawn number back to
your input. And **never measure an annotated image**: the boxes and numerals are
pixels, so feeding it back into the measurement tools measures the annotation.

Where a numeral cannot be placed cleanly the tool says so rather than hiding it,
via per-entry `glyph_hazards` and payload `flags`; a numeral that had to be
forced into the frame is named `clamped`. Read those before trusting a crowded
overlay. The read-back evidence is in
[`runs/2026-08-20-annotate-readback/`](../../runs/2026-08-20-annotate-readback/README.md).

### `pil_image_info.py` — what the file says

```bash
... pil_image_info.py "<image>" ["<image>" ...]
```

Images reach a model stripped and resampled, so it sees none of this: true pixel
dimensions, mode, bit depth, whether an alpha channel exists **and** whether it
is actually used, ICC presence, EXIF (including `EXIF.*`/`GPS.*` sub-IFD tags),
DPI, frame count.

It reports what the file *claims* and refuses to infer past it. An ICC profile's
presence is not a colour-space guarantee; EXIF is producer-supplied and can be
stale or wrong. Metadata has three states, not two: absent is `null`,
**unreadable is `null` plus a flag** (`exif_unreadable`, `icc_unreadable`). One
unreadable file never aborts a batch — it reports `readable: false` with a
reason while its siblings still report.

## `pil_alignment.py --colors` — WCAG contrast ratio

```bash
... pil_alignment.py contrast --colors "#RRGGBB" "#RRGGBB"
```

WCAG 2.x relative luminance and contrast-ratio arithmetic, verified against the
standard's own published worked examples (black-vs-white = 21.0 exactly,
`#767676`-vs-white = 4.54, and further landmarks), not statistically calibrated
— it is a fixed public formula, the same way `pil_color.py`'s CIEDE2000 is
verified against published reference values rather than derived from a corpus.

The tool's other half — `pil_alignment.py alignment`, projection-profile edge
detection for "aligned within N px" verdicts — exists but is **demoted**: its
own discrimination gate found a 65px noise floor against an 8px useful ceiling,
and it fails to detect real shifts on cluttered or dark-background scenes. It
still emits real diagnostic baselines/margins/pixel deltas, carries an explicit
`demoted` flag and cites its evidence bundle, but is not advertised as a
validated alignment-verdict capability. See
[`runs/2026-08-20-alignment-discrimination/`](../../runs/2026-08-20-alignment-discrimination/derived-thresholds.json).

## `--region` on the measurement tools

```bash
... pil_structure_diff.py A.png B.png --region "0.1,0.4,0.3,0.9" [--region-space foreground]
```

Scopes every metric to a fractional box, applied to **both** images at full
resolution before anything else. A `--region` measurement equals pre-cropping
the file with `pil_crop` and measuring that, byte for byte — the two share one
parser — with one documented exception, the `region_background_estimate_diverged`
flag, which structurally cannot exist on an already-cropped file.

Composed with `--foreground`, the region crops first and the mask (and, on the
border-median path, the background estimate) is re-derived from the crop's own
borders rather than inherited from the frame.

## `pil_blender_mesh.py` — real geometry, from the scene, never from pixels

```bash
... pil_blender_mesh.py "<scene.blend>" [--blender-executable path\to\blender.exe]
```

The one sanctioned route to a geometry answer. It shells out to Blender headless
(`blender.exe --background <scene> --python <embedded probe>`, since `bpy` is not
importable outside Blender's own bundled interpreter) and reports, per mesh
object, straight from Blender's scene data: polygon count, vertex count,
material slot count/names, and scene-level bounding dimensions. Never renders,
never touches a pixel. Blender's install path is not assumed to be on `PATH`; if
it cannot be found, the tool exits 2 with empty stdout and a named reason — a
clean `UNMEASURABLE` upstream, never a traceback.

Feed its output to `pil_contract_verdict.py` via `--scene-stats-a`/
`--scene-stats-b` to resolve `geometry.poly_count.*`, `geometry.vertex_count.*`
and `geometry.topology_preserved` predicates to real `SATISFIED`/`VIOLATED`
verdicts. Without scene stats, those predicates still refuse exactly as before.
Verified against a real production asset's revision history — see
[`runs/2026-08-20-blender-mesh-validation/`](../../runs/2026-08-20-blender-mesh-validation/README.md),
which also caught and resolved a real discrepancy in that corpus's own
tracked-parts sidecar, and found a "topology-preserving" round-trip pair was
actually scene-level `VIOLATED` (one object silently dropped) even though every
surviving object's own topology matched exactly.

## These tools do not measure geometry from pixels

`edge_mean` and `entropy` (in `pil_structure_diff`) are 2D image-complexity
proxies. They are **not** polygon counts, mesh density, or topology, and must
never be used as a proxy for them. Shading, normal maps, lighting and camera
angle all move these numbers independently of the underlying model — a
smooth-shaded low-poly render can score as more complex than a flat-shaded
high-poly one.

For polygon count, mesh density, topology or "is this lower-poly than that",
use `pil_blender_mesh.py` above (or the 3D scene's own tooling directly, e.g. a
Blender MCP server) — never infer it from a rendered image, which produces
confident, wrong answers.

Every `pil_structure_diff` payload repeats this limit under `interpretation_limits`.

## Other limits worth stating to the user

- Palette distance is Euclidean RGB, which is not perceptually uniform. Treat it
  as a relative signal between comparable images, not an absolute perceptual delta.
- Accent membership is a hard HSV threshold, echoed in the output as
  `accent_thresholds`. Colours near the boundary can flip between palettes.
- Thresholds are calibrated against synthetic perturbations with exact ground
  truth, Neyman–Pearson with recorded n and α, and every metric's detection
  limit is published. Synthetic controls underestimate real-revision
  difficulty, so treat detection limits as best-case bounds. Bundles:
  [full-frame + method](../../runs/2026-08-19-phase2-calibration/README.md),
  [foreground by mask source](../../runs/2026-08-20-foreground-recalibration/README.md),
  [real-image validation](../../runs/2026-08-20-phase2-real-validation/README.md).
- **Foreground thresholds are split by how the mask was derived**, because they
  differ by more than an order of magnitude. `threshold_foreground_alpha`
  applies when the file carried real transparency; `..._estimate` when the mask
  came from the border-median colour; `..._estimate_no_placement` is the same
  derivation with the resampling control family excluded. Measured for
  luminance: **0.997** on the alpha path, **34.166** on the estimate path,
  **1.452** excluding placement noise. Reading the wrong one by an order of
  magnitude is the easiest mistake to make here.
- On **RGBA input in `--foreground` mode**, statistics are coverage-weighted:
  each pixel's true colour is recovered and weighted by how much of the pixel
  the object covers. Before 0.4.0 a partially-covered pixel was composited onto
  black and counted at full weight, which read a 5px-wide blade ~28 code values
  too dark and made a quarter-pixel re-render of an *unchanged* object look
  like a real change. Full-frame behaviour is unchanged.
- `entropy_delta` is demoted: calibration found it unable to resolve 24 of 26
  perturbation families. Do not let it decide anything.
