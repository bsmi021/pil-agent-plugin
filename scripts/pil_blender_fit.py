#!/usr/bin/env python
"""Probe or clearance-fit a garment against a body using Blender's BVH tree.

The default ``probe`` mode is read-only. ``apply-copy`` can optionally load a
solved vertex set, move vertices outward along the nearest body normal until a
requested clearance is met, and save a new .blend. It never overwrites the
input scene.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pil_blender_mesh import resolve_blender_executable

TOOL_VERSION = "0.7.0"
_BEGIN = "<<<PIL_AGENT_BLENDER_FIT_BEGIN>>>"
_END = "<<<PIL_AGENT_BLENDER_FIT_END>>>"


class FitError(ValueError):
    pass


_PROBE_BODY = r'''
import json
import sys

import bpy
from mathutils.bvhtree import BVHTree

BEGIN = "<<<PIL_AGENT_BLENDER_FIT_BEGIN>>>"
END = "<<<PIL_AGENT_BLENDER_FIT_END>>>"


def emit(payload):
    sys.stdout.write(BEGIN + "\n")
    json.dump(payload, sys.stdout, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n" + END + "\n")


def inspect(body, garment, bvh):
    rows = []
    body_inverse = body.matrix_world.inverted()
    normal_matrix = body.matrix_world.to_3x3().inverted().transposed()
    for vertex in garment.data.vertices:
        world = garment.matrix_world @ vertex.co
        nearest = bvh.find_nearest(body_inverse @ world)
        if nearest is None:
            continue
        point_local, normal_local, _face_index, _distance_local = nearest
        point = body.matrix_world @ point_local
        normal = (normal_matrix @ normal_local).normalized()
        signed = float((world - point).dot(normal))
        rows.append((vertex.index, signed, float((world - point).length), point, normal, world))
    return rows


def summary(rows, clearance):
    signed = [row[1] for row in rows]
    return {
        "sample_count": len(rows),
        "minimum_signed_clearance": min(signed) if signed else None,
        "maximum_signed_clearance": max(signed) if signed else None,
        "mean_signed_clearance": (sum(signed) / len(signed)) if signed else None,
        "penetrating_vertex_count": sum(value < 0.0 for value in signed),
        "clearance_violation_count": sum(value < clearance for value in signed),
    }


def main():
    body = bpy.data.objects.get(PIL_PARAMS["body_object"])
    garment = bpy.data.objects.get(PIL_PARAMS["garment_object"])
    if body is None or body.type != "MESH":
        emit({"status": "FIT_BLOCKED", "reason": "body object not found or not a mesh"})
        return
    if garment is None or garment.type != "MESH":
        emit({"status": "FIT_BLOCKED", "reason": "garment object not found or not a mesh"})
        return
    if body == garment:
        emit({"status": "FIT_BLOCKED", "reason": "body and garment objects must be different"})
        return

    if PIL_PARAMS["solution_path"]:
        with open(PIL_PARAMS["solution_path"], "r", encoding="utf-8") as handle:
            solution = json.load(handle)
        vertices = solution.get("vertices")
        if solution.get("status") != "SOLVED" or vertices is None:
            emit({"status": "FIT_BLOCKED", "reason": "solution has no usable vertices"})
            return
        if len(vertices) != len(garment.data.vertices):
            emit({"status": "FIT_BLOCKED", "reason": "solution vertex count does not match garment"})
            return
        for vertex, coordinate in zip(garment.data.vertices, vertices):
            vertex.co = coordinate
        garment.data.update()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    bpy.context.view_layer.update()
    bvh = BVHTree.FromObject(body, depsgraph, deform=True, cage=False)
    before_rows = inspect(body, garment, bvh)
    before = summary(before_rows, PIL_PARAMS["clearance"])
    if PIL_PARAMS["mode"] == "probe":
        emit({"status": "PROBED", "before": before, "after": before, "output_path": None})
        return

    required = []
    for index, signed, _distance, point, normal, world in before_rows:
        displacement = max(0.0, PIL_PARAMS["clearance"] - signed)
        required.append((index, displacement, normal, world))
    maximum = max((row[1] for row in required), default=0.0)
    if maximum > PIL_PARAMS["max_displacement"]:
        emit({
            "status": "FIT_BLOCKED",
            "reason": "required displacement exceeds max_displacement",
            "required_max_displacement": maximum,
            "before": before,
        })
        return
    inverse = garment.matrix_world.inverted()
    for index, displacement, normal, world in required:
        if displacement > 0.0:
            garment.data.vertices[index].co = inverse @ (world + normal * displacement)
    garment.data.update()
    bpy.context.view_layer.update()
    after_rows = inspect(body, garment, bvh)
    after = summary(after_rows, PIL_PARAMS["clearance"])
    bpy.ops.wm.save_as_mainfile(filepath=PIL_PARAMS["output_path"], check_existing=False)
    emit({
        "status": "FITTED",
        "before": before,
        "after": after,
        "maximum_applied_displacement": maximum,
        "output_path": PIL_PARAMS["output_path"],
    })


main()
'''


def build_probe_source(body_object: str, garment_object: str, clearance: float, max_displacement: float, mode: str, output_path: Path | None, solution_path: Path | None) -> str:
    params = {
        "body_object": body_object,
        "garment_object": garment_object,
        "clearance": float(clearance),
        "max_displacement": float(max_displacement),
        "mode": mode,
        "output_path": str(output_path.resolve()) if output_path else None,
        "solution_path": str(solution_path.resolve()) if solution_path else None,
    }
    return "import json\nPIL_PARAMS = json.loads(r'''" + json.dumps(params, sort_keys=True) + "''')\n" + _PROBE_BODY


def _extract_payload(stdout: str):
    matches = re.findall(
        re.escape(_BEGIN) + r"\r?\n(.*?)\r?\n" + re.escape(_END),
        stdout,
        flags=re.DOTALL,
    )
    if len(matches) != 1:
        return None
    try:
        return json.loads(matches[0])
    except ValueError:
        return None


def run_fit(blender: str, blend: Path, body_object: str, garment_object: str, clearance: float, max_displacement: float, mode: str, output_path: Path | None, solution_path: Path | None, timeout=300):
    source = build_probe_source(body_object, garment_object, clearance, max_displacement, mode, output_path, solution_path)
    with tempfile.NamedTemporaryFile("w", suffix="_pil_blender_fit.py", delete=False, encoding="utf-8") as handle:
        handle.write(source)
        probe = Path(handle.name)
    try:
        proc = subprocess.run(
            [blender, "--factory-startup", "--background", str(blend), "--python", str(probe), "--python-exit-code", "1"],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FitError(f"Blender fit failed: {exc}") from exc
    finally:
        probe.unlink(missing_ok=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:]
        raise FitError(f"Blender exited {proc.returncode}: {tail[0] if tail else 'no diagnostic'}")
    payload = _extract_payload(proc.stdout)
    if payload is None:
        raise FitError("Blender emitted no valid fit payload")
    return payload


def _reject(reason):
    print(f"pil_blender_fit: {reason}", file=sys.stderr)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Probe or clearance-fit one Blender garment object against a body BVH.")
    parser.add_argument("blend")
    parser.add_argument("--body-object", required=True)
    parser.add_argument("--garment-object", required=True)
    parser.add_argument("--clearance", type=float, required=True)
    parser.add_argument("--max-displacement", type=float, default=0.05)
    parser.add_argument("--mode", choices=("probe", "apply-copy"), default="probe")
    parser.add_argument("--output")
    parser.add_argument("--solution")
    parser.add_argument("--blender-executable")
    args = parser.parse_args(argv)
    blend = Path(args.blend).resolve()
    if not blend.is_file():
        return _reject(f"blend file not found: {blend}")
    blender = resolve_blender_executable(args.blender_executable)
    if blender is None:
        return _reject("Blender executable not found")
    output = Path(args.output).resolve() if args.output else None
    if args.mode == "apply-copy" and output is None:
        return _reject("--output is required for apply-copy")
    if output is not None and output == blend:
        return _reject("output must not overwrite the input .blend")
    if output is not None and output.exists():
        return _reject(f"output already exists: {output}")
    solution = Path(args.solution).resolve() if args.solution else None
    if solution is not None and not solution.is_file():
        return _reject(f"solution not found: {solution}")
    if args.clearance < 0 or args.max_displacement < 0:
        return _reject("clearance and max-displacement must be non-negative")
    try:
        fit = run_fit(blender, blend, args.body_object, args.garment_object, args.clearance, args.max_displacement, args.mode, output, solution)
    except FitError as exc:
        return _reject(str(exc))
    payload = {
        "tool": "pil_blender_fit",
        "version": TOOL_VERSION,
        "parameters": {
            "blend": str(blend),
            "body_object": args.body_object,
            "garment_object": args.garment_object,
            "clearance": args.clearance,
            "max_displacement": args.max_displacement,
            "mode": args.mode,
            "solution": str(solution) if solution else None,
        },
        "fit": fit,
        "interpretation_limits": [
            "Signed clearance is the nearest-point displacement dotted with the body's evaluated surface normal; inverted or inconsistent body normals weaken penetration classification.",
            "Clearance fitting is a bounded normal displacement, not cloth simulation, retopology, rigging, or deformation testing.",
            "apply-copy writes a new .blend and refuses to overwrite the source or an existing destination.",
        ],
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
