---
name: image-analysis
description: 'Use this skill whenever a 3D model, character, prop or Blender scene is being judged against reference pictures (concept art, a reference photo, a sheet) and the answer needs both what it looks like and what the mesh is doing. Typical asks: "does the model match the concept?", "are the proportions or silhouette off?", "is this thinner than the reference or just a different angle?", "does the helmet really sink into the head or is it just lighting?", "something looks wrong in the mesh - what?". Covers a model, render, viewport screenshot or .blend checked against reference images, and the diagnosis of whether a visible problem is appearance (colour, lighting, camera angle) or geometry (clipping, depth order, hidden dents). Not for a plain image-to-image diff or colour readout, a Blender-only mesh audit with no reference images, generating or editing images, or modeling something new.'
---

# Combined image analysis

Use this skill when one answer must synthesize more than one evidence layer.
Keep each layer's claim boundary visible instead of turning unrelated signals
into a single confidence score.

## Hand the review to a specialized agent

This skill runs on the main thread, which owns the final report. Never give
these requests to a general-purpose agent; use the Agent tool with the agent
below. Subagents cannot spawn subagents, so each agent runs its own tools.

| Request | Send it to |
|---|---|
| Multi-layer review: concept art vs a model or render, image plus mesh, clipping diagnosis, appearance vs geometry | `pil-agent-plugin:combined-evidence-analyst` |
| Single layer: 2D comparison of images (colour, layout, changed regions) | `pil-agent-plugin:image-comparison-analyst` |
| Single layer: Blender mesh defects, depth order, a dent flat renders hide | `blender-inspect:blender-model-inspector`, when the `blender-inspect` plugin is installed |
| Single layer: fitting an existing template to calibrated views | the [`multiview-reconstruction`](../multiview-reconstruction/SKILL.md) skill, and its agent (`multiview-fit-analyst`) when present |

Give the agent the file paths, the question and the evidence you need back
(measurements, locations, evidence images). Independent single-layer agents may
run in parallel; the main thread then combines their findings. If an agent type
is not available, say so and apply the matching skill yourself.

Skill-level routing, for the work an agent or you carry out:

- Pixel/file facts, exact colours, regions, or a two-image comparison with
  nothing else: use [`image-measurement`](../image-measurement/SKILL.md) directly.
- Blender mesh defects, depth-revealing renders, which part is in front, or a
  dent the flat renders hide: use the `blender-inspect` plugin's
  `model-inspection` skill (and its `blender-model-inspector` agent), when that
  plugin is installed. If it is not, say so; do not improvise mesh checks.
- Relative depth of a concept image, or whether it agrees with a model's depth
  render: `pil_depth.py` (`estimate`, then `compare`). See the depth notes below.
- Image-plus-mesh review, Blender bounds, clipping or clearance, or a
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
| Blender scene probes (`blender-inspect`) | Mesh counts, bounds, topology defects with locations, depth order, signed clearance | Visual or semantic match by itself |
| Concept-image depth (`pil_depth`) | Model-inferred *relative* inverse depth of one image, and its rank agreement with a render's depth | Metric depth, hidden geometry, or which source is right where they disagree |
| Locked render review | Matched projected appearance across requested views | Physical clearance or deformation quality |

## Working method

Inventory and match the requested views before comparing them. Inspect them
visually before reading scores, then use `image-measurement` for reproducible
pixel/file evidence. Keep normalization, masks, registration transforms, raw
measurements, and domain-profile identity with the result.

Prepare every requested multi-view contour, including refused views. Run a
template solve only when calibration and correspondences are sufficient, and
continue to geometry mutation only from `SOLVED`. Use
`blender_fit.py --mode probe` for penetration or clearance,
`blender_mesh.py` for counts and bounds, and `blender_mesh_audit.py` for
topology defects, all from `blender-inspect` (load its `model-inspection`
skill, which resolves that plugin's scripts directory); silhouettes are not
collision evidence. The `pil_blender_*` copies of these tools are deprecated and go away
in the next minor release: use them only when `blender-inspect` is not
installed, and say that the deprecated copy ran.

Concept depth is one extra layer, not a verdict: `pil_depth.py estimate`
gives model-inferred relative inverse depth, and `compare` aligns it to a
render's depth `.npy` from `blender_inspect_render.py`. Read the Spearman rank
correlation together with AbsRel, `valid_pixels`, the region boxes and the
disagreement image; AbsRel alone tracks camera distance, and the two views need
matching framing and pose. There are no pass/fail thresholds.

Render only the decisive requested views with locked framing, then aggregate
matched comparisons with `pil_multiview_review.py`. A missing, refused, or
violated required view must remain visible in the overall result.

Run `pil_capabilities.py` to list every tool's arguments and dependency
status, or `pil_capabilities.py --tool pil_palette_diff` for a single tool.
The executable 0.9.0 normalization, selection, registration, local-diff,
calibration, batch, and MCP workflows are in [measurement workflows](../../docs/measurement-workflows.md).

## Synthesize and report

Group findings by evidence layer, then state which differences are appearance
(colour, shading, camera, lighting) and which are geometry. For a requested
match target, define the required views, metrics, thresholds, geometry
constraints, and worst-case rule before judging it. Report the weakest required
view, all refusal states, and the assumptions that limit each claim. The
analysis is complete when every requested layer is measured or explicitly
marked unavailable; do not stop at a first render or first plausible score when
the request includes the full loop.
