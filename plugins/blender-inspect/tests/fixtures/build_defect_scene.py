"""Build the planted-defect scene used by the mesh audit tests.

Run inside Blender; nothing binary is committed:

    blender --factory-startup --background --python build_defect_scene.py -- scene.blend [expected.json]

Each object plants one defect class (plus the incidental counts that follow from
its shape), so `EXPECTED_OBJECTS` is a full matrix: every class for every object.
The module imports without `bpy`, so tests can read the expectations on any Python.
"""

import json
import math
import sys

CLASSES = (
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


def counts(**planted):
    return {name: planted.get(name, 0) for name in CLASSES}


# Object name -> the count of every defect class the audit must report for it.
EXPECTED_OBJECTS = {
    "CleanCube": counts(),
    # Two "books" of three quads sharing one edge. Normal recalculation treats the
    # pages as separate, so nothing flips.
    "NonManifold": counts(non_manifold_edges=2, boundary_edges=18),
    # A cube with its top face deleted.
    "OpenBox": counts(boundary_edges=4),
    # A cube plus three edges that belong to no face.
    "WireEdges": counts(wire_edges=3),
    # A cube plus four isolated vertices; the object is rotated and scaled.
    "LooseVerts": counts(loose_verts=4),
    # A seam of two coincident vertex pairs between two quads, plus three
    # coincident loose vertices (two of them would be merged away).
    "DuplicateVerts": counts(duplicate_verts=4, boundary_edges=8, loose_verts=3),
    # Two triangles whose three vertices are collinear.
    "DegenerateFaces": counts(degenerate_faces=2, boundary_edges=6),
    # A hexagonal prism: two six-sided caps.
    "NgonPrism": counts(ngons=2),
    # A UV sphere with 8 segments: a pole of valence 8 at each end.
    "PoleSphere": counts(poles=2),
    # A cube with its top and bottom faces flipped.
    "FlippedFaces": counts(inconsistent_normals=2),
    # A cube pierced by a quad that crosses its top and bottom faces.
    "PiercedCube": counts(boundary_edges=4, self_intersections=2),
    # A quad with an Array modifier (3 copies) on an object hidden in the
    # viewport: the audit must see the 3 evaluated quads.
    "ArrayQuads": counts(boundary_edges=12),
    # The same, with the modifier enabled for render only.
    "RenderOnlyArray": counts(boundary_edges=12),
    "PairA": counts(),
    "PairB": counts(),
    "PairC": counts(),
}

# "A:B" -> face pairs of A that cross faces of B.
EXPECTED_PAIRS = {"PairA:PairB": 6, "PairA:PairC": 0}

# Present in the file but not render-visible mesh objects.
NOT_AUDITED = ("HiddenDefect", "InHiddenCollection", "Marker")

# Evaluated face counts for the modifier objects.
EXPECTED_EVALUATED_FACES = {"ArrayQuads": 3, "RenderOnlyArray": 3}

# LooseVerts is rotated 90 degrees about Z, scaled by 2, and moved.
LOOSE_SCALE = 2.0
LOOSE_LOCAL_VERTS = ((3.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 5.0), (-2.0, -2.0, 1.0))

LOCATIONS = {
    "CleanCube": (0, 0, 0),
    "NonManifold": (10, 0, 0),
    "OpenBox": (20, 0, 0),
    "LooseVerts": (30, 5, 1),
    "WireEdges": (40, 0, 0),
    "DuplicateVerts": (50, 0, 0),
    "DegenerateFaces": (60, 0, 0),
    "NgonPrism": (70, 0, 0),
    "PoleSphere": (80, 0, 0),
    "FlippedFaces": (90, 0, 0),
    "PiercedCube": (100, 0, 0),
    "ArrayQuads": (110, 0, 0),
    "RenderOnlyArray": (120, 0, 0),
    "PairA": (0, 20, 0),
    "PairB": (1.0, 20.8, 0.6),
    "PairC": (0, 40, 0),
    "HiddenDefect": (0, -20, 0),
    "InHiddenCollection": (10, -20, 0),
}


def loose_world_positions():
    """Where the four isolated vertices of LooseVerts land in world space."""
    ox, oy, oz = LOCATIONS["LooseVerts"]
    return [
        (ox - LOOSE_SCALE * y, oy + LOOSE_SCALE * x, oz + LOOSE_SCALE * z)
        for x, y, z in LOOSE_LOCAL_VERTS
    ]


CUBE_VERTS = [
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
]
# Outward-facing winding; index 1 is the top face.
CUBE_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
QUAD = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]


def _hex_prism():
    bottom = [(math.cos(i * math.pi / 3), math.sin(i * math.pi / 3), 0.0) for i in range(6)]
    top = [(x, y, 1.0) for x, y, _ in bottom]
    faces = [tuple(reversed(range(6))), tuple(range(6, 12))]
    for i in range(6):
        j = (i + 1) % 6
        faces.append((i, j, 6 + j, 6 + i))
    return bottom + top, faces


