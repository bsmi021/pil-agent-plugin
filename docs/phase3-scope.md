# Phase 3 scope — closed loops between measurement and vision

Last updated: 2026-08-19
Status: proposed, awaiting sign-off. Phase 2's WP1–WP4 are being implemented
concurrently on `feat/phase2-verdicts`, so phase 3's work packages are labelled
`A1`–`A5` and `B1`–`B3` rather than continuing that numbering.

## Premise

Phase 1 established that neither native vision nor measurement subsumes the other
([`design-rationale.md`](design-rationale.md)); phase 2 makes the numbers
trustworthy — perceptually uniform, calibrated, delivered as contract verdicts
carrying their own detection limits ([`phase2-scope.md`](phase2-scope.md)). The
obvious phase 3 is *more metrics*. That is the wrong thesis. The complement to
multimodal vision is **closed loops** between measurement and vision, plus a
vocabulary for what vision constitutionally cannot do. Three loops, each with
evidence already in this repository.

**The zoom loop — resolution recovery.** An image is resampled to fit a vision
encoder; detail below that resolution is unrecoverable, and the model does not know
the true pixel dimensions of what it saw. The tools already emit *where to look* —
`changed_region_bbox_fractional`, `most_divergent_cells` and the 0.2.0 foreground
`bbox_fractional` in [`pil_structure_diff.py`](../scripts/pil_structure_diff.py).
The missing piece is handing vision back a native-resolution crop.

**The grounding loop — shared coordinates.** An LLM cannot point precisely; the
tools cannot see semantics. Fractional bounding boxes are the shared coordinate
system, and must flow *both* ways: tool → LLM already works, LLM → tool does not.
Annotated overlays the model reads back close the first direction visually;
`--region` closes the second. Together they make language about an image precise
and checkable rather than gestural.

