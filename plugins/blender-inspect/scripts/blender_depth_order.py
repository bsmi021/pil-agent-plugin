#!/usr/bin/env python
"""Report which object is in front of which, per view, from the scene's geometry.

For each view (a named 3/4 orbit preset or a `render-views-v1` manifest view)
one perspective or orthographic camera is placed, framed on all render-visible
mesh geometry, and the tool reports:

- per object: its nearest and farthest distance along the view direction (from
  the evaluated vertices), its screen-space bounding box, how many pixels it
  covers when rendered alone and how many of those it wins in the full scene;
- per overlapping pair: the front object, the depth gap, the overlap fraction
  and a plain-language `summary` line.

Pixels come from EEVEE passes at one sample. One render of the whole scene
(Depth, Alpha, Cryptomatte object IDs) says which object is visible where. It
cannot say anything about a surface that is hidden, so each object is also
rendered alone (the others are hidden from the render), which gives its full
footprint and its own depth under any occluder. An object that leaves no pixels
of its own (a wire-only mesh, say) falls back to projecting its vertices.

The host side is stdlib-only. Blender's bundled Python does the numeric work
through `blender_depth_kernels`, and the launch, sentinel and refusal plumbing
comes from `blender_common`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import blender_common  # noqa: E402
import blender_depth_kernels as depth_kernels  # noqa: E402
from blender_inspect_render import (  # noqa: E402
    DEFAULT_LENS_MM,
    DEFAULT_MARGIN,
    MAX_RESOLUTION,
    MIN_RESOLUTION,
    PRESETS,
    PROJECTIONS,
    RenderRequestError,
    check_options,
    resolve_views,
)

TOOL = "blender_depth_order"
TOOL_VERSION = "0.1.0"
SCHEMA = "depth-order-v1"

DEFAULT_RESOLUTION = 512
DEFAULT_TIMEOUT_SECONDS = 900
# Two depths closer than this fraction of the camera distance count as equal.
TIE_TOLERANCE_FRACTION = 1.0e-5

INTERPRETATION_LIMITS = [
    "Depth is planar: the distance along the camera's forward axis, in scene units, not the distance from the camera centre. `depth_range` spans all of an object's evaluated vertices, so it includes surfaces the camera cannot see.",
    "`depth_gap` is the median, over the pixels where two objects both cover the image, of the difference between their own nearest-surface depths; it is a surface separation, not the empty space between the objects. `range_gap` is the signed geometric gap (the back object's near depth minus the front object's far depth): negative means their depth ranges interleave, and a small object resting on a larger one has a negative `range_gap` and a small positive `depth_gap`.",
    "`front_fraction` is the share of overlap pixels where the front object is nearer. A value well below 1 means the two interleave or interpenetrate, and the single `front` label hides that.",
    "Overlap is measured between silhouettes at the render resolution (each object rendered alone), so it counts pixels that another object may hide in the full scene; `visible_pixels` is what is actually seen. Sub-pixel overlaps are not reported.",
    "Pixels come from EEVEE at one sample: an object whose material is transparent or whose settings keep it out of the depth pass can be under-measured, and the pair is then reported by its vertices if it leaves no pixels.",
    "A pair with `method: vertices` is an estimate from projected vertex bounding boxes and near depths, not a per-pixel measurement; its overlap is a bounding-box overlap and its front object is the one with the smaller near depth.",
    "Only render-visible mesh objects are considered; framing is sized to the geometry of objects that have faces, and a wire-only object may fall partly outside the image (`in_frame` is false).",
]


# Executed inside Blender's bundled Python after `blender_common`'s header
# (which supplies `emit`) and the `PARAMS` line from `build_probe_source`.
_PROBE_BODY = r'''
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, PARAMS["scripts_dir"])
import blender_render_kernels as K
import blender_depth_kernels as D

WIDTH, HEIGHT = PARAMS["width"], PARAMS["height"]
FAR_BACKGROUND = 1.0e8  # EEVEE writes 1e10 for pixels with no surface
r6 = D.r6


def walk(layer_coll, parent_ok, visit):
    ok = parent_ok and not layer_coll.exclude and not layer_coll.collection.hide_render
    visit(layer_coll, ok)
    for child in layer_coll.children:
        walk(child, ok, visit)


def render_visible_meshes(scene, view_layer):
    """Mesh objects a render of ``scene`` would show, by name."""
    in_scene = {o.name for o in scene.objects}
    names = set()

    def visit(layer_coll, ok):
        layer_coll.hide_viewport = False
        layer_coll.collection.hide_viewport = False
        if ok:
            names.update(o.name for o in layer_coll.collection.objects)

    walk(view_layer.layer_collection, True, visit)
    return sorted(
        name for name in names & in_scene
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


def object_points(names):
    """``({name: world vertices}, [names with faces])`` for the evaluated meshes.

    An object with faces contributes the vertices its faces use; a wire-only or
    loose-vertex object contributes all of its vertices.
    """
    for name in names:
        prepare_for_evaluation(bpy.data.objects[name])
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points, with_faces = {}, []
    for name in names:
        evaluated = bpy.data.objects[name].evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
            mesh.vertices.foreach_get("co", coords)
            coords = coords.reshape(-1, 3)
            if len(mesh.polygons):
                with_faces.append(name)
                loops = np.empty(len(mesh.loops), dtype=np.int64)
                mesh.loops.foreach_get("vertex_index", loops)
                coords = coords[np.unique(loops)]
            matrix = np.array(evaluated.matrix_world, dtype=np.float64)
            points[name] = coords @ matrix[:3, :3].T + matrix[:3, 3]
        finally:
            evaluated.to_mesh_clear()
    return points, with_faces


def configure_common(scene):
    scene.render.resolution_x = WIDTH
    scene.render.resolution_y = HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.use_stamp = False
    scene.render.use_sequencer = False
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"


def make_camera(scene, location, toward, right, up, framing):
    data = bpy.data.cameras.new("BlenderInspectDepthCameraData")
    data.sensor_fit = "AUTO"
    data.sensor_width = K.SENSOR_WIDTH_MM
    if PARAMS["projection"] == "orthographic":
        data.type = "ORTHO"
        data.ortho_scale = framing["ortho_scale"]
    else:
        data.type = "PERSP"
        data.lens = PARAMS["lens"]
    data.clip_start = framing["clip_start"]
    data.clip_end = framing["clip_end"]
    camera = bpy.data.objects.new("BlenderInspectDepthCamera", data)
    scene.collection.objects.link(camera)
    # A nested tuple is read column by column; only a Matrix takes rows.
    camera.matrix_world = Matrix(K.camera_matrix_rows(location, toward, right, up))
    scene.camera = camera
    bpy.context.view_layer.update()
    return camera, data


def teardown_camera(camera, data):
    bpy.data.objects.remove(camera, do_unlink=True)
    bpy.data.cameras.remove(data)


def camera_record(camera, data, distance):
    record = {
        "matrix_world": [[round(float(v), 9) for v in row] for row in camera.matrix_world],
        "projection": PARAMS["projection"],
        "distance_from_target": r6(distance),
        "sensor_width_mm": K.SENSOR_WIDTH_MM,
        "sensor_fit": "AUTO",
        "clip_start": r6(data.clip_start),
        "clip_end": r6(data.clip_end),
    }
    if PARAMS["projection"] == "orthographic":
        record["lens_mm"] = None
        record["ortho_scale"] = r6(data.ortho_scale)
        record["fov_x_degrees"] = record["fov_y_degrees"] = None
    else:
        tx, ty = K.half_fov_tangents(data.lens, WIDTH, HEIGHT)
        record["lens_mm"] = r6(data.lens)
        record["ortho_scale"] = None
        record["fov_x_degrees"] = r6(math.degrees(2 * math.atan(tx)))
        record["fov_y_degrees"] = r6(math.degrees(2 * math.atan(ty)))
    return record


def eevee_passes(scene, view_layer, wanted, samples):
    """Render with EEVEE and return ``{name: float32 array}`` for ``wanted``.

    ``wanted`` is a list of ``(render-layer output, item name, socket type)``.
    The passes go through a compositor File Output node to single-layer float
    EXRs, because a multilayer EXR cannot be read back into Blender.
    """
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = samples
    scene.render.use_compositing = True
    group = bpy.data.node_groups.new("BlenderInspectDepthComp", "CompositorNodeTree")
    scene.compositing_node_group = group
    tmp = tempfile.mkdtemp(prefix="blender_inspect_")
    try:
        layers = group.nodes.new("CompositorNodeRLayers")
        layers.update()
        output = group.nodes.new("CompositorNodeOutputFile")
        output.format.media_type = "IMAGE"
        output.format.file_format = "OPEN_EXR"
        output.format.color_depth = "32"
        output.format.exr_codec = "NONE"
        output.save_as_render = False
        output.directory = tmp
        output.file_name = "pass_"
        for source, name, socket in wanted:
            item = output.file_output_items.new(socket, name)
            item.save_as_render = False
            group.links.new(layers.outputs[source], output.inputs[name])
        bpy.ops.render.render()
        arrays = {}
        for _source, name, _socket in wanted:
            image = bpy.data.images.load(os.path.join(tmp, f"pass_{name}.exr"))
            try:
                image.colorspace_settings.name = "Non-Color"
                w, h = image.size
                channels = image.channels
                flat = np.empty(w * h * channels, dtype=np.float32)
                image.pixels.foreach_get(flat)
                arrays[name] = flat.reshape(h, w, channels)[::-1].copy()
            finally:
                bpy.data.images.remove(image)
        return arrays
    finally:
        scene.compositing_node_group = None
        bpy.data.node_groups.remove(group)
        shutil.rmtree(tmp, ignore_errors=True)


def render_alone(scene, view_layer, target, with_faces):
    """The ``(H, W)`` depth of ``target`` rendered by itself; NaN off its footprint."""
    for name in with_faces:
        bpy.data.objects[name].hide_render = name != target
    try:
        passes = eevee_passes(scene, view_layer, [("Depth", "Depth", "FLOAT"), ("Alpha", "Alpha", "FLOAT")], 1)
    finally:
        for name in with_faces:
            bpy.data.objects[name].hide_render = False
    depth = passes["Depth"][..., 0]
    covered = (passes["Alpha"][..., 0] > 0.5) & (depth < FAR_BACKGROUND)
    return np.where(covered, depth, np.nan).astype(np.float32)


def main():
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    visible = render_visible_meshes(scene, view_layer)
    points, with_faces = object_points(visible)
    views = PARAMS["views"]
    result = {
        "blender_version": ".".join(str(v) for v in bpy.app.version),
        "visible_objects": visible,
        "objects_with_faces": with_faces,
        "unit_system": scene.unit_settings.system,
        "unit_scale_length": r6(scene.unit_settings.scale_length),
    }
    if not with_faces:
        reason = "scene has no render-visible mesh geometry with faces"
        result["status"] = "RENDER_BLOCKED"
        result["reason"] = reason
        result["views"] = [
            {"name": v["name"], "status": "RENDER_BLOCKED", "reason": reason,
             "direction": v["direction"], "up": v["up"], "objects": [], "pairs": []}
            for v in views
        ]
        emit(result)
        return

    framed = np.concatenate([points[name] for name in with_faces])
    lo, hi = framed.min(axis=0), framed.max(axis=0)
    centre = (lo + hi) / 2.0
    extent = float(np.linalg.norm(framed - centre, axis=1).max())
    if extent <= 1e-9:
        raise RuntimeError("render-visible geometry has zero extent")
    orientations = [(v["direction"], v["up"]) for v in views]
    if PARAMS["projection"] == "orthographic":
        distance = extent * 3.0
        framing = {"ortho_scale": K.orthographic_scale(framed, centre, orientations, WIDTH, HEIGHT, PARAMS["margin"])}
    else:
        distance = K.perspective_distance(framed, centre, orientations, PARAMS["lens"], WIDTH, HEIGHT, PARAMS["margin"])
        framing = {}
    framing["clip_start"] = max((distance - extent) * 0.5, distance * 1e-4)
    framing["clip_end"] = (distance + extent) * 2.0
    tolerance = PARAMS["tie_tolerance_fraction"] * max(distance, 1.0)
    result["scene"] = {
        "bounds_min": [r6(v) for v in lo],
        "bounds_max": [r6(v) for v in hi],
        "center": [r6(v) for v in centre],
        "radius": r6(extent),
    }
    result["framing"] = {
        "locked": True,
        "camera_distance": r6(distance),
        "ortho_scale": r6(framing["ortho_scale"]) if "ortho_scale" in framing else None,
        "lens_mm": None if PARAMS["projection"] == "orthographic" else PARAMS["lens"],
        "tie_tolerance": tolerance,
    }

    configure_common(scene)
    view_layer.use_pass_z = True
    view_layer.use_pass_normal = False
    view_layer.use_pass_ambient_occlusion = False
    view_layer.use_pass_cryptomatte_object = True
    view_layer.pass_cryptomatte_depth = 2
    id_by_bits = {K.cryptomatte_id_bits(name): name for name in visible}

    results = []
    for view in views:
        toward, right, up = K.camera_basis(view["direction"], view["up"])
        location = [centre[i] + toward[i] * distance for i in range(3)]
        camera, data = make_camera(scene, location, toward, right, up, framing)
        try:
            model = D.camera_model(
                location, toward, right, up, PARAMS["projection"], WIDTH, HEIGHT,
                lens_mm=None if PARAMS["projection"] == "orthographic" else PARAMS["lens"],
                ortho_scale=framing.get("ortho_scale"),
            )
            entry = {
                "name": view["name"], "direction": view["direction"], "up": view["up"],
                "camera": camera_record(camera, data, distance),
            }
            combined = eevee_passes(
                scene, view_layer,
                [("Depth", "Depth", "FLOAT"), ("Alpha", "Alpha", "FLOAT"), ("CryptoObject00", "Crypto", "RGBA")], 1,
            )
            depth = combined["Depth"][..., 0]
            foreground = (combined["Alpha"][..., 0] > 0.5) & (depth < FAR_BACKGROUND)
            count = int(foreground.sum())
            entry["coverage"] = {"width": WIDTH, "height": HEIGHT, "foreground_pixels": count,
                                 "foreground_fraction": r6(count / (WIDTH * HEIGHT))}
            visible_counts = {name: 0 for name in visible}
            if count:
                ids = np.ascontiguousarray(combined["Crypto"][..., 0]).view(np.uint32)
                labels, unmatched = K.decode_object_ids(ids, foreground, id_by_bits)
                for name in visible:
                    visible_counts[name] = int((labels == name).sum())
                entry["coverage"]["unmatched_foreground_pixels"] = unmatched

            maps = {}
            objects = []
            for name in visible:
                obj_depth, obj_u, obj_v = D.project_points(points[name], model) if len(points[name]) else (np.zeros(0), np.zeros(0), np.zeros(0))
                record = {
                    "name": name, "method": "none",
                    "depth_range": D.depth_extent(obj_depth),
                    "screen_bbox": None, "screen_pixels": None,
                    "visible_pixels": None, "visible_fraction": None,
                    "in_frame": False,
                }
                vertex_bbox = D.bbox_from_uv(obj_u, obj_v)
                record["in_frame"] = D.bbox_in_frame(vertex_bbox)
                if record["depth_range"] is not None:
                    record["method"] = "vertices"
                    record["screen_bbox"] = vertex_bbox
                if name in with_faces and count:
                    alone = render_alone(scene, view_layer, name, with_faces)
                    mask = np.isfinite(alone)
                    pixels = int(mask.sum())
                    if pixels:
                        maps[name] = alone
                        record["method"] = "passes"
                        record["screen_bbox"] = D.bbox_from_mask(mask)
                        record["screen_pixels"] = pixels
                        record["visible_pixels"] = visible_counts[name]
                        record["visible_fraction"] = r6(visible_counts[name] / pixels)
                objects.append(record)
            by_name = {o["name"]: o for o in objects}
            pairs = []
            for i, name_a in enumerate(visible):
                for name_b in visible[i + 1:]:
                    pair = D.pair_record(
                        name_a, by_name[name_a], maps.get(name_a), name_b, by_name[name_b], maps.get(name_b), tolerance,
                    )
                    if pair is not None:
                        pairs.append(pair)
            pairs.sort(key=lambda p: (-(p["overlap_fraction_of_front"]), p["front"], p["back"]))
            entry["objects"] = objects
            entry["pairs"] = pairs
            if count:
                entry["status"] = "OK"
            else:
                entry["status"] = "RENDER_BLOCKED"
                entry["reason"] = "no geometry is visible in this view (it covers no pixels); objects are estimated from projected vertices"
            results.append(entry)
        finally:
            teardown_camera(camera, data)
    result["views"] = results
    blocked = [v["name"] for v in results if v["status"] == "RENDER_BLOCKED"]
    result["status"] = "RENDER_BLOCKED" if blocked else "OK"
    result["blocked_views"] = blocked
    emit(result)


main()
'''


def build_probe_source(params: dict) -> str:
    """The probe body with its parameters injected as a `PARAMS` dict."""
    return "import json\nPARAMS = json.loads(r'''" + json.dumps(params, sort_keys=True) + "''')\n" + _PROBE_BODY


def probe_params(views, projection, lens, width, height, margin) -> dict:
    return {
        "views": views,
        "scripts_dir": str(Path(__file__).resolve().parent),
        "projection": projection,
        "lens": float(lens),
        "width": int(width),
        "height": int(height),
        "margin": float(margin),
        "tie_tolerance_fraction": TIE_TOLERANCE_FRACTION,
    }


def run_depth_order(blender: str, blend: Path, params: dict, timeout: int):
    """Run the probe and return its block; raise RenderRequestError on any failure."""
    payload, error = blender_common.run_probe(blender, TOOL, build_probe_source(params), blend=blend, timeout=timeout)
    if payload is None:
        raise RenderRequestError(error)
    return payload


def add_summaries(views):
    """Give every pair in every view its plain-language ``summary`` line."""
    for view in views:
        for pair in view.get("pairs", []):
            pair["summary"] = depth_kernels.summary_line(view["name"], pair)
    return views


def build_payload(blend: Path, params: dict, render: dict) -> dict:
    views = add_summaries(render.pop("views"))
    return {
        "tool": TOOL,
        "version": TOOL_VERSION,
        "schema": SCHEMA,
        "parameters": {
            "blend": str(blend),
            "views": [v["name"] for v in params["views"]],
            "projection": params["projection"],
            "lens_mm": params["lens"] if params["projection"] == "perspective" else None,
            "width": params["width"],
            "height": params["height"],
            "margin": params["margin"],
        },
        **render,
        "views": views,
        "interpretation_limits": INTERPRETATION_LIMITS,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report per view which render-visible object is in front of which, with depth ranges, screen boxes, overlap fractions and depth gaps."
    )
    parser.add_argument("blend")
    parser.add_argument(
        "--views",
        nargs="+",
        required=True,
        help=f"one or more view sources: a preset name ({', '.join(PRESETS)}) or a render-views-v1 manifest file",
    )
    parser.add_argument("--projection", choices=PROJECTIONS, default="perspective")
    parser.add_argument("--lens", type=float, default=DEFAULT_LENS_MM, help=f"perspective focal length in mm (default {DEFAULT_LENS_MM:g}; ignored when orthographic)")
    parser.add_argument("--width", type=int, default=DEFAULT_RESOLUTION, help=f"render width in pixels, {MIN_RESOLUTION} to {MAX_RESOLUTION} (default {DEFAULT_RESOLUTION})")
    parser.add_argument("--height", type=int, default=DEFAULT_RESOLUTION, help=f"render height in pixels, {MIN_RESOLUTION} to {MAX_RESOLUTION} (default {DEFAULT_RESOLUTION})")
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN)
    blender_common.add_blender_arguments(parser, timeout=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    blend = Path(args.blend).resolve()
    reason = check_options(args)
    if reason:
        return blender_common.refuse(TOOL, reason)
    if not blend.is_file():
        return blender_common.refuse(TOOL, f"blend file not found: {blend}")
    try:
        views = resolve_views(args.views)
    except RenderRequestError as exc:
        return blender_common.refuse(TOOL, str(exc))
    blender, reason = blender_common.resolve_blender_executable(args.blender_executable)
    if blender is None:
        return blender_common.refuse(TOOL, reason)
    params = probe_params(views, args.projection, args.lens, args.width, args.height, args.margin)
    try:
        render = run_depth_order(blender, blend, params, args.timeout)
    except (OSError, ValueError) as exc:
        return blender_common.refuse(TOOL, str(exc))
    blender_common.write_payload(build_payload(blend, params, render))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
