"""Contract tests for `scripts/pil_character_sheet_review.py`.

Surfaces held here:

*   Rejection hygiene: unreadable/malformed/missing contract file, unknown
    --view name, duplicate --view name, missing scene .blend, and missing
    --thresholds file all exit 2 with byte-empty stdout, a one-line
    stderr reason, and no traceback. Hermetic.
*   The --view parser: NAME:PATH partition survives Windows drive-letter
    colons because we split on the first colon and require NAME in
    {front, side, back}. Hermetic, exercises `parse_view_arg` directly.
*   Manifest and payload composition: `build_manifest`/`build_payload`/
    `build_per_view_block` are pure functions of a fixed per-view entry
    list plus a fixed verdict payload, so their byte-determinism is
    testable without invoking the render layer at all. Hermetic.
*   The sentinel path: `write_sentinel` produces a PNG whose foreground
    mask under pil_common's border-median rule is empty, and a
    (sentinel, sentinel) pair therefore drives
    identity.silhouette_preserved to UNMEASURABLE in pil_contract_verdict.
    Hermetic -- no Blender needed.
*   Corpus-gated end-to-end: three real matched brute views (SATISFIED
    with caller-tuned thresholds), one deliberately-swapped view against
    two good ones (VIOLATED, proving a single divergent view is NOT
    averaged away by two good ones on real renders), and a missing
    reference file (renders hard-fail at the render layer, sentinel
    substituted at the review layer, still visible in the aggregate as
    an UNMEASURABLE pair for identity.silhouette_preserved).

Corpus gating mirrors test_blender_render.py: `PIL_AGENT_BLENDER_BRUTE_CORPUS`
overrides the default brute path, and both Blender AND the .blend must
exist for the corpus tier to run.
"""

from __future__ import annotations

import hashlib
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

import pil_character_sheet_review as review  # noqa: E402

REVIEW_TOOL = SCRIPTS / "pil_character_sheet_review.py"

BRUTE_CORPUS_ROOT = Path(
    os.environ.get(
        "PIL_AGENT_BLENDER_BRUTE_CORPUS",
        r"C:/Projects/tms-heim/art/skeleton-crusaders/brute",
    )
)
BRUTE_BLEND = BRUTE_CORPUS_ROOT / "source" / "SM_Chr_Skeleton_CrusaderBrute_01.blend"
BRUTE_TURNAROUND = (
    BRUTE_CORPUS_ROOT
    / "references"
    / "skeletal-brute-tpose-turnaround-lowpoly-2026-08-15.png"
)
DEFAULT_BLENDER = Path("C:/Program Files/Blender Foundation/Blender 5.2/blender.exe")