**The verification loop — epistemic division of labour.** The field trial had four
confident visual conclusions overturned by measurement
([trial bundle](../runs/2026-08-18-skeleton-warrior-asset-review/README.md#where-measurement-overruled-vision)),
and phase 1 had measurement wrong where vision was right — a global quantisation
reporting zero vivid colours
([`design-rationale.md`](design-rationale.md#what-the-experiment-established)).
Neither side is referee; the vision-first / measure-second / reconcile protocol *is*
the product. It works only if every number carries validity metadata — 0.2.0's
`flags` family, WP2's published detection limits — because an LLM over-trusts fluent
JSON and reads an uncaveated number as a guarantee.

Phase 3 therefore splits in two. **Track A** is domain-agnostic instrumentation for
those loops; **Track B** is the Blender character-sheet loop that
[`phase2-scope.md`](phase2-scope.md#explicitly-out-of-scope-phase-3) deferred here.

## In scope

### Track A — domain-agnostic instrumentation

Ranked by leverage. **A1–A4 add no new judgment metrics** — they relocate, scope and
present measurements that already exist — so they carry no calibration dependency
and may ship independently as 0.3.x/0.4.x while phase 2 is still landing. A5 does
add judgment, and is gated accordingly.

#### WP A1 — `pil_crop.py`, native-resolution crop

Crop a fractional bbox at native resolution to a file, with an optional integer
upscale for pixel-level inspection. ~50 lines. Closes the zoom loop: the agent reads
the crop back with the same vision it used on the whole frame, at a resolution the
encoder never received.

*Deliverables:* fractional bbox input in the format the tools already emit;
`--scale N` nearest-neighbour upscale, nearest so magnification invents no colour;
JSON on stdout recording source dimensions, resolved pixel rect and output path.
*Tests:* byte-identical output file across runs; fractional boxes resolve to the
expected pixel rect at several sizes, including odd dimensions where rounding
decides; `--scale` output contains only colours present in the source.

#### WP A2 — `pil_annotate.py`, overlays vision can read back

Draw numbered boxes, gridlines and component labels onto a *copy* of an image, so an
agent can Read the annotated version and then say "region 3" instead of "the bit
near the top left". Closes the grounding loop's tool → vision direction.

*Deliverables:* numbered rectangles from a list of fractional boxes; the
`pil_structure_diff` grid drawn to scale, so `most_divergent_cells` becomes visible;
a legend mapping numbers to the caller's own labels. **No system-font dependency** —
determinism is the repository's core contract
([`design-rationale.md`](design-rationale.md#determinism-as-a-requirement)) and a
font resolved from the host makes output machine-dependent, so use Pillow's bundled
default font or pure geometric markers with the legend in the JSON sidecar.
*Tests:* byte-identical output across runs *and* across machines with different
fonts installed; overlay drawn only on a copy, source untouched; box numbering
stable and independent of input ordering.

#### WP A3 — `pil_image_info.py`, what vision never receives

True pixel dimensions, mode, bit depth, alpha presence, ICC profile, EXIF, DPI and
animation frame count. Near-free, and vision sees none of it: images reach the model
stripped of metadata and resampled, so it cannot report even the dimensions of what
it was shown.

*Deliverables:* one JSON payload per file, with explicit nulls for absent metadata
rather than omitted keys, so "no ICC profile" is distinguishable from "not checked".
*Tests:* byte-determinism; alpha detection matches the loader's own rule in
[`pil_common.py`](../scripts/pil_common.py) — a file carrying an alpha channel that
uses no transparency reports no usable alpha, exactly as `load_rgb_alpha` does.

#### WP A4 — `--region FRACTIONAL_BBOX` on both existing tools

Every metric runs within a caller-named region of each image. Provenance is the
field trial's first ask: *"Region cutting should be a tool"* — cutting matched
regions at identical fractions of each figure's silhouette bounding box was **"the
single highest-leverage thing in the harness"**, and is what made a 1254×1254
multi-view sheet comparable to a 900×1395 single-view render, part by part
([trial bundle](../runs/2026-08-18-skeleton-warrior-asset-review/README.md#what-this-exercise-wants-from-phase-2)).
That bundle's capability table lists region cutting under **harness**; this WP moves
it into the plugin, and makes the trial's material probes — "ad-hoc numpy probes
placed by eye", recorded as values rather than as reproducible steps — reproducible.

*Deliverables:* `--region` on `pil_palette_diff` and `pil_structure_diff`, echoed
into the payload beside its resolved pixel rect; composition with `--foreground`
defined and documented, not left implicit.
*Tests:* metrics over `--region R` equal metrics over a file pre-cropped to `R`; the
existing byte-determinism test still passes with and without `--region`; an
out-of-range or inverted region is rejected, not silently clamped.

#### WP A5 — discrimination-gated metric candidates

These add judgment, so **each must pass WP2's methodology before shipping** —
response curves, a published detection limit, and demotion as an acceptable outcome.
Phase 1's precedent is the standard: only 4 of 11 metrics survived
[the discrimination matrix](../runs/2026-08-18-pil-agent-plugin-phase1/10-metric-discrimination-matrix.md),
and one answered its question backwards.

- **Connected-component instance counting**, with per-blob area, centroid and bbox.
  Answers "how many separate objects are here, and where" — a question vision
  answers well but cannot localise numerically. Implementation constraint: a
  pure-numpy two-pass union-find. `scipy.ndimage.label` is the obvious route and is
  barred, because the runtime dependency set is **Pillow and numpy, no other runtime
  dependencies** ([`../README.md#requirements`](../README.md#requirements)) and a
  plugin users install must not grow one.
- **Silhouette shape descriptors** on the 0.2.0 foreground mask: fill ratio,
  perimeter-squared-over-area, orientation histogram. Gated harder than the rest,
  because the field trial *deliberately declined* to propose general proportion
  measurement as a tool — head count and skull aspect "worked here because both
  subjects are single figures on flat backdrops, and it would mislead on anything
  else". These ship only if they also degrade honestly under the existing
  mask-quality flags (`foreground_too_small`, `foreground_mask_empty`,
  `background_dominant`), returning nothing rather than a number on an unreliable
  mask.
- **Projection-profile alignment measurement**: edge-map row and column sums
  yielding baselines, margins and "aligned within 1px" verdicts, plus WCAG
  contrast-ratio arithmetic. Aimed at UI review, where alignment and contrast are
  exactly the checkable facts vision reports impressionistically.

**Deferred until a field trial demands them**, recorded so they are not re-proposed
from first principles: FFT periodicity and tiling detection; blur, noise and
compression quality statistics; corpus-scale hash indexing. Each is plausible and
none has a real caller yet — a metric here earns its place from a measurement on a
real image, not from anticipation.

### Track B — the Blender character-sheet loop

[`phase2-scope.md`](phase2-scope.md#explicitly-out-of-scope-phase-3) recorded these
"as decided, not open", and quoting it is the specification:

> - **Optional Blender mesh-statistics tool.** Agreed it belongs in this plugin as a
>   standalone script that runs only when scene data is available, so
>   `geometry.poly_count.decrease` can resolve to a real verdict instead of
>   `UNMEASURABLE`. Deferred because phase 2 is domain-agnostic.
> - **Render-matching-views orchestration.** Agreed approach: render front/side/back
>   to match the reference's views and compare 1:1, rather than segmenting a sheet
>   heuristically. Deferred for the same reason.
> - **The character-sheet revision loop itself**, which composes all of the above.

#### WP B1 — Blender mesh statistics

A standalone script, run only when scene data is available: polygon and vertex
counts, object and material inventory, bounding dimensions — from the scene, never
from pixels. *Tests:* `geometry.*` predicates resolve to `SATISFIED` / `VIOLATED`
with scene data and stay `UNMEASURABLE` without it; absence of Blender is a clean
`UNMEASURABLE`, never an error and never a pixel-derived approximation.

#### WP B2 — matched-view render orchestration

Render front, side and back to match a reference sheet's views, and compare 1:1. The
field trial is the motivating counter-example: a T-pose render against an A-pose
concept view produced `structural_similarity` 0.900 alongside `aspect_ratio_mismatch`
and `resolution_mismatch`, and those numbers were correctly discarded as describing
framing and pose rather than the model. The trial also covered **one front view**,
leaving the back panel and profile unverified. *Tests:* rendered views register
against reference views without the mismatch flags firing; the orchestrator refuses
rather than warps when a view cannot be matched.

#### WP B3 — the revision loop

Compose WP3 contracts across matched views and aggregate with WP4, so a
character-sheet review is one contract evaluated over N registered pairs. *Tests:*
WP4's rule holds across views — a single diverging view cannot be averaged away.

## Explicitly out of scope

Refused, not deferred:

- **Semantic segmentation and any ML model.** A large dependency and nondeterminism,
  bought to duplicate what the calling agent's vision already does well.
- **OCR and text-content extraction.** Native vision produced "complete text
  transcription" unassisted in phase 1; a second, worse transcriber is negative
  value.
- **`style.*` and `identity.same_character`.** Phase 2 refuses these as not
  reducible to pixel statistics; phase 3 does not reopen them.
- **Geometry inferred from pixels.** Permanently refused — edge density is not a
  polygon count
  ([README](../README.md#scope-limit-these-tools-do-not-measure-geometry)), and
  WP B1 is the only sanctioned route to a geometry answer.

## Sequencing and gates

| WP | Depends on | Gate to proceed |
|---|---|---|
| A1 crop | nothing | Byte-determinism; fractional→pixel rect exact at odd sizes; documented |
| A2 annotate | A1 (shared bbox parsing) | Byte-determinism across machines with different fonts; source file unmodified |
| A3 image info | nothing | Byte-determinism; alpha rule matches `load_rgb_alpha` |
| A4 `--region` | A1 | Region metrics equal pre-cropped-file metrics; existing determinism test unbroken; `--region` × `--foreground` semantics documented |
| A5 new metrics | phase 2 WP2 | Each candidate independently passes WP2's methodology: response curves, published detection limit, demotion accepted |
| B1 mesh stats | phase 2 WP3 landing; Blender scene access | `geometry.*` resolves with scene data, stays `UNMEASURABLE` without it |
| B2 view matching | B1 | Matched views register without `aspect_ratio_mismatch` / `resolution_mismatch` |
| B3 revision loop | B2, phase 2 WP4 | A single diverging view cannot be averaged away |

A1–A4 gate only on determinism and documentation, so they are shippable while phase
2 is in flight. A5 cannot start before WP2's calibration harness exists, because its
gate *is* that harness. Track B waits on phase 2 landing and additionally on Blender
scene access — without it B1 is untestable and must not be faked.

## What would make phase 3 a failure

Stated up front so it can be checked honestly at the end:

- **A metric that never usefully disagrees with vision.** Disagreement is the
  product; a metric with no measured disagreement case cost dependencies and runtime
  for nothing.
- **An annotate tool whose overlays vision misreads.** An overlay that occludes what
  it labels, or numbers the model reads wrongly, corrupts the grounding loop — so it
  is verified by a model reading the output back, not by asserting pixels were drawn.
- **A region API that breaks byte-determinism**, or whose rounding makes
  `--region R` disagree with a file pre-cropped to `R`. Reproducible, diffable
  output is the whole value proposition.
- **Crediting the plugin with harness capabilities.** The field trial drew that line
  explicitly and it stays drawn: until A4 and A5 ship and pass their gates, the
  plugin has no region-cutting, silhouette or proportion capability.
- **A geometry answer inferred from pixels** because B1's scene access was
  unavailable and an approximation looked close enough.
- **Track A blocked on Track B.** The instrumentation is domain-agnostic and useful
  to every caller; letting it wait on Blender availability is a scheduling failure,
  not a technical one.

## Open questions

1. **`--region` × `--foreground` composition.** Is a region a fraction of the frame
   or of the foreground bounding box? The trial cut at silhouette-bbox fractions,
   arguing for the latter; the tools' existing fractional coordinates are
   frame-relative, arguing for the former. Likely both behind an explicit flag,
   since silently picking one will mislead the other caller.
2. **Where written images go.** A1 and A2 are the first tools that emit files rather
   than JSON. Output path, overwrite policy and cleanup need a decision, and
   `runs/**/*.png` is already gitignored for good reason.
3. **Whether A5's silhouette descriptors should exist at all**, given the trial
   declined general proportion measurement as too easily misled. The alternative is
   connected components and alignment profiles only, leaving silhouette shape to
   vision plus WP B1's real geometry.
