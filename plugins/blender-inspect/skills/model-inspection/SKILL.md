---
name: model-inspection
description: Find mesh defects, hidden dents, flipped faces, interpenetration and front-back order in a Blender scene from the scene's own data. Use when modeling or reviewing a .blend and flat renders cannot show depth, surface or topology problems.
---

# Blender model inspection

Flat, orthographic renders hide flipped faces, dents, holes and parts that
pass through each other. The `.blend` holds the exact topology and depth, so
read it with these tools instead of judging from a screenshot. Every tool
runs Blender headless with `--factory-startup --background`, never overwrites the
`.blend`, and needs Blender; if it is missing or unverified, use the sibling
[`bootstrap`](../bootstrap/SKILL.md) skill first.

Resolve `<plugin-root>` two levels above this file. Tools live at
`<plugin-root>/scripts/blender_*.py` and run under any Python 3.11+.

## The loop

1. **Audit** with `blender_mesh_audit.py`. It gives exact counts and
   world-space locations for ten defect classes. Start here: it is the only
   tool that counts defects.
2. **Look** with `blender_inspect_render.py` at the locations the audit named,
   or at a suspicion the audit cannot test (a dent, a soft surface problem).
3. **Order** with `blender_depth_order.py` when the question is which part is
   in front, or whether layers sit on each other or pierce each other.
4. **Fix** in the scene. That is the user's modeling step; inspecting never
   edits.
5. **Re-audit**, and re-render the same views. A fix is shown only when the
   counts or images change. Never report a fix from the edit alone.

Stop when every requested check has a payload or a named refusal. Do not run
all three tools on every scene; choose by the question.

## Which tool for which question

| Question | Tool |
|---|---|
| Object, polygon, vertex counts, materials, bounds | `blender_mesh.py` |
| Holes, loose or doubled geometry, flipped or crossing faces | `blender_mesh_audit.py` |
| Do objects A and B pass through each other | `blender_mesh_audit.py --pairs A:B` |
| Can I see the dent, seam, flipped face or crossing | `blender_inspect_render.py` |
| Which part is in front, by how much, how much do they overlap | `blender_depth_order.py` |
| Garment clearance against a body | `blender_fit.py --mode probe` |
| A plain named-view render, or a render compared with a reference | `blender_render.py`, `blender_multiview_render.py` |

`blender_fit.py` and the two plain renderers are the earlier tools. Its
`apply-copy` mode writes a new `.blend`, so use `probe` unless the user
authorised a bounded edit.

## Read each payload

Every payload carries `interpretation_limits`; read them before a claim.
Refusals exit 2 with empty stdout and one stderr line: report the line, never
infer a result.

**`blender_mesh_audit`** (`mesh-audit-v1`; `--max-locations` defaults to 50):
per object `defects.<class>.count` and `locations`, plus `clean`, and a
`summary`. The ten classes are `non_manifold_edges` (3+ faces),
`boundary_edges`, `wire_edges`, `loose_verts`, `duplicate_verts`,
`degenerate_faces`, `ngons`, `poles`, `inconsistent_normals` and
`self_intersections`.

- `count` is exact; `locations` is cut at the limit, lowest index first.
- Read the counts, not only `clean`: a cylinder cap (n-gon) or a sphere pole
  makes `clean` false without a bug. Report deliberate topology as such.
- Locations are world-space scene units. An edge's is its midpoint; a
  crossing's is near, not on, the crossing. Indices refer to the **evaluated**
  mesh (render modifiers applied), so they match the base mesh only without
  modifiers.
- It does not see: a uniformly inverted island or negative-scaled object
  (count 0), a shell wholly inside another, long sliver faces above the area
  threshold, or whether a defect matters.

**`blender_inspect_render`** (`inspect-render-v1`; needs `--output-dir` and
`--views`: presets `front-left-high`, `front-right-high`, `back-left-high`,
`back-right-high`, or a `render-views-v1` manifest; `--modes` default to all
five; perspective, 50 mm by default): per view, `files.<mode>` paths, `camera`,
`coverage`, `depth_range`, and `RENDER_BLOCKED` for a view with no geometry.
Framing is locked across views and modes. Open the PNGs and describe what you
see.

| Mode | Use it for | Blind spot |
|---|---|---|
| `matcap` | dents, ridges, seams, creases (cavity shading on); a flipped face renders as a **hole** (back-face culling on) | culling hides a single-sided quad seen from behind |
| `normal` | crossings, faceting, sudden direction changes | **does not show a flipped face**: back faces are turned toward the camera |
| `depth` | relief and distance: near is warm, far is cool; the PNG's bottom strip carries the near/far legend in scene units; the `.npy` is float32 planar depth, NaN for background | depth is along the camera axis, not the distance from the camera centre |
| `ao` | contact, crevices and gaps (convex shapes show almost none) | not a defect detector on its own |
| `object-id` | which object owns each pixel; the JSON maps colour to object | it is coverage, not geometry |

The matcap and normal renders can disagree about a flipped face; that
disagreement is itself the evidence. Use several views: a defect facing away
from one camera shows in another.

**`blender_depth_order`** (`depth-order-v1`; same `--views`, no output
directory): per view, `objects[]` with `depth_range`, `screen_bbox` and
`visible_fraction`, and `pairs[]` sorted by overlap. Read each pair's
`summary` line first, then check before trusting it:

- `front_fraction` near 1.0 means a clean order. A low value means the two
  interleave or interpenetrate; follow up with `blender_mesh_audit --pairs`.
- `method: passes` is per-pixel; `method: vertices` is a bounding-box
  estimate. Say which.
- `depth_gap` is the surface separation (median over shared pixels);
  `range_gap` is the empty space between the extents. A part resting on another
  has a negative `range_gap` and a small positive `depth_gap`: that is normal.
- Overlap compares silhouettes, including pixels another object hides;
  `visible_fraction` is what is seen. A preset's direction runs from subject to
  camera with front = -Y and up = +Z, and a thin part can be edge-on to one.

**`blender_mesh`**: counts come from the base mesh, so unapplied modifiers
change the render-visible density; bounds are in scene units.

## What each layer does not establish

- Counts and locations do not say a defect is visible or matters. Pair them
  with a render.
- Renders and depth do not prove topology: a sealed-looking surface can hide
  doubled vertices. They cover only the views requested.
- Depth order is from the camera's view. It does not establish physical
  clearance or contact; use `--pairs` or `blender_fit --mode probe`.
- Nothing here establishes that the model matches a concept image or is
  stylistically right. For the concept side use the `pil-agent-plugin` tools
  (relative concept depth is its `pil_depth.py`).

## Report

For each finding give the object, the class or pair, the count, the
world-space location, the view and image path that shows it, and the limit
that applies. State which checks were not run. After a fix, cite the before
and after counts.
