"""Contract tests for `scripts/pil_blender_render.py`.

Surfaces held here:

*   The wrapper itself: missing Blender, missing .blend, and missing reference
    file are clean rejections (exit 2, byte-empty stdout, one-line stderr,
    no traceback). These run hermetically with no Blender install.
*   The probe-payload parser: sentinel-delimited JSON extraction rejects
    malformation. Hermetic.
*   The comparison helper: refusal reasons in reference/render `images.*.flags`
    surface into `comparison.refused` (not `diff.flags`, which is a different
    field). Hermetic -- exercises `build_comparison` directly with a hand-built
    diff payload.
*   Corpus-gated: a real Blender subprocess against the brute .blend renders
    front/side/back, compares against reference crops taken at test time from
    the real turnaround sheet (never committed, never copied back), and pins
    the whole acceptance surface from the plan's §4.1: two renders of the
    same (.blend, view) pair are byte-identical PNGs, all three views produce
    comparisons that neither refuse nor fire aspect_ratio_mismatch /
    resolution_mismatch, an empty-scene .blend refuses every view without a
    crash, a mostly-background reference refuses cleanly, and side-view
    discrimination against the wrong-view reference holds.

Corpus and Blender are detected via `PIL_AGENT_BLENDER_BRUTE_CORPUS` (points
at the brute asset root) plus DEFAULT_BLENDER; both must exist for the corpus
tier to run, following the `PIL_AGENT_BLENDER_CORPUS` pattern established by
test_blender_mesh.py.
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

import pil_blender_render  # noqa: E402

RENDER_TOOL = SCRIPTS / "pil_blender_render.py"

# The brute asset lives outside this repo (per docs/phase3-b2-b3-build-plan
# §4.1 acceptance criteria, we compare against the real corpus rather than a
# synthetic fixture). Set PIL_AGENT_BLENDER_BRUTE_CORPUS to override.
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
DEFAULT_BLENDER = Path("C:/Program Files/Blender Foundation/Blender 5.1/blender.exe")


# --- helpers -----------------------------------------------------------------


def _run(*args, cwd=None):
    """Invoke a bundled tool the way an agent would, and return the process."""
    cmd = [sys.executable, *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- rejection paths (hermetic) ---------------------------------------------


class TestRejection:
    """Every rejection path must exit 2 with byte-empty stdout, a one-line
    stderr reason, and no Python traceback -- so a caller cannot confuse a
    refusal for a partial payload."""

    def test_missing_blender_executable_exits_2_with_empty_stdout(self, tmp_path):
        blend = tmp_path / "any.blend"
        blend.write_bytes(b"")
        out = tmp_path / "out.png"

        proc = _run(
            RENDER_TOOL, blend, "--view", "front", "--out", out,
            "--blender-executable", tmp_path / "nope.exe",
        )

        assert proc.returncode == 2
        assert proc.stdout == "", "rejection path must not leak partial stdout"
        assert proc.stderr.strip().startswith("pil_blender_render:")
        assert "Traceback" not in proc.stderr
        assert not out.exists(), "no PNG must appear on the rejection path"

    def test_missing_blend_file_exits_2_with_empty_stdout(self, tmp_path):
        # Any existing file is a resolvable "Blender" so this test runs on
        # machines without a real Blender install.
        fake_blender = tmp_path / "fake_blender.exe"
        fake_blender.write_bytes(b"")

        proc = _run(
            RENDER_TOOL,
            tmp_path / "does_not_exist.blend",
            "--view", "front", "--out", tmp_path / "out.png",
            "--blender-executable", fake_blender,
        )

        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "blend file not found" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_missing_reference_file_exits_2_with_empty_stdout(self, tmp_path):
        fake_blender = tmp_path / "fake_blender.exe"
        fake_blender.write_bytes(b"")
        blend = tmp_path / "any.blend"
        blend.write_bytes(b"")

        proc = _run(
            RENDER_TOOL, blend, "--view", "front",
            "--out", tmp_path / "out.png",
            "--reference", tmp_path / "missing.png",
            "--blender-executable", fake_blender,
        )

        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "reference file not found" in proc.stderr
        assert "Traceback" not in proc.stderr


# --- probe payload parser (hermetic) ----------------------------------------


class TestProbeParser:
    """The render probe's stdout goes through _extract_render_payload, the
    trust boundary between Blender's chatty output and the wrapper's payload;
    it must reject anything the probe did not itself write."""

    B = pil_blender_render._SENTINEL_BEGIN
    E = pil_blender_render._SENTINEL_END

    def test_a_clean_lf_payload_parses(self):
        text = f"noise\n{self.B}\n{{\"k\": 1}}\n{self.E}\nmore\n"
        assert pil_blender_render._extract_render_payload(text) == {"k": 1}

    def test_a_crlf_payload_parses_on_windows(self):
        text = f"noise\r\n{self.B}\r\n{{\"k\": 2}}\r\n{self.E}\r\n"
        assert pil_blender_render._extract_render_payload(text) == {"k": 2}

    def test_no_sentinels_is_a_none_payload(self):
        assert pil_blender_render._extract_render_payload("nothing here") is None

    def test_two_sentinel_blocks_is_a_none_payload(self):
        text = (
            f"{self.B}\n{{\"k\":1}}\n{self.E}\n"
            f"{self.B}\n{{\"k\":2}}\n{self.E}\n"
        )
        assert pil_blender_render._extract_render_payload(text) is None

    def test_non_json_body_is_a_none_payload(self):
        text = f"{self.B}\nnot json\n{self.E}\n"
        assert pil_blender_render._extract_render_payload(text) is None


# --- comparison refusal semantics (hermetic) --------------------------------


class TestBuildComparisonRefusal:
    """build_comparison reads refusal flags from images.a.flags / images.b.flags,
    NOT from diff.flags. This test pins that choice: reading the wrong list
    would silently let foreground_mask_empty pass through as a fabricated
    similarity number (criterion 4 in the plan)."""

    @staticmethod
    def _diff_payload(a_flags, b_flags):
        return {
            "images": {
                "a": {
                    "size": [768, 512],
                    "flags": list(a_flags),
                    "foreground": {"applied": True, "source": "background_estimate"},
                },
                "b": {
                    "size": [768, 512],
                    "flags": list(b_flags),
                    "foreground": {"applied": True, "source": "alpha"},
                },
            },
            "diff": {
                "structural_similarity": 0.9,
                "dhash_distance": 3,
                "ahash_distance": 4,
                "changed_area_fraction": 0.1,
                "changed_region_bbox_fractional": [0.1, 0.1, 0.9, 0.9],
                "flags": ["foreground_source_mismatch"],
            },
        }

    def test_neither_side_flagged_is_not_refused(self, tmp_path):
        c = pil_blender_render.build_comparison(
            self._diff_payload([], []),
            tmp_path / "ref.png",
            tmp_path / "render.png",
        )
        assert c["refused"] is False
        assert c["refused_reason"] is None
        assert c["structural_similarity"] == 0.9
        assert c["diff_flags"] == ["foreground_source_mismatch"]

    def test_reference_foreground_mask_empty_forces_refusal(self, tmp_path):
        c = pil_blender_render.build_comparison(
            self._diff_payload(["foreground_mask_empty"], []),
            tmp_path / "ref.png",
            tmp_path / "render.png",
        )
        assert c["refused"] is True
        assert c["structural_similarity"] is None
        assert c["dhash_distance"] is None
        assert c["changed_area_fraction"] is None
        assert "reference:foreground_mask_empty" in c["refused_reason"]

    def test_render_foreground_too_small_forces_refusal(self, tmp_path):
        c = pil_blender_render.build_comparison(
            self._diff_payload([], ["foreground_too_small"]),
            tmp_path / "ref.png",
            tmp_path / "render.png",
        )
        assert c["refused"] is True
        assert "render:foreground_too_small" in c["refused_reason"]

    def test_diff_flags_alone_do_not_trigger_refusal(self, tmp_path):
        # foreground_source_mismatch appears in diff.flags, not images.*.flags.
        # It must NOT force refusal -- an alpha render vs a border-median
        # reference is a legitimate composition of B2's own output with any
        # non-alpha caller reference.
        payload = self._diff_payload([], [])
        payload["diff"]["flags"] = [
            "foreground_source_mismatch",
            "foreground_aspect_mismatch",
        ]
        c = pil_blender_render.build_comparison(
            payload, tmp_path / "ref.png", tmp_path / "render.png"
        )
        assert c["refused"] is False
        assert c["diff_flags"] == [
            "foreground_source_mismatch",
            "foreground_aspect_mismatch",
        ]

    def test_foreground_support_insufficient_forces_refusal(self, tmp_path):
        payload = self._diff_payload([], [])
        payload["diff"]["structural_similarity"] = None
        payload["diff"]["flags"] = ["foreground_support_insufficient"]
        c = pil_blender_render.build_comparison(
            payload, tmp_path / "ref.png", tmp_path / "render.png"
        )
        assert c["refused"] is True
        assert c["structural_similarity"] is None
        assert "foreground_support_insufficient" in c["refused_reason"]


class TestAtomicOutput:
    def test_render_failure_preserves_existing_output_and_removes_stage(
        self, tmp_path, monkeypatch, capsys
    ):
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"placeholder")
        out = tmp_path / "render.png"
        original = b"pre-existing caller output"
        out.write_bytes(original)

        monkeypatch.setattr(
            pil_blender_render, "resolve_blender_executable", lambda _explicit: "blender"
        )

        def fail_after_partial_write(
            _blender, _blend, _view, staged, _width, _height, _margin, timeout
        ):
            staged.write_bytes(b"partial render")
            return None, "synthetic render failure"

        monkeypatch.setattr(pil_blender_render, "run_render", fail_after_partial_write)
        rc = pil_blender_render.main([
            str(blend), "--view", "front", "--out", str(out)
        ])
        captured = capsys.readouterr()

        assert rc == 2
        assert captured.out == ""
        assert "synthetic render failure" in captured.err
        assert out.read_bytes() == original
        assert list(tmp_path.glob(f".{out.name}.*.png")) == []


# --- corpus-gated: real Blender against real .blend + real turnaround sheet -


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
    """Crop the 1536x1024 turnaround sheet into four 768x512 quadrants.

    Crops live in a pytest tempdir, never committed into this repo and never
    written back into the tms-heim tree. This mirrors B1's external-corpus
    rule: read-only, derived-at-test-time, not persisted.
    """
    from PIL import Image

    crops_dir = tmp_path_factory.mktemp("brute_crops")
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


def _render(tmp_path_factory, view, reference=None):
    """One CLI invocation per (view, reference) pair; return (proc, payload)."""
    outdir = tmp_path_factory.mktemp(f"render_{view}")
    out_path = outdir / f"{view}.png"
    args = [
        RENDER_TOOL, BRUTE_BLEND,
        "--view", view,
        "--out", out_path,
    ]
    if reference is not None:
        args += ["--reference", reference]
    proc = _run(*args)
    assert proc.returncode == 0, (
        f"pil_blender_render exited {proc.returncode}\nSTDERR:\n{proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    payload["_out_path"] = out_path
    return proc, payload


# Session-scoped: one render per (view, has-reference) tuple lives across the
# whole corpus tier so we do not pay Blender's ~30s startup per assertion.
@pytest.fixture(scope="module")
def front_pair(tmp_path_factory, turnaround_crops):
    return _render(tmp_path_factory, "front", turnaround_crops["front"])


@pytest.fixture(scope="module")
def side_pair(tmp_path_factory, turnaround_crops):
    return _render(tmp_path_factory, "side", turnaround_crops["side"])


@pytest.fixture(scope="module")
def back_pair(tmp_path_factory, turnaround_crops):
    return _render(tmp_path_factory, "back", turnaround_crops["back"])


@CORPUS_MISSING
class TestCorpusRealBlenderRender:
    def test_front_reference_render_does_not_refuse_or_fire_framing_flags(self, front_pair):
        _, payload = front_pair
        c = payload["comparison"]
        assert c["refused"] is False, c["refused_reason"]
        assert "aspect_ratio_mismatch" not in c["diff_flags"], c["diff_flags"]
        assert "resolution_mismatch" not in c["diff_flags"], c["diff_flags"]

    def test_side_reference_render_does_not_refuse_or_fire_framing_flags(self, side_pair):
        _, payload = side_pair
        c = payload["comparison"]
        assert c["refused"] is False, c["refused_reason"]
        assert "aspect_ratio_mismatch" not in c["diff_flags"], c["diff_flags"]
        assert "resolution_mismatch" not in c["diff_flags"], c["diff_flags"]

    def test_back_reference_render_does_not_refuse_or_fire_framing_flags(self, back_pair):
        _, payload = back_pair
        c = payload["comparison"]
        assert c["refused"] is False, c["refused_reason"]
        assert "aspect_ratio_mismatch" not in c["diff_flags"], c["diff_flags"]
        assert "resolution_mismatch" not in c["diff_flags"], c["diff_flags"]

    def test_render_resolution_is_pinned_to_the_reference_dimensions(self, front_pair):
        _, payload = front_pair
        # tl_front crop is 768x512; render must match to keep the two framing
        # flags from firing (§4.1 the whole point of --reference).
        assert payload["parameters"]["resolution_used"] == [768, 512]
        assert payload["parameters"]["resolution_source"] == "reference_pinned"

    def test_two_renders_of_the_same_view_are_byte_identical_pngs(
        self, tmp_path_factory
    ):
        # Determinism is a scoped claim (same-machine, same-install) but is a
        # required test per §4.1 criterion 1. Two independent CLI invocations,
        # not one render loaded twice -- "compare against itself" would be
        # theatre.
        outdir = tmp_path_factory.mktemp("determinism")
        out_a = outdir / "a.png"
        out_b = outdir / "b.png"
        args_base = [RENDER_TOOL, BRUTE_BLEND, "--view", "front"]
        p1 = _run(*args_base, "--out", out_a)
        p2 = _run(*args_base, "--out", out_b)
        assert p1.returncode == 0 and p2.returncode == 0
        assert out_a.is_file() and out_b.is_file()
        assert _sha256(out_a) == _sha256(out_b), (
            "two renders of the same (.blend, view) produced different PNGs; "
            "byte-determinism (same-machine, same-install) is broken"
        )

    def test_two_invocations_with_identical_args_produce_identical_json_stdout(
        self, tmp_path_factory
    ):
        # docs/phase3-handoff.md §3: "Identical inputs produce byte-identical
        # output, JSON and images alike". The PNG determinism test above uses
        # different --out paths (echoed into parameters), so it cannot compare
        # stdout. This test uses ONE --out path, run twice, so stdout must
        # match byte-for-byte -- covering the same guarantee for the JSON
        # payload that test_blender_mesh.test_a_blend_probe_is_byte_
        # deterministic_across_runs covers for pil_blender_mesh.
        outdir = tmp_path_factory.mktemp("json_determinism")
        out = outdir / "same.png"
        args = [RENDER_TOOL, BRUTE_BLEND, "--view", "front", "--out", out]
        p1 = _run(*args)
        p2 = _run(*args)
        assert p1.returncode == 0 and p2.returncode == 0
        assert p1.stdout == p2.stdout, (
            "identical CLI invocations produced different JSON stdout; "
            "some payload field is non-deterministic"
        )

    def test_missing_blender_with_valid_brute_blend_still_rejects_cleanly(
        self, tmp_path
    ):
        proc = _run(
            RENDER_TOOL, BRUTE_BLEND,
            "--view", "front",
            "--out", tmp_path / "out.png",
            "--blender-executable", tmp_path / "no_such_blender.exe",
        )
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "blender executable not found" in proc.stderr
        assert "Traceback" not in proc.stderr

    def test_side_render_beats_wrong_view_references_by_ssim(
        self, side_pair, turnaround_crops
    ):
        # Discrimination assertion the plan's intent requires but its
        # letter does not: the framing flags depend only on image size,
        # so acceptance criterion 2 would pass even if front/side/back
        # were mislabeled. This test ties labels to reality via the side
        # profile, which is unambiguously different from front and back
        # (front and back both show a T-pose silhouette with cape; side
        # is a narrow vertical profile). Front and back are structurally
        # similar enough that ssim can rank them either way -- verified
        # empirically at build time; do NOT tighten this to include front
        # or back symmetry.
        _, payload = side_pair
        side_render_path = payload["_out_path"]

        def _ssim(ref, render):
            tool = SCRIPTS / "pil_structure_diff.py"
            proc = _run(tool, ref, render, "--foreground")
            assert proc.returncode == 0, proc.stderr
            return json.loads(proc.stdout)["diff"]["structural_similarity"]

        side = _ssim(turnaround_crops["side"], side_render_path)
        front = _ssim(turnaround_crops["front"], side_render_path)
        back = _ssim(turnaround_crops["back"], side_render_path)
        assert side > front, (
            f"side-render vs side-ref ssim {side} not greater than vs front-ref {front}; "
            "the axis convention or the reference-crop layout is likely wrong"
        )
        assert side > back, (
            f"side-render vs side-ref ssim {side} not greater than vs back-ref {back}; "
            "the axis convention or the reference-crop layout is likely wrong"
        )

    def test_render_reports_visible_mesh_count_not_all_meshes(self, front_pair):
        # The bounding box the auto-frame uses must exclude hide_render=True
        # meshes; on the brute .blend the character has 49 total meshes but
        # only 16 render (the rest are donor/reference geometry). Including
        # the hidden meshes shrinks the visible character to a single-pixel
        # speck. Guard against a future regression that reverts the filter.
        _, payload = front_pair
        visible = payload["render"]["bounding_box_world_visible"]
        assert visible["visible_mesh_count"] == 16, (
            f"expected 16 render-visible meshes, got {visible['visible_mesh_count']}"
        )


# --- corpus-gated: empty-scene refusal (needs Blender, not the brute corpus) -


BLENDER_MISSING = pytest.mark.skipif(
    not DEFAULT_BLENDER.is_file(),
    reason=f"Blender {DEFAULT_BLENDER} missing",
)


@pytest.fixture(scope="module")
def empty_blend(tmp_path_factory):
    """Create an empty .blend (no MESH objects with hide_render=False).

    Uses Blender itself to write a genuine empty scene rather than a hand-
    rolled byte sequence -- the wrapper's refusal path is what we are
    verifying, and it must survive a real, well-formed .blend that happens
    to have nothing renderable.
    """
    outdir = tmp_path_factory.mktemp("empty_blend")
    empty_path = outdir / "empty.blend"
    script = outdir / "make_empty.py"
    script.write_text(
        (
            "import bpy\n"
            "for obj in list(bpy.data.objects):\n"
            "    bpy.data.objects.remove(obj, do_unlink=True)\n"
            f"bpy.ops.wm.save_as_mainfile(filepath={str(empty_path)!r})\n"
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            str(DEFAULT_BLENDER),
            "--factory-startup",
            "--background",
            "--python", str(script),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"failed to create empty .blend: exit {proc.returncode}\n{proc.stderr}"
    )
    assert empty_path.is_file(), "empty .blend was not written"
    return empty_path


@BLENDER_MISSING
class TestDegenerateSceneRefusal:
    """A scene with no render-visible mesh geometry is a valid input the tool
    must handle without crashing -- the refusal reason lands in the payload,
    exit is 0, and no PNG is written on the render side."""

    def test_empty_blend_refuses_every_view_without_crashing(
        self, empty_blend, tmp_path
    ):
        for view in ("front", "side", "back"):
            outdir = tmp_path / view
            outdir.mkdir()
            out_path = outdir / f"{view}.png"
            proc = _run(RENDER_TOOL, empty_blend, "--view", view, "--out", out_path)
            assert proc.returncode == 0, (
                f"view {view!r}: expected exit 0, got {proc.returncode}\n"
                f"STDERR:\n{proc.stderr}"
            )
            payload = json.loads(proc.stdout)
            assert payload["render"]["rendered"] is False
            assert (
                "no render-visible mesh geometry"
                in payload["render"]["refused_reason"]
            )
            assert payload["render"]["output_path"] is None
            assert not out_path.is_file(), (
                f"view {view!r}: refused render must not leave a PNG at {out_path}"
            )
            # No reference means comparison stays None -- refusal is on the
            # render side, not the comparison side.
            assert payload["comparison"] is None

    def test_empty_blend_with_reference_refuses_comparison_end_to_end(
        self, empty_blend, tmp_path
    ):
        # A synthetic non-empty reference (small grey square with a black
        # square in the middle) so foreground detection would succeed on
        # the reference side alone; the render side is what forces refusal.
        from PIL import Image

        ref = tmp_path / "ref.png"
        image = Image.new("RGB", (128, 128), (180, 180, 180))
        for y in range(48, 80):
            for x in range(48, 80):
                image.putpixel((x, y), (0, 0, 0))
        image.save(ref)

        out = tmp_path / "out.png"
        proc = _run(
            RENDER_TOOL, empty_blend, "--view", "front",
            "--out", out, "--reference", ref,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["render"]["rendered"] is False
        c = payload["comparison"]
        assert c["refused"] is True
        assert c["render"] is None
        assert c["structural_similarity"] is None
        assert "scene has no mesh geometry" in c["refused_reason"]


# --- corpus-gated: background-dominant reference refusal --------------------


@BLENDER_MISSING
class TestBackgroundDominantRefusal:
    """A mostly-background reference makes pil_structure_diff report
    foreground_mask_empty (nothing survives the mask); the wrapper turns
    that into comparison.refused=true rather than emit a fabricated ssim."""

    def test_almost_solid_background_reference_refuses_comparison(
        self, tmp_path
    ):
        # A 128x128 reference that is entirely one colour: foreground mask
        # comes up empty because there is no border/interior separation.
        from PIL import Image

        ref = tmp_path / "solid_bg.png"
        Image.new("RGB", (128, 128), (200, 200, 200)).save(ref)

        out = tmp_path / "out.png"
        proc = _run(
            RENDER_TOOL, BRUTE_BLEND, "--view", "front",
            "--out", out, "--reference", ref,
        )
        # Requires Blender AND the brute .blend for the render side.
        if proc.returncode == 2 and "blend file not found" in proc.stderr:
            pytest.skip("brute .blend absent")

        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        c = payload["comparison"]
        assert c["refused"] is True, (
            f"expected refusal on a solid-colour reference; got {c}"
        )
        assert c["structural_similarity"] is None
        assert c["dhash_distance"] is None
        assert "reference:foreground_mask_empty" in c["refused_reason"]
