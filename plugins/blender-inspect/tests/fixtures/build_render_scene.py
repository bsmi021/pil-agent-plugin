"""Build the small scenes used by the render-mode tests.

Run inside Blender; nothing binary is committed:

    blender --factory-startup --background --python build_render_scene.py -- out_dir

It writes one `.blend` per scene (`SCENES`). The module imports without `bpy`, so
tests can read the known positions on any Python.
"""

import sys

# Object name -> (world centre, radius). High-resolution UV spheres, so the depth
# at a sphere's centre pixel is the depth of a known point on a known sphere.
SPHERES = {
    "SphereA": ((-3.0, 0.0, 0.0), 1.0),
    "SphereB": ((3.0, 4.0, 1.0), 1.0),
    "SphereC": ((0.0, -4.0, -2.0), 1.5),
}
SPHERE_SEGMENTS, SPHERE_RINGS = 96, 48

CUBE_VERTS = [
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
]
# Outward-facing winding; index 1 is the top face.
CUBE_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
QUAD = [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)]

SCENES = ("spheres", "flat_quad", "no_faces", "clean_cube", "flipped_cube")


def _sphere(bpy, bmesh, name, centre, radius):
    bm = bmesh.new()
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=SPHERE_SEGMENTS, v_segments=SPHERE_RINGS, radius=radius)
    except TypeError:
        bmesh.ops.create_uvsphere(bm, u_segments=SPHERE_SEGMENTS, v_segments=SPHERE_RINGS, diameter=radius * 2.0)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = centre
    return obj


def _pydata(bpy, name, verts, edges, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, edges, faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def build_scene(scene_name):
    import bmesh
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    if scene_name == "spheres":
        for name, (centre, radius) in SPHERES.items():
            _sphere(bpy, bmesh, name, centre, radius)
    elif scene_name == "flat_quad":
        _pydata(bpy, "FlatQuad", QUAD, [], [(0, 1, 2, 3)])
    elif scene_name == "no_faces":
        # Wire-only geometry has nothing to render; a hidden cube adds none either.
        _pydata(bpy, "WireOnly", CUBE_VERTS[:4], [(0, 1), (1, 2)], [])
        hidden = _pydata(bpy, "HiddenCube", CUBE_VERTS, [], CUBE_FACES)
        hidden.hide_render = True
    elif scene_name == "clean_cube":
        _pydata(bpy, "Cube", CUBE_VERTS, [], CUBE_FACES)
    elif scene_name == "flipped_cube":
        flipped = [tuple(reversed(f)) if i == 1 else f for i, f in enumerate(CUBE_FACES)]
        _pydata(bpy, "Cube", CUBE_VERTS, [], flipped)
    else:
        raise SystemExit(f"unknown scene {scene_name!r}")


def build(out_dir):
    import bpy
    from pathlib import Path

    for scene_name in SCENES:
        build_scene(scene_name)
        bpy.ops.wm.save_as_mainfile(filepath=str(Path(out_dir) / f"{scene_name}.blend"))


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not args:
        raise SystemExit("usage: blender --background --python build_render_scene.py -- out_dir")
    build(args[0])
