"""Contract tests for `scripts/pil_blender_mesh.py` and the geometry.* unlock
in `scripts/pil_contract_verdict.py`.

The tool has two independent surfaces to hold:

*   The wrapper itself: rejection paths must be clean (byte-empty stdout, exit
    2, one-line stderr) and the sentinel-delimited probe parser must not accept
    malformed output. These run hermetically with no Blender install.
*   The contract predicate: geometry.* stays UNMEASURABLE without scene stats,
    and resolves to SATISFIED / VIOLATED when both sides are supplied. The
    scene-stats JSONs are synthesised in-file so this surface is testable
    without Blender or a corpus.

A third tier is *corpus-gated*: it spawns a real Blender subprocess against a
`.blend` in the swordsman corpus, so the tabard verification, whole-model
totals, and the source-vs-roundtrip topology finding live in code rather than
in prose. Those tests skip cleanly on machines that lack either Blender or the
corpus via `PIL_AGENT_BLENDER_CORPUS`, mirroring the `PIL_AGENT_REFERENCE_IMAGE`
convention in `conftest.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pil_blender_mesh  # noqa: E402

MESH_TOOL = SCRIPTS / "pil_blender_mesh.py"
VERDICT_TOOL = SCRIPTS / "pil_contract_verdict.py"

CORPUS_ROOT = Path(
    os.environ.get(
        "PIL_AGENT_BLENDER_CORPUS",
        r"C:/Projects/tms-heim/art/skeleton-crusaders/swordsman",
    )
)
DEFAULT_BLENDER = Path("C:/Program Files/Blender Foundation/Blender 5.2/blender.exe")


# --- helpers -----------------------------------------------------------------


def _run(*args, cwd=None):
    """Invoke a bundled tool the way an agent would, and return the process."""
    cmd = [sys.executable, *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def _write_stats(path, mesh_objects, extras=None):
    """Compose a pil_blender_mesh payload from raw mesh counts.

    Only the fields the geometry predicates read are populated; the rest of
    the wrapper's payload is elided because the loader validates just
    scene.mesh_objects and scene.totals.
    """
    totals = {
        "mesh_object_count": len(mesh_objects),
        "polys": sum(v["polys"] for v in mesh_objects.values()),
        "verts": sum(v["verts"] for v in mesh_objects.values()),
        "edges": sum(v.get("edges", 0) for v in mesh_objects.values()),
    }
    payload = {
        "tool": "pil_blender_mesh",
        "version": pil_blender_mesh.TOOL_VERSION,
        "parameters": {"blend": "synthetic", "blender_executable": None,
                       "blender_version": "synthetic", "timeout_seconds": 300},
        "scene": {
            "path": "synthetic",
            "mesh_objects": mesh_objects,
            "totals": totals,
            "bounding_dimensions_world": None,
        },
        "interpretation_limits": [],
    }
    if extras:
        payload["scene"].update(extras)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _tabard_stats(tmp_path, polys, verts, name="stats.json"):
    """One mesh object named like the corpus asset, so predicate-by-name works."""
    return _write_stats(
        tmp_path / name,
        {"SKS_Garment_Tabard_01": {"polys": polys, "verts": verts, "edges": polys * 2,
                                    "material_slot_count": 1, "materials": ["Mat"],
                                    "dimensions": [1.0, 1.0, 1.0]}},
    )


def _write_contract(tmp_path, expect=(), invariant=(), name="contract.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"expect_change": list(expect),
                                "invariant": list(invariant)}), encoding="utf-8")
    return path


def _item_for(result, predicate, pair=0):
    matches = [i for i in result["pairs"][pair]["items"] if i["predicate"] == predicate]
    assert len(matches) == 1, f"expected exactly one {predicate!r}, got {matches}"
    return matches[0]


# Simple 1x1 PNGs to satisfy pil_contract_verdict's image-pair positional args
# when the test is really about scene stats -- the tool still reads the images,
# but no image-derived predicate is declared, so their content does not matter.
def _make_png(path):
    from PIL import Image
    Image.new("RGB", (4, 4), (0, 0, 0)).save(path)
    return path


# --- rejection paths (hermetic) ---------------------------------------------


class TestRejection:
    def test_missing_blender_executable_exits_2_with_empty_stdout(self, tmp_path):
        # Arrange: point at a path that resolves to nothing, and pass any .blend
        # (the executable check runs first).
        blend = tmp_path / "any.blend"
        blend.write_bytes(b"")

        # Act
        proc = _run(MESH_TOOL, blend, "--blender-executable", tmp_path / "nope.exe")

        # Assert
        assert proc.returncode == 2
        assert proc.stdout == "", "rejection path must not leak partial stdout"
        assert proc.stderr.strip().startswith("pil_blender_mesh:"), proc.stderr
        assert "Traceback" not in proc.stderr

    def test_missing_blend_file_exits_2_with_empty_stdout(self, tmp_path):
        # Arrange: any existing file counts as a resolvable Blender for
        # `resolve_blender_executable`, which lets this path be exercised on
        # machines without Blender.
        fake_blender = tmp_path / "fake_blender.exe"
        fake_blender.write_bytes(b"")

        # Act
        proc = _run(
            MESH_TOOL,
            tmp_path / "does_not_exist.blend",
            "--blender-executable",
            fake_blender,
        )

        # Assert
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "blend file not found" in proc.stderr
        assert "Traceback" not in proc.stderr


# --- probe payload parser (hermetic) ----------------------------------------


class TestProbeParser:
    """`_extract_probe_payload` is the trust boundary between Blender's chatty
    stdout and the wrapper's payload; it must reject anything the probe did not
    itself write."""

    B = pil_blender_mesh._SENTINEL_BEGIN
    E = pil_blender_mesh._SENTINEL_END

    def test_a_clean_lf_payload_parses(self):
        text = f"noise\n{self.B}\n{{\"k\": 1}}\n{self.E}\nmore\n"
        assert pil_blender_mesh._extract_probe_payload(text) == {"k": 1}

    def test_a_crlf_payload_parses_on_windows(self):
        text = f"noise\r\n{self.B}\r\n{{\"k\": 2}}\r\n{self.E}\r\n"
        assert pil_blender_mesh._extract_probe_payload(text) == {"k": 2}

    def test_no_sentinels_is_a_none_payload(self):
        assert pil_blender_mesh._extract_probe_payload("nothing here") is None

    def test_two_sentinel_blocks_is_a_none_payload(self):
        text = (
            f"{self.B}\n{{\"k\":1}}\n{self.E}\n"
            f"{self.B}\n{{\"k\":2}}\n{self.E}\n"
        )
        assert pil_blender_mesh._extract_probe_payload(text) is None

    def test_non_json_body_is_a_none_payload(self):
        text = f"{self.B}\nnot json\n{self.E}\n"
        assert pil_blender_mesh._extract_probe_payload(text) is None


# --- contract geometry predicates against synthesised stats (hermetic) ------


class TestContractGeometryUnlock:
    """With scene stats supplied for both sides, geometry.* resolves; missing
    exactly one side turns it back into UNMEASURABLE with the missing side
    named. The refuse-list default (no stats at all) is covered by
    test_contract_verdict.py and re-asserted once here for safety."""

    UNCALIBRATED = "uncalibrated: detection limit not available"

    def _pair(self, tmp_path):
        return (
            _make_png(tmp_path / "a.png"),
            _make_png(tmp_path / "b.png"),
        )

    def _verdict(self, tmp_path, contract, stats_a=None, stats_b=None,
                 expect=(), invariant=()):
        a, b = self._pair(tmp_path)
        if contract is None:
            contract_path = _write_contract(tmp_path, expect=expect, invariant=invariant)
        else:
            contract_path = contract
        args = [VERDICT_TOOL, a, b, "--contract", contract_path]
        if stats_a is not None:
            args += ["--scene-stats-a", stats_a]
        if stats_b is not None:
            args += ["--scene-stats-b", stats_b]
        proc = _run(*args)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout), proc.stdout

    def test_whole_scene_poly_count_decrease_resolves(self, tmp_path):
        a_stats = _tabard_stats(tmp_path, polys=1350, verts=1584, name="a.json")
        b_stats = _tabard_stats(tmp_path, polys=787, verts=928, name="b.json")
        result, _ = self._verdict(
            tmp_path, None, a_stats, b_stats,
            expect=["geometry.poly_count.decrease"],
        )
        item = _item_for(result, "geometry.poly_count.decrease")
        assert item["verdict"] == "SATISFIED"
        assert item["evidence"] == {"a_polys": 1350, "b_polys": 787,
                                    "scope": "scene_totals"}

    def test_named_object_topology_preserved_on_matching_counts(self, tmp_path):
        a_stats = _tabard_stats(tmp_path, polys=362, verts=384, name="a.json")
        b_stats = _tabard_stats(tmp_path, polys=362, verts=384, name="b.json")
        result, _ = self._verdict(
            tmp_path, None, a_stats, b_stats,
            invariant=["geometry.topology_preserved(SKS_Garment_Tabard_01)"],
        )
        item = _item_for(result, "geometry.topology_preserved(SKS_Garment_Tabard_01)")
        assert item["verdict"] == "SATISFIED"
        assert item["evidence"]["a_polys"] == 362
        assert item["evidence"]["b_polys"] == 362

    def test_whole_scene_topology_violated_when_an_object_is_removed(self, tmp_path):
        # Mirrors the real source-vs-roundtrip finding: same per-object counts,
        # but one object dropped from side b.
        a_stats = _write_stats(
            tmp_path / "a.json",
            {
                "SKS_Garment_Tabard_01": {"polys": 546, "verts": 624, "edges": 0,
                                          "material_slot_count": 1, "materials": [],
                                          "dimensions": [0, 0, 0]},
                "SKS_Donor_BodyBelowChin_01": {"polys": 2243, "verts": 1668, "edges": 0,
                                               "material_slot_count": 1, "materials": [],
                                               "dimensions": [0, 0, 0]},
            },
        )
        b_stats = _write_stats(
            tmp_path / "b.json",
            {
                "SKS_Garment_Tabard_01": {"polys": 546, "verts": 624, "edges": 0,
                                          "material_slot_count": 1, "materials": [],
                                          "dimensions": [0, 0, 0]},
            },
        )
        result, _ = self._verdict(
            tmp_path, None, a_stats, b_stats,
            invariant=["geometry.topology_preserved"],
        )
        item = _item_for(result, "geometry.topology_preserved")
        assert item["verdict"] == "VIOLATED"
        assert item["evidence"]["objects_removed_from_b"] == ["SKS_Donor_BodyBelowChin_01"]
        assert item["evidence"]["objects_with_changed_counts"] == []

    def test_named_object_absent_from_stats_is_unmeasurable(self, tmp_path):
        a_stats = _tabard_stats(tmp_path, polys=100, verts=100, name="a.json")
        b_stats = _tabard_stats(tmp_path, polys=100, verts=100, name="b.json")
        result, _ = self._verdict(
            tmp_path, None, a_stats, b_stats,
            expect=["geometry.poly_count.decrease(SKS_Not_A_Real_Object_01)"],
        )
        item = _item_for(result, "geometry.poly_count.decrease(SKS_Not_A_Real_Object_01)")
        assert item["verdict"] == "UNMEASURABLE"
        assert "not present in scene stats" in item["reason"]
        # No numeric evidence: absence must not read as a count of zero.
        assert item["evidence"] == {}

    def test_only_one_side_supplied_is_unmeasurable_naming_the_missing_side(
        self, tmp_path
    ):
        a_stats = _tabard_stats(tmp_path, polys=100, verts=100)
        contract = _write_contract(
            tmp_path, expect=["geometry.poly_count.decrease"]
        )
        a, b = self._pair(tmp_path)
        proc = _run(VERDICT_TOOL, a, b, "--contract", contract,
                    "--scene-stats-a", a_stats)
        assert proc.returncode == 0
        result = json.loads(proc.stdout)
        item = _item_for(result, "geometry.poly_count.decrease")
        assert item["verdict"] == "UNMEASURABLE"
        assert "missing side b" in item["reason"]

    def test_no_scene_stats_preserves_the_default_refusal(self, tmp_path):
        # Byte-compatibility guard: without stats, geometry.* still refuses via
        # the refuse-list path -- the diff must not have changed that.
        result, _ = self._verdict(
            tmp_path, None,
            expect=["geometry.poly_count.decrease"],
        )
        item = _item_for(result, "geometry.poly_count.decrease")
        assert item["verdict"] == "UNMEASURABLE"
        assert "scene mesh statistics" in item["reason"]

    def test_multi_pair_with_scene_stats_is_a_parser_error(self, tmp_path):
        a_stats = _tabard_stats(tmp_path, polys=100, verts=100)
        a, b = self._pair(tmp_path)
        manifest = tmp_path / "pairs.json"
        manifest.write_text(json.dumps([{"a": str(a), "b": str(b)}]), encoding="utf-8")
        contract = _write_contract(tmp_path, expect=["geometry.poly_count.decrease"])
        proc = _run(VERDICT_TOOL, "--pairs", manifest, "--contract", contract,
                    "--scene-stats-a", a_stats)
        assert proc.returncode == 2
        assert "single-pair only" in proc.stderr

    def test_geometry_negative_finding_carries_the_uncalibrated_limit(self, tmp_path):
        # A held invariant is a negative finding -- "no decrease observed" --
        # so the detection limit must be attached. There is no shipped limit
        # for poly_count, so the uncalibrated sentinel is what we expect.
        a_stats = _tabard_stats(tmp_path, polys=100, verts=100, name="a.json")
        b_stats = _tabard_stats(tmp_path, polys=100, verts=100, name="b.json")
        result, _ = self._verdict(
            tmp_path, None, a_stats, b_stats,
            invariant=["geometry.poly_count.unchanged"],
        )
        item = _item_for(result, "geometry.poly_count.unchanged")
        assert item["verdict"] == "SATISFIED"
        assert item["detection_limit"] == self.UNCALIBRATED

    def test_verdict_run_with_scene_stats_is_byte_deterministic(self, tmp_path):
        a_stats = _tabard_stats(tmp_path, polys=1350, verts=1584, name="a.json")
        b_stats = _tabard_stats(tmp_path, polys=787, verts=928, name="b.json")
        contract = _write_contract(
            tmp_path,
            expect=["geometry.poly_count.decrease"],
            invariant=["geometry.topology_preserved(SKS_Garment_Tabard_01)"],
        )
        a, b = self._pair(tmp_path)
        args = [VERDICT_TOOL, a, b, "--contract", contract,
                "--scene-stats-a", a_stats, "--scene-stats-b", b_stats]
        r1 = _run(*args)
        r2 = _run(*args)
        assert r1.returncode == 0 and r2.returncode == 0
        assert r1.stdout == r2.stdout


# --- corpus-gated: real Blender against real .blend files -------------------

CORPUS_MISSING = pytest.mark.skipif(
    not CORPUS_ROOT.is_dir() or not DEFAULT_BLENDER.is_file(),
    reason=(
        f"corpus {CORPUS_ROOT} or Blender {DEFAULT_BLENDER} missing; set "
        "PIL_AGENT_BLENDER_CORPUS to enable"
    ),
)


def _probe_blend(blend_path):
    """Invoke the CLI on a real .blend and return (payload_dict, raw_stdout)."""
    proc = _run(MESH_TOOL, blend_path)
    assert proc.returncode == 0, (
        f"pil_blender_mesh exited {proc.returncode}\nSTDERR:\n{proc.stderr}"
    )
    return json.loads(proc.stdout), proc.stdout


# Probing Blender takes ~10-20s per file; cache per session so the whole
# corpus-gated tier stays under half a minute on this machine.
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
    # Two roundtrip files sit side by side; pin the exact one so a naming
    # change over there does not silently switch what we're comparing.
    return _probe_blend(
        CORPUS_ROOT
        / "staging"
        / "roundtrip"
        / "SM_Chr_Skeleton_BlackOrderSwordsman_01.roundtrip.blend"
    )


@CORPUS_MISSING
class TestCorpusRealBlender:
    def test_rev2_tabard_matches_docs_handoff_figure(self, rev2_probe):
        payload, _ = rev2_probe
        tabard = payload["scene"]["mesh_objects"]["SKS_Garment_Tabard_01"]
        assert tabard["polys"] == 1350
        assert tabard["verts"] == 1584

    def test_rev3_tabard_matches_docs_handoff_figure(self, rev3_probe):
        payload, _ = rev3_probe
        tabard = payload["scene"]["mesh_objects"]["SKS_Garment_Tabard_01"]
        assert tabard["polys"] == 787

    def test_rev2_and_rev3_whole_scene_totals(self, rev2_probe, rev3_probe):
        # Ground truth for the run bundle's parts.json reconciliation.
        assert rev2_probe[0]["scene"]["totals"]["polys"] == 9120
        assert rev3_probe[0]["scene"]["totals"]["polys"] == 16276
        assert rev2_probe[0]["scene"]["totals"]["mesh_object_count"] == 19
        assert rev3_probe[0]["scene"]["totals"]["mesh_object_count"] == 19

    def test_undermailskirt_is_the_no_change_control_across_all_three_revisions(
        self, source_probe, rev2_probe, rev3_probe
    ):
        # docs/phase3-handoff.md section 6 claims this part sits at 362 polys
        # across source / rev2 / rev3. Verified live.
        for name, probe in [("source", source_probe), ("rev2", rev2_probe),
                            ("rev3", rev3_probe)]:
            skirt = probe[0]["scene"]["mesh_objects"]["SKS_Garment_UnderMailSkirt_01"]
            assert skirt["polys"] == 362, f"{name} skirt drifted: {skirt['polys']}"
            assert skirt["verts"] == 384

    def test_a_blend_probe_is_byte_deterministic_across_runs(self, tmp_path):
        # One .blend probed twice. Two independent CLI invocations must produce
        # identical stdout, or downstream diffs would flap.
        blend = CORPUS_ROOT / "runs" / "checkpoint-rev2-lower-20260818.blend"
        proc1 = _run(MESH_TOOL, blend)
        proc2 = _run(MESH_TOOL, blend)
        assert proc1.returncode == 0 and proc2.returncode == 0
        assert proc1.stdout == proc2.stdout

    def test_source_to_roundtrip_topology_is_object_preserving_but_scene_violated(
        self, tmp_path, source_probe, roundtrip_probe
    ):
        # The whole-scene topology invariant is VIOLATED because the roundtrip
        # export drops SKS_Donor_BodyBelowChin_01, but every object that DID
        # survive matches polys/verts exactly -- so a per-object topology check
        # on the tabard is SATISFIED. Both facts belong in the record.
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

        tabard = _item_for(
            result, "geometry.topology_preserved(SKS_Garment_Tabard_01)"
        )
        assert tabard["verdict"] == "SATISFIED"
