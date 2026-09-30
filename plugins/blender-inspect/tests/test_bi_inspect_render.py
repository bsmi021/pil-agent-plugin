"""blender_inspect_render: option handling and payload shape (no Blender needed),
and the renders themselves against scenes built by `fixtures/build_render_scene.py`
(skipped when Blender is missing)."""

import importlib.util
import json
import math
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

try:
    import numpy as np
except ImportError:  # only the Blender-gated render tests need it
    np = None

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
BUILDER = HERE / "fixtures" / "build_render_scene.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blender_common  # noqa: E402
import blender_inspect_render as R  # noqa: E402

TOOL_PATH = SCRIPTS / "blender_inspect_render.py"
BLENDER, _ = blender_common.resolve_blender_executable()
needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not installed")


def _load_builder():
    spec = importlib.util.spec_from_file_location("bi_build_render_scene", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def _png_with_text_chunk(path):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"\x00\x10\x20\x30\xff")
    path.write_bytes(
        bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
        + chunk(b"IHDR", ihdr) + chunk(b"tEXt", b"Time\x00now") + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )


# --- views ---------------------------------------------------------------------


def test_the_four_named_three_quarter_presets_exist():
    assert set(R.PRESETS) == {"front-left-high", "front-right-high", "back-left-high", "back-right-high"}


@pytest.mark.parametrize("name, x_sign, y_sign", [
    ("front-left-high", -1, -1),
    ("front-right-high", 1, -1),
    ("back-left-high", -1, 1),
    ("back-right-high", 1, 1),
])
def test_each_preset_is_a_unit_direction_raised_thirty_degrees_toward_its_corner(name, x_sign, y_sign):
    x, y, z = R.PRESETS[name]

    assert math.sqrt(x * x + y * y + z * z) == pytest.approx(1.0)
    assert math.degrees(math.asin(z)) == pytest.approx(30.0)
    assert (math.copysign(1, x), math.copysign(1, y)) == (x_sign, y_sign)
    assert abs(x) == pytest.approx(abs(y))


def test_front_left_uses_the_same_convention_as_the_manifests_front_left_view():
    # blender_multiview_render's seven-view set calls [-1, -1, 0] "front_left".
    x, y, _z = R.PRESETS["front-left-high"]
    assert x < 0 and y < 0


def test_resolve_views_takes_presets_manifests_or_a_mix(tmp_path):
    manifest = tmp_path / "views.json"
    manifest.write_text(json.dumps({
        "schema": "render-views-v1",
        "views": [{"name": "top", "direction": [0, 0, 5], "up": [0, 1, 0]}, {"name": "side", "direction": [1, 0, 0]}],
    }), encoding="utf-8")

    views = R.resolve_views(["front-left-high", str(manifest), "back-right-high"])

    assert [v["name"] for v in views] == ["front-left-high", "top", "side", "back-right-high"]
    assert views[1]["direction"] == [0.0, 0.0, 1.0]           # normalised, as the existing manifests are
    assert views[2]["up"] == [0.0, 0.0, 1.0]                  # default up
    assert views[0]["up"] == [0.0, 0.0, 1.0]


@pytest.mark.parametrize("bad", [[], ["not-a-preset-or-file"]])
def test_resolve_views_refuses_empty_and_unknown_sources(bad):
    with pytest.raises(R.RenderRequestError):
        R.resolve_views(bad)


def test_resolve_views_names_the_presets_when_a_value_is_unknown():
    with pytest.raises(R.RenderRequestError, match="front-left-high"):
        R.resolve_views(["nope"])