def _books():
    verts, faces = [], []
    for dx in (0.0, 5.0):
        spine = len(verts)
        verts += [(dx, 0.0, 0.0), (dx, 0.0, 1.0)]
        for angle in (0.0, 120.0, 240.0):
            x = dx + math.cos(math.radians(angle))
            y = math.sin(math.radians(angle))
            page = len(verts)
            verts += [(x, y, 0.0), (x, y, 1.0)]
            faces.append((spine, page, page + 1, spine + 1))
    return verts, faces


def _mesh_specs():
    """Name -> (verts, edges, faces) in object-local coordinates."""
    open_cube = [f for i, f in enumerate(CUBE_FACES) if i != 1]
    prism = _hex_prism()
    books = _books()
    specs = {
        "CleanCube": (CUBE_VERTS, [], CUBE_FACES),
        "NonManifold": (books[0], [], books[1]),
        "OpenBox": (CUBE_VERTS, [], open_cube),
        "WireEdges": (
            CUBE_VERTS + [(3, 3, 3), (4, 3, 3), (3, 4, 3), (4, 4, 3), (3, 5, 3), (4, 5, 3)],
            [(8, 9), (10, 11), (12, 13)],
            CUBE_FACES,
        ),
        "LooseVerts": (CUBE_VERTS + list(LOOSE_LOCAL_VERTS), [], CUBE_FACES),
        "DuplicateVerts": (
            QUAD + [(1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)] + [(5, 5, 5)] * 3,
            [],
            [(0, 1, 2, 3), (4, 5, 6, 7)],
        ),
        "DegenerateFaces": (
            [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 3, 0), (1, 3, 0), (3, 3, 0)],
            [],
            [(0, 1, 2), (3, 4, 5)],
        ),
        "NgonPrism": (prism[0], [], prism[1]),
        "FlippedFaces": (
            CUBE_VERTS,
            [],
            [tuple(reversed(f)) if i in (0, 1) else f for i, f in enumerate(CUBE_FACES)],
        ),
        "PiercedCube": (
            CUBE_VERTS + [(-0.3, 0, -2), (0.3, 0, -2), (0.3, 0, 2), (-0.3, 0, 2)],
            [],
            CUBE_FACES + [(8, 9, 10, 11)],
        ),
        "ArrayQuads": (QUAD, [], [(0, 1, 2, 3)]),
        "RenderOnlyArray": (QUAD, [], [(0, 1, 2, 3)]),
        "HiddenDefect": (CUBE_VERTS, [], open_cube),
        "InHiddenCollection": (CUBE_VERTS, [], open_cube),
    }
    corner_cube = [(x + 1, y + 1, z + 1) for x, y, z in CUBE_VERTS]
    for name in ("PairA", "PairB", "PairC"):
        specs[name] = (corner_cube, [], CUBE_FACES)
    return specs


def _sphere_mesh(bpy, bmesh):
    bm = bmesh.new()
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=8, v_segments=5, radius=1.0)
    except TypeError:
        bmesh.ops.create_uvsphere(bm, u_segments=8, v_segments=5, diameter=1.0)
    mesh = bpy.data.meshes.new("PoleSphere")
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def _add_array(obj, render_only):
    array = obj.modifiers.new("Array", "ARRAY")
    array.count = 3
    array.use_relative_offset = False
    array.use_constant_offset = True
    array.constant_offset_displace = (2.0, 0.0, 0.0)
    if render_only:
        array.show_viewport = False
        array.show_render = True


def build(out_path, expected_path=None):
    import bmesh
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    hidden = bpy.data.collections.new("HiddenCollection")
    scene.collection.children.link(hidden)
    hidden.hide_render = True

    for name, (verts, edges, faces) in _mesh_specs().items():
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, edges, faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        (hidden if name == "InHiddenCollection" else scene.collection).objects.link(obj)
        obj.location = LOCATIONS[name]

    sphere = bpy.data.objects.new("PoleSphere", _sphere_mesh(bpy, bmesh))
    scene.collection.objects.link(sphere)
    sphere.location = LOCATIONS["PoleSphere"]

    loose = bpy.data.objects["LooseVerts"]
    loose.rotation_euler = (0.0, 0.0, math.pi / 2)
    loose.scale = (LOOSE_SCALE,) * 3
    bpy.data.objects["HiddenDefect"].hide_render = True

    _add_array(bpy.data.objects["ArrayQuads"], render_only=False)
    bpy.data.objects["ArrayQuads"].hide_viewport = True
    _add_array(bpy.data.objects["RenderOnlyArray"], render_only=True)

    scene.collection.objects.link(bpy.data.objects.new("Marker", None))

    bpy.ops.wm.save_as_mainfile(filepath=str(out_path))
    if expected_path:
        with open(expected_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "objects": EXPECTED_OBJECTS,
                    "pairs": EXPECTED_PAIRS,
                    "not_audited": list(NOT_AUDITED),
                },
                handle,
                indent=2,
                sort_keys=True,
            )


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not args:
        raise SystemExit("usage: blender --background --python build_defect_scene.py -- scene.blend [expected.json]")
    build(args[0], args[1] if len(args) > 1 else None)
