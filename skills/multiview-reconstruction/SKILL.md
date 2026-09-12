---
name: multiview-reconstruction
description: Fit an existing 3D template to calibrated multi-view image constraints, probe Blender clearance, and review locked renders. Use when projections, correspondences, and scale anchors are available.
---

# Multi-view reconstruction

Use this skill to constrain an existing template mesh from several calibrated
views and evaluate the result in Blender. It does not infer hidden topology or
metric 3D geometry from images alone.

Use [`image-measurement`](../image-measurement/SKILL.md) for ordinary image
inspection or pair comparison. Use the
[`image-analysis`](../image-analysis/SKILL.md) umbrella when the request also
needs semantic inspection, pixel comparison, or a full concept-to-model review.

## Required evidence

A solve needs named views, calibrated projection matrices, explicit template
correspondences, and scale anchors or fixed coordinates. Do not invent hidden or
ambiguous landmarks. If independent observation rank is insufficient or views
conflict, preserve the tool's `UNDERDETERMINED` or `VIEW_CONFLICT` result.

Contours and landmarks are projected-appearance evidence. Probe clipping and
body clearance from the Blender scene with `pil_blender_fit.py`; silhouettes do
not prove collision state. Keep every required view in preparation, rendering,
and review so a failure cannot disappear from the aggregate.

## Run the needed stages

Discover current commands and readiness with `pil_capabilities.py --tool NAME`.
If reconstruction dependencies are missing and setup is part of the request,
use the sibling [`bootstrap`](../bootstrap/SKILL.md) workflow with
`--reconstruction`. Blender remains a separate prerequisite.

Use these stages as the task requires:

- `pil_multiview_prepare.py`: normalize each named view and retain its ordered
  contour or refusal state.
- `pil_multiview_solve.py`: fit the template under correspondence and geometry
  constraints. Geometry mutation is eligible only from `SOLVED`.
- `pil_blender_fit.py`: run `probe` first; use `apply-copy` only for an
  authorized bounded edit. It creates a new `.blend` and does not overwrite an
  existing file.
- `pil_multiview_render.py`: render the decisive views with locked framing and
  an explicit `analysis`, `beauty`, or `silhouette` mode.
- `pil_multiview_review.py`: compare every required render to its matching
  reference using worst-case aggregation.

For a repeatable end-to-end job, `pil_reconstruct.py` composes the stages from a
`reconstruction-job-v1` manifest and stops at `UNDERDETERMINED`,
`VIEW_CONFLICT`, `FIT_BLOCKED`, or `RENDER_BLOCKED`.

Schemas live under [`schemas/`](../../schemas/); the numerical model, dependency
boundary, and field-level scope are in
[`docs/phase4-scope.md`](../../docs/phase4-scope.md). Read only the schema or
section needed for the current stage.

## Report and finish

Read each payload's status, flags, residuals, and `interpretation_limits`.
Report per-view evidence and the weakest required view. A high image similarity
does not establish clearance, topology, rig deformation, or cloth behavior.

The task is complete when each requested stage either succeeds with retained
artifacts or ends in its explicit blocking status. If the request includes the
full reconstruction loop, continue through locked renders and worst-case review
rather than returning after the first solved mesh.