def test_resolve_views_refuses_duplicate_names_and_bad_manifests(tmp_path):
    with pytest.raises(R.RenderRequestError, match="duplicate view name: front-left-high"):
        R.resolve_views(["front-left-high", "front-left-high"])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "render-views-v1", "views": [{"name": "x", "direction": [0, 0, 1], "up": [0, 0, 1]}]}), encoding="utf-8")
    with pytest.raises(R.RenderRequestError, match="parallel"):
        R.resolve_views([str(bad)])
    unreadable = tmp_path / "junk.json"
    unreadable.write_text("{nope", encoding="utf-8")
    with pytest.raises(R.RenderRequestError, match="not readable JSON"):
        R.resolve_views([str(unreadable)])


# --- CLI refusals ----------------------------------------------------------------


def _run(argv, capsys):
    code = R.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def blend(tmp_path):
    path = tmp_path / "scene.blend"
    path.write_bytes(b"placeholder")
    return path


@pytest.mark.parametrize("flags, reason", [
    (["--lens", "0"], "--lens"),
    (["--lens", "-5"], "--lens"),
    (["--lens", "nan"], "--lens"),
    (["--width", "8"], "--width"),
    (["--height", "99999"], "--height"),
    (["--margin", "-0.1"], "--margin"),
])
def test_bad_option_values_are_refused_before_blender_starts(blend, tmp_path, capsys, monkeypatch, flags, reason):
    monkeypatch.setattr(R.blender_common, "run_probe", lambda *a, **k: pytest.fail("Blender must not start"))

    code, out, err = _run([str(blend), "--views", "front-left-high", "--output-dir", str(tmp_path / "o"), *flags], capsys)

    assert (code, out) == (2, "")
    assert err.startswith("blender_inspect_render: ") and reason in err and err.count("\n") == 1


def test_missing_blend_and_unknown_view_are_one_line_refusals(tmp_path, capsys):
    code, out, err = _run([str(tmp_path / "nope.blend"), "--views", "front-left-high", "--output-dir", str(tmp_path)], capsys)
    assert (code, out) == (2, "") and "blend file not found" in err

    blend = tmp_path / "s.blend"
    blend.write_bytes(b"x")
    code, out, err = _run([str(blend), "--views", "sideways", "--output-dir", str(tmp_path)], capsys)
    assert (code, out) == (2, "") and "sideways" in err and err.count("\n") == 1


def test_unknown_mode_and_projection_are_rejected_by_argparse(blend, tmp_path):
    for flags in (["--modes", "sparkle"], ["--projection", "fisheye"]):
        with pytest.raises(SystemExit) as info:
            R.main([str(blend), "--views", "front-left-high", "--output-dir", str(tmp_path), *flags])
        assert info.value.code == 2


