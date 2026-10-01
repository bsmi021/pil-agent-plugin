#!/usr/bin/env python
"""Mesh defect audit read from a Blender scene, never inferred from pixels.

`blender_mesh` reports how big a mesh is; this tool reports whether it is
sound. For every render-visible mesh object (modifiers evaluated, world space)
it counts ten classes of mesh defect and lists where the first few are:

- `non_manifold_edges`: edges shared by three or more faces.
- `boundary_edges`: edges used by exactly one face (the rim of a hole).
- `wire_edges`: edges used by no face.
- `loose_verts`: vertices used by no edge.
- `duplicate_verts`: vertices that `find_doubles` would merge into another
  within `--merge-distance` (three coincident vertices count as two).
- `degenerate_faces`: faces whose area is below `DEGENERATE_AREA`.
- `ngons`: faces with more than four vertices.
- `poles`: vertices with six or more edges.
- `inconsistent_normals`: faces that would flip if normals were recalculated,
  taken per connected island as the smaller of "flip" and "stay".
- `self_intersections`: pairs of faces of one object that cross each other and
  share no vertex.

`--pairs A:B` additionally reports how many faces of A cross faces of B.

Rejection paths (exit 2, byte-empty stdout, one-line reason on stderr):
- Blender executable not found (see `blender_common` for the search order).
- Requested `.blend` file does not exist.
- A name in `--objects` or `--pairs` is not a render-visible mesh object, or
  there is no render-visible mesh object at all.
- A bad option value.
- Blender subprocess timed out, exited non-zero, or produced no sentinel-wrapped
  probe payload.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import blender_common  # noqa: E402

TOOL = "blender_mesh_audit"
TOOL_VERSION = "0.1.0"
SCHEMA = "mesh-audit-v1"

DEFECT_CLASSES = (
    "non_manifold_edges",
    "boundary_edges",
    "wire_edges",
    "loose_verts",
    "duplicate_verts",
    "degenerate_faces",
    "ngons",
    "poles",
    "inconsistent_normals",
    "self_intersections",
)

DEFAULT_MERGE_DISTANCE = 1e-5
DEFAULT_MAX_LOCATIONS = 50

# World-space area below which a face counts as degenerate. It is a constant, not
# a flag, and it is echoed in `parameters.degenerate_area_tolerance`.
DEGENERATE_AREA = 1e-9

# A vertex with at least this many edges is a pole.
POLE_VALENCE = 6

# Executed inside Blender's bundled Python after `blender_common`'s header,
# which supplies `ARGS` and `emit`. `ARGS[0]` is one JSON config object.
_PROBE_BODY = r'''
import json
import math

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

CFG = json.loads(ARGS[0])
CLASSES = CFG["classes"]


def _r(v):
    return round(float(v), 6)


def _vec(v):
    return [_r(v[0]), _r(v[1]), _r(v[2])]


class Refusal(Exception):
    """The audit cannot answer; the message becomes the tool's refusal reason."""


def fail(message):
    raise Refusal(message)


def walk(layer_coll, parent_ok, visit):
    ok = parent_ok and not layer_coll.exclude and not layer_coll.collection.hide_render
    visit(layer_coll, ok)
    for child in layer_coll.children:
        walk(child, ok, visit)


def render_visible_meshes(scene, view_layer):
    """Mesh objects that a render of ``scene`` would show, by name."""
    in_scene = {o.name for o in scene.objects}
    names = set()

    def visit(layer_coll, ok):
        # The layer collection's hide flags only affect the viewport; open them
        # so the object is evaluated. The file is never saved.
        layer_coll.hide_viewport = False
        layer_coll.collection.hide_viewport = False
        if ok:
            names.update(o.name for o in layer_coll.collection.objects)

    walk(view_layer.layer_collection, True, visit)
    return sorted(
        name
        for name in names & in_scene
        if bpy.data.objects[name].type == "MESH" and not bpy.data.objects[name].hide_render
    )


def prepare_for_evaluation(obj):
    """Evaluate ``obj`` as a render would: shown, with its render modifiers."""
    obj.hide_viewport = False
    try:
        obj.hide_set(False)
    except RuntimeError:
        pass
    for md in obj.modifiers:
        md.show_viewport = md.show_render
        if md.type == "SUBSURF":
            md.levels = md.render_levels


