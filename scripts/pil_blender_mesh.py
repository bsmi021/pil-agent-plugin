#!/usr/bin/env python
"""Mesh statistics read from a Blender scene, never inferred from pixels.

`bpy` cannot be imported outside Blender's own bundled interpreter, so this
wrapper -- run under the plugin's Python 3.11+ / uv venv -- invokes Blender
headless as a subprocess and parses the embedded probe's stdout. Same shape as
`calibration/measure.py`: invoke as a CLI, parse output.

The tool answers geometry questions from the scene itself -- polygon and vertex
counts per mesh object, material slot inventory, and world-space bounding
dimensions -- so `pil_contract_verdict`'s `geometry.*` predicates can resolve to
`SATISFIED`/`VIOLATED` instead of the load-bearing `UNMEASURABLE` refusal that
applies when no scene data is available. It never approximates from a render:
that is the boundary this plugin exists to hold.

Rejection paths (exit 2, byte-empty stdout, one-line reason on stderr):
- Blender executable not found under `--blender-executable` or the default
  search list.
- Requested `.blend` file does not exist.
- Blender subprocess timed out, exited non-zero, or produced no sentinel-wrapped
  probe payload -- any of which means the probe did not complete cleanly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL_VERSION = "0.4.0"

# Default search order for the Blender executable. `--blender-executable` wins;
# the Windows install path used to build this tool is second; then a bare
# ``blender`` on PATH. Kept explicit so a missing Blender surfaces as one clear
# reason rather than a cryptic FileNotFoundError.
DEFAULT_BLENDER_SEARCH = (
    "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe",
    "C:/Program Files/Blender Foundation/Blender/blender.exe",
    "blender",
)

# Sentinels bracket the probe's JSON so it can be extracted from Blender's own
# chatty startup and shutdown log lines without pattern-matching them.
_SENTINEL_BEGIN = "<<<PIL_AGENT_BLENDER_MESH_BEGIN>>>"
_SENTINEL_END = "<<<PIL_AGENT_BLENDER_MESH_END>>>"

# Executed inside Blender's bundled Python (5.x, `bpy` present). The API calls
# used here -- `bpy.data.objects`, `Mesh.polygons/vertices/edges`,
# `Object.material_slots`, `Object.matrix_world`, `Object.bound_box`, and
# `mathutils.Vector` -- were verified against Blender 5.1.2 on this machine
# before the wrapper was written. Kept pure-stdlib + `bpy` + `mathutils` so it
# has no dependency on the plugin's venv.
_PROBE_SOURCE = r'''
import json
import sys

import bpy
import mathutils

BEGIN = "<<<PIL_AGENT_BLENDER_MESH_BEGIN>>>"
END = "<<<PIL_AGENT_BLENDER_MESH_END>>>"


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


sys.stdout.write(BEGIN + "\n")
json.dump(gather(), sys.stdout, indent=2, sort_keys=True, allow_nan=False)
sys.stdout.write("\n" + END + "\n")
sys.stdout.flush()
'''

INTERPRETATION_LIMITS = [
    "Counts are read from Blender's scene data (`Mesh.polygons`/`vertices`/`edges`, `Object.material_slots`) as declared on the base mesh -- unapplied modifiers (Subdivision, Mirror, Solidify, ...) mean the render-visible density can differ from these counts. Apply the modifiers in Blender first if that difference matters, or compare two scenes whose modifier states you know match.",
    "`bounding_dimensions_world` is an axis-aligned box over every mesh object's world-space bound_box corners; it reflects Blender scene units without unit conversion (a scene authored in metres reports metres).",
    "`mesh_objects` is keyed by Blender object name, not by mesh datablock. Two objects that instance one mesh appear as two entries reporting the same counts.",
    "This tool measures the .blend file's scene; it does not render or otherwise consult pixels. A geometry answer this tool cannot produce -- Blender absent, .blend unreadable, probe failed -- is a clean UNMEASURABLE at the contract layer, never a pixel-derived approximation.",
]


def resolve_blender_executable(explicit):
    """Return an absolute path to a runnable Blender, or None.

    An explicit path wins outright; the default search is a hardcoded list of
    common install locations plus a PATH lookup for a bare ``blender``. Returns
    None rather than raising, so ``main`` can turn absence into the documented
    exit-2 rejection with its own reason.
    """
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p)
        return None
    for candidate in DEFAULT_BLENDER_SEARCH:
        p = Path(candidate)
        if p.is_file():
            return str(p)
        located = shutil.which(candidate)
        if located:
            return located
    return None


def _extract_probe_payload(stdout):
    """Return the JSON dict from the probe, or None on any malformation.

    The probe writes exactly one sentinel-delimited block; anything else --
    duplicated blocks, missing terminator, non-JSON body -- means the probe did
    not complete cleanly and the wrapper turns it into a rejection.
    """
    pattern = re.escape(_SENTINEL_BEGIN) + r"\r?\n(.*?)\r?\n" + re.escape(_SENTINEL_END)
    matches = re.findall(pattern, stdout, flags=re.S)
    if len(matches) != 1:
        return None
    try:
        return json.loads(matches[0])
    except ValueError:
        return None


def probe_blend(blender_executable, blend_path, timeout=300):
    """Invoke Blender headless on ``blend_path`` and return the probe payload.

    Returns (payload_dict, error_reason). Exactly one is populated: on success,
    payload_dict is set; on any failure -- non-zero exit, missing sentinels,
    timeout -- error_reason carries a one-line explanation and payload is None.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix="_pil_blender_mesh_probe.py", delete=False, encoding="utf-8"
    ) as probe_file:
        probe_file.write(_PROBE_SOURCE)
        probe_path = probe_file.name

    try:
        cmd = [
            blender_executable,
            "--factory-startup",
            "--background",
            str(blend_path),
            "--python",
            probe_path,
            "--python-exit-code",
            "1",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return None, f"blender timed out after {timeout}s on {blend_path}"
        except OSError as exc:
            return None, f"blender spawn failed: {exc}"
    finally:
        try:
            os.unlink(probe_path)
        except OSError:
            pass

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
        detail = tail[0] if tail else "no output"
        return None, f"blender exited {proc.returncode} on {blend_path}: {detail}"

    payload = _extract_probe_payload(proc.stdout)
    if payload is None:
        return None, f"probe emitted no sentinel-wrapped payload for {blend_path}"
    return payload, None


def build_payload(blend_path, blender_executable, scene, blender_version):
    """Deterministic tool payload for a successful probe.

    Parameters echo the resolved Blender path (as evidence, not the temp probe
    path -- that varies per run and would break byte-determinism), the blend
    file, and the timeout ceiling. Scene data lands under ``scene`` unchanged.
    """
    return {
        "tool": "pil_blender_mesh",
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


def _reject(reason):
    """Every rejection path: byte-empty stdout, one-line reason on stderr, exit 2."""
    sys.stderr.write(f"pil_blender_mesh: {reason}\n")
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Read mesh statistics (poly/vert counts, material slots, bounding "
            "dimensions) from a Blender .blend file via a headless subprocess."
        )
    )
    parser.add_argument("blend", help="path to a .blend file")
    parser.add_argument(
        "--blender-executable",
        help=(
            "path to blender.exe; when absent, searches "
            + ", ".join(DEFAULT_BLENDER_SEARCH)
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="seconds to wait for the Blender subprocess (default 300)",
    )
    args = parser.parse_args(argv)

    blender = resolve_blender_executable(args.blender_executable)
    if blender is None:
        return _reject(
            "blender executable not found; pass --blender-executable or install "
            "Blender at " + DEFAULT_BLENDER_SEARCH[0]
        )

    blend_path = Path(args.blend)
    if not blend_path.is_file():
        return _reject(f"blend file not found: {blend_path}")

    payload, error = probe_blend(blender, blend_path, timeout=args.timeout)
    if payload is None:
        return _reject(error)

    scene = {
        "path": str(blend_path.resolve()),
        "mesh_objects": payload["mesh_objects"],
        "totals": payload["totals"],
        "bounding_dimensions_world": payload["bounding_dimensions_world"],
    }
    output = build_payload(blend_path, blender, scene, payload["blender_version"])
    json.dump(output, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
