---
name: image-analysis
description: Combine visual, pixel, calibrated multi-view, and Blender evidence. Use for concept-to-model review, image-plus-mesh comparison, clipping diagnosis, or questions that must separate appearance from geometry.
---

# Combined image analysis

Use this skill when one answer must synthesize more than one evidence layer.
Keep each layer's claim boundary visible instead of turning unrelated signals
into a single confidence score.

## Route only what the task needs

- Pixel/file facts, exact colours, regions, or a two-image comparison: use
  [`image-measurement`](../image-measurement/SKILL.md).
- An existing template fit with calibrated projections, correspondences, and
  scale anchors: use
  [`multiview-reconstruction`](../multiview-reconstruction/SKILL.md).
- Image-plus-mesh review, Blender topology/bounds, clipping or clearance, or a
  concept-to-model loop: continue here and load only the relevant sibling skill.

Several images do not by themselves justify a 3D solve. Without the required
camera and correspondence data, complete the valid 2D analysis and report the
reconstruction layer as underconstrained.

## Evidence layers

| Layer | Establishes | Does not establish |
|---|---|---|
| Visual inspection | Subject, style, apparent silhouette, likely problem areas | Exact pixel values, hidden geometry, collision |
| Image measurement | File facts, colour, structure, masks, local changes, projected silhouette | Metric depth, topology, rig or cloth behavior |
| Calibrated reconstruction | Constraints on an existing template and per-view residuals | Unseen topology or meaning from pixels |
| Blender scene probes | Mesh counts, bounds, topology, signed clearance | Visual or semantic match by itself |
| Locked render review | Matched projected appearance across requested views | Physical clearance or deformation quality |

## Working method

Inventory and match the requested views before comparing them. Inspect them
visually before reading scores, then use `image-measurement` for reproducible
pixel/file evidence. Keep normalization, masks, registration transforms, raw
measurements, and domain-profile identity with the result.

Prepare every requested multi-view contour, including refused views. Run a
template solve only when calibration and correspondences are sufficient, and
continue to geometry mutation only from `SOLVED`. Use
`pil_blender_fit.py --mode probe` for penetration or clearance and
`pil_blender_mesh.py` for topology and bounds; silhouettes are not collision
evidence.

Render only the decisive requested views with locked framing, then aggregate
matched comparisons with `pil_multiview_review.py`. A missing, refused, or
violated required view must remain visible in the overall result.

Discover current arguments and dependency status with
`pil_capabilities.py --tool NAME`. The executable 0.9.0 normalization,
selection, registration, local-diff, calibration, batch, and MCP workflows are
in [measurement workflows](../../docs/measurement-workflows.md).

These are local CLI instructions. `NAME` is selected from the fixed public tool
catalog; it is not shell-expanded or used to build a remote command. MCP calls
use local stdio and do not forward the host's credential environment to tool
subprocesses.

## Report and finish

Group findings by evidence layer. For a requested match target, define the
required views, metrics, thresholds, geometry constraints, and worst-case rule
before judging it. Report the weakest required view, all refusal states, and
the assumptions that limit each claim. The analysis is complete when every
requested layer is measured or explicitly marked unavailable; do not stop at a
first render or first plausible score when the request includes the full loop.
