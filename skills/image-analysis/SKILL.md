---
name: image-analysis
description: Combines native visual inspection, quantitative Pillow image measurement, calibrated multi-view reconstruction constraints, Blender BVH geometry checks, and matched-render review. Use when an image-analysis request spans several evidence layers, such as concept-to-model comparison, seven-view character or garment review, clipping diagnosis, or iterative image-and-mesh evaluation. Use image-measurement alone for pixel-only questions and multiview-reconstruction alone for an already-calibrated template fit.
---

# Combined image analysis

Use this skill as the umbrella for work that crosses both image evidence and 3D
scene evidence. Route each question to the narrowest valid layer, then synthesize
the results without allowing one layer to impersonate another.

## Capability comparison

| Concern | `image-measurement` | `multiview-reconstruction` |
|---|---|---|
| Primary evidence | Image pixels and file metadata | Calibrated image constraints plus Blender scene geometry |
| Typical input | One image or a before/after pair | Several named views, a template mesh, camera projections, correspondences, and optional `.blend` |
| Core libraries | Pillow and NumPy | OpenCV and SciPy; Blender `bpy`/BVH for scene stages |
| Strong outputs | Exact colours, foreground coverage, changed regions, structural and silhouette comparisons, contract verdicts | Ordered contours, constrained 3D vertices, rank/conflict state, signed body clearance, locked-view renders |
| Cannot establish | Hidden topology, metric depth, collision, rig behavior | Semantic identity from pixels alone, uncalibrated depth, unseen topology, cloth behavior |
| Refusal form | Flags and `UNMEASURABLE` predicates | `UNDERDETERMINED`, `VIEW_CONFLICT`, `FIT_BLOCKED`, or `RENDER_BLOCKED` |

The overlap is intentional: both can inspect silhouettes and matched renders.
Use the image layer to measure projected appearance. Use Blender BVH or scene
statistics for clipping, clearance, topology, dimensions, and object identity.

## Route the request

- For exact colours, metadata, regions, foreground coverage, or a two-image
  comparison, load and follow `image-measurement` only.
- For an already calibrated several-view template fit, load and follow
  `multiview-reconstruction` only.
- For concept-to-model analysis, seven-view review, clipping diagnosis, or an
  iterative model-polish loop, load both specialized skills and use the
  combined workflow below.
- Several views do not automatically authorize a 3D solve. If cameras,
  correspondences, or scale anchors are absent, perform the 2D analysis and
  report the reconstruction as underconstrained.

## Combined workflow

1. Inventory the sources. Record every requested view, its role, dimensions,
   alpha use, crop, and whether it is concept art, a render, or scene-derived
   evidence. Match like views before comparing them.
2. Inspect visually before reading scores. Describe the subject, silhouette,
   drape, overlaps, lighting, perspective, and uncertain regions so a later
   metric does not anchor semantic judgment.
3. Follow `image-measurement` for file facts, foreground masks, palette,
   structure, crops, annotations, and declared-intent comparison. On object
   renders, use foreground mode and read flags before scores.
4. When several views matter, run `pil_multiview_prepare.py` to retain an
   ordered normalized contour for every view. An unmeasurable view remains in
   the packet.
5. Only when calibrated projections and correspondences exist, follow
   `multiview-reconstruction` and run the constrained template solve. Continue
   to geometry mutation only from `SOLVED`.
6. For a Blender model, measure scene facts directly. Use
   `pil_blender_fit.py --mode probe` for body clearance and penetration; never
   infer clipping from a silhouette. Use `pil_blender_mesh.py` for topology and
   bounds.
7. Render the decisive view set with locked framing. For a seven-view character
   or garment packet, keep all seven views through rendering and review.
8. Run pixel-level comparisons per matched view and aggregate with
   `pil_multiview_review.py`. One violated, refused, or missing view must affect
   the overall result.
9. Synthesize findings by evidence layer: visual interpretation, pixel/file
   measurements, calibrated reconstruction constraints, Blender geometry, and
   remaining uncertainty.

## Match reporting

Do not manufacture a single confidence score by averaging unrelated metrics.
If the user requests a target such as "95% match," define the contract first:
which views count, which image metrics and thresholds count, which Blender
clearance/dimension constraints count, and whether the target is per-view or
worst-case. Report the weakest required view and every refusal alongside the
aggregate.

A useful report shape is:

| Layer | Finding | Measurement | Status | Limitation |
|---|---|---|---|---|
| Visual | Human-readable interpretation | observed feature | pass/fail/uncertain | perspective or occlusion |
| Pixels | Appearance comparison | exact plugin fields | satisfied/violated/unmeasurable | flags and detection limit |
| Reconstruction | Multi-view constraint fit | rank and per-view residual | solved/refused | calibration assumptions |
| Blender | Geometry and clearance | scene stats or signed BVH distance | pass/blocked | pose, normals, or modifier state |

Use the specialized skills for exact commands and field interpretation:

- [`image-measurement`](../image-measurement/SKILL.md)
- [`multiview-reconstruction`](../multiview-reconstruction/SKILL.md)
