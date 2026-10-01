#!/usr/bin/env python
"""Matched-view Blender Workbench render, optionally 1:1 compared to a reference.

The canonical copy of pil-agent-plugin's `pil_blender_render`: same CLI, same
payload shape, `tool` renamed to `blender_render`. The launch, sentinel and
refusal plumbing comes from `blender_common`, and the host side is stdlib
only: Blender itself reads the reference's pixel size, and PNG metadata is
stripped by `blender_common`'s chunk filter.

Headless Blender renders one named view (front, side, back) of a `.blend` scene
using the Workbench engine on CPU. The camera is orthographic and auto-framed
from the world-space axis-aligned bounding box of every mesh in the scene,
using the same box computation `blender_mesh.py`'s probe emits (duplicated
inside this tool's own embedded Blender-side script because `bpy` runs in a
separate interpreter and cannot import across that boundary). With
`--reference REF.png`, the render's pixel dimensions are pinned to REF's own
dimensions (so `pil_structure_diff --foreground`'s
`aspect_ratio_mismatch`/`resolution_mismatch` cannot fire from a framing
difference) and a `pil_structure_diff --foreground` subprocess call is folded
into the payload under `comparison`.

`pil_structure_diff.py` belongs to pil-agent-plugin. It is found through the
`BLENDER_INSPECT_STRUCTURE_DIFF` environment variable (a path to the script),
then at `scripts/pil_structure_diff.py` in the repository that holds both
plugins. When neither exists the render is still published, pinned to the
reference's size, and `comparison` is a refusal naming the missing tool. A
structure diff that is found but fails is a rejection, as before.

Refusal, not warping. A scene with no mesh geometry, or a reference that yields
`foreground_mask_empty`/`foreground_too_small` on either side (in the diff's
own images.a.flags / images.b.flags), is reported as `comparison.refused=true`
with numeric fields nulled -- never as a fabricated similarity score. Missing
Blender, missing .blend, or missing reference file is a rejection (exit 2,
byte-empty stdout, one-line stderr, no traceback).

The determinism claim is scoped: two renders of the same (.blend, view) pair
on this machine, this install, produce byte-identical PNGs. Cross-machine or
cross-install byte-identical rendering is NOT claimed. Comparison metrics on a
fixed image pair remain fully deterministic everywhere, as they do for every
other tool in this repository.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import blender_common  # noqa: E402
from blender_common import resolve_blender_executable  # noqa: E402

TOOL = "blender_render"
TOOL_VERSION = "0.1.0"

STRUCTURE_DIFF_ENV = "BLENDER_INSPECT_STRUCTURE_DIFF"
# plugins/blender-inspect/scripts/ -> the repository root holding both plugins.
REPO_STRUCTURE_DIFF = Path(__file__).resolve().parents[3] / "scripts" / "pil_structure_diff.py"

# Named view → which world axis the camera sits on relative to the character.
# Verified against the brute corpus's own render-visible bbox (16 meshes with
# hide_render=False after filtering out the "Static" donor/reference geometry):
# X range 2.365 (T-pose shoulders), Y range 0.94 (cape extends to +Y), Z range
# 2.14 (feet at Z=0, head at Z=2.14). This is Blender's standard Z-up
# convention -- character stands along +Z, character faces -Y (cape hangs
# behind at +Y), character's right hand extends to +X.
#
# Views are constructed via look_at_matrix() at render time -- a target-pointed
# camera at the named world-axis position with +Z as world up. This is far more
# robust than hand-rolled Euler angles, and swaps trivially if a future corpus
# uses a different up axis (change WORLD_UP + camera locations, not rotation
# arithmetic).
#
# Caller responsibility: match the physical side your reference image shows to
# this tool's "side" definition. A wearer's-right reference against a
# wearer's-left render will not fire the framing flags (those depend only on
# image size), but structural_similarity will drop; the axis label is real,
# grade against it before trusting a comparison.
WORLD_UP = (0.0, 0.0, 1.0)
VIEW_CAMERAS = {
    # front: camera in front of the character (at -Y, looking +Y)
    "front": {"camera_axis": "-Y", "description": "camera at -Y looking +Y (faces character's face)"},
    # back: camera behind the character (at +Y, looking -Y)
    "back": {"camera_axis": "+Y", "description": "camera at +Y looking -Y (faces character's back/cape)"},
    # side: camera at wearer's right (at +X, looking -X)
    "side": {"camera_axis": "+X", "description": "camera at +X looking -X (wearer's-right profile)"},
}

# 10% margin around the auto-framed bounding box, so the character does not
# touch the frame edge in any view.
DEFAULT_MARGIN = 0.10

# Default render resolution when --reference is NOT provided. When --reference
# is provided, the render dimensions are pinned to the reference's own size --
# see the docstring and INTERPRETATION_LIMITS for why.
DEFAULT_RESOLUTION = 1024

INTERPRETATION_LIMITS = [
    "Render determinism is scoped: two renders of the same (.blend, view) "
    "pair on this machine, this Blender install, produce byte-identical PNGs. "
    "Cross-machine or cross-install byte-identical rendering is NOT claimed -- "
    "Blender version, OS PNG codec, and Workbench studio-light bundling can "
    "all shift bytes across installs. Comparison metrics on a fixed image "
    "pair remain fully deterministic everywhere, as they do for every other "
    "tool in this repository -- what is scoped is only the render step that "
    "produces one side of the pair.",
    "Camera convention: the scene is treated as standard Z-up (character "
    "stands along +Z, world +Z is up) with the character facing -Y. 'front' "
    "puts an orthographic camera at -Y looking +Y (aimed at the character's "
    "face); 'back' at +Y looking -Y (aimed at the back/cape); 'side' at +X "
    "looking -X (wearer's-right profile -- camera on the wearer's right hand "
    "side, looking across the body). Verified empirically against the brute "
    "corpus: after filtering hide_render=True donor meshes, the visible "
    "geometry has X range 2.37 (T-pose shoulders), Y range 0.94 (cape at "
    "+Y), Z range 2.14 (feet Z=0 to head Z=2.14). If a scene is authored "
    "with a different up axis or facing direction, front/back/side will not "
    "match the caller's expectation -- verify against a known-good reference "
    "before trusting the labelling. The framing flags "
    "(aspect_ratio_mismatch, resolution_mismatch) cannot catch this because "
    "they depend only on image size.",
    "The auto-frame reads world-space vertex positions from the DEPSGRAPH-"
    "EVALUATED mesh of every render-visible object (hide_render=False), "
    "computes the axis-aligned bounding box, then fits it into the render "
    "frame with a 10% margin. hide_render=True meshes (donor/reference "
    "geometry) are excluded -- otherwise their transforms can dominate the "
    "bbox and shrink the visible character to a speck. Orthographic "
    "projection means no perspective foreshortening.",
    "When aspect_ratio_mismatch or resolution_mismatch appears in the diff's "
    "own flags on a non-refused comparison, those flags describe framing and "
    "pose -- not the model. This is quoting the field trial's own finding "
    "(a T-pose render vs an A-pose reference scored structural_similarity "
    "0.900 with both flags firing, correctly discarded). --reference pins "
    "the render size to the reference's size, so these two flags should not "
    "fire from this tool's own output.",
    "foreground_source_mismatch is EXPECTED in comparison.diff_flags whenever "
    "the render has an alpha channel (film_transparent=True, which this tool "
    "sets unconditionally) and the reference does not. The render's alpha is "
    "true coverage information; the reference's foreground is derived from "
    "its own border-median colour. Both are valid definitions of foreground; "
    "they are just not the same one. The flag surfaces the source difference "
    "for honest reporting -- it does not cause refusal, and comparison "
    "metrics remain deterministic. Callers who want to eliminate this flag "
    "can render their references with matching alpha, or accept the small "
    "colour-comparison drift it signals.",
    "Refusal semantics: a scene with no mesh geometry, or a comparison "
    "whose diff reports foreground_mask_empty or foreground_too_small on "
    "either image (in images.a.flags or images.b.flags), is reported as "
    "comparison.refused=true with numeric fields nulled -- never as a "
    "fabricated similarity number. Missing Blender, missing .blend, or a "
    "missing reference file is a rejection (exit 2, empty stdout, stderr "
    "reason). This mirrors pil_contract_verdict's UNMEASURABLE pattern at "
    "the render-orchestration layer.",
]


# The Blender-side probe. Runs in Blender's bundled Python (5.x, `bpy` and
# `mathutils` present, no plugin venv). Runs after `blender_common`'s header,
# which supplies `emit`, and reads parameters from an injected PARAMS dict
# (see build_probe_source).
_PROBE_BODY = r'''
import math

import bpy
import mathutils


def gather_bbox():
    """World-space AA bbox over every RENDER-VISIBLE mesh, depsgraph-evaluated.

    Same spirit as blender_mesh's gather() -- world-space, axis-aligned,
    every mesh -- but with two additions the render use case forces:

    *   `hide_render=True` meshes are EXCLUDED. In the brute corpus, the
        "Static" donor/reference meshes are hidden from render and sit at a
        different scale from the visible character; including them would
        dominate the bbox and shrink the visible character to a single-pixel
        speck in the final frame (measured: 8x3 pixels of visible signal at
        ortho_scale 273 before this filter landed).
    *   Vertex positions read from the DEPSGRAPH-EVALUATED mesh
        (`obj.evaluated_get(depsgraph).to_mesh()`) rather than `obj.bound_box`
        directly, so modifiers and armature deform contribute correctly to
        the render-visible bounds. For the brute .blend the two happen to
        agree numerically, but a rigged character with active shape-key
        drivers or subdivision would diverge.

    blender_mesh's own gather() does neither of these because it reports
    mesh-datablock statistics for verdict predicates, not a render frame -- a
    hidden mesh is still a real mesh, and its unshaped topology is what
    `geometry.poly_count.*` asks about.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    xs, ys, zs = [], [], []
    visible_names = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if obj.hide_render:
            continue
        visible_names.append(obj.name)
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        mat = eval_obj.matrix_world
        try:
            for vertex in mesh.vertices:
                world = mat @ vertex.co
                xs.append(world.x)
                ys.append(world.y)
                zs.append(world.z)
        finally:
            eval_obj.to_mesh_clear()
    if not xs:
        return None
    return {
        "min": [float(min(xs)), float(min(ys)), float(min(zs))],
        "max": [float(max(xs)), float(max(ys)), float(max(zs))],
        "visible_mesh_count": len(visible_names),
    }


