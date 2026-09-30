#!/usr/bin/env python
"""Render a Blender scene in modes that reveal depth and surface defects.

For each view (a named 3/4 orbit preset or a `render-views-v1` manifest view)
one perspective or orthographic camera is placed, and every requested mode is
rendered from that same camera, so the images line up pixel for pixel:

- `matcap`: Workbench MATCAP light with ridge and valley cavity and backface
  culling on, so a flipped face renders as a hole that shows the interior.
- `depth`: the EEVEE Depth pass as a heat-map PNG with a printed near/far
  legend, the raw float32 `.npy`, and a multilayer `.exr`.
- `normal`: the EEVEE Normal pass as an RGB-encoded PNG and a float32 `.npy`.
- `ao`: the EEVEE ambient-occlusion pass as a greyscale PNG.
- `object-id`: a Cryptomatte-decoded colour mask PNG and a JSON colour map.

The host side is stdlib-only. Blender's bundled Python does the numeric work,
through `blender_render_kernels`, and the launch, sentinel and refusal plumbing
comes from `blender_common`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import blender_common  # noqa: E402
from blender_multiview_render import ViewManifestError, validate_view_manifest  # noqa: E402

TOOL = "blender_inspect_render"
TOOL_VERSION = "0.1.0"
SCHEMA = "inspect-render-v1"

MODES = ("matcap", "depth", "normal", "ao", "object-id")
PROJECTIONS = ("perspective", "orthographic")

DEFAULT_LENS_MM = 50.0
DEFAULT_MARGIN = 0.10
DEFAULT_RESOLUTION = 1024
DEFAULT_TIMEOUT_SECONDS = 600
MIN_RESOLUTION, MAX_RESOLUTION = 16, 8192

PRESET_ELEVATION_DEGREES = 30.0
WORLD_UP = [0.0, 0.0, 1.0]


def _orbit_direction(x_sign, y_sign):
    horizontal = math.cos(math.radians(PRESET_ELEVATION_DEGREES)) / math.sqrt(2.0)
    return [x_sign * horizontal, y_sign * horizontal, math.sin(math.radians(PRESET_ELEVATION_DEGREES))]


# Directions run from the subject to the camera, as in `render-views-v1`. A
# subject faces -Y, so "front" is -Y, and "left" is -X, the same convention as
# the manifest's `front_left` view ([-1, -1, 0]). Each preset is raised 30 degrees.
PRESETS = {
    "front-left-high": _orbit_direction(-1, -1),
    "front-right-high": _orbit_direction(1, -1),
    "back-left-high": _orbit_direction(-1, 1),
    "back-right-high": _orbit_direction(1, 1),
}

MATCAP_PREFERENCE = "clay_studio.exr"
CAVITY_RIDGE_FACTOR = 1.5
CAVITY_VALLEY_FACTOR = 1.5

INTERPRETATION_LIMITS = [
    "Depth is planar z-depth: the distance along the camera's forward axis to the visible surface, not the distance from the camera centre. It is in scene units.",
    "Normals are world-space, from EEVEE, which turns a back-facing normal toward the camera; a flipped face is therefore reliably revealed only by the matcap render, where backface culling turns it into a hole.",
    "Backface culling in the matcap render also hides any single-sided face seen from behind (an open surface, or a quad viewed from its back side), so compare it with the normal, depth and object-id renders, which draw both sides.",
    "Only render-visible mesh objects are framed and rendered by these modes; wire edges and loose vertices have no surface and do not appear.",
    "Locked framing uses one camera distance (or orthographic scale) for every view and mode, sized to the scene's render-visible geometry; it does not calibrate concept-art cameras.",
    "Render byte determinism is claimed only for the same scene, machine, and Blender install.",
]


class RenderRequestError(ValueError):
    """A request the tool refuses before it starts Blender."""


def resolve_views(values):
    """Turn ``--views`` values (preset names or manifest paths) into view dicts.

    A value that is a preset name is a preset; anything else must be a path to a
    `render-views-v1` manifest. View names must be unique across all values.
    """
    if not values:
        raise RenderRequestError("--views needs at least one preset name or manifest path")
    views = []
    for value in values:
        if value in PRESETS:
            manifest = {"schema": "render-views-v1", "views": [{"name": value, "direction": PRESETS[value], "up": WORLD_UP}]}
        else:
            path = Path(value)
            if not path.is_file():
                raise RenderRequestError(f"--views value is neither a preset ({', '.join(PRESETS)}) nor a manifest file: {value}")
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RenderRequestError(f"view manifest is not readable JSON: {path}: {exc}")
        try:
            views.extend(validate_view_manifest(manifest))
        except ViewManifestError as exc:
            raise RenderRequestError(f"{value}: {exc}")
    names = [view["name"] for view in views]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise RenderRequestError(f"duplicate view name: {', '.join(duplicates)}")
    return views


def check_options(args):
    """Refuse a bad option value before Blender is started; return the reason or None."""
    if not (isinstance(args.lens, float) and math.isfinite(args.lens) and args.lens > 0):
        return "--lens must be a positive number of millimetres"
    if not (math.isfinite(args.margin) and args.margin >= 0):
        return "--margin must be zero or positive"
    for label, value in (("--width", args.width), ("--height", args.height)):
        if not MIN_RESOLUTION <= value <= MAX_RESOLUTION:
            return f"{label} must be between {MIN_RESOLUTION} and {MAX_RESOLUTION}"
    return None


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
from mathutils import Matrix, Vector

sys.path.insert(0, PARAMS["scripts_dir"])
import blender_render_kernels as K

MODES = PARAMS["modes"]
OUT = Path(PARAMS["output_dir"])
WIDTH, HEIGHT = PARAMS["width"], PARAMS["height"]
FAR_BACKGROUND = 1.0e8  # EEVEE writes 1e10 for pixels with no surface


def r6(value):
    return round(float(value), 6)


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


def surface_points(names):
    """World-space vertices that belong to a face, over the named objects."""
    for name in names:
        prepare_for_evaluation(bpy.data.objects[name])
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    chunks = []
    with_faces = []
    for name in names:
        evaluated = bpy.data.objects[name].evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            if len(mesh.polygons) == 0:
                continue
            with_faces.append(name)
            coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
            mesh.vertices.foreach_get("co", coords)
            coords = coords.reshape(-1, 3)
            loops = np.empty(len(mesh.loops), dtype=np.int64)
            mesh.loops.foreach_get("vertex_index", loops)
            used = coords[np.unique(loops)]
            matrix = np.array(evaluated.matrix_world, dtype=np.float64)
            chunks.append(used @ matrix[:3, :3].T + matrix[:3, 3])
        finally:
            evaluated.to_mesh_clear()
    if not chunks:
        return np.zeros((0, 3)), with_faces
    return np.concatenate(chunks), with_faces


def configure_common(scene):
    scene.render.resolution_x = WIDTH
    scene.render.resolution_y = HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.use_stamp = False
    scene.render.use_sequencer = False
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"


def make_camera(scene, view, centre, distance, framing):
    toward, right, up = K.camera_basis(view["direction"], view["up"])
    location = [centre[i] + toward[i] * distance for i in range(3)]
    data = bpy.data.cameras.new("BlenderInspectRenderCameraData")
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
    camera = bpy.data.objects.new("BlenderInspectRenderCamera", data)
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
    group = bpy.data.node_groups.new("BlenderInspectComp", "CompositorNodeTree")
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


def save_multilayer_exr(scene, path):
    settings = scene.render.image_settings
    settings.media_type = "MULTI_LAYER_IMAGE"
    settings.file_format = "OPEN_EXR_MULTILAYER"
    settings.color_depth = "32"
    bpy.data.images["Render Result"].save_render(filepath=str(path))


def render_matcap(scene, camera, path):
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.use_compositing = False
    settings = scene.render.image_settings
    settings.media_type = "IMAGE"
    settings.file_format = "PNG"
    settings.color_mode = "RGBA"
    settings.color_depth = "8"
    settings.compression = 15
    scene.display.render_aa = "8"
    shading = scene.display.shading
    shading.type = "SOLID"
    shading.light = "MATCAP"
    shading.color_type = "SINGLE"
    shading.single_color = (0.8, 0.8, 0.8)
    shading.show_cavity = True
    shading.cavity_type = "BOTH"
    shading.cavity_ridge_factor = PARAMS["cavity_ridge_factor"]
    shading.cavity_valley_factor = PARAMS["cavity_valley_factor"]
    shading.curvature_ridge_factor = 0.0
    shading.curvature_valley_factor = 0.0
    shading.show_backface_culling = True
    shading.show_object_outline = False
    shading.show_shadows = False
    shading.show_specular_highlight = False
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def choose_matcap():
    matcaps = sorted(s.name for s in bpy.context.preferences.studio_lights if s.type == "MATCAP")
    if not matcaps:
        return None
    return PARAMS["matcap"] if PARAMS["matcap"] in matcaps else matcaps[0]


def main():
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    visible = render_visible_meshes(scene, view_layer)
    points, with_faces = surface_points(visible)
    blender_version = ".".join(str(v) for v in bpy.app.version)
    result = {
        "blender_version": blender_version,
        "visible_objects": visible,
        "objects_with_faces": with_faces,
        "unit_system": scene.unit_settings.system,
        "unit_scale_length": r6(scene.unit_settings.scale_length),
    }
    views = PARAMS["views"]
    if len(points) == 0:
        reason = "scene has no render-visible mesh geometry with faces"
        result["status"] = "RENDER_BLOCKED"
        result["reason"] = reason
        result["views"] = [
            {"name": v["name"], "status": "RENDER_BLOCKED", "reason": reason,
             "direction": v["direction"], "up": v["up"], "files": {}}
            for v in views
        ]
        emit(result)
        return

    lo, hi = points.min(axis=0), points.max(axis=0)
    centre = (lo + hi) / 2.0
    extent = float(np.linalg.norm(points - centre, axis=1).max())
    if extent <= 1e-9:
        raise RuntimeError("render-visible geometry has zero extent")
    pairs = [(v["direction"], v["up"]) for v in views]
    if PARAMS["projection"] == "orthographic":
        distance = extent * 3.0
        framing = {"ortho_scale": K.orthographic_scale(points, centre, pairs, WIDTH, HEIGHT, PARAMS["margin"])}
        nearest = distance - extent
    else:
        distance = K.perspective_distance(points, centre, pairs, PARAMS["lens"], WIDTH, HEIGHT, PARAMS["margin"])
        framing = {}
        nearest = distance - extent
    framing["clip_start"] = max(nearest * 0.5, distance * 1e-4)
    framing["clip_end"] = (distance + extent) * 2.0
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
    }

    configure_common(scene)
    OUT.mkdir(parents=True, exist_ok=True)
    view_layer.use_pass_z = True
    view_layer.use_pass_normal = "normal" in MODES
    view_layer.use_pass_cryptomatte_object = "object-id" in MODES
    view_layer.pass_cryptomatte_depth = 2
    view_layer.use_pass_ambient_occlusion = False

    matcap_name = choose_matcap() if "matcap" in MODES else None
    if "matcap" in MODES:
        if matcap_name is None:
            raise RuntimeError("no MATCAP studio light is available")
        # The studio_light enum lists the matcaps only once the light is MATCAP.
        scene.display.shading.light = "MATCAP"
        scene.display.shading.studio_light = matcap_name
        result["matcap"] = {
            "studio_light": matcap_name,
            "cavity_type": "BOTH",
            "cavity_ridge_factor": PARAMS["cavity_ridge_factor"],
            "cavity_valley_factor": PARAMS["cavity_valley_factor"],
            "backface_culling": True,
        }
    id_by_bits = {K.cryptomatte_id_bits(name): name for name in visible}
    palette = K.object_palette(visible)

    results = []
    for view in views:
        camera, data = make_camera(scene, view, centre, distance, framing)
        try:
            entry = {
                "name": view["name"], "direction": view["direction"], "up": view["up"],
                "camera": camera_record(camera, data, distance), "files": {}, "modes": {},
            }
            wanted = [("Depth", "Depth", "FLOAT"), ("Alpha", "Alpha", "FLOAT")]
            if "normal" in MODES:
                wanted.append(("Normal", "Normal", "VECTOR"))
            if "object-id" in MODES:
                wanted.append(("CryptoObject00", "Crypto", "RGBA"))
            passes = eevee_passes(scene, view_layer, wanted, 1)
            depth = passes["Depth"][..., 0]
            foreground = (passes["Alpha"][..., 0] > 0.5) & (depth < FAR_BACKGROUND)
            count = int(foreground.sum())
            entry["coverage"] = {
                "width": WIDTH, "height": HEIGHT, "foreground_pixels": count,
                "foreground_fraction": r6(count / (WIDTH * HEIGHT)),
                "background_fraction": r6(1.0 - count / (WIDTH * HEIGHT)),
            }
            if count == 0:
                entry["status"] = "RENDER_BLOCKED"
                entry["reason"] = "no geometry is visible in this view (it covers no pixels)"
                entry["depth_range"] = None
                results.append(entry)
                continue
            near, far = float(depth[foreground].min()), float(depth[foreground].max())
            entry["status"] = "RENDERED"
            entry["depth_range"] = {
                "near": r6(near), "far": r6(far), "unit": "scene units",
                "kind": "planar z-depth along the camera forward axis, to the visible surface",
            }
            name = view["name"]

            if "depth" in MODES:
                clean = np.where(foreground, depth, np.nan).astype(np.float32)
                npy = OUT / f"{name}_depth.npy"
                np.save(str(npy), clean)
                png = OUT / f"{name}_depth.png"
                K.write_png(str(png), K.depth_heatmap(depth, foreground, near, far))
                exr = OUT / f"{name}_depth.exr"
                save_multilayer_exr(scene, exr)
                entry["files"]["depth"] = {"png": str(png), "npy": str(npy), "exr": str(exr)}
                entry["modes"]["depth"] = {
                    "legend": K.depth_legend(r6(near), r6(far)),
                    "npy_shape": [HEIGHT, WIDTH],
                    "npy_background": "NaN",
                    "png_legend_strip_rows": K.LEGEND_STRIP_ROWS,
                    "png_size": [WIDTH, HEIGHT + K.LEGEND_STRIP_ROWS],
                }
            if "normal" in MODES:
                normals = passes["Normal"][..., :3]
                clean = np.where(foreground[..., None], normals, np.nan).astype(np.float32)
                npy = OUT / f"{name}_normal.npy"
                np.save(str(npy), clean)
                png = OUT / f"{name}_normal.png"
                K.write_png(str(png), K.normal_image(normals, foreground))
                entry["files"]["normal"] = {"png": str(png), "npy": str(npy)}
                entry["modes"]["normal"] = {"space": "world", "encoding": "rgb = (n + 1) / 2", "npy_background": "NaN"}
            if "object-id" in MODES:
                ids = np.ascontiguousarray(passes["Crypto"][..., 0]).view(np.uint32)
                labels, unmatched = K.decode_object_ids(ids, foreground, id_by_bits)
                image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
                counts = {}
                for obj_name in visible:
                    hit = labels == obj_name
                    counts[obj_name] = int(hit.sum())
                    image[hit] = palette[obj_name]
                png = OUT / f"{name}_object-id.png"
                K.write_png(str(png), image)
                mapping = {
                    "schema": "object-id-map-v1", "view": name, "background_rgb": [0, 0, 0],
                    "unmatched_foreground_pixels": unmatched,
                    "objects": [
                        {"name": obj_name, "rgb": list(palette[obj_name]), "pixels": counts[obj_name]}
                        for obj_name in visible
                    ],
                }
                mapping_path = OUT / f"{name}_object-id.json"
                mapping_path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")
                entry["files"]["object-id"] = {"png": str(png), "json": str(mapping_path)}
                entry["modes"]["object-id"] = {
                    "method": "cryptomatte", "background_rgb": [0, 0, 0],
                    "unmatched_foreground_pixels": unmatched, "objects": mapping["objects"],
                }
            if "ao" in MODES:
                view_layer.use_pass_ambient_occlusion = True
                view_layer.eevee.ambient_occlusion_distance = max(extent * 0.15, 1e-3)
                ao = eevee_passes(scene, view_layer, [("Ambient Occlusion", "AO", "FLOAT")], PARAMS["ao_samples"])["AO"][..., 0]
                view_layer.use_pass_ambient_occlusion = False
                png = OUT / f"{name}_ao.png"
                K.write_png(str(png), K.gray_image(ao, foreground))
                entry["files"]["ao"] = {"png": str(png)}
                entry["modes"]["ao"] = {"distance": r6(max(extent * 0.15, 1e-3)), "samples": PARAMS["ao_samples"]}
            if "matcap" in MODES:
                png = OUT / f"{name}_matcap.png"
                render_matcap(scene, camera, png)
                entry["files"]["matcap"] = {"png": str(png)}
            results.append(entry)
        finally:
            teardown_camera(camera, data)
    result["views"] = results
    blocked = [v["name"] for v in results if v["status"] == "RENDER_BLOCKED"]
    result["status"] = "RENDER_BLOCKED" if blocked else "RENDERED"
    result["blocked_views"] = blocked
    emit(result)


main()
'''


def build_probe_source(params: dict) -> str:
    """The probe body with its parameters injected as a `PARAMS` dict."""
    return "import json\nPARAMS = json.loads(r'''" + json.dumps(params, sort_keys=True) + "''')\n" + _PROBE_BODY


def probe_params(views, modes, output_dir: Path, projection, lens, width, height, margin) -> dict:
    return {
        "views": views,
        "modes": list(modes),
        "output_dir": str(Path(output_dir).resolve()),
        "scripts_dir": str(Path(__file__).resolve().parent),
        "projection": projection,
        "lens": float(lens),
        "width": int(width),
        "height": int(height),
        "margin": float(margin),
        "matcap": MATCAP_PREFERENCE,
        "cavity_ridge_factor": CAVITY_RIDGE_FACTOR,
        "cavity_valley_factor": CAVITY_VALLEY_FACTOR,
        "ao_samples": 32,
    }


def run_render(blender: str, blend: Path, params: dict, timeout: int):
    """Run the probe and return its block, with the matcap PNGs' metadata stripped."""
    Path(params["output_dir"]).mkdir(parents=True, exist_ok=True)
    payload, error = blender_common.run_probe(blender, TOOL, build_probe_source(params), blend=blend, timeout=timeout)
    if payload is None:
        raise RenderRequestError(error)
    for view in payload.get("views", []):
        for kinds in view.get("files", {}).values():
            for key, value in kinds.items():
                if not Path(value).is_file():
                    raise RenderRequestError(f"Blender reported a missing file: {value}")
        matcap = view.get("files", {}).get("matcap")
        if matcap:
            blender_common.strip_png_metadata(matcap["png"])
    return payload


