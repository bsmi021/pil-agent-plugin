"""Real-Blender fit and multiview render, moved from pil-agent-plugin's
`tests/test_phase4_blender_integration.py`. Skips when Blender is missing."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blender_common  # noqa: E402

BLENDER, _ = blender_common.resolve_blender_executable()
pytestmark = pytest.mark.skipif(BLENDER is None, reason="Blender is not installed")


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
    root = tmp_path_factory.mktemp("bi_blender")
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

bpy.ops.wm.save_as_mainfile(filepath=FIXTURE_OUTPUT)
""".replace("FIXTURE_OUTPUT", repr(str(source))),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [BLENDER, "--factory-startup", "--background", "--python-exit-code", "1", "--python", str(create_script)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    probe = _run(
        "blender_fit.py", source,
        "--body-object", "Body",
        "--garment-object", "Cloak",
        "--clearance", "0.02",
        "--mode", "probe",
        "--blender-executable", BLENDER,
    )
    assert probe["tool"] == "blender_fit"
    assert probe["fit"]["status"] == "PROBED"
    assert probe["fit"]["before"]["clearance_violation_count"] == 4

    fitted = root / "fitted.blend"
    result = _run(
        "blender_fit.py", source,
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


def test_a_missing_garment_is_fit_blocked_not_an_error(fitted_fixture):
    _root, source, _fitted = fitted_fixture
    payload = _run(
        "blender_fit.py", source,
        "--body-object", "Body",
        "--garment-object", "NoSuchObject",
        "--clearance", "0.02",
    )
    assert payload["fit"] == {"status": "FIT_BLOCKED", "reason": "garment object not found or not a mesh"}


def _seven_view_manifest(root):
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
    return manifest


def test_blender_locked_framing_renders_all_seven_views(fitted_fixture):
    root, _source, fitted = fitted_fixture
    renders = root / "renders"

    payload = _run(
        "blender_multiview_render.py", fitted,
        "--manifest", _seven_view_manifest(root),
        "--output-dir", renders,
        "--width", "128",
        "--height", "128",
        "--blender-executable", BLENDER,
    )

    assert payload["tool"] == "blender_multiview_render"
    assert payload["render"]["status"] == "RENDERED"
    assert payload["render"]["locked_framing"] is True
    assert len(payload["render"]["views"]) == 7
    assert all(Path(view["path"]).is_file() for view in payload["render"]["views"])
    for view in payload["render"]["views"]:
        data = Path(view["path"]).read_bytes()
        assert b"tEXt" not in data and b"tIME" not in data, view["name"]


def test_two_multiview_runs_write_byte_identical_pngs(fitted_fixture):
    root, _source, fitted = fitted_fixture
    digests = []
    for name in ("run_a", "run_b"):
        payload = _run(
            "blender_multiview_render.py", fitted,
            "--manifest", _seven_view_manifest(root),
            "--output-dir", root / name,
            "--width", "96",
            "--height", "96",
        )
        digests.append({
            view["name"]: hashlib.sha256(Path(view["path"]).read_bytes()).hexdigest()
            for view in payload["render"]["views"]
        })
    assert digests[0] == digests[1]


@pytest.fixture(scope="module")
def off_centre_box(tmp_path_factory):
    """A 1 x 3 x 2 box centred at (5, 4, 1): asymmetric, and wholly at y > 0."""
    root = tmp_path_factory.mktemp("bi_off_centre")
    blend = root / "box.blend"
    create_script = root / "create_box.py"
    create_script.write_text(
        """
import bpy

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(5, 4, 1), scale=(1, 3, 2))
bpy.context.object.name = 'Box'
bpy.ops.wm.save_as_mainfile(filepath=FIXTURE_OUTPUT)
""".replace("FIXTURE_OUTPUT", repr(str(blend))),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [BLENDER, "--factory-startup", "--background", "--python-exit-code", "1", "--python", str(create_script)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return root, blend


def test_multiview_cameras_face_the_scene_along_each_view_direction(off_centre_box):
    from PIL import Image

    root, blend = off_centre_box
    manifest = root / "views.json"
    manifest.write_text(json.dumps({
        "schema": "render-views-v1",
        "views": [
            {"name": "front", "direction": [0, -1, 0]},
            {"name": "right", "direction": [1, 0, 0]},
        ],
    }), encoding="utf-8")
    size = 128
    payload = _run(
        "blender_multiview_render.py", blend,
        "--manifest", manifest,
        "--output-dir", root / "renders",
        "--width", str(size),
        "--height", str(size),
        "--mode", "silhouette",
    )

    # Seen from the front the box is 1 wide (X); from the right it is 3 wide (Y).
    # Both are 2 tall and centred in the frame, because each camera looks at the box.
    expected_width = {"front": 1.0, "right": 3.0}
    for view in payload["render"]["views"]:
        scale = size / view["ortho_scale"]
        alpha = Image.open(view["path"]).getchannel("A")
        box = alpha.point(lambda value: 255 if value >= 128 else 0).getbbox()
        assert box is not None, f"{view['name']} rendered nothing"
        left, top, right, bottom = box
        assert abs((right - left) - expected_width[view["name"]] * scale) <= 2, (view["name"], box)
        assert abs((bottom - top) - 2.0 * scale) <= 2, (view["name"], box)
        assert abs((left + right) / 2 - size / 2) <= 2, (view["name"], box)
        assert abs((top + bottom) / 2 - size / 2) <= 2, (view["name"], box)
