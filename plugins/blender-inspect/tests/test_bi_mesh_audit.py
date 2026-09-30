"""blender_mesh_audit: option handling and payload shape (no Blender needed), and
the audit itself against a planted-defect scene built by
`fixtures/build_defect_scene.py` (skipped when Blender is missing)."""

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
SCHEMA_PATH = HERE.parent / "schemas" / "mesh-audit-v1.schema.json"
BUILDER = HERE / "fixtures" / "build_defect_scene.py"
PROOF_V2_SCENE = HERE.parents[2] / "runs" / "2026-09-05-six-capabilities" / "proof-v2" / "scene.blend"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blender_common  # noqa: E402
import blender_mesh_audit  # noqa: E402

BLENDER, _ = blender_common.resolve_blender_executable()
needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not installed")


def _load_builder():
    spec = importlib.util.spec_from_file_location("bi_build_defect_scene", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def _schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate(payload):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(payload, _schema())


def _zero_counts():
    return {name: 0 for name in blender_mesh_audit.DEFECT_CLASSES}


def _defects(counts=None, locations=None):
    counts = counts or {}
    return {
        name: {"count": counts.get(name, 0), "locations": (locations or {}).get(name, [])}
        for name in blender_mesh_audit.DEFECT_CLASSES
    }


# --- pure host-side logic ----------------------------------------------------


def test_schema_file_follows_the_repo_schema_conventions():
    schema = _schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema"]["const"] == "mesh-audit-v1" == blender_mesh_audit.SCHEMA
    assert schema["properties"]["tool"]["const"] == blender_mesh_audit.TOOL
    assert "schema" in schema["required"]
    counted = schema["$defs"]["counts"]["required"]
    assert tuple(counted) == blender_mesh_audit.DEFECT_CLASSES
    assert set(schema["$defs"]["object"]["properties"]["defects"]["required"]) == set(counted)


def test_fixture_expectations_cover_every_class_for_every_object():
    for name, row in builder.EXPECTED_OBJECTS.items():
        assert tuple(row) == blender_mesh_audit.DEFECT_CLASSES, name
    assert tuple(builder.CLASSES) == blender_mesh_audit.DEFECT_CLASSES
    # Every class is planted somewhere.
    for cls in blender_mesh_audit.DEFECT_CLASSES:
        assert any(row[cls] for row in builder.EXPECTED_OBJECTS.values()), cls


def test_probe_body_is_valid_python():
    compile(blender_mesh_audit._PROBE_BODY, "<probe>", "exec")


def test_summary_counts_clean_and_defective_objects():
    objects = [
        {"name": "A", "defects": _defects(), "clean": True},
        {"name": "B", "defects": _defects({"boundary_edges": 4, "ngons": 1}), "clean": False},
        {"name": "C", "defects": _defects({"boundary_edges": 2}), "clean": False},
    ]
    pairs = [{"interpenetrating": True}, {"interpenetrating": False}]
    summary = blender_mesh_audit.build_summary(objects, pairs)
    assert summary["objects_audited"] == 3
    assert summary["objects_clean"] == 1
    assert summary["objects_with_defects"] == ["B", "C"]
    assert summary["clean"] is False
    assert summary["totals"]["boundary_edges"] == 6
    assert summary["totals"]["ngons"] == 1
    assert summary["pairs_checked"] == 2
    assert summary["pairs_interpenetrating"] == 1


def _config(**overrides):
    config = {
        "classes": list(blender_mesh_audit.DEFECT_CLASSES),
        "objects": None,
        "pairs": [],
        "merge_distance": 1e-5,
        "max_locations": 50,
        "degenerate_area": blender_mesh_audit.DEGENERATE_AREA,
        "pole_valence": blender_mesh_audit.POLE_VALENCE,
    }
    config.update(overrides)
    return config


def test_built_payload_validates_against_the_schema():
    objects = [
        {
            "name": "Cube",
            "evaluated": {"verts": 8, "edges": 12, "faces": 6},
            "defects": _defects(
                {"boundary_edges": 1, "self_intersections": 1, "duplicate_verts": 1},
                {
                    "boundary_edges": [{"index": 3, "verts": [1, 2], "location": [0.0, 0.5, 1.0]}],
                    "self_intersections": [{"faces": [0, 6], "location": [1.0, 2.0, 3.0]}],
                    "duplicate_verts": [{"index": 4, "merges_into": 1, "location": [0.0, 0.0, 0.0]}],
                },
            ),
            "clean": False,
        }
    ]
    pairs = [
        {
            "a": "Cube",
            "b": "Other",
            "overlapping_face_pairs": 1,
            "interpenetrating": True,
            "locations": [{"face_a": 0, "face_b": 2, "location": [0.0, 0.0, 0.0]}],
        }
    ]
    payload = blender_mesh_audit.build_payload(
        Path("scene.blend"), "blender", "5.2.0", _config(pairs=["Cube:Other"]), 300, objects, pairs
    )
    _validate(payload)
    assert payload["parameters"]["degenerate_area_tolerance"] == blender_mesh_audit.DEGENERATE_AREA
    assert payload["summary"]["clean"] is False
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload


def test_schema_rejects_a_location_without_coordinates():
    objects = [
        {
            "name": "Cube",
            "evaluated": {"verts": 8, "edges": 12, "faces": 6},
            "defects": _defects({"loose_verts": 1}, {"loose_verts": [{"index": 0}]}),
            "clean": False,
        }
    ]
    payload = blender_mesh_audit.build_payload(
        Path("scene.blend"), "blender", "5.2.0", _config(), 300, objects, []
    )
    jsonschema = pytest.importorskip("jsonschema")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, _schema())


