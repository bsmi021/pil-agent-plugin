# Phase 4 handoff

Last updated: 2026-08-25

## Capability boundary

The reconstruction extension is packaged as the sibling
`multiview-reconstruction` skill. Existing `image-measurement` invocations and
core dependencies are unchanged. Install the optional extra only for contour
preparation and numerical solving; Blender probes and render orchestration use
the same external-executable pattern as Phase 3.

## Status vocabulary

- `PREPARED`: every requested image yielded a foreground contour.
- `PREPARED_WITH_REFUSALS`: every view remains represented, but at least one is
  `UNMEASURABLE`.
- `SOLVED`: observations have full rank and the fitted landmark RMSE meets the
  declared limit.
- `UNDERDETERMINED`: the observation/fixed-coordinate matrix cannot constrain
  all vertex coordinates.
- `VIEW_CONFLICT`: optimization completed, but the landmark RMSE exceeds the
  declared limit.
- `PROBED`: Blender BVH measurements were read without changing the scene.
- `FITTED`: bounded clearance correction was saved to a new blend file.
- `FIT_BLOCKED`: object identity, vertex count, or displacement bounds prevent
  a safe fit.
- `RENDERED` / `RENDER_BLOCKED`: all requested renders exist, or the scene has
  no render-visible mesh geometry.
- `COMPLETED`: the requested orchestrated stages finished without a refusal.

## Extension points

The v1 solve owns template vertex fitting, not topology synthesis. A future
cloth-specific layer can add planarity, fold-line, thickness, and rig-pose
constraints without weakening the full-rank gate. Camera estimation for real
photographs should be a separate calibrated input producer; generated concept
views must not be silently treated as photogrammetry.

Project-specific live Blender MCP adapters can consume the same solution and
clearance payloads. They should remain outside this general plugin when their
scene mutation and approval rules are repository-specific.