def build_payload(blend: Path, params: dict, render: dict) -> dict:
    views = render.pop("views")
    return {
        "tool": TOOL,
        "version": TOOL_VERSION,
        "schema": SCHEMA,
        "parameters": {
            "blend": str(blend),
            "modes": params["modes"],
            "views": [v["name"] for v in params["views"]],
            "projection": params["projection"],
            "lens_mm": params["lens"] if params["projection"] == "perspective" else None,
            "width": params["width"],
            "height": params["height"],
            "margin": params["margin"],
            "output_dir": params["output_dir"],
        },
        **render,
        "views": views,
        "interpretation_limits": INTERPRETATION_LIMITS,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render a Blender scene in depth-revealing modes from locked perspective or orthographic cameras.")
    parser.add_argument("blend")
    parser.add_argument(
        "--views",
        nargs="+",
        required=True,
        help=f"one or more view sources: a preset name ({', '.join(PRESETS)}) or a render-views-v1 manifest file",
    )
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES), help="modes to render (default: all)")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--projection", choices=PROJECTIONS, default="perspective")
    parser.add_argument("--lens", type=float, default=DEFAULT_LENS_MM, help=f"perspective focal length in mm (default {DEFAULT_LENS_MM:g}; ignored when orthographic)")
    parser.add_argument("--width", type=int, default=DEFAULT_RESOLUTION)
    parser.add_argument("--height", type=int, default=DEFAULT_RESOLUTION)
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
    modes = [mode for mode in MODES if mode in args.modes]
    params = probe_params(views, modes, Path(args.output_dir), args.projection, args.lens, args.width, args.height, args.margin)
    try:
        render = run_render(blender, blend, params, args.timeout)
    except (OSError, ValueError) as exc:
        return blender_common.refuse(TOOL, str(exc))
    blender_common.write_payload(build_payload(blend, params, render))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