def world_bmesh(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    evaluated.to_mesh_clear()
    bm.transform(evaluated.matrix_world)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.normal_update()
    return bm


class Defect:
    """One defect class: the full count, and the first ``limit`` examples."""

    def __init__(self, limit):
        self.limit = limit
        self.count = 0
        self.locations = []

    def add(self, make_record):
        self.count += 1
        if len(self.locations) < self.limit:
            self.locations.append(make_record())

    def as_dict(self):
        return {"count": self.count, "locations": self.locations}


def vert_record(v):
    return {"index": v.index, "location": _vec(v.co)}


def edge_record(e):
    mid = (e.verts[0].co + e.verts[1].co) / 2
    return {"index": e.index, "verts": [e.verts[0].index, e.verts[1].index], "location": _vec(mid)}


def face_record(f):
    return {"index": f.index, "location": _vec(f.calc_center_median())}


def overlap_centre(face_a, face_b):
    """Centre of the box shared by two faces' bounding boxes."""
    lo = [-math.inf] * 3
    hi = [math.inf] * 3
    for face in (face_a, face_b):
        f_lo = [min(v.co[i] for v in face.verts) for i in range(3)]
        f_hi = [max(v.co[i] for v in face.verts) for i in range(3)]
        lo = [max(a, b) for a, b in zip(lo, f_lo)]
        hi = [min(a, b) for a, b in zip(hi, f_hi)]
    return [_r((a + b) / 2) for a, b in zip(lo, hi)]


def islands(bm):
    """Faces grouped by shared edges, as lists of face indices."""
    parent = list(range(len(bm.faces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for edge in bm.edges:
        faces = edge.link_faces
        for other in faces[1:]:
            a, b = find(faces[0].index), find(other.index)
            if a != b:
                parent[max(a, b)] = min(a, b)
    groups = {}
    for i in range(len(bm.faces)):
        groups.setdefault(find(i), []).append(i)
    return [groups[k] for k in sorted(groups)]


def flipped_face_indices(bm):
    """Faces in the minority orientation of their island, ascending."""
    recalculated = bm.copy()
    recalculated.faces.ensure_lookup_table()
    bmesh.ops.recalc_face_normals(recalculated, faces=recalculated.faces[:])
    would_flip = [
        bm.faces[i].normal.dot(recalculated.faces[i].normal) < 0 for i in range(len(bm.faces))
    ]
    recalculated.free()
    minority = []
    for group in islands(bm):
        flips = [i for i in group if would_flip[i]]
        if len(flips) * 2 <= len(group):
            minority.extend(flips)
        else:
            minority.extend(i for i in group if not would_flip[i])
    return sorted(minority)


def crossing_pairs(bm_a, bm_b=None):
    """Sorted (face_a, face_b) pairs whose triangles cross.

    Within one mesh (``bm_b`` is None) each pair appears once with a < b and pairs
    that share a vertex are dropped.
    """
    tree_a = BVHTree.FromBMesh(bm_a)
    if bm_b is None:
        found = set()
        for a, b in tree_a.overlap(tree_a):
            if a >= b:
                continue
            verts_a = {v.index for v in bm_a.faces[a].verts}
            if any(v.index in verts_a for v in bm_a.faces[b].verts):
                continue
            found.add((a, b))
        return sorted(found)
    tree_b = BVHTree.FromBMesh(bm_b)
    return sorted(set(tree_a.overlap(tree_b)))


def audit_object(obj, depsgraph):
    limit = CFG["max_locations"]
    bm = world_bmesh(obj, depsgraph)
    found = {name: Defect(limit) for name in CLASSES}

    for edge in bm.edges:
        n = len(edge.link_faces)
        if n >= 3:
            found["non_manifold_edges"].add(lambda e=edge: edge_record(e))
        elif n == 1:
            found["boundary_edges"].add(lambda e=edge: edge_record(e))
        elif n == 0:
            found["wire_edges"].add(lambda e=edge: edge_record(e))

    for vert in bm.verts:
        valence = len(vert.link_edges)
        if valence == 0:
            found["loose_verts"].add(lambda v=vert: vert_record(v))
        if valence >= CFG["pole_valence"]:
            found["poles"].add(lambda v=vert: dict(vert_record(v), valence=len(v.link_edges)))

    targets = bmesh.ops.find_doubles(bm, verts=bm.verts[:], dist=CFG["merge_distance"])["targetmap"]
    for vert in sorted(targets, key=lambda v: v.index):
        found["duplicate_verts"].add(
            lambda v=vert: dict(vert_record(v), merges_into=targets[v].index)
        )

    for face in bm.faces:
        if face.calc_area() < CFG["degenerate_area"]:
            found["degenerate_faces"].add(lambda f=face: face_record(f))
        if len(face.verts) > 4:
            found["ngons"].add(lambda f=face: dict(face_record(f), sides=len(f.verts)))

    for index in flipped_face_indices(bm):
        found["inconsistent_normals"].add(lambda f=bm.faces[index]: face_record(f))

    for a, b in crossing_pairs(bm):
        found["self_intersections"].add(
            lambda fa=bm.faces[a], fb=bm.faces[b]: {
                "faces": [fa.index, fb.index],
                "location": overlap_centre(fa, fb),
            }
        )

    counts = {name: found[name].count for name in CLASSES}
    result = {
        "name": obj.name,
        "evaluated": {"verts": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces)},
        "defects": {name: found[name].as_dict() for name in CLASSES},
        "clean": not any(counts.values()),
    }
    bm.free()
    return result


def resolve_pair(spec, visible):
    options = [
        (spec[:i], spec[i + 1:])
        for i, ch in enumerate(spec)
        if ch == ":" and spec[:i] in visible and spec[i + 1:] in visible
    ]
    if len(options) != 1:
        fail("--pairs %r does not name two render-visible mesh objects as A:B" % spec)
    a, b = options[0]
    if a == b:
        fail("--pairs %r names the same object twice" % spec)
    return a, b


def audit_pair(name_a, name_b, depsgraph):
    limit = CFG["max_locations"]
    bm_a = world_bmesh(bpy.data.objects[name_a], depsgraph)
    bm_b = world_bmesh(bpy.data.objects[name_b], depsgraph)
    pairs = crossing_pairs(bm_a, bm_b)
    locations = [
        {
            "face_a": a,
            "face_b": b,
            "location": overlap_centre(bm_a.faces[a], bm_b.faces[b]),
        }
        for a, b in pairs[:limit]
    ]
    bm_a.free()
    bm_b.free()
    return {
        "a": name_a,
        "b": name_b,
        "overlapping_face_pairs": len(pairs),
        "interpenetrating": bool(pairs),
        "locations": locations,
    }


def gather():
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    visible = render_visible_meshes(scene, view_layer)
    if not visible:
        fail("no render-visible mesh objects to audit")

    requested = CFG["objects"]
    if requested is not None:
        missing = [n for n in requested if n not in visible]
        if missing:
            fail("not render-visible mesh objects: " + ", ".join(sorted(missing)))
        audited = sorted(set(requested))
    else:
        audited = visible

    pair_names = []
    for spec in CFG["pairs"]:
        pair = resolve_pair(spec, set(visible))
        if pair not in pair_names:
            pair_names.append(pair)

    for name in sorted(set(audited) | {n for p in pair_names for n in p}):
        prepare_for_evaluation(bpy.data.objects[name])
    view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()

    return {
        "blender_version": ".".join(str(x) for x in bpy.app.version),
        "objects": [audit_object(bpy.data.objects[n], depsgraph) for n in audited],
        "pairs": [audit_pair(a, b, depsgraph) for a, b in pair_names],
    }


try:
    emit(gather())
except Refusal as refusal:
    emit({"error": str(refusal)})
'''

INTERPRETATION_LIMITS = [
    "Everything is read from Blender's scene data with modifiers evaluated as a render would see them (an object hidden only in the viewport is still audited, modifiers are enabled when their render flag is on, and Subdivision Surface uses its render levels). Element `index` values refer to that evaluated mesh, so they match the base mesh only when the object has no modifiers.",
    "Locations are world-space coordinates in scene units. An edge's location is its midpoint, a face's is its median centre, and a crossing's is the centre of the overlap of the two faces' bounding boxes, which lies near the crossing rather than on it.",
    "The classes overlap by design: a boundary edge is not counted as non-manifold, but an unclosed seam of duplicate vertices also produces boundary edges, and an isolated extra vertex is both a loose vertex and, if it coincides with another, a duplicate. `non_manifold_edges` counts only edges shared by three or more faces. A vertex pinched between two faces (a bow-tie) is not counted.",
    "`clean` is true only when every class is zero. `ngons` and `poles` are topology properties that are often deliberate (a cylinder cap, a sphere pole), so `clean: false` does not mean the mesh is broken; read the counts.",
    "`inconsistent_normals` compares each face with the direction mesh recalculation would give it and counts the smaller side of each connected island. An island that is uniformly inside out, or an object with a negative scale, is consistent by this test and reports zero; a matcap render with back-face culling shows it.",
    "`self_intersections` counts pairs of faces that cross and share no vertex. Faces that only touch along a shared edge or at a shared vertex are not counted, and neither is one closed shell lying wholly inside another with no surface crossing. `--pairs` has the same blind spot: it reports surfaces that cross, not containment.",
    "`duplicate_verts` counts the vertices a merge at `--merge-distance` would remove, so three coincident vertices count as two.",
    "`degenerate_faces` uses a fixed world-space area threshold (`parameters.degenerate_area_tolerance`). It does not flag sliver faces that are long and thin but above that area.",
    "This tool measures the .blend file's mesh data; it does not render or consult pixels, and it does not judge whether a defect matters for the model's purpose.",
]


def probe_blend(blender_executable, blend_path, config, timeout=300):
    """Run the audit probe on ``blend_path``; return ``(payload, error)``."""
    return blender_common.run_probe(
        blender_executable,
        TOOL,
        _PROBE_BODY,
        blend=blend_path,
        script_args=[json.dumps(config, sort_keys=True)],
        timeout=timeout,
    )


def build_summary(objects, pairs):
    totals = {name: sum(o["defects"][name]["count"] for o in objects) for name in DEFECT_CLASSES}
    with_defects = [o["name"] for o in objects if not o["clean"]]
    return {
        "objects_audited": len(objects),
        "objects_clean": len(objects) - len(with_defects),
        "objects_with_defects": with_defects,
        "clean": not with_defects,
        "totals": totals,
        "pairs_checked": len(pairs),
        "pairs_interpenetrating": sum(1 for p in pairs if p["interpenetrating"]),
    }


def build_payload(blend_path, blender_executable, blender_version, config, timeout, objects, pairs):
    """Deterministic tool payload for a successful probe."""
    return {
        "tool": TOOL,
        "version": TOOL_VERSION,
        "schema": SCHEMA,
        "parameters": {
            "blend": str(blend_path),
            "blender_executable": blender_executable,
            "blender_version": blender_version,
            "timeout_seconds": timeout,
            "merge_distance": config["merge_distance"],
            "max_locations": config["max_locations"],
            "degenerate_area_tolerance": config["degenerate_area"],
            "pole_valence": config["pole_valence"],
            "objects_requested": config["objects"],
            "pairs_requested": config["pairs"],
        },
        "objects": objects,
        "pairs": pairs,
        "summary": build_summary(objects, pairs),
        "interpretation_limits": INTERPRETATION_LIMITS,
    }


def _check_options(args):
    """A one-line reason for the first bad option value, else None."""
    if not math.isfinite(args.merge_distance) or args.merge_distance < 0:
        return f"--merge-distance must be a finite number >= 0, got {args.merge_distance}"
    if args.max_locations < 0:
        return f"--max-locations must be >= 0, got {args.max_locations}"
    if args.timeout <= 0:
        return f"--timeout must be > 0, got {args.timeout}"
    for spec in args.pairs:
        if ":" not in spec:
            return f"--pairs must look like A:B, got {spec!r}"
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Audit the mesh objects of a Blender .blend file for defects (non-manifold, "
            "boundary and wire edges, loose and duplicate vertices, degenerate faces, "
            "n-gons, poles, inconsistent normals, self-intersections) with world-space "
            "locations, via a headless subprocess."
        )
    )
    parser.add_argument("blend", help="path to a .blend file")
    parser.add_argument(
        "--objects",
        nargs="+",
        action="extend",
        metavar="NAME",
        help="audit only these render-visible mesh objects (default: all of them)",
    )
    parser.add_argument(
        "--merge-distance",
        type=float,
        default=DEFAULT_MERGE_DISTANCE,
        help=f"vertices closer than this count as duplicates (default {DEFAULT_MERGE_DISTANCE:g})",
    )
    parser.add_argument(
        "--max-locations",
        type=int,
        default=DEFAULT_MAX_LOCATIONS,
        help=f"examples listed per defect class per object (default {DEFAULT_MAX_LOCATIONS}; counts are always exact)",
    )
    parser.add_argument(
        "--pairs",
        action="append",
        default=[],
        metavar="A:B",
        help="also report face crossings between objects A and B (repeatable)",
    )
    blender_common.add_blender_arguments(parser)
    args = parser.parse_args(argv)

    problem = _check_options(args)
    if problem:
        return blender_common.refuse(TOOL, problem)

    blender, reason = blender_common.resolve_blender_executable(args.blender_executable)
    if blender is None:
        return blender_common.refuse(TOOL, reason)

    blend_path = Path(args.blend)
    if not blend_path.is_file():
        return blender_common.refuse(TOOL, f"blend file not found: {blend_path}")

    config = {
        "classes": list(DEFECT_CLASSES),
        "objects": sorted(set(args.objects)) if args.objects else None,
        "pairs": list(dict.fromkeys(args.pairs)),
        "merge_distance": args.merge_distance,
        "max_locations": args.max_locations,
        "degenerate_area": DEGENERATE_AREA,
        "pole_valence": POLE_VALENCE,
    }
    payload, error = probe_blend(blender, blend_path, config, timeout=args.timeout)
    if payload is None:
        return blender_common.refuse(TOOL, error)
    if "error" in payload:
        return blender_common.refuse(TOOL, payload["error"])

    output = build_payload(
        blend_path,
        blender,
        payload["blender_version"],
        config,
        args.timeout,
        payload["objects"],
        payload["pairs"],
    )
    blender_common.write_payload(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
