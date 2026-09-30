#!/usr/bin/env python
"""Render an arbitrary, locked-framing Blender view manifest in one headless run.

The canonical copy of pil-agent-plugin's `pil_multiview_render`: same CLI, same
payload shape, `tool` renamed to `blender_multiview_render`. The launch,
sentinel and refusal plumbing comes from `blender_common`, and PNG metadata is
stripped with its stdlib chunk filter instead of a Pillow re-save, so the
host side needs no third-party package.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import blender_common  # noqa: E402

TOOL = "blender_multiview_render"
TOOL_VERSION = "0.1.0"


class ViewManifestError(ValueError):
    pass


def _vector(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise ViewManifestError(f"{label} must be a 3-vector")
    vector = [float(v) for v in value]
    length = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(length) or length <= 1e-9:
        raise ViewManifestError(f"{label} must be finite and non-zero")
    return [v / length for v in vector]


def validate_view_manifest(manifest: dict) -> list[dict]:
    if not isinstance(manifest, dict) or manifest.get("schema") != "render-views-v1":
        raise ViewManifestError("manifest schema must be 'render-views-v1'")
    raw_views = manifest.get("views")
    if not isinstance(raw_views, list) or not raw_views:
        raise ViewManifestError("manifest requires a non-empty views array")
    names = set()
    views = []
    for raw in raw_views:
        if not isinstance(raw, dict):
            raise ViewManifestError("each view must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ViewManifestError("view names must match [A-Za-z0-9_-]+")
        if name in names:
            raise ViewManifestError(f"duplicate view name: {name}")
        names.add(name)
        direction = _vector(raw.get("direction"), f"view {name!r} direction")
        up = _vector(raw.get("up", [0, 0, 1]), f"view {name!r} up")
        cross = [
            direction[1] * up[2] - direction[2] * up[1],
            direction[2] * up[0] - direction[0] * up[2],
            direction[0] * up[1] - direction[1] * up[0],
        ]
        if math.sqrt(sum(v * v for v in cross)) <= 1e-6:
            raise ViewManifestError(f"view {name!r} direction and up are parallel")
        views.append({"name": name, "direction": direction, "up": up})
    return views


# Executed inside Blender's bundled Python after `blender_common`'s header
# (which supplies `emit`) and the `PARAMS` line from `build_probe_source`.
_PROBE_BODY = r'''
from pathlib import Path

import bpy
from mathutils import Matrix, Vector


def bbox_points():
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    names = []
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.hide_render:
            continue
        names.append(obj.name)
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            points.extend(evaluated.matrix_world @ vertex.co for vertex in mesh.vertices)
        finally:
            evaluated.to_mesh_clear()
    return points, sorted(names)


def basis(view):
    direction = Vector(view["direction"]).normalized()
    up_hint = Vector(view["up"]).normalized()
    right = direction.cross(up_hint).normalized()
    up = right.cross(direction).normalized()
    return direction, right, up


def main():
    points, object_names = bbox_points()
    if not points:
        emit({"status": "RENDER_BLOCKED", "reason": "scene has no render-visible mesh geometry", "views": []})
        return
    center = sum(points, Vector()) / len(points)
    spans = {}
    for view in PARAMS["views"]:
        _direction, right, up = basis(view)
        horizontal = [point.dot(right) for point in points]
        vertical = [point.dot(up) for point in points]
        spans[view["name"]] = [max(horizontal) - min(horizontal), max(vertical) - min(vertical)]
    aspect = PARAMS["width"] / PARAMS["height"]
    scales = {}
    for name, (width, height) in spans.items():
        scales[name] = max(height, width / aspect) * (1.0 + PARAMS["margin"])
    locked_scale = max(scales.values()) if PARAMS["lock_framing"] else None

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = PARAMS["width"]
    scene.render.resolution_y = PARAMS["height"]
    scene.render.resolution_percentage = 100
    scene.render.use_stamp = False
    scene.render.use_compositing = False
    scene.render.use_sequencer = False
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 15
    shading = scene.display.shading
    shading.type = "SOLID"
    shading.light = "STUDIO" if PARAMS["mode"] == "beauty" else "FLAT"
    shading.show_shadows = PARAMS["mode"] == "beauty"
    shading.show_cavity = PARAMS["mode"] == "beauty"
    shading.show_object_outline = False
    shading.show_specular_highlight = PARAMS["mode"] == "beauty"
    shading.color_type = "SINGLE" if PARAMS["mode"] == "silhouette" else "TEXTURE"
    if PARAMS["mode"] == "silhouette":
        shading.single_color = (1.0, 1.0, 1.0)
    scene.display.viewport_aa = "FXAA"

    extent = max((point - center).length for point in points)
    distance = extent * 4.0 + 10.0
    results = []
    for view in PARAMS["views"]:
        direction, right, up = basis(view)
        location = center + direction * distance
        camera_data = bpy.data.cameras.new("BlenderInspectMultiViewCameraData")
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = locked_scale if locked_scale is not None else scales[view["name"]]
        camera_data.clip_start = 0.001
        camera_data.clip_end = distance * 8.0 + 1000.0
        camera = bpy.data.objects.new("BlenderInspectMultiViewCamera", camera_data)
        bpy.context.collection.objects.link(camera)
        forward = (center - location).normalized()
        corrected_right = forward.cross(up).normalized()
        corrected_up = corrected_right.cross(forward).normalized()
        # A nested tuple is read column by column; only a Matrix takes rows.
        camera.matrix_world = Matrix((
            (corrected_right.x, corrected_up.x, -forward.x, location.x),
            (corrected_right.y, corrected_up.y, -forward.y, location.y),
            (corrected_right.z, corrected_up.z, -forward.z, location.z),
            (0.0, 0.0, 0.0, 1.0),
        ))
        scene.camera = camera
        output = str(Path(PARAMS["output_dir"]) / (view["name"] + ".png"))
        scene.render.filepath = output
        bpy.ops.render.render(write_still=True)
        results.append({
            "name": view["name"],
            "path": output,
            "direction": view["direction"],
            "up": view["up"],
            "ortho_scale": float(camera_data.ortho_scale),
        })
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(camera_data)
    emit({
        "status": "RENDERED",
        "blender_version": ".".join(str(value) for value in bpy.app.version),
        "visible_objects": object_names,
        "locked_framing": bool(PARAMS["lock_framing"]),
        "views": results,
    })


main()
'''

INTERPRETATION_LIMITS = [
    "Locked framing makes silhouette scale comparable across views but does not calibrate concept-art cameras.",
    "Render byte determinism is claimed only for the same scene, machine, and Blender install.",
]


def build_probe_source(views, output_dir: Path, width: int, height: int, margin: float, mode: str, lock_framing: bool) -> str:
    """The probe body with its parameters injected as a `PARAMS` dict."""
    params = {
        "views": views,
        "output_dir": str(output_dir.resolve()),
        "width": int(width),
        "height": int(height),
        "margin": float(margin),
        "mode": mode,
        "lock_framing": bool(lock_framing),
    }
    return "import json\nPARAMS = json.loads(r'''" + json.dumps(params, sort_keys=True) + "''')\n" + _PROBE_BODY


def render_manifest(blender: str, blend: Path, views: list[dict], output_dir: Path, width=1024, height=1024, margin=0.1, mode="analysis", lock_framing=True, timeout=300):
    """Render every view in one Blender run; return the probe's render block."""
    output_dir.mkdir(parents=True, exist_ok=True)
    body = build_probe_source(views, output_dir, width, height, margin, mode, lock_framing)
    payload, error = blender_common.run_probe(blender, TOOL, body, blend=blend, timeout=timeout)
    if payload is None:
        raise ViewManifestError(error)
    if payload.get("status") == "RENDERED":
        for view in payload["views"]:
            path = Path(view["path"])
            if not path.is_file():
                raise ViewManifestError(f"Blender reported a missing render: {path}")
            blender_common.strip_png_metadata(path)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render arbitrary orthographic Blender views with optional locked framing.")
    parser.add_argument("blend")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--margin", type=float, default=0.1)
    parser.add_argument("--mode", choices=("analysis", "beauty", "silhouette"), default="analysis")
    parser.add_argument("--independent-framing", action="store_true")
    blender_common.add_blender_arguments(parser)
    args = parser.parse_args(argv)
    blend = Path(args.blend).resolve()
    manifest_path = Path(args.manifest).resolve()
    blender, reason = blender_common.resolve_blender_executable(args.blender_executable)
    if not blend.is_file():
        return blender_common.refuse(TOOL, f"blend file not found: {blend}")
    if not manifest_path.is_file():
        return blender_common.refuse(TOOL, f"manifest not found: {manifest_path}")
    if blender is None:
        return blender_common.refuse(TOOL, reason)
    try:
        views = validate_view_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
        render = render_manifest(blender, blend, views, Path(args.output_dir), args.width, args.height, args.margin, args.mode, not args.independent_framing, timeout=args.timeout)
    except (OSError, ValueError, ViewManifestError) as exc:
        return blender_common.refuse(TOOL, str(exc))
    payload = {
        "tool": TOOL,
        "version": TOOL_VERSION,
        "parameters": {"blend": str(blend), "manifest": str(manifest_path), "mode": args.mode, "width": args.width, "height": args.height, "margin": args.margin},
        "render": render,
        "interpretation_limits": INTERPRETATION_LIMITS,
    }
    blender_common.write_payload(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
