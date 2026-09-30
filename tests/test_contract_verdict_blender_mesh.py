"""`pil_contract_verdict --scene-stats-a/-b` accepts blender-inspect's
`blender_mesh.py` payloads.

Hermetic: payloads built by `blender_mesh.build_payload` itself (the real
payload builder, not a hand-written dict with the tool name swapped).
Blender-gated: real `blender_mesh.py` probes of two scenes built at test
time, including one side probed by the deprecated `pil_blender_mesh.py`,
since both emit the same `scene` shape.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

import blender_common
import blender_mesh

REPO_ROOT = Path(__file__).resolve().parents[1]
VERDICT_TOOL = REPO_ROOT / "scripts" / "pil_contract_verdict.py"
OLD_MESH_TOOL = REPO_ROOT / "scripts" / "pil_blender_mesh.py"
NEW_MESH_TOOL = REPO_ROOT / "plugins" / "blender-inspect" / "scripts" / "blender_mesh.py"

BLENDER, _ = blender_common.resolve_blender_executable()


def _run(*args):
    return subprocess.run(
        [sys.executable, *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8",
    )


def _scene(objects):
    return {
        "path": "synthetic.blend",
        "mesh_objects": objects,
        "totals": {
            "mesh_object_count": len(objects),
            "polys": sum(o["polys"] for o in objects.values()),
            "verts": sum(o["verts"] for o in objects.values()),
            "edges": sum(o["edges"] for o in objects.values()),
        },
        "bounding_dimensions_world": None,
    }


def _mesh(polys, verts):
    return {"polys": polys, "verts": verts, "edges": polys * 2, "material_slot_count": 0,
            "materials": [], "dimensions": [1.0, 1.0, 1.0]}


def _stats(path, objects):
    payload = blender_mesh.build_payload(Path("synthetic.blend"), "blender", _scene(objects), "5.2.0")
    assert payload["tool"] == "blender_mesh"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _verdict(tmp_path, stats_a, stats_b, invariant):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    Image.new("RGB", (4, 4)).save(a)
    Image.new("RGB", (4, 4)).save(b)
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"invariant": invariant}), encoding="utf-8")
    proc = _run(VERDICT_TOOL, a, b, "--contract", contract,
                "--scene-stats-a", stats_a, "--scene-stats-b", stats_b)
    assert proc.returncode == 0, proc.stderr
    return {item["predicate"]: item for item in json.loads(proc.stdout)["pairs"][0]["items"]}


def test_blender_mesh_payloads_resolve_geometry_predicates(tmp_path):
    a = _stats(tmp_path / "a.json", {"Tabard": _mesh(10, 12), "Belt": _mesh(4, 8)})
    same = _stats(tmp_path / "same.json", {"Tabard": _mesh(10, 12), "Belt": _mesh(4, 8)})
    changed = _stats(tmp_path / "changed.json", {"Tabard": _mesh(9, 12), "Belt": _mesh(4, 8)})

    kept = _verdict(tmp_path, a, same, ["geometry.topology_preserved"])
    broken = _verdict(tmp_path, a, changed, [
        "geometry.topology_preserved", "geometry.topology_preserved(Belt)",
    ])

    assert kept["geometry.topology_preserved"]["verdict"] == "SATISFIED"
    assert broken["geometry.topology_preserved"]["verdict"] == "VIOLATED"
    assert broken["geometry.topology_preserved(Belt)"]["verdict"] == "SATISFIED"


def test_a_payload_without_the_scene_shape_names_blender_mesh(tmp_path):
    bogus = tmp_path / "bogus.json"
    bogus.write_text(json.dumps({"tool": "blender_mesh"}), encoding="utf-8")
    image = tmp_path / "a.png"
    Image.new("RGB", (4, 4)).save(image)
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"invariant": ["geometry.topology_preserved"]}), encoding="utf-8")

    proc = _run(VERDICT_TOOL, image, image, "--contract", contract,
                "--scene-stats-a", bogus, "--scene-stats-b", bogus)

    assert proc.returncode != 0
    assert proc.stdout == ""
    assert "blender_mesh.py" in proc.stderr


@pytest.fixture(scope="module")
def two_blends(tmp_path_factory):
    root = tmp_path_factory.mktemp("verdict_blender_mesh")
    blends = {}
    for name, subdivide in (("before", 0), ("after", 1)):
        blend = root / f"{name}.blend"
        script = root / f"make_{name}.py"
        script.write_text(
            "import bpy\n"
            "bpy.ops.object.select_all(action='SELECT')\n"
            "bpy.ops.object.delete(use_global=False)\n"
            "bpy.ops.mesh.primitive_cube_add()\n"
            "bpy.context.object.name = 'Body'\n"
            "bpy.ops.mesh.primitive_plane_add(location=(0, 0, 2))\n"
            "bpy.context.object.name = 'Garment'\n"
            f"if {subdivide}:\n"
            "    bpy.ops.object.mode_set(mode='EDIT')\n"
            "    bpy.ops.mesh.subdivide()\n"
            "    bpy.ops.object.mode_set(mode='OBJECT')\n"
            f"bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r})\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [BLENDER, "--factory-startup", "--background", "--python-exit-code", "1",
             "--python", str(script)],
            capture_output=True, text=True, timeout=300,
        )
        assert proc.returncode == 0, proc.stderr
        blends[name] = blend
    return root, blends


@pytest.mark.skipif(BLENDER is None, reason="Blender is not installed")
def test_real_blender_mesh_probes_drive_the_verdict(two_blends, tmp_path):
    _root, blends = two_blends
    stats = {}
    for name, tool in (("before", NEW_MESH_TOOL), ("after", NEW_MESH_TOOL), ("before_old", OLD_MESH_TOOL)):
        blend = blends[name.removesuffix("_old")]
        proc = _run(tool, blend)
        assert proc.returncode == 0, proc.stderr
        stats[name] = tmp_path / f"{name}.json"
        stats[name].write_text(proc.stdout, encoding="utf-8")
    assert json.loads(stats["before"].read_text(encoding="utf-8"))["tool"] == "blender_mesh"

    items = _verdict(tmp_path, stats["before"], stats["after"], [
        "geometry.topology_preserved(Body)", "geometry.topology_preserved(Garment)",
    ])
    mixed = _verdict(tmp_path, stats["before_old"], stats["before"], ["geometry.topology_preserved"])

    assert items["geometry.topology_preserved(Body)"]["verdict"] == "SATISFIED"
    assert items["geometry.topology_preserved(Garment)"]["verdict"] == "VIOLATED"
    assert mixed["geometry.topology_preserved"]["verdict"] == "SATISFIED"