def _run_main(argv, capsys):
    code = blender_mesh_audit.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize(
    "extra, fragment",
    [
        (["--merge-distance", "-1"], "--merge-distance"),
        (["--merge-distance", "nan"], "--merge-distance"),
        (["--max-locations", "-1"], "--max-locations"),
        (["--timeout", "0"], "--timeout"),
        (["--pairs", "no-colon"], "--pairs"),
    ],
)
def test_bad_options_are_refused_before_blender_is_touched(extra, fragment, capsys, tmp_path):
    code, out, err = _run_main([str(tmp_path / "x.blend"), *extra], capsys)
    assert code == 2
    assert out == ""
    assert err.startswith("blender_mesh_audit: ") and err.count("\n") == 1
    assert fragment in err


def test_missing_blender_is_refused(capsys, tmp_path):
    code, out, err = _run_main(
        [str(tmp_path / "x.blend"), "--blender-executable", str(tmp_path / "no-blender")], capsys
    )
    assert code == 2 and out == "" and "--blender-executable" in err


def test_missing_blend_is_refused(capsys, tmp_path):
    code, out, err = _run_main(
        [str(tmp_path / "missing.blend"), "--blender-executable", sys.executable], capsys
    )
    assert code == 2 and out == "" and "blend file not found" in err


def test_probe_failure_and_probe_refusal_are_both_refused(monkeypatch, capsys, tmp_path):
    blend = tmp_path / "scene.blend"
    blend.write_bytes(b"")
    args = [str(blend), "--blender-executable", sys.executable]

    monkeypatch.setattr(blender_mesh_audit, "probe_blend", lambda *a, **k: (None, "blender exited 1"))
    code, out, err = _run_main(args, capsys)
    assert (code, out) == (2, "") and "blender exited 1" in err

    monkeypatch.setattr(
        blender_mesh_audit, "probe_blend", lambda *a, **k: ({"error": "not render-visible mesh objects: X"}, None)
    )
    code, out, err = _run_main(args, capsys)
    assert (code, out) == (2, "") and "not render-visible mesh objects: X" in err


