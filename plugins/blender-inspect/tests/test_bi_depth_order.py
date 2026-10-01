"""blender_depth_order: the pair maths, summary text, projection fallback, option
handling and payload shape (no Blender needed), and the reports themselves on the
scenes built by `fixtures/build_stacked_boxes.py` (skipped when Blender is missing)."""

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import numpy as np
except ImportError:  # only the array tests and the Blender-gated ones need it
    np = None

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
BUILDER = HERE / "fixtures" / "build_stacked_boxes.py"
RENDER_BUILDER = HERE / "fixtures" / "build_render_scene.py"
SCHEMA_PATH = HERE.parent / "schemas" / "depth-order-v1.schema.json"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blender_common  # noqa: E402
import blender_depth_kernels as D  # noqa: E402
import blender_depth_order as O  # noqa: E402

TOOL_PATH = SCRIPTS / "blender_depth_order.py"
BLENDER, _ = blender_common.resolve_blender_executable()
needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not installed")
needs_numpy = pytest.mark.skipif(np is None, reason="numpy is not installed")


def _load_builder(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boxes = _load_builder("bi_build_stacked_boxes", BUILDER)
render_scenes = _load_builder("bi_build_render_scene_for_depth", RENDER_BUILDER)


# --- summary text ------------------------------------------------------------------------


def _pair(**overrides):
    base = {
        "front": "Belt", "back": "Tunic", "method": "passes", "depth_gap": 0.021, "tie": False,
        "front_fraction": 1.0, "overlap_fraction_of_front": 0.34,
    }
    base.update(overrides)
    return base


def test_summary_line_uses_the_spec_example_wording():
    assert D.summary_line("front", _pair()) == "Belt is 0.021 in front of Tunic in view front (overlap 34% of Belt)"


@pytest.mark.parametrize("gap, text", [(1.2, "1.2"), (2.0, "2"), (0.0213456, "0.0213"), (12.3456, "12.3"), (0.00001234, "0.0000123")])
def test_summary_gap_is_three_significant_figures_and_never_exponent_form(gap, text):
    line = D.summary_line("v", _pair(depth_gap=gap))

    assert f"Belt is {text} in front of Tunic" in line
    assert "e-" not in line


def test_summary_says_same_depth_for_a_tie():
    line = D.summary_line("top", _pair(depth_gap=0.0, tie=True))

    assert line == "Belt is at the same depth as Tunic in view top (overlap 34% of Belt)"


def test_summary_says_how_little_of_an_interleaved_overlap_the_front_object_wins():
    line = D.summary_line("v", _pair(front_fraction=0.62))

    assert line.endswith("; Belt is nearer on only 62% of the overlap")
    assert "nearer on only" not in D.summary_line("v", _pair(front_fraction=0.97))


def test_summary_flags_a_vertex_estimate_and_tiny_overlaps():
    line = D.summary_line("v", _pair(method="vertices", front_fraction=None, overlap_fraction_of_front=0.001))

    assert "(overlap under 1% of Belt)" in line and line.endswith(", estimated from projected vertices")


# --- camera model and projection ------------------------------------------------------------


def _front_camera(projection="perspective", width=200, height=100):
    toward, right, up = D.K.camera_basis([0, -1, 0], [0, 0, 1])
    location = (0.0, -10.0, 0.0)
    if projection == "orthographic":
        return D.camera_model(location, toward, right, up, projection, width, height, ortho_scale=8.0)
    return D.camera_model(location, toward, right, up, projection, width, height, lens_mm=50.0)


@needs_numpy
def test_perspective_projection_gives_planar_depth_and_image_fractions():
    camera = _front_camera()
    tan_x = 18.0 / 50.0
    points = [(0, 0, 0), (0, 5, 0), (tan_x * 10.0, 0, 0), (0, 0, tan_x * 5.0)]

    depth, u, v = D.project_points(points, camera)

    assert depth.tolist() == pytest.approx([10.0, 15.0, 10.0, 10.0])
    assert u.tolist() == pytest.approx([0.5, 0.5, 1.0, 0.5])         # the long side spans the sensor width
    assert v.tolist() == pytest.approx([0.5, 0.5, 0.5, 0.0])                # the image is half as tall, so the same offset reaches the top edge


@needs_numpy
def test_orthographic_projection_ignores_depth_for_position():
    camera = _front_camera("orthographic")

    depth, u, v = D.project_points([(0, 0, 0), (2, 6, 0), (0, 0, 1)], camera)

    assert depth.tolist() == pytest.approx([10.0, 16.0, 10.0])
    assert u.tolist() == pytest.approx([0.5, 0.75, 0.5])              # ortho_scale 8 spans the width
    assert v.tolist() == pytest.approx([0.5, 0.5, 0.5 - 1.0 / 4.0])   # half height is 2


@needs_numpy
def test_a_point_behind_the_camera_has_depth_but_no_screen_position():
    depth, u, v = D.project_points([(0, -20, 0)], _front_camera())

    assert depth[0] == pytest.approx(-10.0)
    assert math.isnan(u[0]) and math.isnan(v[0])
    assert D.bbox_from_uv(u, v) is None


def test_camera_model_refuses_missing_projection_parameters():
    toward, right, up = D.K.camera_basis([0, -1, 0], [0, 0, 1])
    with pytest.raises(ValueError):
        D.camera_model((0, 0, 0), toward, right, up, "orthographic", 10, 10)
    with pytest.raises(ValueError):
        D.camera_model((0, 0, 0), toward, right, up, "perspective", 10, 10)
    with pytest.raises(ValueError):
        D.camera_model((0, 0, 0), toward, right, up, "fisheye", 10, 10, lens_mm=50.0)


@needs_numpy
def test_bounding_boxes_from_points_and_from_masks():
    assert D.bbox_from_uv([0.25, 0.5], [0.1, 0.9]) == {"x_min": 0.25, "y_min": 0.1, "x_max": 0.5, "y_max": 0.9}
    assert D.bbox_from_uv([], []) is None
    mask = np.zeros((10, 20), dtype=bool)
    mask[2:5, 4:8] = True

    assert D.bbox_from_mask(mask) == {"x_min": 0.2, "y_min": 0.2, "x_max": 0.4, "y_max": 0.5}
    assert D.bbox_from_mask(np.zeros((4, 4), dtype=bool)) is None


def test_bbox_frame_and_overlap_arithmetic():
    a = {"x_min": 0.0, "y_min": 0.0, "x_max": 0.5, "y_max": 0.5}
    b = {"x_min": 0.25, "y_min": 0.25, "x_max": 1.0, "y_max": 1.0}
    c = {"x_min": 0.6, "y_min": 0.6, "x_max": 0.7, "y_max": 0.7}

    assert D.bbox_area(a) == pytest.approx(0.25)
    assert D.bbox_overlap_area(a, b) == pytest.approx(0.0625)
    assert D.bbox_overlap_area(a, c) == 0.0
    assert D.bbox_in_frame(a) and not D.bbox_in_frame({**a, "x_min": -0.01}) and not D.bbox_in_frame(None)


def test_range_gap_is_signed():
    assert D.range_gap({"near": 1.0, "far": 3.0}, {"near": 4.0, "far": 6.0}) == 1.0
    assert D.range_gap({"near": 1.0, "far": 3.0}, {"near": 2.0, "far": 6.0}) == -1.0


# --- pair maths on synthetic depth maps --------------------------------------------------------


def _map(rows, cols, depth, shape=(10, 10)):
    out = np.full(shape, np.nan, dtype=np.float32)
    out[rows, cols] = depth
    return out


@needs_numpy
def test_pixel_pair_takes_the_nearer_surface_as_front_and_the_median_as_the_gap():
    a = _map(slice(0, 6), slice(0, 6), 5.0)
    b = _map(slice(3, 9), slice(3, 9), 5.4)

    found = D.pixel_pair(a, b, 1e-4)

    assert found["a_front"] is True
    assert found["overlap_pixels"] == 9
    assert found["depth_gap"] == pytest.approx(0.4, abs=1e-6)
    assert (found["depth_gap_min"], found["depth_gap_max"]) == (pytest.approx(0.4, abs=1e-6),) * 2
    assert found["front_fraction"] == 1.0 and found["tie"] is False


@needs_numpy
def test_pixel_pair_swaps_the_front_when_b_is_nearer():
    a = _map(slice(0, 6), slice(0, 6), 5.4)
    b = _map(slice(3, 9), slice(3, 9), 5.0)

    found = D.pixel_pair(a, b, 1e-4)

    assert found["a_front"] is False and found["depth_gap"] == pytest.approx(0.4, abs=1e-6)


@needs_numpy
def test_pixel_pair_reports_interleaving_through_front_fraction_and_signed_extremes():
    a = np.full((4, 4), 5.0, dtype=np.float32)
    b = np.full((4, 4), 5.5, dtype=np.float32)
    b[0, :] = 4.0                       # one row where B is actually nearer: 4 of 16 pixels

    found = D.pixel_pair(a, b, 1e-4)

    assert found["a_front"] is True and found["depth_gap"] == pytest.approx(0.5)
    assert found["front_fraction"] == pytest.approx(12 / 16)
    assert found["depth_gap_min"] == pytest.approx(-1.0) and found["depth_gap_max"] == pytest.approx(0.5)


@needs_numpy
def test_pixel_pair_calls_equal_depths_a_tie_and_needs_a_shared_pixel():
    flat = np.full((3, 3), 2.0, dtype=np.float32)

    tie = D.pixel_pair(flat, flat.copy(), 1e-4)

    assert tie["tie"] is True and tie["depth_gap"] == 0.0 and tie["front_fraction"] == 0.0
    assert D.pixel_pair(_map(slice(0, 2), slice(0, 2), 1.0), _map(slice(5, 7), slice(5, 7), 2.0), 1e-4) is None


def _object(near, far, bbox, pixels=None):
    return {"depth_range": {"near": near, "far": far}, "screen_bbox": bbox, "screen_pixels": pixels}


@needs_numpy
def test_pair_record_from_passes_carries_both_overlap_fractions_and_the_range_gap():
    a = _map(slice(0, 6), slice(0, 6), 5.0)         # 36 pixels
    b = _map(slice(3, 9), slice(3, 9), 5.4)         # 36 pixels, 9 shared
    obj_a = _object(4.0, 6.0, None, 36)
    obj_b = _object(5.0, 8.0, None, 36)

    pair = D.pair_record("A", obj_a, a, "B", obj_b, b, 1e-4)

    assert (pair["front"], pair["back"], pair["method"]) == ("A", "B", "passes")
    assert pair["depth_gap"] == pytest.approx(0.4, abs=1e-6)
    assert pair["overlap_pixels"] == 9
    assert pair["overlap_fraction_of_front"] == pytest.approx(0.25) and pair["overlap_fraction_of_back"] == pytest.approx(0.25)
    assert pair["range_gap"] == pytest.approx(-1.0)  # back.near 5 - front.far 6


@needs_numpy
def test_pair_record_uses_the_front_objects_own_pixel_count_for_its_fraction():
    a = _map(slice(0, 2), slice(0, 2), 5.4)          # 4 pixels, all shared, and the farther one
    b = _map(slice(0, 10), slice(0, 10), 5.0)        # 100 pixels

    pair = D.pair_record("A", _object(0, 1, None, 4), a, "B", _object(0, 1, None, 100), b, 1e-4)

    assert pair["front"] == "B" and pair["back"] == "A"
    assert pair["overlap_fraction_of_front"] == pytest.approx(0.04)
    assert pair["overlap_fraction_of_back"] == pytest.approx(1.0)


def test_pair_record_falls_back_to_bounding_boxes_and_near_depths_without_a_depth_map():
    wire = _object(6.5, 6.5, {"x_min": 0.2, "y_min": 0.4, "x_max": 0.6, "y_max": 0.6}, None)
    box = _object(8.0, 11.0, {"x_min": 0.0, "y_min": 0.0, "x_max": 0.5, "y_max": 1.0}, 900)

    pair = D.pair_record("Box", box, None, "Wire", wire, None, 1e-4)

    assert (pair["front"], pair["back"], pair["method"]) == ("Wire", "Box", "vertices")
    assert pair["depth_gap"] == pytest.approx(1.5)
    assert pair["overlap_pixels"] is None and pair["front_fraction"] is None
    assert pair["depth_gap_min"] is None and pair["depth_gap_max"] is None
    assert pair["overlap_fraction_of_front"] == pytest.approx((0.3 * 0.2) / (0.4 * 0.2))  # 0.2..0.5 wide, 0.4..0.6 tall
    assert pair["overlap_fraction_of_back"] == pytest.approx(0.06 / 0.5)
    assert pair["range_gap"] == pytest.approx(1.5)


def test_pair_record_is_none_for_objects_that_do_not_overlap_on_screen():
    left = _object(5, 6, {"x_min": 0.0, "y_min": 0.0, "x_max": 0.4, "y_max": 0.4}, 10)
    right = _object(5, 6, {"x_min": 0.5, "y_min": 0.5, "x_max": 0.9, "y_max": 0.9}, 10)

    assert D.pair_record("L", left, None, "R", right, None, 1e-4) is None
    assert D.pair_record("L", {**left, "screen_bbox": None}, None, "R", right, None, 1e-4) is None


@needs_numpy
def test_pair_record_is_none_when_silhouettes_share_no_pixel():
    a = _map(slice(0, 2), slice(0, 2), 5.0)
    b = _map(slice(5, 7), slice(5, 7), 6.0)

    assert D.pair_record("A", _object(0, 1, None, 4), a, "B", _object(0, 1, None, 4), b, 1e-4) is None


# --- option handling ----------------------------------------------------------------------------


def _run(argv, capsys):
    code = O.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def blend(tmp_path):
    path = tmp_path / "scene.blend"
    path.write_bytes(b"placeholder")
    return path


@pytest.mark.parametrize("flags, reason", [
    (["--lens", "0"], "--lens"),
    (["--lens", "nan"], "--lens"),
    (["--width", "8"], "--width"),
    (["--height", "99999"], "--height"),
    (["--margin", "-0.1"], "--margin"),
])
def test_bad_option_values_are_refused_before_blender_starts(blend, capsys, monkeypatch, flags, reason):
    monkeypatch.setattr(O.blender_common, "run_probe", lambda *a, **k: pytest.fail("Blender must not start"))

    code, out, err = _run([str(blend), "--views", "front-left-high", *flags], capsys)

    assert (code, out) == (2, "")
    assert err.startswith("blender_depth_order: ") and reason in err and err.count("\n") == 1


def test_missing_blend_unknown_view_and_missing_views_are_refused(tmp_path, capsys):
    code, out, err = _run([str(tmp_path / "nope.blend"), "--views", "front-left-high"], capsys)
    assert (code, out) == (2, "") and "blend file not found" in err

    blend = tmp_path / "s.blend"
    blend.write_bytes(b"x")
    code, out, err = _run([str(blend), "--views", "sideways"], capsys)
    assert (code, out) == (2, "") and "sideways" in err and err.count("\n") == 1

    with pytest.raises(SystemExit) as info:
        O.main([str(blend)])
    assert info.value.code == 2


def test_missing_blender_and_a_blender_failure_are_one_line_refusals(blend, capsys, monkeypatch):
    monkeypatch.setattr(O.blender_common, "resolve_blender_executable", lambda explicit=None: (None, "blender executable not found"))
    code, out, err = _run([str(blend), "--views", "front-left-high"], capsys)
    assert (code, out) == (2, "") and "blender executable not found" in err

    monkeypatch.setattr(O.blender_common, "resolve_blender_executable", lambda explicit=None: ("blender", None))
    monkeypatch.setattr(O.blender_common, "run_probe", lambda *a, **k: (None, "blender timed out after 900s"))
    code, out, err = _run([str(blend), "--views", "front-left-high"], capsys)
    assert (code, out) == (2, "")
    assert err == "blender_depth_order: blender timed out after 900s\n"


def test_views_take_the_same_presets_and_manifests_as_the_render_tool(tmp_path):
    manifest = tmp_path / "views.json"
    manifest.write_text(json.dumps({"schema": "render-views-v1", "views": [{"name": "front", "direction": [0, -1, 0]}]}), encoding="utf-8")

    views = O.resolve_views(["front-left-high", str(manifest)])

    assert [v["name"] for v in views] == ["front-left-high", "front"]
    assert set(O.PRESETS) == {"front-left-high", "front-right-high", "back-left-high", "back-right-high"}


# --- probe source and payload ---------------------------------------------------------------------


def _params(**overrides):
    base = dict(views=O.resolve_views(["front-left-high"]), projection="perspective", lens=50.0, width=64, height=64, margin=0.1)
    base.update(overrides)
    return O.probe_params(**base)


def test_probe_source_compiles_and_reads_the_passes_the_spec_names():
    source = blender_common.probe_source(O.TOOL, O.build_probe_source(_params()))

    compile(source, "probe", "exec")
    body = O._PROBE_BODY
    assert "use_pass_cryptomatte_object" in body and "CryptoObject00" in body and '"Depth"' in body and '"Alpha"' in body
    assert "hide_render = name != target" in body               # each object is rendered alone
    assert "Matrix(K.camera_matrix_rows(" in body                # never a bare nested tuple
    assert "BLENDER_EEVEE" in body
    assert _params()["scripts_dir"] == str(SCRIPTS)


def test_payload_adds_a_summary_to_every_pair_and_states_its_limits():
    pair = {
        "front": "Belt", "back": "Tunic", "method": "passes", "depth_gap": 0.021, "tie": False,
        "front_fraction": 1.0, "overlap_fraction_of_front": 0.34,
    }
    render = {"status": "OK", "views": [{"name": "front", "status": "OK", "pairs": [pair]}]}

    payload = O.build_payload(Path("s.blend"), _params(), render)

    assert payload["tool"] == "blender_depth_order" and payload["version"] == O.TOOL_VERSION == "0.1.0"
    assert payload["schema"] == "depth-order-v1"
    assert payload["views"][0]["pairs"][0]["summary"] == "Belt is 0.021 in front of Tunic in view front (overlap 34% of Belt)"
    assert payload["parameters"]["lens_mm"] == 50.0 and payload["parameters"]["views"] == ["front-left-high"]
    assert any("planar" in limit for limit in payload["interpretation_limits"])
    assert any("method: vertices" in limit for limit in payload["interpretation_limits"])


def test_orthographic_payload_has_no_lens():
    payload = O.build_payload(Path("s.blend"), _params(projection="orthographic"), {"status": "OK", "views": []})

    assert payload["parameters"]["projection"] == "orthographic" and payload["parameters"]["lens_mm"] is None


def test_schema_file_follows_the_repo_schema_conventions():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema"]["const"] == "depth-order-v1" == O.SCHEMA
    assert schema["properties"]["tool"]["const"] == O.TOOL
    assert "schema" in schema["required"]
    assert set(schema["$defs"]["pair"]["required"]) >= {"front", "back", "depth_gap", "overlap_fraction_of_front", "summary"}


def test_schema_accepts_a_built_payload_and_rejects_a_pair_without_a_summary():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    pair = {
        "front": "A", "back": "B", "method": "vertices", "depth_gap": 1.5, "depth_gap_min": None, "depth_gap_max": None,
        "front_fraction": None, "tie": False, "range_gap": 1.5, "overlap_pixels": None,
        "overlap_fraction_of_front": 0.5, "overlap_fraction_of_back": 0.25,
    }
    render = {
        "status": "OK", "blender_version": "5.2.0", "visible_objects": ["A", "B"], "objects_with_faces": ["A"],
        "views": [{"name": "v", "status": "OK", "direction": [0, -1, 0], "up": [0, 0, 1], "objects": [], "pairs": [dict(pair)]}],
    }

    payload = O.build_payload(Path("s.blend"), _params(), render)
    jsonschema.validate(payload, schema)

    del payload["views"][0]["pairs"][0]["summary"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)


# --- Blender-gated: the reports ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    if BLENDER is None:
        pytest.skip("Blender is not installed")
    if np is None:
        pytest.skip("numpy is not installed")
    out = tmp_path_factory.mktemp("depth_scenes")
    for builder_path in (BUILDER, RENDER_BUILDER):
        subprocess.run(
            [BLENDER, "--factory-startup", "--background", "--python-exit-code", "1", "--python", str(builder_path), "--", str(out)],
            check=True, capture_output=True, text=True, timeout=300,
        )
    return out


@pytest.fixture(scope="module")
def views_manifest(tmp_path_factory):
    path = tmp_path_factory.mktemp("depth_views") / "views.json"
    path.write_text(json.dumps({
        "schema": "render-views-v1",
        "views": [
            {"name": "front", "direction": [0, -1, 0]},      # the camera sits on -Y
            {"name": "side", "direction": [1, 0, 0]},        # the camera sits on +X
        ],
    }), encoding="utf-8")
    return path


def _report(blend, *args):
    proc = subprocess.run(
        [sys.executable, str(TOOL_PATH), str(blend), "--blender-executable", BLENDER, *args],
        capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def stacked(built, views_manifest):
    return _report(built / "stacked.blend", "--views", str(views_manifest), "front-left-high")


def _view(payload, name):
    return next(v for v in payload["views"] if v["name"] == name)


def _pair_by_names(view, one, other):
    return next(p for p in view["pairs"] if {p["front"], p["back"]} == {one, other})


def _to_camera_frame(view, points):
    """``(planar depth, camera x, camera y)`` of world points from the payload's camera."""
    matrix = np.array(view["camera"]["matrix_world"])
    local = (np.linalg.inv(matrix) @ np.c_[np.asarray(points, dtype=float), np.ones(len(points))].T).T
    return -local[:, 2], local[:, 0], local[:, 1]


def _forward(view):
    """The camera's forward unit vector in world space (it looks down its local -Z)."""
    return -np.array(view["camera"]["matrix_world"])[:3, 2]


def _oracle_depth(view, name, width, height):
    """Per-pixel planar depth of a box, by slab intersection in the box's own frame,
    from the payload's camera. Independent of the tool's renders."""
    cam = view["camera"]
    matrix = np.array(cam["matrix_world"])
    tan_x, tan_y = math.tan(math.radians(cam["fov_x_degrees"]) / 2), math.tan(math.radians(cam["fov_y_degrees"]) / 2)
    cols, rows = np.meshgrid(np.arange(width) + 0.5, np.arange(height) + 0.5)
    ndc_x, ndc_y = cols / width * 2 - 1, 1 - rows / height * 2
    local = np.stack([ndc_x * tan_x, ndc_y * tan_y, -np.ones_like(ndc_x)], axis=-1)
    direction = local @ matrix[:3, :3].T
    origin = matrix[:3, 3]
    frame = np.array([boxes.AXIS_T, boxes.AXIS_N, boxes.AXIS_Z])           # rows: the box's axes in world space
    centre = np.array(boxes.BOXES[name])
    box_direction = direction @ frame.T
    box_origin = frame @ (origin - centre)
    half = np.array(boxes.HALF)
    with np.errstate(divide="ignore", invalid="ignore"):
        t1, t2 = (-half - box_origin) / box_direction, (half - box_origin) / box_direction
    t_near = np.minimum(t1, t2).max(axis=-1)
    t_far = np.maximum(t1, t2).min(axis=-1)
    hit = t_far >= np.maximum(t_near, 0.0)
    return np.where(hit, t_near, np.nan)    # the local z component of each ray is -1, so t is planar depth


def _oracle_pair(view, one, other, width, height):
    a, b = _oracle_depth(view, one, width, height), _oracle_depth(view, other, width, height)
    both = np.isfinite(a) & np.isfinite(b)
    diff = b[both] - a[both]
    median = float(np.median(diff))
    front, back = (one, other) if median >= 0 else (other, one)
    signed = diff if median >= 0 else -diff
    front_hits = np.isfinite(a if median >= 0 else b).sum()
    return {
        "front": front, "back": back, "gap": abs(median), "overlap_of_front": both.sum() / front_hits,
        "front_fraction": float((signed > 1e-4).mean()),
    }


PAIRS = [("BoxA", "BoxB"), ("BoxB", "BoxC"), ("BoxA", "BoxC")]
VIEW_NAMES = ["front", "side", "front-left-high"]
# Nearest first. The camera is on -Y for `front`, on +X for `side`, and above front-left for the 3/4 view.
NEAREST_FIRST = {"front": ["BoxA", "BoxB", "BoxC"], "side": ["BoxC", "BoxB", "BoxA"], "front-left-high": ["BoxA", "BoxB", "BoxC"]}


def _expected_front(view_name, one, other):
    order = NEAREST_FIRST[view_name]
    return one if order.index(one) < order.index(other) else other


# --- the fixture itself (no Blender needed) ------------------------------------------------------------


def test_the_stacked_boxes_are_pairwise_disjoint_along_their_common_normal():
    """Guards the fixture: boxes that intersect make 'which is in front' ambiguous per pixel."""
    assert boxes.SPACING > boxes.THICKNESS
    intervals = {}
    for name in boxes.BOXES:
        along_n = [sum(c * a for c, a in zip(corner, boxes.AXIS_N)) for corner in boxes.box_corners(name)]
        intervals[name] = (min(along_n), max(along_n))
        assert max(along_n) - min(along_n) == pytest.approx(boxes.THICKNESS)
    ordered = sorted(intervals.values())
    assert all(low_next - high > 0.1 for (_, high), (low_next, _) in zip(ordered, ordered[1:]))


def test_the_box_corners_are_the_boxes_local_corners_turned_into_world_space():
    for name in boxes.BOXES:
        local = sorted(tuple(round(abs(v), 9) for v in boxes.to_local(name, corner)) for corner in boxes.box_corners(name))
        assert set(local) == {boxes.HALF}


@needs_blender
def test_the_payload_validates_against_the_schema(stacked):
    jsonschema = pytest.importorskip("jsonschema")

    jsonschema.validate(stacked, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    assert stacked["status"] == "OK" and stacked["schema"] == "depth-order-v1"
    assert [v["name"] for v in stacked["views"]] == VIEW_NAMES


@needs_blender
@pytest.mark.parametrize("view_name", VIEW_NAMES)
def test_every_object_reports_its_exact_depth_range_and_screen_box(stacked, view_name):
    view = _view(stacked, view_name)
    width, height = view["coverage"]["width"], view["coverage"]["height"]

    for record in view["objects"]:
        depth, x, y = _to_camera_frame(view, boxes.box_corners(record["name"]))
        assert record["depth_range"]["near"] == pytest.approx(depth.min(), abs=1e-4)
        assert record["depth_range"]["far"] == pytest.approx(depth.max(), abs=1e-4)
        assert record["method"] == "passes" and record["in_frame"] is True
        # The rendered footprint of a box is the hull of its projected corners, to within a pixel.
        tan_x = math.tan(math.radians(view["camera"]["fov_x_degrees"]) / 2)
        tan_y = math.tan(math.radians(view["camera"]["fov_y_degrees"]) / 2)
        u, v = (x / depth / tan_x + 1) / 2, (1 - y / depth / tan_y) / 2
        box = record["screen_bbox"]
        assert box["x_min"] == pytest.approx(u.min(), abs=1.5 / width) and box["x_max"] == pytest.approx(u.max(), abs=1.5 / width)
        assert box["y_min"] == pytest.approx(v.min(), abs=1.5 / height) and box["y_max"] == pytest.approx(v.max(), abs=1.5 / height)
        assert 0 < record["visible_pixels"] <= record["screen_pixels"]


@needs_blender
@pytest.mark.parametrize("view_name", VIEW_NAMES)
def test_visible_pixels_come_from_the_object_id_pass_and_partition_the_foreground(stacked, view_name):
    view = _view(stacked, view_name)

    assert sum(o["visible_pixels"] for o in view["objects"]) == view["coverage"]["foreground_pixels"]
    assert view["coverage"]["unmatched_foreground_pixels"] == 0


@needs_blender
@pytest.mark.parametrize("view_name", VIEW_NAMES)
def test_every_pair_overlaps_on_screen_in_every_view(stacked, view_name):
    view = _view(stacked, view_name)

    assert len(view["pairs"]) == 3
    assert all(p["method"] == "passes" and p["overlap_pixels"] > 0 and p["depth_gap"] > 0 and not p["tie"] for p in view["pairs"])


@needs_blender
@pytest.mark.parametrize("view_name", VIEW_NAMES)
def test_ordering_is_exact_and_never_interleaves_for_disjoint_boxes(stacked, view_name):
    view = _view(stacked, view_name)

    for one, other in PAIRS:
        pair = _pair_by_names(view, one, other)
        front = _expected_front(view_name, one, other)
        assert (pair["front"], pair["back"]) == (front, other if front == one else one)
        # Disjoint convex solids: a separating plane means the front box is nearer at every shared pixel.
        assert pair["front_fraction"] == 1.0 and pair["depth_gap_min"] > 0
        # The geometric gap is the vertex-extent difference along the view axis, exactly.
        near_back = next(o for o in view["objects"] if o["name"] == pair["back"])["depth_range"]["near"]
        far_front = next(o for o in view["objects"] if o["name"] == pair["front"])["depth_range"]["far"]
        assert pair["range_gap"] == pytest.approx(near_back - far_front, abs=1e-4)


@needs_blender
@pytest.mark.parametrize("view_name", VIEW_NAMES)
def test_pairs_match_an_independent_ray_cast_of_the_fixture_in_every_view(stacked, view_name):
    view = _view(stacked, view_name)
    width, height = view["coverage"]["width"], view["coverage"]["height"]

    for one, other in PAIRS:
        oracle = _oracle_pair(view, one, other, width, height)
        pair = _pair_by_names(view, one, other)
        assert (pair["front"], pair["back"]) == (oracle["front"], oracle["back"])
        assert pair["depth_gap"] == pytest.approx(oracle["gap"], abs=2e-3)
        assert pair["overlap_fraction_of_front"] == pytest.approx(oracle["overlap_of_front"], abs=0.01)
        assert oracle["front_fraction"] == 1.0 == pair["front_fraction"]


@needs_blender
def test_orthographic_gaps_equal_the_spacing_over_the_view_axis_component_along_the_stack(built, views_manifest):
    payload = _report(built / "stacked.blend", "--views", str(views_manifest), "front-left-high", "--projection", "orthographic", "--width", "256", "--height", "256")

    assert payload["parameters"]["projection"] == "orthographic" and payload["parameters"]["lens_mm"] is None
    for view_name in VIEW_NAMES:
        view = _view(payload, view_name)
        cosine = abs(float(np.dot(_forward(view), boxes.AXIS_N)))
        assert cosine > 0.5, "the stack must not be seen edge-on"
        for one, other in PAIRS:
            steps = abs(list(boxes.BOXES).index(one) - list(boxes.BOXES).index(other))
            pair = _pair_by_names(view, one, other)
            assert pair["front"] == _expected_front(view_name, one, other)
            assert pair["depth_gap"] == pytest.approx(steps * boxes.SPACING / cosine, abs=1e-4)
            assert pair["front_fraction"] == 1.0 and pair["depth_gap_max"] == pytest.approx(pair["depth_gap"], abs=1e-4)


@needs_blender
def test_every_pair_has_a_plain_language_summary_in_its_own_view(stacked):
    for view_name in VIEW_NAMES:
        for pair in _view(stacked, view_name)["pairs"]:
            percent = round(pair["overlap_fraction_of_front"] * 100)
            assert pair["summary"] == f"{pair['front']} is {pair['depth_gap']:.3g} in front of {pair['back']} in view {view_name} (overlap {percent}% of {pair['front']})"


@needs_blender
def test_camera_and_framing_are_reported_and_locked(stacked):
    assert stacked["framing"]["locked"] is True and stacked["parameters"]["projection"] == "perspective"
    distances = {v["camera"]["distance_from_target"] for v in stacked["views"]}
    assert len(distances) == 1
    assert all(len(v["camera"]["matrix_world"]) == 4 for v in stacked["views"])


@needs_blender
def test_a_wire_only_object_falls_back_to_projected_vertices(built, views_manifest):
    payload = _report(built / "with_wire.blend", "--views", str(views_manifest), "--width", "256", "--height", "256")
    view = _view(payload, "front")

    wire = next(o for o in view["objects"] if o["name"] == boxes.WIRE_NAME)
    wire_near = min(_to_camera_frame(view, boxes.WIRE_VERTS)[0])
    assert wire["method"] == "vertices" and wire["screen_pixels"] is None and wire["visible_pixels"] is None
    assert wire["depth_range"]["near"] == pytest.approx(wire_near, abs=1e-4)
    with_wire = [p for p in view["pairs"] if boxes.WIRE_NAME in (p["front"], p["back"])]
    assert with_wire and all(p["method"] == "vertices" and p["summary"].endswith("estimated from projected vertices") for p in with_wire)
    box_a = _pair_by_names(view, boxes.WIRE_NAME, "BoxA")
    box_a_near = min(_to_camera_frame(view, boxes.box_corners("BoxA"))[0])
    assert box_a["front"] == boxes.WIRE_NAME and box_a["depth_gap"] == pytest.approx(box_a_near - wire_near, abs=1e-4)
    # The boxes among themselves are still measured from pixels.
    assert _pair_by_names(view, "BoxA", "BoxB")["method"] == "passes"
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(payload, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


@needs_blender
def test_a_single_object_has_no_pairs_and_a_scene_without_faces_is_blocked(built):
    single = _report(built / "flat_quad.blend", "--views", "front-left-high", "--width", "128", "--height", "128")
    view = single["views"][0]
    assert view["status"] == "OK" and len(view["objects"]) == 1 and view["pairs"] == []

    blocked = _report(built / "no_faces.blend", "--views", "front-left-high", "back-right-high")
    assert blocked["status"] == "RENDER_BLOCKED" and "no render-visible mesh geometry" in blocked["reason"]
    assert [v["name"] for v in blocked["views"]] == ["front-left-high", "back-right-high"]
    assert all(v["status"] == "RENDER_BLOCKED" and v["pairs"] == [] for v in blocked["views"])


@needs_blender
def test_the_blend_file_is_left_untouched(built):
    blend = built / "stacked.blend"
    before = blend.read_bytes()

    _report(blend, "--views", "front-left-high", "--width", "64", "--height", "64")

    assert blend.read_bytes() == before
