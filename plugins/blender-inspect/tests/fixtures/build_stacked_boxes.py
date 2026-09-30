"""Build the scenes used by the depth-order tests.

Run inside Blender; nothing binary is committed:

    blender --factory-startup --background --python build_stacked_boxes.py -- out_dir

It writes one `.blend` per scene (`SCENES`). The module imports without `bpy`, so
tests can read the known geometry on any Python.

`stacked` holds three cubes, staggered along a diagonal so that in the front view
(camera on -Y), the side view (camera on +X) and a 3/4 view every pair overlaps
on screen and no two depths coincide. Along the front view's depth axis the order
is A, B, C (A nearest); along the side view's it is C, B, A. `with_wire` adds a
wire-only object that renders no pixels, to exercise the vertex fallback.
"""

import sys

HALF = 1.5
# Object name -> world centre. Cubes are axis aligned with half-extent HALF.
BOXES = {
    "BoxA": (0.0, 0.0, 0.0),
    "BoxB": (1.0, 1.2, 0.6),
    "BoxC": (2.0, 2.4, 1.2),
}

CUBE_VERTS = [
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
]
# Outward-facing winding.
CUBE_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]

# A wire-only object in front of BoxA in the front view (y = -3) but with no faces.
WIRE_NAME = "WireOnly"
WIRE_VERTS = [(0.0, -3.0, 0.0), (1.0, -3.0, 0.0), (1.0, -3.0, 1.0)]
WIRE_EDGES = [(0, 1), (1, 2)]

SCENES = ("stacked", "with_wire")


def box_corners(name):
    """The eight world-space corners of a box, as tuples."""
    cx, cy, cz = BOXES[name]
    return [(cx + x * HALF, cy + y * HALF, cz + z * HALF) for x, y, z in CUBE_VERTS]


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