def test_missing_blender_is_a_refusal_with_empty_stdout(blend, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(R.blender_common, "resolve_blender_executable", lambda explicit=None: (None, "blender executable not found"))

    code, out, err = _run([str(blend), "--views", "front-left-high", "--output-dir", str(tmp_path)], capsys)

    assert (code, out) == (2, "") and "blender executable not found" in err


def test_a_blender_failure_is_a_one_line_refusal(blend, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(R.blender_common, "resolve_blender_executable", lambda explicit=None: ("blender", None))
    monkeypatch.setattr(R.blender_common, "run_probe", lambda *a, **k: (None, "blender timed out after 600s"))

    code, out, err = _run([str(blend), "--views", "front-left-high", "--output-dir", str(tmp_path)], capsys)

    assert (code, out) == (2, "")
    assert err == "blender_inspect_render: blender timed out after 600s\n"


def test_a_file_the_probe_reports_but_did_not_write_is_refused(tmp_path, monkeypatch):
    payload = {"status": "RENDERED", "views": [{"name": "v", "files": {"depth": {"png": str(tmp_path / "gone.png")}}}]}
    monkeypatch.setattr(R.blender_common, "run_probe", lambda *a, **k: (payload, None))

    with pytest.raises(R.RenderRequestError, match="missing file"):
        R.run_render("blender", tmp_path / "s.blend", R.probe_params([], ["depth"], tmp_path, "perspective", 50, 64, 64, 0.1), 60)


def test_matcap_pngs_are_stripped_of_metadata_but_the_tools_own_pngs_are_left_alone(tmp_path, monkeypatch):
    matcap, depth = tmp_path / "v_matcap.png", tmp_path / "v_depth.png"
    _png_with_text_chunk(matcap)
    _png_with_text_chunk(depth)
    payload = {"status": "RENDERED", "views": [{"name": "v", "files": {"matcap": {"png": str(matcap)}, "depth": {"png": str(depth)}}}]}
    monkeypatch.setattr(R.blender_common, "run_probe", lambda *a, **k: (payload, None))

    R.run_render("blender", tmp_path / "s.blend", R.probe_params([], ["matcap", "depth"], tmp_path, "perspective", 50, 64, 64, 0.1), 60)

    assert b"tEXt" not in matcap.read_bytes()
    assert b"tEXt" in depth.read_bytes()


# --- probe source and payload --------------------------------------------------------


def _params(**overrides):
    views = R.resolve_views(["front-left-high"])
    base = dict(views=views, modes=list(R.MODES), output_dir=Path("out"), projection="perspective", lens=50.0, width=64, height=64, margin=0.1)
    base.update(overrides)
    return R.probe_params(**base)


def test_probe_source_compiles_and_carries_its_parameters():
    source = blender_common.probe_source(R.TOOL, R.build_probe_source(_params()))

    compile(source, "probe", "exec")
    assert '"projection": "perspective"' in source
    assert _params()["scripts_dir"] == str(SCRIPTS)


def test_probe_uses_the_settings_the_spec_names():
    body = R._PROBE_BODY

    assert 'shading.light = "MATCAP"' in body
    assert 'shading.cavity_type = "BOTH"' in body
    assert "cavity_ridge_factor" in body and "cavity_valley_factor" in body
    assert "shading.show_backface_culling = True" in body
    assert 'BLENDER_WORKBENCH' in body and 'BLENDER_EEVEE' in body
    assert 'OPEN_EXR_MULTILAYER' in body
    assert "Matrix(K.camera_matrix_rows(" in body          # never a bare nested tuple
    assert 'ambient_occlusion' in body and 'use_pass_cryptomatte_object' in body


def test_modes_are_the_five_the_spec_names():
    assert R.MODES == ("matcap", "depth", "normal", "ao", "object-id")
    assert R.PROJECTIONS == ("perspective", "orthographic")


def test_payload_keeps_every_view_and_states_its_limits():
    params = _params(modes=["depth"])
    render = {"status": "RENDER_BLOCKED", "blocked_views": ["a"], "views": [{"name": "a", "status": "RENDER_BLOCKED"}, {"name": "b", "status": "RENDERED"}]}

    payload = R.build_payload(Path("s.blend"), params, render)

    assert payload["tool"] == "blender_inspect_render" and payload["version"] == R.TOOL_VERSION == "0.1.0"
    assert payload["schema"] == "inspect-render-v1"
    assert [v["name"] for v in payload["views"]] == ["a", "b"]
    assert payload["parameters"]["modes"] == ["depth"] and payload["parameters"]["lens_mm"] == 50.0
    assert any("planar" in limit for limit in payload["interpretation_limits"])
    assert any("matcap" in limit for limit in payload["interpretation_limits"])


def test_orthographic_payload_has_no_lens():
    payload = R.build_payload(Path("s.blend"), _params(projection="orthographic"), {"status": "RENDERED", "views": []})
    assert payload["parameters"]["projection"] == "orthographic" and payload["parameters"]["lens_mm"] is None


# --- Blender-gated: the renders ---------------------------------------------------------


@pytest.fixture(scope="module")
def scenes(tmp_path_factory):
    if BLENDER is None:
        pytest.skip("Blender is not installed")
    if np is None:
        pytest.skip("numpy is not installed")
    out = tmp_path_factory.mktemp("render_scenes")
    subprocess.run(
        [BLENDER, "--factory-startup", "--background", "--python-exit-code", "1", "--python", str(BUILDER), "--", str(out)],
        check=True, capture_output=True, text=True, timeout=300,
    )
    return {name: out / f"{name}.blend" for name in builder.SCENES}


def _render(blend, out_dir, *args):
    proc = subprocess.run(
        [sys.executable, str(TOOL_PATH), str(blend), "--output-dir", str(out_dir), "--blender-executable", BLENDER, *args],
        capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _camera(view):
    """``(matrix_world, projection, tan half fov x, tan half fov y, ortho_scale)`` from a view."""
    cam = view["camera"]
    tx = math.tan(math.radians(cam["fov_x_degrees"]) / 2) if cam["fov_x_degrees"] else None
    ty = math.tan(math.radians(cam["fov_y_degrees"]) / 2) if cam["fov_y_degrees"] else None
    return np.array(cam["matrix_world"]), cam["projection"], tx, ty, cam["ortho_scale"]


def _pixel_and_planar_depth(view, point, width, height):
    """Where a world point lands in the image, and its planar depth, from the payload's camera."""
    matrix, projection, tx, ty, ortho_scale = _camera(view)
    local = np.linalg.inv(matrix) @ np.append(np.asarray(point, dtype=float), 1.0)
    depth = -local[2]
    if projection == "perspective":
        ndc_x, ndc_y = local[0] / (depth * tx), local[1] / (depth * ty)
    else:
        half_long = ortho_scale / 2.0
        half_x, half_y = (half_long, half_long * height / width) if width >= height else (half_long * width / height, half_long)
        ndc_x, ndc_y = local[0] / half_x, local[1] / half_y
    return int(round(width / 2 + ndc_x * width / 2 - 0.5)), int(round(height / 2 - ndc_y * height / 2 - 0.5)), depth


SPHERE_VIEWS = ["front-left-high", "front-right-high", "back-left-high", "back-right-high"]


@pytest.fixture(scope="module")
def sphere_renders(scenes, tmp_path_factory):
    out = tmp_path_factory.mktemp("spheres")
    return {
        projection: _render(scenes["spheres"], out / projection, "--views", *SPHERE_VIEWS, "--projection", projection, "--width", "512", "--height", "384")
        for projection in ("perspective", "orthographic")
    }


@needs_blender
def test_every_mode_writes_every_file_and_the_payload_lists_them(sphere_renders):
    payload = sphere_renders["perspective"]

    assert payload["status"] == "RENDERED" and payload["blocked_views"] == []
    assert payload["visible_objects"] == sorted(builder.SPHERES)
    assert [v["name"] for v in payload["views"]] == SPHERE_VIEWS
    for view in payload["views"]:
        assert set(view["files"]) == set(R.MODES)
        assert set(view["files"]["depth"]) == {"png", "npy", "exr"}
        assert set(view["files"]["normal"]) == {"png", "npy"}
        assert set(view["files"]["object-id"]) == {"png", "json"}
        for kinds in view["files"].values():
            for path in kinds.values():
                assert Path(path).is_file() and Path(path).stat().st_size > 0, path
        cam = view["camera"]
        assert cam["projection"] == "perspective" and cam["lens_mm"] == 50.0
        assert len(cam["matrix_world"]) == 4 and cam["matrix_world"][3] == [0.0, 0.0, 0.0, 1.0]
        assert view["depth_range"]["near"] < view["depth_range"]["far"]
        assert 0 < view["coverage"]["foreground_fraction"] < 1
        assert view["coverage"]["foreground_fraction"] + view["coverage"]["background_fraction"] == pytest.approx(1.0, abs=1e-5)


@needs_blender
def test_depth_range_and_legend_agree_with_the_raw_depth_array(sphere_renders):
    import blender_render_kernels as K

    for view in sphere_renders["perspective"]["views"]:
        depth = np.load(view["files"]["depth"]["npy"])
        assert depth.dtype == np.float32 and depth.shape == (384, 512)
        foreground = ~np.isnan(depth)
        assert foreground.sum() == view["coverage"]["foreground_pixels"]
        assert float(np.nanmin(depth)) == pytest.approx(view["depth_range"]["near"], abs=1e-5)
        assert float(np.nanmax(depth)) == pytest.approx(view["depth_range"]["far"], abs=1e-5)
        legend = view["modes"]["depth"]["legend"]
        assert (legend["near"], legend["far"], legend["unit"]) == (view["depth_range"]["near"], view["depth_range"]["far"], "scene units")
        png = K.read_png(view["files"]["depth"]["png"])
        assert png.shape == (384 + K.LEGEND_STRIP_ROWS, 512, 4)
        assert view["modes"]["depth"]["png_size"] == [512, 384 + K.LEGEND_STRIP_ROWS]
        assert (png[:384][~foreground][:, 3] == 0).all() and (png[:384][foreground][:, 3] == 255).all()


@needs_blender
@pytest.mark.parametrize("projection", ["perspective", "orthographic"])
@pytest.mark.parametrize("sphere", sorted(builder.SPHERES))
def test_depth_at_each_object_centre_matches_the_camera_distance_within_one_percent(sphere_renders, projection, sphere):
    """AC-REN-02. The pixel is where the sphere's known centre projects; the depth there is the
    planar distance from the camera to the sphere's front surface along that pixel's ray."""
    import blender_render_kernels as K

    centre, radius = builder.SPHERES[sphere]
    centre = np.array(centre)
    checked = 0
    for view in sphere_renders[projection]["views"]:
        matrix = np.array(view["camera"]["matrix_world"])
        camera_location, forward = matrix[:3, 3], -matrix[:3, 2]
        px, py, centre_depth = _pixel_and_planar_depth(view, centre, 512, 384)
        ids = json.loads(Path(view["files"]["object-id"]["json"]).read_text(encoding="utf-8"))
        mask = K.read_png(view["files"]["object-id"]["png"])
        colour = {o["name"]: o["rgb"] for o in ids["objects"]}[sphere]
        if mask[py, px].tolist() != colour:
            continue          # another sphere is in front of this one's centre in this view
        if projection == "perspective":
            ray = centre - camera_location
            expected = float((centre - camera_location) @ forward) * (1.0 - radius / np.linalg.norm(ray))
        else:
            expected = float(centre_depth) - radius
        depth = np.load(view["files"]["depth"]["npy"])[py, px]
        assert depth == pytest.approx(expected, rel=0.01), (view["name"], depth, expected)
        checked += 1
    assert checked >= 2, "each sphere must be unobstructed at its centre in at least two views"


@needs_blender
def test_object_id_masks_use_the_json_colour_map_and_cover_each_object(sphere_renders):
    import blender_render_kernels as K

    for view in sphere_renders["perspective"]["views"]:
        mapping = json.loads(Path(view["files"]["object-id"]["json"]).read_text(encoding="utf-8"))
        image = K.read_png(view["files"]["object-id"]["png"])
        assert image.shape == (384, 512, 3)
        assert mapping["schema"] == "object-id-map-v1" and mapping["unmatched_foreground_pixels"] == 0
        total = 0
        for entry in mapping["objects"]:
            pixels = int((image == np.array(entry["rgb"])).all(axis=-1).sum())
            assert pixels == entry["pixels"]
            total += pixels
        assert total == view["coverage"]["foreground_pixels"]
        background = (image == 0).all(axis=-1).sum()
        assert background == 512 * 384 - total
        assert view["modes"]["object-id"]["objects"] == mapping["objects"]


@needs_blender
def test_normals_are_world_space_and_face_the_camera_at_a_sphere_centre(sphere_renders):
    view = sphere_renders["perspective"]["views"][0]
    normals = np.load(view["files"]["normal"]["npy"])
    assert normals.dtype == np.float32 and normals.shape == (384, 512, 3)
    foreground = ~np.isnan(normals[..., 0])
    assert foreground.sum() == view["coverage"]["foreground_pixels"]
    length = np.linalg.norm(normals[foreground], axis=1)
    assert length.min() > 0.98 and length.max() < 1.02

    matrix = np.array(view["camera"]["matrix_world"])
    camera_location = matrix[:3, 3]
    for name, (centre, _radius) in builder.SPHERES.items():
        px, py, _ = _pixel_and_planar_depth(view, centre, 512, 384)
        ids = json.loads(Path(view["files"]["object-id"]["json"]).read_text(encoding="utf-8"))
        import blender_render_kernels as K
        if K.read_png(view["files"]["object-id"]["png"])[py, px].tolist() != {o["name"]: o["rgb"] for o in ids["objects"]}[name]:
            continue
        toward_camera = camera_location - np.array(centre)
        toward_camera /= np.linalg.norm(toward_camera)
        assert normals[py, px] @ toward_camera > 0.97        # world-space normal points back at the camera


@needs_blender
def test_ao_and_matcap_pngs_exist_with_the_render_size_and_transparent_background(sphere_renders):
    import blender_render_kernels as K

    view = sphere_renders["perspective"]["views"][0]
    ao = K.read_png(view["files"]["ao"]["png"])
    assert ao.shape == (384, 512, 4)
    assert (ao[..., 0] == ao[..., 1]).all()
    depth = np.load(view["files"]["depth"]["npy"])
    assert (ao[..., 3][np.isnan(depth)] == 0).all() and (ao[..., 3][~np.isnan(depth)] == 255).all()
    assert view["files"]["matcap"]["png"].endswith("_matcap.png")
    assert view["modes"]["ao"]["samples"] == R.probe_params([], [], Path("."), "perspective", 50, 8, 8, 0.1)["ao_samples"]


@needs_blender
def test_matcap_metadata_is_stripped_and_the_settings_are_reported(sphere_renders):
    payload = sphere_renders["perspective"]
    matcap = payload["matcap"]

    assert matcap["cavity_type"] == "BOTH" and matcap["backface_culling"] is True
    assert matcap["cavity_ridge_factor"] > 0 and matcap["cavity_valley_factor"] > 0
    assert matcap["studio_light"].endswith(".exr")
    for view in payload["views"]:
        data = Path(view["files"]["matcap"]["png"]).read_bytes()
        for chunk in (b"tEXt", b"zTXt", b"iTXt", b"tIME"):
            assert chunk not in data


@needs_blender
def test_framing_is_locked_across_views_and_modes(sphere_renders):
    perspective = sphere_renders["perspective"]
    distances = {v["camera"]["distance_from_target"] for v in perspective["views"]}
    assert len(distances) == 1 and distances == {perspective["framing"]["camera_distance"]}
    assert perspective["framing"]["locked"] is True and perspective["framing"]["ortho_scale"] is None
    orthographic = sphere_renders["orthographic"]
    scales = {v["camera"]["ortho_scale"] for v in orthographic["views"]}
    assert len(scales) == 1 and scales == {orthographic["framing"]["ortho_scale"]}
    assert all(v["camera"]["lens_mm"] is None for v in orthographic["views"])
    # Every mode of a view shares its camera, so the foreground is identical in every image.
    view = perspective["views"][0]
    import blender_render_kernels as K
    depth_fg = ~np.isnan(np.load(view["files"]["depth"]["npy"]))
    assert ((K.read_png(view["files"]["object-id"]["png"]) != 0).any(axis=-1) == depth_fg).all()
    assert (K.read_png(view["files"]["ao"]["png"])[..., 3] == 255)[depth_fg].all()


@needs_blender
def test_a_custom_manifest_view_and_a_preset_render_together(scenes, tmp_path):
    manifest = tmp_path / "views.json"
    manifest.write_text(json.dumps({"schema": "render-views-v1", "views": [{"name": "top", "direction": [0, 0.001, 1], "up": [0, 1, 0]}]}), encoding="utf-8")

    payload = _render(scenes["spheres"], tmp_path / "out", "--views", str(manifest), "front-left-high", "--modes", "depth", "--width", "128", "--height", "128")

    assert [v["name"] for v in payload["views"]] == ["top", "front-left-high"]
    assert all(set(v["files"]) == {"depth"} for v in payload["views"])
    assert payload["parameters"]["modes"] == ["depth"]
    top = payload["views"][0]["camera"]["matrix_world"]
    assert top[2][3] > top[0][3] and top[2][3] > top[1][3]      # the top camera sits above the scene


@needs_blender
def test_a_view_that_sees_no_geometry_is_blocked_without_being_dropped(scenes, tmp_path):
    manifest = tmp_path / "views.json"
    # A horizontal camera sees the flat quad exactly edge on.
    manifest.write_text(json.dumps({"schema": "render-views-v1", "views": [{"name": "edge-on", "direction": [0, -1, 0], "up": [0, 0, 1]}]}), encoding="utf-8")

    payload = _render(scenes["flat_quad"], tmp_path / "out", "--views", str(manifest), "front-left-high", "--width", "128", "--height", "128")

    assert payload["status"] == "RENDER_BLOCKED" and payload["blocked_views"] == ["edge-on"]
    by_name = {v["name"]: v for v in payload["views"]}
    assert set(by_name) == {"edge-on", "front-left-high"}
    blocked = by_name["edge-on"]
    assert blocked["status"] == "RENDER_BLOCKED" and "no geometry" in blocked["reason"]
    assert blocked["files"] == {} and blocked["coverage"]["foreground_pixels"] == 0
    assert blocked["camera"]["matrix_world"] and blocked["depth_range"] is None
    rendered = by_name["front-left-high"]
    assert rendered["status"] == "RENDERED" and set(rendered["files"]) == set(R.MODES)


@needs_blender
def test_a_scene_with_nothing_to_render_blocks_every_view_and_keeps_them(scenes, tmp_path):
    payload = _render(scenes["no_faces"], tmp_path / "out", "--views", "front-left-high", "back-right-high", "--width", "64", "--height", "64")

    assert payload["status"] == "RENDER_BLOCKED" and "no render-visible mesh geometry" in payload["reason"]
    assert [(v["name"], v["status"]) for v in payload["views"]] == [("front-left-high", "RENDER_BLOCKED"), ("back-right-high", "RENDER_BLOCKED")]
    assert all(v["files"] == {} for v in payload["views"])
    assert payload["visible_objects"] == ["WireOnly"] and payload["objects_with_faces"] == []
    assert not list((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else True


@needs_blender
def test_matcap_backface_culling_turns_a_flipped_face_into_a_hole(scenes, tmp_path):
    """A cube with its top face flipped must not render like the clean cube."""
    Image = pytest.importorskip("PIL.Image")
    args = ("--views", "front-left-high", "--modes", "matcap", "--width", "256", "--height", "256")
    clean = _render(scenes["clean_cube"], tmp_path / "clean", *args)["views"][0]["files"]["matcap"]["png"]
    flipped = _render(scenes["flipped_cube"], tmp_path / "flipped", *args)["views"][0]["files"]["matcap"]["png"]

    a = np.asarray(Image.open(clean).convert("RGBA"), dtype=int)
    b = np.asarray(Image.open(flipped).convert("RGBA"), dtype=int)
    silhouette = a[..., 3] > 0
    changed = (np.abs(a - b).max(axis=-1) > 24) & silhouette

    assert silhouette.sum() > 1000
    # The top face is the largest visible face from a high 3/4 view: the interior shows there.
    assert changed.sum() > 0.2 * silhouette.sum()