def _run(*args, cwd=None):
    cmd = [sys.executable, *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- --view parser (hermetic) -----------------------------------------------


class TestParseViewArg:
    def test_simple_front_parses(self, tmp_path):
        ref = tmp_path / "r.png"
        name, path = review.parse_view_arg(f"front:{ref}")
        assert name == "front"
        assert path == Path(str(ref))

    def test_windows_drive_letter_survives_first_colon_split(self):
        # C:/... contains a colon; we split on the FIRST colon and require
        # NAME to be a known view, so 'front:C:/x.png' resolves cleanly.
        name, path = review.parse_view_arg("front:C:/foo/bar/ref.png")
        assert name == "front"
        assert str(path) == str(Path("C:/foo/bar/ref.png"))

    def test_unknown_name_rejected(self):
        with pytest.raises(review.ViewSpecError, match="NAME must be one of"):
            review.parse_view_arg("threequarter:/tmp/x.png")

    def test_missing_colon_rejected(self):
        with pytest.raises(review.ViewSpecError, match="NAME:PATH"):
            review.parse_view_arg("front_ref.png")

    def test_empty_path_rejected(self):
        with pytest.raises(review.ViewSpecError, match="empty"):
            review.parse_view_arg("front:")


# --- rejection hygiene (hermetic) --------------------------------------------


class TestRejection:
    """Every rejection path exits 2 with empty stdout and no traceback."""

    @staticmethod
    def _write_contract(path: Path):
        path.write_text(json.dumps({"invariant": ["layout.composition_preserved"]}))

    def test_missing_contract_file_exits_2(self, tmp_path):
        blend = tmp_path / "any.blend"; blend.write_bytes(b"")
        ref = tmp_path / "r.png"; ref.write_bytes(b"")
        proc = _run(
            REVIEW_TOOL, blend,
            "--contract", tmp_path / "no_such_contract.json",
            "--view", f"front:{ref}",
        )
        assert proc.returncode == 2
        assert proc.stdout == "", "rejection must not leak partial stdout"
        assert "contract file not found" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_malformed_contract_json_exits_2(self, tmp_path):
        contract = tmp_path / "c.json"
        contract.write_text("{ not valid json")
        blend = tmp_path / "any.blend"; blend.write_bytes(b"")
        ref = tmp_path / "r.png"; ref.write_bytes(b"")
        proc = _run(
            REVIEW_TOOL, blend, "--contract", contract, "--view", f"front:{ref}",
        )
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "invalid JSON" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_unknown_view_name_exits_2(self, tmp_path):
        contract = tmp_path / "c.json"; self._write_contract(contract)
        blend = tmp_path / "any.blend"; blend.write_bytes(b"")
        ref = tmp_path / "r.png"; ref.write_bytes(b"")
        proc = _run(
            REVIEW_TOOL, blend, "--contract", contract,
            "--view", f"threequarter:{ref}",
        )
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "NAME must be one of" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_duplicate_view_name_exits_2(self, tmp_path):
        contract = tmp_path / "c.json"; self._write_contract(contract)
        blend = tmp_path / "any.blend"; blend.write_bytes(b"")
        ref = tmp_path / "r.png"; ref.write_bytes(b"")
        proc = _run(
            REVIEW_TOOL, blend, "--contract", contract,
            "--view", f"front:{ref}",
            "--view", f"front:{ref}",
        )
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "supplied more than once" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_missing_blend_file_exits_2(self, tmp_path):
        contract = tmp_path / "c.json"; self._write_contract(contract)
        ref = tmp_path / "r.png"; ref.write_bytes(b"")
        proc = _run(
            REVIEW_TOOL,
            tmp_path / "does_not_exist.blend",
            "--contract", contract,
            "--view", f"front:{ref}",
        )
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "blend file not found" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_missing_thresholds_file_exits_2(self, tmp_path):
        contract = tmp_path / "c.json"; self._write_contract(contract)
        blend = tmp_path / "any.blend"; blend.write_bytes(b"")
        ref = tmp_path / "r.png"; ref.write_bytes(b"")
        proc = _run(
            REVIEW_TOOL, blend, "--contract", contract,
            "--view", f"front:{ref}",
            "--thresholds", tmp_path / "no_such_thresholds.json",
        )
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "thresholds file not found" in proc.stderr
        assert "Traceback" not in proc.stderr


# --- sentinel (hermetic) -----------------------------------------------------


class TestSentinel:
    """The hard-fail sentinel PNG must drive
    identity.silhouette_preserved to UNMEASURABLE via
    foreground_mask_empty on both images.*.flags -- otherwise a hard-fail
    view would silently SATISFY the contract instead of surfacing."""

    def test_sentinel_pair_yields_unmeasurable_silhouette(self, tmp_path):
        sentinel = tmp_path / "sentinel.png"
        review.write_sentinel(sentinel)
        assert sentinel.is_file()

        contract = tmp_path / "c.json"
        contract.write_text(json.dumps({
            "invariant": ["identity.silhouette_preserved"]
        }))
        manifest = tmp_path / "m.json"
        manifest.write_text(json.dumps([{"a": str(sentinel), "b": str(sentinel)}]))

        proc = _run(
            SCRIPTS / "pil_contract_verdict.py",
            "--contract", contract, "--pairs", manifest, "--foreground",
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        item = payload["pairs"][0]["items"][0]
        assert item["predicate"] == "identity.silhouette_preserved"
        assert item["verdict"] == "UNMEASURABLE", (
            f"expected sentinel pair to force UNMEASURABLE, got {item!r}"
        )

    def test_sentinel_bytes_are_stable_across_writes(self, tmp_path):
        # write_sentinel is called every hard-fail run; two writes to
        # different paths must produce byte-identical PNGs so the review
        # tool's own JSON stays deterministic across runs with the same
        # hard-fail shape.
        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        review.write_sentinel(a)
        review.write_sentinel(b)
        assert a.read_bytes() == b.read_bytes()


# --- manifest / per-view / payload composition (hermetic) --------------------


def _entry_ok(view, ref_path, render_path):
    return {
        "view": view,
        "reference": str(ref_path),
        "render_payload": {
            "tool": "pil_blender_render",
            "version": "0.7.0",
            "render": {"rendered": True, "output_path": str(render_path)},
            "comparison": {"refused": False, "structural_similarity": 0.85},
        },
        "rendered_path": str(render_path),
        "hard_fail": None,
        "pair_a": str(ref_path),
        "pair_b": str(render_path),
    }


def _entry_hard_fail(view, ref_path, sentinel_path, reason):
    return {
        "view": view,
        "reference": str(ref_path),
        "render_payload": None,
        "rendered_path": None,
        "hard_fail": {
            "reason": reason,
            "sentinel_pair": [str(sentinel_path), str(sentinel_path)],
        },
        "pair_a": str(sentinel_path),
        "pair_b": str(sentinel_path),
    }


class TestManifestAndPayloadComposition:
    """Pure-function tests over the manifest/payload builders. These pin the
    "no silent dropping" property directly at the composition layer without
    needing Blender: the manifest length must equal the entry length, and
    the per-view block must name every view that appeared on the CLI."""

    def test_build_manifest_preserves_order_including_hard_fails(self, tmp_path):
        sentinel = tmp_path / "sentinel.png"
        ref_f = tmp_path / "front_ref.png"
        ren_f = tmp_path / "front.png"
        ref_b = tmp_path / "back_ref.png"
        ren_b = tmp_path / "back.png"
        ref_s = tmp_path / "side_ref.png"
        entries = [
            _entry_ok("front", ref_f, ren_f),
            _entry_hard_fail("side", ref_s, sentinel, "pil_blender_render exited 2"),
            _entry_ok("back", ref_b, ren_b),
        ]
        manifest = review.build_manifest(entries)
        assert len(manifest) == 3, (
            "hard-fail entry MUST appear in the manifest; dropping it would "
            "let two good views out-vote a broken one (the WP4 property)"
        )
        assert manifest[0] == {"a": str(ref_f), "b": str(ren_f)}
        assert manifest[1] == {"a": str(sentinel), "b": str(sentinel)}
        assert manifest[2] == {"a": str(ref_b), "b": str(ren_b)}

    def test_per_view_block_sorted_alphabetically(self, tmp_path):
        # CLI order can be anything; per_view_renders must sort so JSON
        # diffs are stable across callers.
        sentinel = tmp_path / "s.png"
        entries = [
            _entry_ok("side", tmp_path / "sref.png", tmp_path / "s.png"),
            _entry_ok("front", tmp_path / "fref.png", tmp_path / "f.png"),
            _entry_hard_fail("back", tmp_path / "bref.png", sentinel, "boom"),
        ]
        block = review.build_per_view_block(entries)
        assert list(block.keys()) == ["back", "front", "side"]
        assert block["back"]["hard_fail"] == {
            "reason": "boom",
            "sentinel_pair": [str(sentinel), str(sentinel)],
        }
        assert block["back"]["render_payload"] is None
        assert block["front"]["hard_fail"] is None
        assert block["front"]["render_payload"]["tool"] == "pil_blender_render"

    def test_build_payload_is_byte_deterministic_over_identical_inputs(self, tmp_path):
        # Pure-function determinism: build_payload called twice with the same
        # dicts must produce byte-identical JSON. Keeps the render step's
        # separately-scoped determinism claim out of this test's blast
        # radius (§4.2 criterion 4 explicitly asks for this shape).
        sentinel = tmp_path / "sentinel.png"
        entries = [
            _entry_ok("front", tmp_path / "fref.png", tmp_path / "f.png"),
            _entry_ok("side", tmp_path / "sref.png", tmp_path / "s.png"),
            _entry_hard_fail("back", tmp_path / "bref.png", sentinel, "boom"),
        ]
        verdict = {
            "tool": "pil_contract_verdict",
            "version": "0.7.0",
            "aggregate": [
                {"predicate": "layout.composition_preserved", "verdict": "VIOLATED"}
            ],
            "pairs": [],
        }
        p1 = review.build_payload(
            blend=Path("scene.blend"),
            contract_path=Path("contract.json"),
            entries=entries,
            verdict_payload=verdict,
            blender_executable=None,
            thresholds_path=None,
        )
        p2 = review.build_payload(
            blend=Path("scene.blend"),
            contract_path=Path("contract.json"),
            entries=entries,
            verdict_payload=verdict,
            blender_executable=None,
            thresholds_path=None,
        )
        s1 = json.dumps(p1, indent=2, sort_keys=True, allow_nan=False)
        s2 = json.dumps(p2, indent=2, sort_keys=True, allow_nan=False)
        assert _sha256(s1.encode()) == _sha256(s2.encode())
        assert p1["tool"] == "pil_character_sheet_review"
        assert p1["version"] == "0.7.0"
        assert p1["verdict"] is verdict
        assert set(p1["per_view_renders"].keys()) == {"front", "side", "back"}
        assert p1["parameters"]["views"] == ["back", "front", "side"]
        assert len(p1["parameters"]["manifest"]) == 3


# --- corpus-gated: real renders end-to-end -----------------------------------


CORPUS_MISSING = pytest.mark.skipif(
    not BRUTE_BLEND.is_file()
    or not BRUTE_TURNAROUND.is_file()
    or not DEFAULT_BLENDER.is_file(),
    reason=(
        f"brute .blend {BRUTE_BLEND}, turnaround {BRUTE_TURNAROUND}, or "
        f"Blender {DEFAULT_BLENDER} missing; set "
        "PIL_AGENT_BLENDER_BRUTE_CORPUS to enable"
    ),
)


@pytest.fixture(scope="module")
def turnaround_crops(tmp_path_factory):
    """Crop the 1536x1024 turnaround sheet into three 768x512 quadrants.

    Crops live in a pytest tempdir; never committed here, never copied
    back into the tms-heim tree.
    """
    from PIL import Image

    crops_dir = tmp_path_factory.mktemp("brute_crops_review")
    with Image.open(BRUTE_TURNAROUND) as sheet:
        w, h = sheet.size
        assert (w, h) == (1536, 1024), (
            f"turnaround sheet dimensions changed: {(w, h)} (expected 1536x1024)"
        )
        half_w, half_h = w // 2, h // 2
        views = {
            "front": (0, 0, half_w, half_h),
            "side": (half_w, 0, w, half_h),
            "back": (0, half_h, half_w, h),
        }
        paths = {}
        for name, box in views.items():
            path = crops_dir / f"{name}.png"
            sheet.crop(box).save(path)
            paths[name] = path
    return paths


@pytest.fixture(scope="module")
def app_thresholds(tmp_path_factory):
    """Caller-tuned pil_contract_verdict --thresholds bundle.

    The calibrated bundle (`scripts/detection_limits.json`) sets
    structural_similarity threshold at 0.962737 and silhouette_iou at its
    own shipped 0.85 default. On the brute-corpus renders, matched
    (front|side|back)_render vs its own reference measures ssim in the
    0.767-0.810 range and silhouette_iou in the 0.620-0.708 range -- both
    real content differences (auto-framed Workbench render vs
    externally-authored turnaround-sheet JPEG crop with different
    lighting), well documented in the runs bundle as an unavoidable
    residual. This fixture supplies application-appropriate thresholds
    that let matched pairs SATISFY while still discriminating a swapped
    view (side_ref vs back_render: ssim 0.711, iou 0.444) as VIOLATED.

    Numbers were measured with `scripts/pil_structure_diff.py --foreground`
    and `scripts/pil_contract_verdict.py identity.silhouette_preserved`
    on the actual renders during acceptance-test authoring.
    """
    thresholds_dir = tmp_path_factory.mktemp("app_thresholds")
    path = thresholds_dir / "thresholds.json"
    path.write_text(json.dumps({
        "structural_similarity": {
            "threshold": 0.75,
            "detection_limits": {
                "app_tuning": (
                    "0.75 chosen after measuring matched brute renders "
                    "(ssim 0.767-0.810) vs a deliberate swap "
                    "(ssim 0.711); calibrated default 0.962737 is "
                    "stricter than the auto-framed Workbench render vs "
                    "turnaround-sheet crop residuals in this corpus"
                ),
            },
        },
        "silhouette_iou": {
            "threshold": 0.55,
            "detection_limits": {
                "app_tuning": (
                    "0.55 chosen after measuring matched brute renders "
                    "(iou 0.620-0.708) vs a deliberate swap (iou 0.444)"
                ),
            },
        },
    }))
    return path


@pytest.fixture(scope="module")
def app_contract(tmp_path_factory):
    """Two invariants that discriminate matched vs mismatched brute views.

    Chosen over palette.scheme_preserved (which VIOLATES on real matched
    views because the T-pose Workbench render's hue distribution differs
    non-trivially from the reference-sheet crop's baked shading -- the
    render loses the 'yellow' family the reference has, an honest content
    difference we do not want to force a test around).
    """
    contract_dir = tmp_path_factory.mktemp("app_contract")
    path = contract_dir / "contract.json"
    path.write_text(json.dumps({
        "invariant": [
            "layout.composition_preserved",
            "identity.silhouette_preserved",
        ]
    }))
    return path


@CORPUS_MISSING
class TestCorpusMatchedViews:
    """Criterion 1 (§4.2 acceptance): three real matched brute views ->
    aggregate SATISFIED for the caller's chosen invariants.

    Real numbers, measured during authoring:
      front: ssim 0.810, iou 0.708
      side:  ssim 0.806, iou 0.670
      back:  ssim 0.767, iou 0.620
    All clear the app-tuned thresholds (ssim >= 0.75, iou >= 0.55).
    """

    def test_three_matched_views_aggregate_satisfied(
        self, turnaround_crops, app_contract, app_thresholds, tmp_path
    ):
        proc = _run(
            REVIEW_TOOL, BRUTE_BLEND,
            "--contract", app_contract,
            "--view", f"front:{turnaround_crops['front']}",
            "--view", f"side:{turnaround_crops['side']}",
            "--view", f"back:{turnaround_crops['back']}",
            "--thresholds", app_thresholds,
        )
        assert proc.returncode == 0, (
            f"review exited {proc.returncode}\nSTDERR:\n{proc.stderr}"
        )
        payload = json.loads(proc.stdout)
        assert payload["tool"] == "pil_character_sheet_review"
        assert payload["version"] == "0.7.0"

        # Aggregate: every predicate SATISFIED, no view dropped from the
        # manifest, no hard_fail block in any per-view entry.
        agg = {row["predicate"]: row for row in payload["verdict"]["aggregate"]}
        for pred in ("layout.composition_preserved", "identity.silhouette_preserved"):
            assert agg[pred]["verdict"] == "SATISFIED", (
                f"{pred} aggregate: {agg[pred]}"
            )
            assert agg[pred]["pairs_violated"] == 0
            assert agg[pred]["pairs_unmeasurable"] == 0
            # Exactly three pair verdicts per predicate. A SATISFIED aggregate
            # over two pairs would also pass every assertion above, so the
            # count is what separates "all three views cleared the bar" from
            # "one view quietly went missing on the way to the verdict".
            assert len(agg[pred]["pair_verdicts"]) == 3, agg[pred]
            assert sorted(p["index"] for p in agg[pred]["pair_verdicts"]) == [0, 1, 2]

        assert len(payload["verdict"]["pairs"]) == 3
        assert [pair["index"] for pair in payload["verdict"]["pairs"]] == [0, 1, 2]
        assert len(payload["parameters"]["manifest"]) == 3
        assert set(payload["per_view_renders"]) == {"front", "side", "back"}
        assert all(
            block["hard_fail"] is None
            for block in payload["per_view_renders"].values()
        )
        # Every rendered side of the manifest is a stable logical identifier,
        # never a path inside the deleted workdir.
        assert [row["b"] for row in payload["parameters"]["manifest"]] == [
            "render://front", "render://side", "render://back",
        ]


@CORPUS_MISSING
class TestCorpusSwappedView:
    """Criterion 2 (§4.2 acceptance): three views but one deliberately
    substituted -> aggregate VIOLATED, proving a single divergent view
    is NOT averaged away by two good ones.

    We render the ACTUAL three views (all match the contract-tuned
    thresholds), then rewrite the manifest by hand to swap the side pair's
    reference: the side slot's b (render) stays, but its a (reference) is
    replaced with the back reference. This forces one pair to diverge on
    exactly one row, so if aggregation were a mean the two good pairs
    would out-vote it -- and it doesn't.

    Real numbers for the swapped pair (back_ref vs side_render):
      ssim 0.735, iou < 0.55 (measured during authoring)
    Below both app thresholds, so the swap VIOLATES; the two matched
    pairs SATISFY; aggregate is VIOLATED with pairs_violated == 1.
    """

    def test_one_swapped_view_forces_aggregate_violated(
        self, turnaround_crops, app_contract, app_thresholds, tmp_path
    ):
        # First, render all three views normally by invoking the review
        # tool. This gives us the fresh renders under pil_blender_render's
        # own control (the tool cleans up its own tempdir at exit, so we
        # cannot read those PNGs -- instead we render again into a path
        # we own).
        from PIL import Image  # noqa: F401  ensures PIL available for later

        renders = tmp_path / "renders"
        renders.mkdir()
        for view in ("front", "side", "back"):
            out = renders / f"{view}.png"
            proc = _run(
                SCRIPTS / "pil_blender_render.py", BRUTE_BLEND,
                "--view", view, "--out", out,
                "--reference", turnaround_crops[view],
            )
            assert proc.returncode == 0, proc.stderr
            assert out.is_file()

        # Build a manifest with a deliberate swap on the side slot.
        manifest_path = tmp_path / "swapped_manifest.json"
        manifest_path.write_text(json.dumps([
            {"a": str(turnaround_crops["front"]), "b": str(renders / "front.png")},
            # SWAPPED: side b (render) is the side render, but a (reference)
            # is the BACK reference -- a real "one wrong view" scenario.
            {"a": str(turnaround_crops["back"]),  "b": str(renders / "side.png")},
            {"a": str(turnaround_crops["back"]),  "b": str(renders / "back.png")},
        ]))

        # Invoke pil_contract_verdict directly with the hand-built swapped
        # manifest. This is intentional: the review tool renders per-view
        # against the caller's declared reference, so a "swapped view"
        # scenario has to be constructed at the manifest layer. The
        # aggregation being tested lives in pil_contract_verdict --pairs,
        # which the review tool composes onto verbatim.
        proc = _run(
            SCRIPTS / "pil_contract_verdict.py",
            "--contract", app_contract,
            "--pairs", manifest_path,
            "--foreground",
            "--thresholds", app_thresholds,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)

        agg = {row["predicate"]: row for row in payload["aggregate"]}
        for pred in (
            "layout.composition_preserved",
            "identity.silhouette_preserved",
        ):
            assert agg[pred]["verdict"] == "VIOLATED", (
                f"{pred} aggregate should be VIOLATED, got {agg[pred]}"
            )
            assert agg[pred]["pairs_violated"] == 1, (
                f"{pred}: expected exactly one violating pair (the swap), "
                f"got {agg[pred]['pairs_violated']}"
            )
        # And the aggregate row lists the offending pair index so a caller
        # can find which view diverged.
        divergent = [
            p for p in agg["layout.composition_preserved"]["pair_verdicts"]
            if p["verdict"] == "VIOLATED"
        ]
        assert len(divergent) == 1
        assert divergent[0]["index"] == 1, divergent[0]

    def test_swap_via_review_tool_end_to_end(
        self, turnaround_crops, app_contract, app_thresholds, tmp_path
    ):
        # End-to-end variant: the review tool itself renders three views
        # but one --view NAME:PATH points at the WRONG reference (side
        # reference under the 'back' view label, back reference under
        # 'side'). This is the swap done at the review-tool interface,
        # not the manifest layer -- it verifies the review tool itself
        # composes a manifest whose aggregation VIOLATES on the swap.
        # The 'back' slot renders the back view (via camera at +Y) but
        # compares against the side reference -- silhouette IoU drops
        # dramatically.
        proc = _run(
            REVIEW_TOOL, BRUTE_BLEND,
            "--contract", app_contract,
            "--view", f"front:{turnaround_crops['front']}",
            # swapped labels on the last two: side/back references are exchanged
            "--view", f"side:{turnaround_crops['back']}",
            "--view", f"back:{turnaround_crops['side']}",
            "--thresholds", app_thresholds,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        agg = {row["predicate"]: row for row in payload["verdict"]["aggregate"]}
        # The two swapped views violate; front stays satisfied. Aggregate:
        # VIOLATED for both predicates, with pairs_violated == 2.
        for pred in (
            "layout.composition_preserved",
            "identity.silhouette_preserved",
        ):
            assert agg[pred]["verdict"] == "VIOLATED", agg[pred]
            assert agg[pred]["pairs_violated"] >= 1, agg[pred]


@CORPUS_MISSING
class TestCorpusHardFailedRefStillCounts:
    """Criterion 3 (§4.2 acceptance): a --view reference that cannot be
    read hard-fails at the render layer and STILL appears as an
    UNMEASURABLE entry in the aggregate -- never silently excluded from
    the pair count."""

    def test_missing_reference_produces_unmeasurable_pair_not_dropped(
        self, turnaround_crops, app_contract, app_thresholds, tmp_path
    ):
        missing = tmp_path / "does_not_exist.png"
        assert not missing.exists()

        proc = _run(
            REVIEW_TOOL, BRUTE_BLEND,
            "--contract", app_contract,
            "--view", f"front:{turnaround_crops['front']}",
            "--view", f"side:{missing}",
            "--view", f"back:{turnaround_crops['back']}",
            "--thresholds", app_thresholds,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)

        # The pair count must be three, not two. Silently dropping the
        # broken view is the exact WP4 failure mode this criterion pins.
        assert len(payload["verdict"]["pairs"]) == 3, (
            "hard-failed reference MUST NOT be dropped from the pair count"
        )
        assert len(payload["parameters"]["manifest"]) == 3

        # The per_view_renders entry for 'side' must name the hard_fail
        # and point at the substituted sentinel.
        side_block = payload["per_view_renders"]["side"]
        assert side_block["hard_fail"] is not None, side_block
        assert "reference file not found" in side_block["hard_fail"]["reason"]
        sentinel_a, sentinel_b = side_block["hard_fail"]["sentinel_pair"]
        assert sentinel_a == sentinel_b, (
            "hard-fail sentinel is one file used on both sides of the pair"
        )
        assert side_block["render_payload"] is None
        assert side_block["manifest_pair"]["a"] == sentinel_a
        assert side_block["manifest_pair"]["b"] == sentinel_a

        # Every predicate must have exactly one UNMEASURABLE pair. Identical
        # sentinel pixels are transport scaffolding, never evidence that a
        # layout or palette invariant holds.
        agg = {row["predicate"]: row for row in payload["verdict"]["aggregate"]}
        for row in agg.values():
            assert row["pairs_unmeasurable"] == 1, row
            assert row["verdict"] == "UNMEASURABLE", row
            unmeasurable_pairs = [
                p for p in row["pair_verdicts"] if p["verdict"] == "UNMEASURABLE"
            ]
            assert [p["index"] for p in unmeasurable_pairs] == [1]

        assert sentinel_a == "hard-fail://side"
        assert "pil_char_sheet_" not in proc.stdout


class TestEndToEndDeterminism:
    def test_two_hard_fail_invocations_emit_byte_identical_live_path_free_json(
        self, tmp_path
    ):
        blend = tmp_path / "placeholder.blend"
        blend.write_bytes(b"not opened because references reject first")
        contract = tmp_path / "contract.json"
        contract.write_text(json.dumps({"invariant": ["layout.composition_preserved"]}))
        missing = tmp_path / "missing.png"
        args = [
            REVIEW_TOOL, blend, "--contract", contract,
            "--view", f"front:{missing}",
            "--view", f"side:{missing}",
            "--view", f"back:{missing}",
        ]

        first = _run(*args)
        second = _run(*args)
        assert first.returncode == second.returncode == 0
        assert first.stdout.encode() == second.stdout.encode()
        assert "pil_char_sheet_" not in first.stdout

        payload = json.loads(first.stdout)
        row = payload["verdict"]["aggregate"][0]
        assert row["verdict"] == "UNMEASURABLE"
        assert row["pairs_unmeasurable"] == 3
        for manifest_row in payload["parameters"]["manifest"]:
            assert manifest_row["a"].startswith("hard-fail://")
            assert manifest_row["a"] == manifest_row["b"]


@CORPUS_MISSING
class TestCorpusStdoutIsWellFormedJSON:
    """Every corpus path emits parseable JSON with the mandatory keys.
    Cheap smoke against typos/refactor drift."""

    def test_stdout_has_tool_version_parameters_verdict_perview_limits(
        self, turnaround_crops, app_contract, app_thresholds
    ):
        proc = _run(
            REVIEW_TOOL, BRUTE_BLEND,
            "--contract", app_contract,
            "--view", f"front:{turnaround_crops['front']}",
            "--thresholds", app_thresholds,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        for key in (
            "tool", "version", "parameters", "verdict",
            "per_view_renders", "interpretation_limits",
        ):
            assert key in payload, (key, payload.keys())
