"""Tests for `plugins/blender-inspect/scripts/blender_mesh.py`.

Moved with the tool from pil-agent-plugin's `tests/test_blender_mesh.py` and
adapted to `blender_common`. The contract-predicate tests stay with
`pil_contract_verdict` in the root `tests/`; this file keeps the tool's own
surfaces:

*   Hermetic: rejection paths are clean (byte-empty stdout, exit 2, one-line
    stderr), and the probe payload parser rejects malformed output.
*   Blender-gated: a real probe on a scene built at test time returns the
    `scene` shape, renamed `tool`, and byte-identical stdout across runs.
*   Corpus-gated: the swordsman corpus figures from the original tests, which
    skip unless both Blender and `PIL_AGENT_BLENDER_CORPUS` exist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blender_common  # noqa: E402
import blender_mesh  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
MESH_TOOL = SCRIPTS / "blender_mesh.py"
VERDICT_TOOL = REPO_ROOT / "scripts" / "pil_contract_verdict.py"

BLENDER, _ = blender_common.resolve_blender_executable()
needs_blender = pytest.mark.skipif(BLENDER is None, reason="Blender is not installed")

CORPUS_ROOT = Path(
    os.environ.get(
        "PIL_AGENT_BLENDER_CORPUS",
        r"C:/Projects/tms-heim/art/skeleton-crusaders/swordsman",
    )
)


def _run(*args, cwd=None):
    """Invoke a tool the way an agent would, and return the process."""
    cmd = [sys.executable, *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def _write_contract(tmp_path, expect=(), invariant=(), name="contract.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"expect_change": list(expect),
                                "invariant": list(invariant)}), encoding="utf-8")
    return path


def _item_for(result, predicate, pair=0):
    matches = [i for i in result["pairs"][pair]["items"] if i["predicate"] == predicate]
    assert len(matches) == 1, f"expected exactly one {predicate!r}, got {matches}"
    return matches[0]


def _make_png(path):
    from PIL import Image
    Image.new("RGB", (4, 4), (0, 0, 0)).save(path)
    return path


# --- rejection paths (hermetic) ---------------------------------------------


class TestRejection:
    def test_missing_blender_executable_exits_2_with_empty_stdout(self, tmp_path):
        blend = tmp_path / "any.blend"
        blend.write_bytes(b"")

        proc = _run(MESH_TOOL, blend, "--blender-executable", tmp_path / "nope.exe")

        assert proc.returncode == 2
        assert proc.stdout == "", "rejection path must not leak partial stdout"
        assert proc.stderr.strip().startswith("blender_mesh:"), proc.stderr
        assert len(proc.stderr.strip().splitlines()) == 1
        assert "Traceback" not in proc.stderr

    def test_missing_blend_file_exits_2_with_empty_stdout(self, tmp_path):
        # Any existing file counts as a resolvable Blender, which lets this
        # path be exercised on machines without Blender.
        fake_blender = tmp_path / "fake_blender.exe"
        fake_blender.write_bytes(b"")

        proc = _run(
            MESH_TOOL,
            tmp_path / "does_not_exist.blend",
            "--blender-executable",
            fake_blender,
        )

        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "blend file not found" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_a_failed_probe_is_a_refusal_not_a_partial_payload(self, tmp_path, monkeypatch, capsys):
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"placeholder")
        monkeypatch.setattr(
            blender_mesh.blender_common, "resolve_blender_executable", lambda _e: ("blender", None)
        )
        monkeypatch.setattr(
            blender_mesh, "probe_blend", lambda *_a, **_k: (None, "blender exited 1: boom")
        )

        rc = blender_mesh.main([str(blend)])
        captured = capsys.readouterr()

        assert rc == 2
        assert captured.out == ""
        assert captured.err == "blender_mesh: blender exited 1: boom\n"


# --- probe payload parser (hermetic) ----------------------------------------


class TestProbeParser:
    """The sentinel parser is the trust boundary between Blender's chatty
    stdout and the wrapper's payload; it must reject anything the probe did not
    itself write."""

    B, E = blender_common.sentinels(blender_mesh.TOOL)

    def _parse(self, text):
        return blender_common.extract_payload(text, blender_mesh.TOOL)

    def test_a_clean_lf_payload_parses(self):
        assert self._parse(f"noise\n{self.B}\n{{\"k\": 1}}\n{self.E}\nmore\n") == {"k": 1}

    def test_a_crlf_payload_parses_on_windows(self):
        assert self._parse(f"noise\r\n{self.B}\r\n{{\"k\": 2}}\r\n{self.E}\r\n") == {"k": 2}

    def test_no_sentinels_is_a_none_payload(self):
        assert self._parse("nothing here") is None

    def test_two_sentinel_blocks_is_a_none_payload(self):
        text = f"{self.B}\n{{\"k\":1}}\n{self.E}\n{self.B}\n{{\"k\":2}}\n{self.E}\n"
        assert self._parse(text) is None

    def test_non_json_body_is_a_none_payload(self):
        assert self._parse(f"{self.B}\nnot json\n{self.E}\n") is None

    def test_the_old_tool_sentinels_are_not_accepted(self):
        text = "<<<PIL_AGENT_BLENDER_MESH_BEGIN>>>\n{}\n<<<PIL_AGENT_BLENDER_MESH_END>>>\n"
        assert self._parse(text) is None


def test_payload_keeps_the_scene_shape_under_the_new_tool_name():
    scene = {"path": "x", "mesh_objects": {}, "totals": {}, "bounding_dimensions_world": None}
    payload = blender_mesh.build_payload(Path("x.blend"), "blender", scene, "5.2.0")

    assert payload["tool"] == "blender_mesh"
    assert payload["version"] == blender_mesh.TOOL_VERSION
    assert set(payload) == {"tool", "version", "parameters", "scene", "interpretation_limits"}
    assert set(payload["parameters"]) == {
        "blend", "blender_executable", "blender_version", "timeout_seconds",
    }
    assert payload["scene"] is scene


# --- Blender-gated: a scene built at test time -------------------------------


@pytest.fixture(scope="module")
def two_object_blend(tmp_path_factory):
    root = tmp_path_factory.mktemp("bi_mesh")
    blend = root / "two.blend"
    script = root / "make.py"
    script.write_text(
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete(use_global=False)\n"
        "bpy.ops.mesh.primitive_cube_add(location=(0, 0, 1))\n"
        "bpy.context.object.name = 'Box'\n"
        "bpy.ops.mesh.primitive_plane_add(size=4)\n"
        "bpy.context.object.name = 'Floor'\n"
        f"bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r})\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [BLENDER, "--factory-startup", "--background", "--python-exit-code", "1",
         "--python", str(script)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    return blend


@needs_blender
class TestRealProbe:
    def test_probe_reports_counts_and_bounds(self, two_object_blend):
        proc = _run(MESH_TOOL, two_object_blend)
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)

        assert payload["tool"] == "blender_mesh"
        objects = payload["scene"]["mesh_objects"]
        assert list(objects) == ["Box", "Floor"]
        assert (objects["Box"]["polys"], objects["Box"]["verts"]) == (6, 8)
        assert (objects["Floor"]["polys"], objects["Floor"]["verts"]) == (1, 4)
        assert payload["scene"]["totals"] == {
            "mesh_object_count": 2, "polys": 7, "verts": 12, "edges": 16,
        }
        assert payload["scene"]["bounding_dimensions_world"]["dimensions"] == [4.0, 4.0, 2.0]

    def test_a_blend_probe_is_byte_deterministic_across_runs(self, two_object_blend):
        proc1 = _run(MESH_TOOL, two_object_blend)
        proc2 = _run(MESH_TOOL, two_object_blend)
        assert proc1.returncode == 0 and proc2.returncode == 0
        assert proc1.stdout == proc2.stdout


# --- corpus-gated: real Blender against real .blend files -------------------

CORPUS_MISSING = pytest.mark.skipif(
    not CORPUS_ROOT.is_dir() or BLENDER is None,
    reason=(
        f"corpus {CORPUS_ROOT} or Blender missing; set "
        "PIL_AGENT_BLENDER_CORPUS to enable"
    ),
)


def _probe_blend(blend_path):
    """Invoke the CLI on a real .blend and return (payload_dict, raw_stdout)."""
    proc = _run(MESH_TOOL, blend_path)
    assert proc.returncode == 0, (
        f"blender_mesh exited {proc.returncode}\nSTDERR:\n{proc.stderr}"
    )
    return json.loads(proc.stdout), proc.stdout


@pytest.fixture(scope="module")
def rev2_probe():
    return _probe_blend(CORPUS_ROOT / "runs" / "checkpoint-rev2-lower-20260818.blend")


@pytest.fixture(scope="module")
def rev3_probe():
    return _probe_blend(CORPUS_ROOT / "runs" / "checkpoint-rev3-faceting-20260818.blend")


@pytest.fixture(scope="module")
def source_probe():
    return _probe_blend(
        CORPUS_ROOT / "source" / "SM_Chr_Skeleton_BlackOrderSwordsman_01.blend"
    )


@pytest.fixture(scope="module")
def roundtrip_probe():
    return _probe_blend(
        CORPUS_ROOT
        / "staging"
        / "roundtrip"
        / "SM_Chr_Skeleton_BlackOrderSwordsman_01.roundtrip.blend"
    )


@CORPUS_MISSING
class TestCorpusRealBlender:
    def test_rev2_tabard_matches_docs_handoff_figure(self, rev2_probe):
        tabard = rev2_probe[0]["scene"]["mesh_objects"]["SKS_Garment_Tabard_01"]
        assert tabard["polys"] == 1350
        assert tabard["verts"] == 1584

    def test_rev3_tabard_matches_docs_handoff_figure(self, rev3_probe):
        tabard = rev3_probe[0]["scene"]["mesh_objects"]["SKS_Garment_Tabard_01"]
        assert tabard["polys"] == 787

    def test_rev2_and_rev3_whole_scene_totals(self, rev2_probe, rev3_probe):
        assert rev2_probe[0]["scene"]["totals"]["polys"] == 9120
        assert rev3_probe[0]["scene"]["totals"]["polys"] == 16276
        assert rev2_probe[0]["scene"]["totals"]["mesh_object_count"] == 19
        assert rev3_probe[0]["scene"]["totals"]["mesh_object_count"] == 19

    def test_undermailskirt_is_the_no_change_control_across_all_three_revisions(
        self, source_probe, rev2_probe, rev3_probe
    ):
        for name, probe in [("source", source_probe), ("rev2", rev2_probe),
                            ("rev3", rev3_probe)]:
            skirt = probe[0]["scene"]["mesh_objects"]["SKS_Garment_UnderMailSkirt_01"]
            assert skirt["polys"] == 362, f"{name} skirt drifted: {skirt['polys']}"
            assert skirt["verts"] == 384

    def test_source_to_roundtrip_topology_is_object_preserving_but_scene_violated(
        self, tmp_path, source_probe, roundtrip_probe
    ):
        # blender_mesh payloads go straight into pil_contract_verdict.
        stats_a = tmp_path / "source.json"
        stats_b = tmp_path / "roundtrip.json"
        stats_a.write_text(json.dumps(source_probe[0]), encoding="utf-8")
        stats_b.write_text(json.dumps(roundtrip_probe[0]), encoding="utf-8")

        contract = _write_contract(
            tmp_path,
            invariant=[
                "geometry.topology_preserved",
                "geometry.topology_preserved(SKS_Garment_Tabard_01)",
            ],
        )
        a = _make_png(tmp_path / "a.png")
        b = _make_png(tmp_path / "b.png")
        proc = _run(VERDICT_TOOL, a, b, "--contract", contract,
                    "--scene-stats-a", stats_a, "--scene-stats-b", stats_b)
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)

        whole = _item_for(result, "geometry.topology_preserved")
        assert whole["verdict"] == "VIOLATED"
        assert "SKS_Donor_BodyBelowChin_01" in whole["evidence"]["objects_removed_from_b"]
        assert whole["evidence"]["objects_with_changed_counts"] == []

        tabard = _item_for(result, "geometry.topology_preserved(SKS_Garment_Tabard_01)")
        assert tabard["verdict"] == "SATISFIED"
