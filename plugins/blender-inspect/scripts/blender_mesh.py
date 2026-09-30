#!/usr/bin/env python
"""Mesh statistics read from a Blender scene, never inferred from pixels.

The canonical copy of pil-agent-plugin's `pil_blender_mesh`: same CLI, same
payload shape, `tool` renamed to `blender_mesh`. The launch, sentinel and
refusal plumbing comes from `blender_common`.

The tool answers geometry questions from the scene itself -- polygon and vertex
counts per mesh object, material slot inventory, and world-space bounding
dimensions. Its payload keeps the `scene` object shape that
`pil_contract_verdict --scene-stats-a/-b` reads, so the verdict tool's
`geometry.*` predicates accept output from either tool. It never approximates
from a render.

Rejection paths (exit 2, byte-empty stdout, one-line reason on stderr):
- Blender executable not found (see `blender_common` for the search order).
- Requested `.blend` file does not exist.
- Blender subprocess timed out, exited non-zero, or produced no sentinel-wrapped
  probe payload -- any of which means the probe did not complete cleanly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import blender_common  # noqa: E402

TOOL = "blender_mesh"
TOOL_VERSION = "0.1.0"

# Executed inside Blender's bundled Python after `blender_common`'s header,
# which supplies `emit`. Uses only `bpy` and `mathutils`.
_PROBE_BODY = r'''
import bpy
import mathutils


def _round(v, digits=6):
    return round(float(v), digits)


def gather():
    mesh_objects = {}
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        mesh = obj.data
        materials = []
        for slot in obj.material_slots:
            materials.append(slot.material.name if slot.material else None)
        mesh_objects[obj.name] = {
            "polys": len(mesh.polygons),
            "verts": len(mesh.vertices),
            "edges": len(mesh.edges),
            "material_slot_count": len(obj.material_slots),
            "materials": materials,
            "dimensions": [_round(d) for d in obj.dimensions],
        }

    totals = {
        "mesh_object_count": len(mesh_objects),
        "polys": sum(m["polys"] for m in mesh_objects.values()),
        "verts": sum(m["verts"] for m in mesh_objects.values()),
        "edges": sum(m["edges"] for m in mesh_objects.values()),
    }

    xs, ys, zs = [], [], []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        mat = obj.matrix_world
        for corner in obj.bound_box:
            world = mat @ mathutils.Vector(corner)
            xs.append(world.x)
            ys.append(world.y)
            zs.append(world.z)
    if xs:
        bounding = {
            "min": [_round(min(xs)), _round(min(ys)), _round(min(zs))],
            "max": [_round(max(xs)), _round(max(ys)), _round(max(zs))],
            "dimensions": [
                _round(max(xs) - min(xs)),
                _round(max(ys) - min(ys)),
                _round(max(zs) - min(zs)),
            ],
        }
    else:
        bounding = None

    return {
        "blender_version": ".".join(str(x) for x in bpy.app.version),
        "mesh_objects": {name: mesh_objects[name] for name in sorted(mesh_objects)},
        "totals": totals,
        "bounding_dimensions_world": bounding,
    }


emit(gather())
'''

INTERPRETATION_LIMITS = [
    "Counts are read from Blender's scene data (`Mesh.polygons`/`vertices`/`edges`, `Object.material_slots`) as declared on the base mesh -- unapplied modifiers (Subdivision, Mirror, Solidify, ...) mean the render-visible density can differ from these counts. Apply the modifiers in Blender first if that difference matters, or compare two scenes whose modifier states you know match.",
    "`bounding_dimensions_world` is an axis-aligned box over every mesh object's world-space bound_box corners; it reflects Blender scene units without unit conversion (a scene authored in metres reports metres).",
    "`mesh_objects` is keyed by Blender object name, not by mesh datablock. Two objects that instance one mesh appear as two entries reporting the same counts.",
    "This tool measures the .blend file's scene; it does not render or otherwise consult pixels. A geometry answer this tool cannot produce -- Blender absent, .blend unreadable, probe failed -- is a clean UNMEASURABLE at the contract layer, never a pixel-derived approximation.",
]


def probe_blend(blender_executable, blend_path, timeout=300):
    """Run the mesh probe on ``blend_path``; return ``(payload, error)``."""
    return blender_common.run_probe(
        blender_executable, TOOL, _PROBE_BODY, blend=blend_path, timeout=timeout
    )


def build_payload(blend_path, blender_executable, scene, blender_version):
    """Deterministic tool payload for a successful probe.

    Parameters echo the resolved Blender path (as evidence, not the temp probe
    path -- that varies per run and would break byte-determinism), the blend
    file, and the timeout ceiling. Scene data lands under ``scene`` unchanged.
    """
    return {
        "tool": TOOL,
        "version": TOOL_VERSION,
        "parameters": {
            "blend": str(blend_path),
            "blender_executable": blender_executable,
            "blender_version": blender_version,
            "timeout_seconds": 300,
        },
        "scene": scene,
        "interpretation_limits": INTERPRETATION_LIMITS,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Read mesh statistics (poly/vert counts, material slots, bounding "
            "dimensions) from a Blender .blend file via a headless subprocess."
        )
    )
    parser.add_argument("blend", help="path to a .blend file")
    blender_common.add_blender_arguments(parser)
    args = parser.parse_args(argv)

    blender, reason = blender_common.resolve_blender_executable(args.blender_executable)
    if blender is None:
        return blender_common.refuse(TOOL, reason)

    blend_path = Path(args.blend)
    if not blend_path.is_file():
        return blender_common.refuse(TOOL, f"blend file not found: {blend_path}")

    payload, error = probe_blend(blender, blend_path, timeout=args.timeout)
    if payload is None:
        return blender_common.refuse(TOOL, error)

    scene = {
        "path": str(blend_path.resolve()),
        "mesh_objects": payload["mesh_objects"],
        "totals": payload["totals"],
        "bounding_dimensions_world": payload["bounding_dimensions_world"],
    }
    output = build_payload(blend_path, blender, scene, payload["blender_version"])
    blender_common.write_payload(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
