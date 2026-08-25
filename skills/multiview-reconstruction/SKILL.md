---
name: multiview-reconstruction
description: Prepares calibrated multi-view concept images, fits a template mesh with OpenCV and SciPy constraints, checks Blender garment clearance with BVH, renders arbitrary locked-framing views, and performs worst-case image review. Use for reconstructing or fitting an existing 3D mesh from several image views when camera projections, correspondences, and scale anchors can be supplied. Does not infer hidden topology or metric 3D geometry from pixels alone.
---

# Multi-view reconstruction

Use this skill when the task is to turn several reference views into measured
constraints on an existing template mesh, then evaluate the result in Blender.
Use `image-measurement` instead for ordinary image inspection or two-image
comparison that does not require a 3D solve.

If the request spans semantic image inspection, quantitative pixel comparison,
template fitting, Blender clearance, and final matched-render review, route
through the sibling `image-analysis` umbrella skill and load both specialized
skills beneath it.

## Environment

The reconstruction tools are an optional install so the core image-measurement
plugin remains Pillow + NumPy only:

```powershell
uv sync --project "${CLAUDE_PLUGIN_ROOT}" --extra reconstruction
```

Agent Plugins hosts may expose the root as `${PLUGIN_ROOT}` instead. Direct
Python use requires Pillow, NumPy, `opencv-python-headless`, and SciPy. Blender
tools additionally require a local Blender executable; `bpy` is used only in
Blender's bundled interpreter.

## Evidence boundary

- Treat OpenCV contours and landmarks as 2D rendered-appearance evidence.
- Do not claim metric depth unless the correspondence file contains calibrated
  projection matrices and enough independent views or fixed coordinates.
- Do not invent correspondences for hidden or ambiguous landmarks. A solver
  refusal is the correct result when depth is underconstrained.
- Measure clipping and body clearance through `pil_blender_fit.py`; silhouettes
  can hide penetration and are not collision evidence.
- Keep every requested view in preparation and review. A failed view blocks or
  makes the aggregate unmeasurable; never discard it to improve the result.
- `apply-copy` creates a new `.blend`. It refuses to overwrite the source or an
  existing output.

## Workflow

1. Create a `multiview-spec-v1` manifest and run `pil_multiview_prepare.py`.
   It uses the shared alpha/border foreground definition, then OpenCV traces and
   simplifies an ordered normalized contour for each view.
2. Supply a `template-mesh-v1`, `correspondences-v1`, and
   `geometry-constraints-v1`. Run `pil_multiview_solve.py`. Continue only from
   `SOLVED`; report `UNDERDETERMINED` or `VIEW_CONFLICT` without smoothing it
   into a success claim.
3. Run `pil_blender_fit.py` in `probe` mode first. If the requested operation is
   authorized and bounded displacement can meet clearance, use `apply-copy`.
4. Render the decisive views with `pil_multiview_render.py`. Use locked framing
   for comparisons; choose `analysis`, `beauty`, or `silhouette` deliberately.
5. Pair every render with its matching reference in `review-views-v1` and run
   `pil_multiview_review.py`. Its verdict is worst-case across views.

For a repeatable job, `pil_reconstruct.py` composes those stages from a
`reconstruction-job-v1` file and stops at `UNDERDETERMINED`, `VIEW_CONFLICT`,
`FIT_BLOCKED`, or `RENDER_BLOCKED`.

Schemas and a complete field reference live under
[`schemas/`](../../schemas/) and
[`docs/phase4-scope.md`](../../docs/phase4-scope.md).

## Commands

```powershell
uv run --project "${CLAUDE_PLUGIN_ROOT}" --extra reconstruction python "${CLAUDE_PLUGIN_ROOT}/scripts/pil_multiview_prepare.py" views.json
uv run --project "${CLAUDE_PLUGIN_ROOT}" --extra reconstruction python "${CLAUDE_PLUGIN_ROOT}/scripts/pil_multiview_solve.py" --template template.json --correspondences correspondences.json --constraints constraints.json --prepared prepared.json --output solution.json
uv run --project "${CLAUDE_PLUGIN_ROOT}" python "${CLAUDE_PLUGIN_ROOT}/scripts/pil_blender_fit.py" scene.blend --body-object Body --garment-object Cloak --clearance 0.01 --mode probe
uv run --project "${CLAUDE_PLUGIN_ROOT}" python "${CLAUDE_PLUGIN_ROOT}/scripts/pil_multiview_render.py" scene.blend --manifest render-views.json --output-dir renders --mode analysis
uv run --project "${CLAUDE_PLUGIN_ROOT}" python "${CLAUDE_PLUGIN_ROOT}/scripts/pil_multiview_review.py" --manifest review-views.json --contract contract.json
```

Read each JSON payload's `status`, flags, residuals, and
`interpretation_limits` before describing the result. A high image similarity
is not proof of clearance, rig deformation, cloth behavior, or topology.