def test_the_probe_receives_the_normalised_options(monkeypatch, capsys, tmp_path):
    blend = tmp_path / "scene.blend"
    blend.write_bytes(b"")
    seen = {}

    def fake_probe(blender, path, config, timeout=300):
        seen.update(config=config, timeout=timeout)
        return {"blender_version": "5.2.0", "objects": [], "pairs": []}, None

    monkeypatch.setattr(blender_mesh_audit, "probe_blend", fake_probe)
    code, out, _ = _run_main(
        [
            str(blend), "--blender-executable", sys.executable,
            "--objects", "B", "A", "--objects", "B",
            "--pairs", "A:B", "--pairs", "A:B",
            "--merge-distance", "0.01", "--max-locations", "3", "--timeout", "42",
        ],
        capsys,
    )
    assert code == 0
    config = seen["config"]
    assert config["objects"] == ["A", "B"]
    assert config["pairs"] == ["A:B"]
    assert config["merge_distance"] == 0.01 and config["max_locations"] == 3
    assert config["degenerate_area"] == blender_mesh_audit.DEGENERATE_AREA
    assert seen["timeout"] == 42
    payload = json.loads(out)
    assert payload["parameters"]["timeout_seconds"] == 42
    assert payload["parameters"]["objects_requested"] == ["A", "B"]


# --- against a real Blender scene ---------------------------------------------


