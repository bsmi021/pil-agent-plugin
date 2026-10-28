---
name: multiview-reconstruction
description: Fit an existing 3D template mesh to several calibrated views, or check whether calibrated views agree. Use for asks like "fit my base mesh to these front/side/back reference photos", "I have camera matrices and landmark correspondences for four views, solve the template", "do these views agree on the model's proportions?", or "check the fitted model against every view with locked cameras". Needs calibrated views, template-to-image correspondences and scale anchors; without them, report underconstrained rather than guess. Not for building a 3D model from uncalibrated photos, generating images, or general Blender modelling.
---

# Multi-view reconstruction

Use this skill to constrain an existing template mesh from several calibrated
views and evaluate the result in Blender. It does not infer hidden topology or
metric 3D geometry from images alone.

## Hand the work to the fit analyst

Run a solve or review through the `pil-agent-plugin:multiview-fit-analyst`
agent (Agent tool, `subagent_type: "pil-agent-plugin:multiview-fit-analyst"`),
not in the main thread and not in a general-purpose agent. The stages produce
long payloads, and the agent reduces them to per-view residuals, refusal states
and the weakest view; it is read-only apart from scratch outputs. Give it the
view names, projection matrices, correspondence files, scale anchors, the
template `.blend` and which stages are wanted, then relay its report. If you are the
multiview-fit-analyst agent, skip this handoff and run the stages yourself. The
sections below are the method the agent follows; read them directly only to
answer a question about the method.

For ordinary image inspection or a pair comparison use
[`image-measurement`](../image-measurement/SKILL.md). Use the
[`image-analysis`](../image-analysis/SKILL.md) umbrella when the request also
needs semantic inspection, pixel comparison, or a full concept-to-model review.

## Required evidence

A solve needs named views, calibrated projection matrices, explicit template
correspondences, and scale anchors or fixed coordinates. Do not invent hidden or
ambiguous landmarks. If independent observation rank is insufficient or views
conflict, preserve the tool's `UNDERDETERMINED` or `VIEW_CONFLICT` result and
tell the user what is missing instead of producing a best guess.

Contours and landmarks are projected-appearance evidence. Probe clipping and
body clearance from the Blender scene with `blender_fit.py` from the
`blender-inspect` plugin; silhouettes do not prove collision state. Keep every
required view in preparation, rendering, and review so a failure cannot
disappear from the aggregate.

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
- `blender_fit.py` (`blender-inspect`): run `probe` first; use `apply-copy`
  only for an authorized bounded edit. It creates a new `.blend` and does not
  overwrite an existing file.
- `blender_multiview_render.py` (`blender-inspect`): render the decisive views
  with locked framing and an explicit `analysis`, `beauty`, or `silhouette`
  mode.
- `pil_multiview_review.py`: compare every required render to its matching
  reference using worst-case aggregation.

For a repeatable end-to-end job, `pil_reconstruct.py` composes the stages from a
`reconstruction-job-v1` manifest and stops at `UNDERDETERMINED`,
`VIEW_CONFLICT`, `FIT_BLOCKED`, or `RENDER_BLOCKED`. To keep Blender work in
`blender-inspect`, run `blender_fit.py` and `blender_multiview_render.py`
yourself (their paths come from that plugin's `model-inspection` skill) and
point the job's `fit` and `render` stages at their saved payloads with
`{"payload": PATH}`; both stages must be external, or the render stage
still calls the deprecated `pil_multiview_render.py`.

The `pil_blender_fit.py`, `pil_blender_mesh.py`, `pil_blender_render.py` and
`pil_multiview_render.py` copies in this plugin are deprecated (removed in the
next minor release). Use them only when `blender-inspect` is not installed.

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
