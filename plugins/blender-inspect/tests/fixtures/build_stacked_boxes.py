"""Build the scenes used by the depth-order tests.

Run inside Blender; nothing binary is committed:

    blender --factory-startup --background --python build_stacked_boxes.py -- out_dir

It writes one `.blend` per scene (`SCENES`). The module imports without `bpy`, so
tests can read the known geometry on any Python.

`stacked` holds three thin boxes (plates), each turned 45 degrees about Z so that
its normal is N = (1, 1, 0) / sqrt(2), and stacked along N with a clear gap
between neighbours (`SPACING` > the plate thickness), so no two of them touch or
intersect. Two disjoint convex solids have a separating plane, which every view
ray crosses at most once, so in any view where N is not edge-on the nearer plate
is nearer at every pixel where the two overlap. Along the front view's depth axis
(camera on -Y) the order is A, B, C (A nearest); from the side view (camera on +X)
it is C, B, A; a front-left-high 3/4 view gives A, B, C again. Avoid
front-right-high, which sees the plates edge-on. Every pair overlaps on screen in
each of those views.

`with_wire` adds a wire-only object that renders no pixels, to exercise the vertex
fallback.
"""

import math
import sys

_S = math.sqrt(0.5)
AXIS_T = (_S, -_S, 0.0)      # along the plate's width
AXIS_N = (_S, _S, 0.0)       # the plate normal, the stacking axis
AXIS_Z = (0.0, 0.0, 1.0)     # along the plate's height
# (T, N, Z) is a right-handed frame, so CUBE_FACES keeps its outward winding.

# Half extents along (T, N, Z): a 4 x 0.3 x 3 plate.
HALF = (2.0, 0.15, 1.5)
THICKNESS = 2 * HALF[1]
SPACING = 1.0      # centre to centre along N; the clear gap is SPACING - THICKNESS

# Object name -> world centre.
BOXES = {name: tuple(i * SPACING * c for c in AXIS_N) for i, name in enumerate(("BoxA", "BoxB", "BoxC"))}

CUBE_VERTS = [
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
]
# Outward-facing winding.
CUBE_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]

# A wire-only object in front of BoxA in the front view (y = -4) but with no faces.
WIRE_NAME = "WireOnly"
WIRE_VERTS = [(0.0, -4.0, 0.0), (1.0, -4.0, 0.0), (1.0, -4.0, 1.0)]
WIRE_EDGES = [(0, 1), (1, 2)]

SCENES = ("stacked", "with_wire")


def box_corners(name):
    """The eight world-space corners of a box, as tuples."""
    centre = BOXES[name]
    corners = []
    for sx, sy, sz in CUBE_VERTS:
        corners.append(tuple(
            centre[i] + sx * HALF[0] * AXIS_T[i] + sy * HALF[1] * AXIS_N[i] + sz * HALF[2] * AXIS_Z[i]
            for i in range(3)
        ))
    return corners


def to_local(name, point):
    """``point`` in a box's own (T, N, Z) frame, relative to its centre."""
    centre = BOXES[name]
    rel = [point[i] - centre[i] for i in range(3)]
    return tuple(sum(rel[i] * axis[i] for i in range(3)) for axis in (AXIS_T, AXIS_N, AXIS_Z))


def _pydata(bpy, name, verts, edges, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def build_scene(scene_name):
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    if scene_name not in SCENES:
        raise SystemExit(f"unknown scene {scene_name!r}")
    for name in BOXES:
        _pydata(bpy, name, box_corners(name), [], CUBE_FACES)
    if scene_name == "with_wire":
        _pydata(bpy, WIRE_NAME, WIRE_VERTS, WIRE_EDGES, [])


def build(out_dir):
    import bpy
    from pathlib import Path

    for scene_name in SCENES:
        build_scene(scene_name)
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(out_dir) / f"{scene_name}.blend"))


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not args:
        raise SystemExit("usage: blender --background --python build_stacked_boxes.py -- out_dir")
    build(args[0])
