# Phase 4: constrained multi-view reconstruction

Last updated: 2026-08-25

## Outcome

Phase 4 adds an optional, explicit reconstruction layer around the existing
image-measurement tools. It converts several image views into contours and
correspondences, solves a caller-provided template mesh under calibrated camera
and geometry constraints, checks body clearance from Blender scene geometry,
renders arbitrary matched views, and aggregates the image review without
dismissing a failed view.

This is not single-image mesh generation. Pixels do not reveal hidden topology,
unseen depth, rig deformation, or cloth behavior. The solver returns
`UNDERDETERMINED` when its observation matrix lacks full rank and
`VIEW_CONFLICT` when calibrated observations cannot meet the declared residual
limit.

## Dependency boundary

The plugin's core remains Pillow + NumPy. Reconstruction is installed with the
optional `reconstruction` extra:

```powershell
uv sync --extra reconstruction
```

That extra adds `opencv-python-headless` for ordered contour extraction and
SciPy for nonlinear least squares and nearest-contour queries. Blender is an
external executable, not a Python package dependency; Blender-side probes use
its bundled `bpy`, `mathutils`, and `BVHTree`.

## Tools

| Tool | Input | Output and stopping rule |
|---|---|---|
| `pil_multiview_prepare.py` | `multiview-spec-v1` | Ordered normalized contour and foreground provenance per requested view; empty views remain present as `UNMEASURABLE`. |
| `pil_multiview_solve.py` | template, correspondences, constraints, optional prepared contours | `SOLVED`, `UNDERDETERMINED`, or `VIEW_CONFLICT`, with rank and per-view residuals. |
| `pil_blender_fit.py` | `.blend`, body object, garment object, clearance | Read-only `probe` or bounded `apply-copy`; `FIT_BLOCKED` when the requested clearance exceeds the displacement bound. |
| `pil_multiview_render.py` | `.blend`, arbitrary direction/up manifest | Orthographic analysis, beauty, or silhouette renders with shared locked framing; `RENDER_BLOCKED` on empty geometry. |
| `pil_multiview_review.py` | named reference/render pairs and an existing contract | Existing `pil_contract_verdict` worst-case aggregation over every named view. |
| `pil_reconstruct.py` | `reconstruction-job-v1` | Composes the stages and stops on any named refusal state. |

## Numerical model

Each landmark contributes two residuals:

`projection_matrix @ vertex + offset - target`

Independent observation rank is calculated from landmark projection rows and
explicitly fixed coordinates only. Template preservation and edge-vector
regularization stabilize the solve but never masquerade as observed depth.
Optional silhouette vertices minimize distance to the matching OpenCV contour;
symmetry pairs constrain a reflected coordinate relationship. The output
reports global and per-view landmark RMSE against a caller-declared maximum.

Generated design sheets commonly have plausible but uncalibrated perspective.
They can supply normalized contours and landmark hypotheses, but metric solving
still needs declared camera projections plus at least one real scale anchor.

## Blender fit model

The body BVH is constructed from the evaluated body object. For each garment
vertex, signed nearest-surface clearance is:

`dot(garment_world_vertex - nearest_body_point, nearest_body_normal)`

Negative values indicate penetration when body normals are consistently
outward. `apply-copy` moves only violating vertices along that nearest normal,
refuses if any required move exceeds `max_displacement`, and saves a new blend
file. It is a geometric clearance correction, not a cloth simulator or rig-pose
qualification.

## Schemas

The versioned JSON schemas are in [`../schemas/`](../schemas/):

- `multiview-spec-v1.schema.json`
- `template-mesh-v1.schema.json`
- `correspondences-v1.schema.json`
- `geometry-constraints-v1.schema.json`
- `render-views-v1.schema.json`
- `review-views-v1.schema.json`
- `reconstruction-job-v1.schema.json`

## Focused acceptance checks

- Alpha-derived foreground produces deterministic ordered normalized contours.
- Two independent calibrated orthographic views recover all three coordinates
  of a synthetic mesh within numerical tolerance.
- A single orthographic view returns `UNDERDETERMINED` instead of preserving an
  arbitrary template depth.
- Conflicting locked observations return `VIEW_CONFLICT`.
- Seven arbitrary Blender view directions validate with locked framing.
- BVH fitting never targets the input blend and preserves a bounded
  `FIT_BLOCKED` path.
- Review manifests retain all named views and reject a missing pair rather than
  silently reducing the aggregate.