def look_at_matrix(camera_location, target, world_up):
    """4x4 world matrix for a camera looking from `camera_location` at `target`.

    Blender camera convention: local -Z points forward (toward the subject),
    local +Y points up in view. This function constructs a right-handed basis
    aligned with those conventions and world_up, then places it at
    camera_location. Robust across scenes with different up axes without any
    Euler-angle bookkeeping.

    world_up is normalised and MUST NOT be parallel to (target - camera_location);
    the caller is responsible for placing the camera off the up axis.
    """
    location = mathutils.Vector(camera_location)
    target = mathutils.Vector(target)
    up = mathutils.Vector(world_up).normalized()
    forward = (target - location).normalized()
    right = forward.cross(up).normalized()
    corrected_up = right.cross(forward).normalized()
    # Columns are camera-local +X, +Y, +Z expressed in world.
    return mathutils.Matrix((
        (right.x, corrected_up.x, -forward.x, location.x),
        (right.y, corrected_up.y, -forward.y, location.y),
        (right.z, corrected_up.z, -forward.z, location.z),
        (0.0, 0.0, 0.0, 1.0),
    ))


def main():
    view = PARAMS["view"]
    out_path = PARAMS["out_path"]
    width = int(PARAMS["width"])
    height = int(PARAMS["height"])

    # With a reference, the render is pinned to the reference's own pixel
    # size. Blender reads it, so the host side needs no image library.
    reference_size = None
    if PARAMS["reference"] is not None:
        try:
            image = bpy.data.images.load(PARAMS["reference"], check_existing=False)
            reference_size = [int(image.size[0]), int(image.size[1])]
            bpy.data.images.remove(image)
        except RuntimeError as exc:
            emit({"reference_error": " ".join(str(exc).split())})
            return
        if reference_size[0] <= 0 or reference_size[1] <= 0:
            emit({"reference_error": "the image decodes to no pixels"})
            return
        width, height = reference_size

    margin = float(PARAMS["margin"])
    camera_axis = PARAMS["camera_axis"]
    world_up = tuple(PARAMS["world_up"])

    # Force the scene through one depsgraph evaluation before reading vertex
    # positions -- without this, modifiers may not be applied in headless mode.
    bpy.context.view_layer.update()

    bbox = gather_bbox()
    if bbox is None:
        emit({
            "rendered": False,
            "reference_size": reference_size,
            "refused_reason": (
                "scene has no render-visible mesh geometry "
                "(no MESH object with hide_render=False)"
            ),
            "blender_version": ".".join(str(x) for x in bpy.app.version),
        })
        return

    min_v = bbox["min"]
    max_v = bbox["max"]
    center = [(a + b) / 2.0 for a, b in zip(min_v, max_v)]
    size = [b - a for a, b in zip(min_v, max_v)]

    # Which two world axes span the render plane depends on the camera axis
    # AND the world-up axis. World-up is the render's vertical; the plane's
    # horizontal is world's third axis (perpendicular to both camera axis and
    # world_up). For the standard case (world_up=+Z, cameras on ±X or ±Y):
    #   camera at +Y or -Y: render plane spans X (horizontal) and Z (vertical)
    #   camera at +X or -X: render plane spans Y (horizontal) and Z (vertical)
    axis_index = {"+X": 0, "-X": 0, "+Y": 1, "-Y": 1, "+Z": 2, "-Z": 2}
    up_index = 0 if abs(world_up[0]) > 0.5 else (1 if abs(world_up[1]) > 0.5 else 2)
    cam_idx = axis_index[camera_axis]
    if cam_idx == up_index:
        raise RuntimeError(
            f"camera axis {camera_axis!r} is parallel to world_up {world_up!r}; "
            "cannot render a valid view"
        )
    horiz_idx = 3 - cam_idx - up_index  # the third axis

    plane_width = size[horiz_idx]
    plane_height = size[up_index]

    bbox_width_needed = plane_width * (1.0 + margin)
    bbox_height_needed = plane_height * (1.0 + margin)

    # Fit the bbox into the render frame; the LIMITING dimension determines
    # world-units-per-pixel. Blender's ortho_scale spans the LONGER edge of
    # the render output (width if landscape, height if portrait).
    ppwu_from_width = width / bbox_width_needed if bbox_width_needed > 0 else float("inf")
    ppwu_from_height = height / bbox_height_needed if bbox_height_needed > 0 else float("inf")
    ppwu = min(ppwu_from_width, ppwu_from_height)
    if ppwu <= 0 or not math.isfinite(ppwu):
        emit({
            "rendered": False,
            "reference_size": reference_size,
            "refused_reason": "bounding box has zero extent in the view plane",
            "blender_version": ".".join(str(x) for x in bpy.app.version),
        })
        return
    render_w_world = width / ppwu
    render_h_world = height / ppwu
    ortho_scale = max(render_w_world, render_h_world)

    # Camera location: along the camera axis at a comfortable distance from
    # the bbox (orthographic projection is depth-independent, but we still
    # need to sit inside near/far clip planes). Perpendicular axes get the
    # bbox center so the frame is centred on the character.
    offset = max(size) * 4.0 + 10.0
    location = list(center)
    if camera_axis == "+X":
        location[0] = max_v[0] + offset
    elif camera_axis == "-X":
        location[0] = min_v[0] - offset
    elif camera_axis == "+Y":
        location[1] = max_v[1] + offset
    elif camera_axis == "-Y":
        location[1] = min_v[1] - offset
    elif camera_axis == "+Z":
        location[2] = max_v[2] + offset
    elif camera_axis == "-Z":
        location[2] = min_v[2] - offset
    else:
        raise RuntimeError(f"unknown camera_axis: {camera_axis!r}")

    look_matrix = look_at_matrix(location, center, world_up)

    # Fresh orthographic camera, added to the scene's active collection so it
    # cannot collide with a saved camera the scene may already own.
    cam_data = bpy.data.cameras.new("BlenderInspectRenderCam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho_scale
    cam_data.clip_start = 0.001
    cam_data.clip_end = offset * 8.0 + 1000.0
    cam_obj = bpy.data.objects.new("BlenderInspectRenderCam", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.matrix_world = look_matrix

    scene = bpy.context.scene
    scene.camera = cam_obj

    # Explicit engine + render-output pins. The .blend's saved settings are
    # invisible inputs otherwise; pinning them here is what makes the
    # determinism claim testable.
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.use_border = False
    scene.render.use_stamp = False
    scene.render.use_sequencer = False
    scene.render.use_compositing = False
    # film_transparent=True writes an alpha channel: transparent for the
    # background, opaque for the rendered object. This lets pil_structure_diff
    # take the alpha path for the render side of the pair, which is more
    # accurate than border-median guessing. The reference will still be
    # border-median (opaque JPG/PNG), so foreground_source_mismatch is
    # expected in comparison.diff_flags -- honest reporting, not a defect.
    scene.render.film_transparent = True
    scene.render.filepath = out_path
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 15

    # Workbench render shading. FLAT lighting is bundle-free (no matcap or
    # studio-light dependency across installs). MATERIAL colour honours the
    # scene's own material assignments so the render shows real object
    # colours. Shadows/cavity/outline off removes install-version drift.
    shading = scene.display.shading
    shading.type = "SOLID"
    shading.light = "FLAT"
    # TEXTURE > MATERIAL for shaded corpora: the brute .blend uses PBR node
    # graphs Workbench cannot evaluate, so MATERIAL falls back to a flat
    # white and the render becomes a silhouette. TEXTURE reads the material's
    # image-texture node when present, otherwise falls back gracefully.
    shading.color_type = "TEXTURE"
    shading.show_shadows = False
    shading.show_cavity = False
    shading.show_object_outline = False
    shading.show_specular_highlight = False
    shading.show_backface_culling = False

    scene.display.viewport_aa = "FXAA"

    bpy.ops.render.render(write_still=True)

    emit({
        "rendered": True,
        "reference_size": reference_size,
        "blender_version": ".".join(str(x) for x in bpy.app.version),
        "camera": {
            "camera_axis": camera_axis,
            "location": [float(v) for v in location],
            "target": [float(v) for v in center],
            "world_up": list(world_up),
            "ortho_scale": float(ortho_scale),
            "type": "ORTHO",
        },
        "bounding_box_world_visible": {
            "min": bbox["min"],
            "max": bbox["max"],
            "visible_mesh_count": bbox["visible_mesh_count"],
        },
        "render_resolution": [width, height],
        "output_path": out_path,
    })


main()
'''


def build_probe_source(view: str, out_path: Path, width: int, height: int, margin: float, reference: Path | None = None) -> str:
    """Inject PARAMS into the probe body so it needs no CLI plumbing."""
    if view not in VIEW_CAMERAS:
        raise ValueError(f"unknown view: {view!r}")
    cfg = VIEW_CAMERAS[view]
    params = {
        "view": view,
        "out_path": str(out_path),
        "width": int(width),
        "height": int(height),
        "margin": float(margin),
        "camera_axis": cfg["camera_axis"],
        "world_up": list(WORLD_UP),
        "reference": str(Path(reference).resolve()) if reference is not None else None,
    }
    return "import json\nPARAMS = json.loads(r'''" + json.dumps(params, sort_keys=True) + "''')\n" + _PROBE_BODY


def _reject(reason: str) -> int:
    """Rejection: exit 2, byte-empty stdout, one-line stderr with tool prefix."""
    return blender_common.refuse(TOOL, reason)


def run_render(
    blender_executable: str,
    blend_path: Path,
    view: str,
    out_path: Path,
    width: int,
    height: int,
    margin: float,
    timeout: int = 600,
    reference: Path | None = None,
):
    """Invoke Blender headless to render one view; return (payload_dict, error).

    On success payload_dict has `rendered=True` and describes the camera; on a
    scene-refusal path payload_dict has `rendered=False` and `refused_reason`;
    when the reference cannot be read it carries only `reference_error`; on a
    subprocess failure (non-zero exit, missing sentinels, timeout) error is set
    and payload_dict is None.
    """
    body = build_probe_source(view, out_path, width, height, margin, reference)
    return blender_common.run_probe(
        blender_executable, TOOL, body, blend=blend_path, timeout=timeout
    )


def find_structure_diff():
    """Path to pil-agent-plugin's `pil_structure_diff.py`, or None.

    `BLENDER_INSPECT_STRUCTURE_DIFF` wins when set; then the copy in the
    repository that holds both plugins.
    """
    configured = os.environ.get(STRUCTURE_DIFF_ENV)
    candidate = Path(configured) if configured else REPO_STRUCTURE_DIFF
    return candidate if candidate.is_file() else None


def run_structure_diff(reference: Path, render: Path, timeout: int = 60, tool: Path | None = None):
    """Invoke pil_structure_diff.py --foreground on (reference, render).

    Argument order is deliberate: reference is `a`, render is `b`, so
    diff.luminance_mean_delta reads as "render minus reference".

    Returns (diff_payload_dict, error). On any non-zero exit or unparseable
    stdout, error is a one-line reason.
    """
    tool = tool or find_structure_diff()
    cmd = [
        sys.executable,
        str(tool),
        str(reference),
        str(render),
        "--foreground",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8"
        )
    except subprocess.TimeoutExpired:
        return None, f"pil_structure_diff timed out after {timeout}s"
    except OSError as exc:
        return None, f"pil_structure_diff spawn failed: {exc}"

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:]
        detail = tail[0] if tail else "no stderr"
        return None, f"pil_structure_diff exited {proc.returncode}: {detail}"
    try:
        return json.loads(proc.stdout), None
    except ValueError as exc:
        return None, f"pil_structure_diff emitted non-JSON stdout: {exc}"


REFUSAL_IMAGE_FLAGS = frozenset({"foreground_mask_empty", "foreground_too_small"})
REFUSAL_DIFF_FLAGS = frozenset({"foreground_support_insufficient"})


def build_comparison(diff_payload: dict, reference: Path, render: Path) -> dict:
    """Fold pil_structure_diff's payload into a comparison block.

    Refusal rule: if EITHER image's flags list contains foreground_mask_empty
    or foreground_too_small, or diff.flags contains
    foreground_support_insufficient, the comparison is refused
    (`refused=true`, numeric fields nulled). The mask-quality flags live in
    `images.a.flags` / `images.b.flags`; the support flag lives in diff.flags.
    """
    a_flags = diff_payload["images"]["a"].get("flags", [])
    b_flags = diff_payload["images"]["b"].get("flags", [])
    diff_flags = diff_payload["diff"].get("flags", [])
    triggering = sorted(set(a_flags + b_flags) & REFUSAL_IMAGE_FLAGS)
    support_triggering = sorted(set(diff_flags) & REFUSAL_DIFF_FLAGS)
    if triggering or support_triggering:
        which = []
        for label, flags in (("reference", a_flags), ("render", b_flags)):
            hit = sorted(set(flags) & REFUSAL_IMAGE_FLAGS)
            if hit:
                which.append(f"{label}:{','.join(hit)}")
        reason_parts = []
        if which:
            reason_parts.append(
                "foreground mask is empty or too small (" + "; ".join(which) + ")"
            )
        if support_triggering:
            reason_parts.append(
                "foreground support is insufficient ("
                + ",".join(support_triggering)
                + ")"
            )
        return {
            "refused": True,
            "refused_reason": "; ".join(reason_parts),
            "reference": str(reference),
            "render": str(render),
            "structural_similarity": None,
            "dhash_distance": None,
            "ahash_distance": None,
            "changed_area_fraction": None,
            "changed_region_bbox_fractional": None,
            "diff_flags": None,
            "reference_image": None,
            "render_image": None,
        }

    diff = diff_payload["diff"]
    return {
        "refused": False,
        "refused_reason": None,
        "reference": str(reference),
        "render": str(render),
        "structural_similarity": diff["structural_similarity"],
        "dhash_distance": diff["dhash_distance"],
        "ahash_distance": diff["ahash_distance"],
        "changed_area_fraction": diff["changed_area_fraction"],
        "changed_region_bbox_fractional": diff["changed_region_bbox_fractional"],
        "diff_flags": list(diff["flags"]),
        "reference_image": {
            "size": diff_payload["images"]["a"]["size"],
            "foreground": diff_payload["images"]["a"]["foreground"],
            "flags": list(a_flags),
        },
        "render_image": {
            "size": diff_payload["images"]["b"]["size"],
            "foreground": diff_payload["images"]["b"]["foreground"],
            "flags": list(b_flags),
        },
    }


def build_scene_refusal_comparison(reference: Path) -> dict:
    """Comparison block when the SCENE (not the reference) forced refusal.

    Used when the render itself was refused (empty scene) so no PNG exists
    on the render side. Numeric fields null, refused_reason populated.
    """
    return {
        "refused": True,
        "refused_reason": "render was refused (scene has no mesh geometry); comparison not run",
        "reference": str(reference),
        "render": None,
        "structural_similarity": None,
        "dhash_distance": None,
        "ahash_distance": None,
        "changed_area_fraction": None,
        "changed_region_bbox_fractional": None,
        "diff_flags": None,
        "reference_image": None,
        "render_image": None,
    }


def build_missing_diff_comparison(reference: Path, render: Path) -> dict:
    """Comparison block when pil-agent-plugin's structure diff is not found.

    The render exists and is pinned to the reference's size, so it is still
    published; only the measurement is refused, with numeric fields null.
    """
    return {
        "refused": True,
        "refused_reason": (
            "pil_structure_diff.py (pil-agent-plugin) not found; set "
            f"{STRUCTURE_DIFF_ENV} to its path to compare against the reference"
        ),
        "reference": str(reference),
        "render": str(render),
        "structural_similarity": None,
        "dhash_distance": None,
        "ahash_distance": None,
        "changed_area_fraction": None,
        "changed_region_bbox_fractional": None,
        "diff_flags": None,
        "reference_image": None,
        "render_image": None,
    }


def build_payload(
    blend_path: Path,
    blender_executable: str,
    view: str,
    out_path: Path,
    resolution: int,
    reference: Path | None,
    reference_pinned_size: tuple[int, int] | None,
    render_payload: dict,
    comparison: dict | None,
) -> dict:
    """Deterministic tool payload; sort_keys at dump time locks byte layout."""
    parameters = {
        "blend": str(blend_path),
        "blender_executable": blender_executable,
        "view": view,
        "out_path": str(out_path),
        "resolution_default": int(resolution),
        "resolution_used": (
            list(reference_pinned_size)
            if reference_pinned_size is not None
            else [int(resolution), int(resolution)]
        ),
        "resolution_source": (
            "reference_pinned" if reference is not None else "square_default"
        ),
        "margin": DEFAULT_MARGIN,
        "reference": str(reference) if reference is not None else None,
        "blender_version": render_payload.get("blender_version"),
        "engine": "BLENDER_WORKBENCH",
        "device": "CPU",
        "camera_convention": {
            "world_up": list(WORLD_UP),
            "character_facing": "-Y",
            "views": {name: dict(cfg) for name, cfg in VIEW_CAMERAS.items()},
        },
    }
    render_block = {
        "rendered": render_payload["rendered"],
        "refused_reason": render_payload.get("refused_reason"),
    }
    if render_payload["rendered"]:
        render_block["output_path"] = render_payload["output_path"]
        render_block["camera"] = render_payload["camera"]
        render_block["bounding_box_world_visible"] = render_payload["bounding_box_world_visible"]
        render_block["render_resolution"] = render_payload["render_resolution"]
    else:
        render_block["output_path"] = None
    return {
        "tool": TOOL,
        "version": TOOL_VERSION,
        "parameters": parameters,
        "render": render_block,
        "comparison": comparison,
        "interpretation_limits": INTERPRETATION_LIMITS,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Render one named view (front/side/back) of a Blender scene with "
            "the Workbench engine and, with --reference, register it against a "
            "reference image via pil_structure_diff --foreground."
        )
    )
    parser.add_argument("blend", help="path to a .blend file")
    parser.add_argument(
        "--view",
        required=True,
        choices=sorted(VIEW_CAMERAS.keys()),
        help="named view to render",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="path where the rendered PNG will be written",
    )
    parser.add_argument(
        "--resolution",
        type=int,
        default=DEFAULT_RESOLUTION,
        help=(
            f"square render resolution when --reference is NOT given "
            f"(default {DEFAULT_RESOLUTION}); IGNORED when --reference is "
            f"given, in which case the render is pinned to the reference's "
            f"own (width, height) so aspect_ratio_mismatch and "
            f"resolution_mismatch cannot fire from a framing difference"
        ),
    )
    parser.add_argument(
        "--reference",
        default=None,
        help=(
            "optional reference image; when supplied the render is pinned to "
            "this file's dimensions (read by Blender) and pil-agent-plugin's "
            "pil_structure_diff --foreground is invoked to produce the "
            f"comparison block; set {STRUCTURE_DIFF_ENV} to its path when it "
            "is not in this repository"
        ),
    )
    blender_common.add_blender_arguments(parser, timeout=600)
    args = parser.parse_args(argv)

    blender, reason = resolve_blender_executable(args.blender_executable)
    if blender is None:
        return _reject(reason)

    blend_path = Path(args.blend)
    if not blend_path.is_file():
        return _reject(f"blend file not found: {blend_path}")

    reference_path = None
    if args.reference is not None:
        reference_path = Path(args.reference)
        if not reference_path.is_file():
            return _reject(f"reference file not found: {reference_path}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # With a reference, the probe replaces this square size with the
    # reference's own (width, height) before it renders.
    width = height = int(args.resolution)

    # Render into a sibling staging file. A failed invocation must not leave a
    # partial PNG, and it must not destroy a caller's pre-existing output.
    #
    # The staging name is DERIVED from --out, not randomised. Uniqueness is not
    # what this file needs -- two concurrent renders to the same --out are
    # already a conflict over the destination itself -- but DETERMINISM is:
    # pil_character_sheet_review renders into a temporary workdir and must emit
    # a byte-identical payload across invocations, so a random component here
    # would become nondeterminism there the moment any diagnostic quoted the
    # staging path. Same directory as --out keeps the publish step a rename
    # within one filesystem, which cannot fail cross-device.
    staged_path = out_path.parent / f".{out_path.name}.staging.png"
    try:
        staged_path.unlink(missing_ok=True)
    except OSError as exc:
        return _reject(f"cannot stage output beside {out_path}: {exc}")

    try:
        render_payload, error = run_render(
            blender,
            blend_path,
            args.view,
            staged_path.resolve(),
            width,
            height,
            DEFAULT_MARGIN,
            timeout=args.timeout,
            reference=reference_path,
        )
        if render_payload is None:
            return _reject(error)
        if "reference_error" in render_payload:
            return _reject(
                f"failed to read reference dimensions: {render_payload['reference_error']}"
            )
        reference_pinned_size = render_payload.get("reference_size")

        if render_payload["rendered"] and staged_path.is_file():
            try:
                blender_common.strip_png_metadata(staged_path)
            except (OSError, ValueError) as exc:
                return _reject(f"cannot normalise the rendered PNG: {exc}")

        comparison = None
        if reference_path is not None:
            if not render_payload["rendered"]:
                comparison = build_scene_refusal_comparison(reference_path)
            else:
                structure_diff = find_structure_diff()
                if structure_diff is None:
                    comparison = build_missing_diff_comparison(reference_path, out_path)
                else:
                    diff_payload, diff_error = run_structure_diff(
                        reference_path, staged_path, tool=structure_diff
                    )
                    if diff_payload is None:
                        return _reject(diff_error)
                    comparison = build_comparison(diff_payload, reference_path, out_path)

        if render_payload["rendered"]:
            try:
                os.replace(staged_path, out_path)
            except OSError as exc:
                return _reject(f"cannot publish rendered output to {out_path}: {exc}")
            render_payload["output_path"] = str(out_path.resolve())
    finally:
        staged_path.unlink(missing_ok=True)

    payload = build_payload(
        blend_path=blend_path,
        blender_executable=blender,
        view=args.view,
        out_path=out_path,
        resolution=args.resolution,
        reference=reference_path,
        reference_pinned_size=reference_pinned_size,
        render_payload=render_payload,
        comparison=comparison,
    )
    blender_common.write_payload(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