def _blender(*args):
    return subprocess.run(
        [BLENDER, "--factory-startup", "--background", "--python-exit-code", "1", *map(str, args)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _audit(blend, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "blender_mesh_audit.py"), str(blend), *map(str, args),
         "--blender-executable", BLENDER],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _audit_ok(blend, *args):
    proc = _audit(blend, *args)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def defect_scene(tmp_path_factory):
    root = tmp_path_factory.mktemp("bi_audit")
    blend = root / "defects.blend"
    proc = _blender("--python", BUILDER, "--", blend)
    assert proc.returncode == 0, proc.stderr
    assert blend.is_file()
    return blend


@pytest.fixture(scope="module")
def full_audit(defect_scene):
    return _audit_ok(defect_scene, "--pairs", "PairA:PairB", "--pairs", "PairA:PairC")


def _by_name(payload):
    return {o["name"]: o for o in payload["objects"]}


def _counts(obj):
    return {name: entry["count"] for name, entry in obj["defects"].items()}


@needs_blender
def test_the_builder_can_write_the_expected_matrix(tmp_path):
    blend, expected = tmp_path / "s.blend", tmp_path / "expected.json"
    proc = _blender("--python", BUILDER, "--", blend, expected)
    assert proc.returncode == 0, proc.stderr
    written = json.loads(expected.read_text(encoding="utf-8"))
    assert written["objects"] == builder.EXPECTED_OBJECTS
    assert written["pairs"] == builder.EXPECTED_PAIRS


@needs_blender
def test_every_planted_defect_class_is_reported_with_exactly_the_planted_count(full_audit):
    audited = _by_name(full_audit)
    assert set(audited) == set(builder.EXPECTED_OBJECTS)
    for name, expected in builder.EXPECTED_OBJECTS.items():
        assert _counts(audited[name]) == expected, name


@needs_blender
def test_each_class_is_planted_and_found_in_at_least_one_object(full_audit):
    totals = full_audit["summary"]["totals"]
    assert all(totals[cls] > 0 for cls in blender_mesh_audit.DEFECT_CLASSES)
    expected = {
        cls: sum(row[cls] for row in builder.EXPECTED_OBJECTS.values())
        for cls in blender_mesh_audit.DEFECT_CLASSES
    }
    assert totals == expected


@needs_blender
def test_a_clean_cube_reports_all_zeros_and_planted_objects_are_not_clean(full_audit):
    audited = _by_name(full_audit)
    cube = audited["CleanCube"]
    assert _counts(cube) == _zero_counts()
    assert cube["clean"] is True
    assert all(entry["locations"] == [] for entry in cube["defects"].values())
    assert cube["evaluated"] == {"verts": 8, "edges": 12, "faces": 6}
    dirty = {n for n, o in audited.items() if not o["clean"]}
    assert dirty == {n for n, row in builder.EXPECTED_OBJECTS.items() if any(row.values())}
    summary = full_audit["summary"]
    assert summary["clean"] is False
    assert summary["objects_with_defects"] == sorted(dirty)
    assert summary["objects_clean"] == len(audited) - len(dirty)


@needs_blender
def test_payload_matches_the_schema_and_lists_objects_in_name_order(full_audit):
    _validate(full_audit)
    names = [o["name"] for o in full_audit["objects"]]
    assert names == sorted(names)
    assert full_audit["tool"] == "blender_mesh_audit"
    assert full_audit["schema"] == "mesh-audit-v1"


@needs_blender
def test_only_render_visible_mesh_objects_are_audited(full_audit):
    audited = _by_name(full_audit)
    for name in builder.NOT_AUDITED:
        assert name not in audited


@needs_blender
def test_modifiers_are_evaluated_even_when_the_object_is_hidden_in_the_viewport(full_audit):
    audited = _by_name(full_audit)
    for name, faces in builder.EXPECTED_EVALUATED_FACES.items():
        assert audited[name]["evaluated"]["faces"] == faces, name


@needs_blender
def test_locations_are_world_space_coordinates_of_the_defect(full_audit):
    loose = _by_name(full_audit)["LooseVerts"]["defects"]["loose_verts"]["locations"]
    # The cube's 8 vertices come first; the four isolated ones are indices 8..11.
    assert [entry["index"] for entry in loose] == [8, 9, 10, 11]
    for entry, expected in zip(loose, builder.loose_world_positions()):
        assert entry["location"] == pytest.approx(expected, abs=1e-5)

    boundary = _by_name(full_audit)["OpenBox"]["defects"]["boundary_edges"]["locations"]
    ox, oy, oz = builder.LOCATIONS["OpenBox"]
    for entry in boundary:
        x, y, z = entry["location"]
        assert z == pytest.approx(oz + 1.0)  # the rim of the deleted top face
        assert abs(x - ox) <= 1.0 + 1e-5 and abs(y - oy) <= 1.0 + 1e-5
        assert len(entry["verts"]) == 2

    crossings = _by_name(full_audit)["PiercedCube"]["defects"]["self_intersections"]["locations"]
    ox, oy, oz = builder.LOCATIONS["PiercedCube"]
    assert sorted(e["location"][2] - oz for e in crossings) == pytest.approx([-1.0, 1.0], abs=1e-5)
    for entry in crossings:
        assert entry["location"][0] == pytest.approx(ox, abs=0.3 + 1e-5)
        assert entry["faces"][1] == 6  # the piercing quad is the seventh face


@needs_blender
def test_flipped_faces_are_the_minority_orientation(full_audit):
    flipped = _by_name(full_audit)["FlippedFaces"]["defects"]["inconsistent_normals"]["locations"]
    assert sorted(entry["index"] for entry in flipped) == [0, 1]  # bottom and top


@needs_blender
def test_pole_and_ngon_records_carry_their_size(full_audit):
    audited = _by_name(full_audit)
    poles = audited["PoleSphere"]["defects"]["poles"]["locations"]
    assert [entry["valence"] for entry in poles] == [8, 8]
    ngons = audited["NgonPrism"]["defects"]["ngons"]["locations"]
    assert [entry["sides"] for entry in ngons] == [6, 6]
    duplicates = audited["DuplicateVerts"]["defects"]["duplicate_verts"]["locations"]
    assert [(e["index"], e["merges_into"]) for e in duplicates][:2] == [(4, 1), (7, 2)]


@needs_blender
def test_pairs_report_the_interpenetration_between_named_objects(full_audit):
    pairs = {(p["a"], p["b"]): p for p in full_audit["pairs"]}
    crossing = pairs[("PairA", "PairB")]
    assert crossing["overlapping_face_pairs"] == builder.EXPECTED_PAIRS["PairA:PairB"] == 6
    assert crossing["interpenetrating"] is True
    assert len(crossing["locations"]) == 6
    # A fills x 0..2, y 20..22, z 0..2 and B is shifted by (1, 0.8, 0.6), so every
    # crossing lies inside their shared box.
    lo = (1.0, 20.8, 0.6)
    hi = (2.0, 22.0, 2.0)
    for entry in crossing["locations"]:
        for value, low, high in zip(entry["location"], lo, hi):
            assert low - 1e-5 <= value <= high + 1e-5
    apart = pairs[("PairA", "PairC")]
    assert apart["overlapping_face_pairs"] == 0
    assert apart["interpenetrating"] is False
    assert apart["locations"] == []
    assert full_audit["summary"]["pairs_checked"] == 2
    assert full_audit["summary"]["pairs_interpenetrating"] == 1


@needs_blender
def test_pairs_work_without_auditing_the_other_objects(defect_scene):
    payload = _audit_ok(defect_scene, "--objects", "CleanCube", "--pairs", "PairB:PairA")
    assert [o["name"] for o in payload["objects"]] == ["CleanCube"]
    assert payload["pairs"][0]["a"] == "PairB"
    assert payload["pairs"][0]["overlapping_face_pairs"] == 6


@needs_blender
def test_max_locations_limits_examples_but_never_the_counts(defect_scene):
    payload = _audit_ok(defect_scene, "--objects", "NonManifold", "PiercedCube", "--max-locations", "1")
    audited = _by_name(payload)
    boundary = audited["NonManifold"]["defects"]["boundary_edges"]
    assert boundary["count"] == 18
    assert len(boundary["locations"]) == 1
    full = _by_name(_audit_ok(defect_scene, "--objects", "NonManifold"))["NonManifold"]
    all_boundary = full["defects"]["boundary_edges"]["locations"]
    assert len(all_boundary) == 18
    assert boundary["locations"] == all_boundary[:1]
    assert [e["index"] for e in all_boundary] == sorted(e["index"] for e in all_boundary)
    assert len(audited["PiercedCube"]["defects"]["self_intersections"]["locations"]) == 1

    counts_only = _audit_ok(defect_scene, "--objects", "OpenBox", "--max-locations", "0")
    box = counts_only["objects"][0]["defects"]["boundary_edges"]
    assert box["count"] == 4 and box["locations"] == []


@needs_blender
def test_objects_option_selects_and_sorts(defect_scene):
    payload = _audit_ok(defect_scene, "--objects", "OpenBox", "CleanCube")
    assert [o["name"] for o in payload["objects"]] == ["CleanCube", "OpenBox"]
    assert payload["summary"]["objects_audited"] == 2
    assert payload["summary"]["totals"]["boundary_edges"] == 4
    assert payload["pairs"] == []


@needs_blender
def test_merge_distance_decides_which_vertices_are_duplicates(defect_scene):
    tight = _audit_ok(defect_scene, "--objects", "CleanCube")
    assert tight["objects"][0]["defects"]["duplicate_verts"]["count"] == 0
    loose = _audit_ok(defect_scene, "--objects", "CleanCube", "--merge-distance", "2.5")
    assert loose["objects"][0]["defects"]["duplicate_verts"]["count"] > 0
    assert loose["parameters"]["merge_distance"] == 2.5


@needs_blender
@pytest.mark.parametrize(
    "args, fragment",
    [
        (["--objects", "NoSuchObject"], "NoSuchObject"),
        (["--objects", "HiddenDefect"], "HiddenDefect"),
        (["--objects", "InHiddenCollection"], "InHiddenCollection"),
        (["--objects", "Marker"], "Marker"),
        (["--pairs", "PairA:NoSuchObject"], "PairA:NoSuchObject"),
        (["--pairs", "PairA:HiddenDefect"], "PairA:HiddenDefect"),
        (["--pairs", "PairA:PairA"], "same object"),
    ],
)
def test_unknown_or_invisible_objects_are_refused(defect_scene, args, fragment):
    proc = _audit(defect_scene, *args)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.count("\n") == 1 and fragment in proc.stderr


@needs_blender
def test_a_scene_with_no_mesh_objects_is_refused(tmp_path):
    empty = tmp_path / "empty.blend"
    script = tmp_path / "make_empty.py"
    script.write_text(
        "import bpy\nbpy.ops.wm.read_factory_settings(use_empty=True)\n"
        f"bpy.ops.wm.save_as_mainfile(filepath={str(empty)!r})\n",
        encoding="utf-8",
    )
    assert _blender("--python", script).returncode == 0
    proc = _audit(empty)
    assert proc.returncode == 2 and proc.stdout == ""
    assert "no render-visible mesh objects" in proc.stderr


@needs_blender
def test_output_is_byte_identical_across_runs(defect_scene):
    first = _audit(defect_scene, "--pairs", "PairA:PairB")
    second = _audit(defect_scene, "--pairs", "PairA:PairB")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout


@needs_blender
def test_a_mirrored_object_is_not_read_as_having_flipped_faces(tmp_path):
    """A negative scale reverses winding in world space; the audit must not read that as flipped faces."""
    blend = tmp_path / "mirrored.blend"
    script = tmp_path / "make_mirrored.py"
    script.write_text(
        "import bpy\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        "bpy.ops.mesh.primitive_cube_add()\n"
        "bpy.context.object.name = 'Cube'\n"
        "bpy.context.object.scale = (-1.0, 1.0, 1.0)\n"
        f"bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r})\n",
        encoding="utf-8",
    )
    assert _blender("--python", script).returncode == 0
    payload = _audit_ok(blend)
    assert _counts(payload["objects"][0]) == _zero_counts()


@needs_blender
@pytest.mark.skipif(not PROOF_V2_SCENE.is_file(), reason="proof-v2 scene.blend is local-only")
def test_proof_v2_garment_reports_its_four_boundary_edges():
    payload = _audit_ok(PROOF_V2_SCENE)
    audited = _by_name(payload)
    garment = audited["Garment"]["defects"]["boundary_edges"]
    assert garment["count"] == 4
    assert len(garment["locations"]) == 4
    assert audited["Body"]["clean"] is True
    _validate(payload)
    assert not any(math.isnan(v) for entry in garment["locations"] for v in entry["location"])


@needs_blender
def test_meshes_without_faces_are_audited_not_refused(tmp_path):
    blend = tmp_path / "faceless.blend"
    script = tmp_path / "make_faceless.py"
    script.write_text(
        "import bpy\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        "scene = bpy.context.scene\n"
        "edge = bpy.data.meshes.new('Edge')\n"
        "edge.from_pydata([(0, 0, 0), (1, 0, 0)], [(0, 1)], [])\n"
        "scene.collection.objects.link(bpy.data.objects.new('EdgeOnly', edge))\n"
        "scene.collection.objects.link(bpy.data.objects.new('Nothing', bpy.data.meshes.new('Nothing')))\n"
        f"bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r})\n",
        encoding="utf-8",
    )
    assert _blender("--python", script).returncode == 0
    payload = _audit_ok(blend, "--pairs", "EdgeOnly:Nothing")
    audited = _by_name(payload)
    assert _counts(audited["EdgeOnly"]) == dict(_zero_counts(), wire_edges=1)
    assert audited["Nothing"]["clean"] is True
    assert audited["Nothing"]["evaluated"] == {"verts": 0, "edges": 0, "faces": 0}
    assert payload["pairs"][0]["overlapping_face_pairs"] == 0
