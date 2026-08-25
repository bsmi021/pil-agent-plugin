import json
import subprocess
import sys
from pathlib import Path

import pytest
from pil_blender_mesh import resolve_blender_executable

BLENDER = resolve_blender_executable(None)
pytestmark = pytest.mark.skipif(BLENDER is None, reason="Blender is not installed")
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _run(script, *args):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def fitted_fixture(tmp_path_factory):
    root = tmp_path_factory.mktemp("phase4_blender")
    source = root / "source.blend"
    create_script = root / "create_fixture.py"
    create_script.write_text(
        """
import bpy

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(location=(2, -1, 0.5))
body = bpy.context.object
body.name = 'Body'

mesh = bpy.data.meshes.new('CloakMesh')
mesh.from_pydata([(1.2, -1.8, 1.505), (2.8, -1.8, 1.505), (2.8, -0.2, 1.505), (1.2, -0.2, 1.505)], [], [(0, 1, 2, 3)])
mesh.update()
cloak = bpy.data.objects.new('Cloak', mesh)
bpy.context.collection.objects.link(cloak)

bpy.ops.wm.save_as_mainfile(filepath=PIL_OUTPUT)
""".replace("PIL_OUTPUT", repr(str(source))),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [BLENDER, "--factory-startup", "--background", "--python", str(create_script), "--python-exit-code", "1"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    probe = _run(
        "pil_blender_fit.py", source,
        "--body-object", "Body",
        "--garment-object", "Cloak",
        "--clearance", "0.02",
        "--mode", "probe",
        "--blender-executable", BLENDER,
    )
    assert probe["fit"]["status"] == "PROBED"
    assert probe["fit"]["before"]["clearance_violation_count"] == 4

    fitted = root / "fitted.blend"
    result = _run(
        "pil_blender_fit.py", source,
        "--body-object", "Body",
        "--garment-object", "Cloak",
        "--clearance", "0.02",
        "--max-displacement", "0.05",
        "--mode", "apply-copy",
        "--output", fitted,
        "--blender-executable", BLENDER,
    )
    assert result["fit"]["status"] == "FITTED"
    assert result["fit"]["after"]["minimum_signed_clearance"] >= 0.0199
    assert fitted.is_file()
    return root, source, fitted


def test_blender_bvh_clearance_fit_writes_a_copy(fitted_fixture):
    _root, source, fitted = fitted_fixture
    assert source.is_file()
    assert fitted.is_file()
    assert source.read_bytes() != fitted.read_bytes()


def test_blender_locked_framing_renders_all_seven_views(fitted_fixture):
    root, _source, fitted = fitted_fixture
    manifest = root / "render-views.json"
    manifest.write_text(json.dumps({
        "schema": "render-views-v1",
        "views": [
            {"name": "front", "direction": [0, -1, 0]},
            {"name": "front_right", "direction": [1, -1, 0]},
            {"name": "right", "direction": [1, 0, 0]},
            {"name": "back_right", "direction": [1, 1, 0]},
            {"name": "back", "direction": [0, 1, 0]},
            {"name": "back_left", "direction": [-1, 1, 0]},
            {"name": "front_left", "direction": [-1, -1, 0]},
        ],
    }), encoding="utf-8")
    renders = root / "renders"

    payload = _run(
        "pil_multiview_render.py", fitted,
        "--manifest", manifest,
        "--output-dir", renders,
        "--width", "128",
        "--height", "128",
        "--blender-executable", BLENDER,
    )

    assert payload["render"]["status"] == "RENDERED"
    assert payload["render"]["locked_framing"] is True
    assert len(payload["render"]["views"]) == 7
    assert all(Path(view["path"]).is_file() for view in payload["render"]["views"])
