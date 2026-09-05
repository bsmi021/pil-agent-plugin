"""Tests for pil_components.py.

Contracts:
  * determinism        -- byte-identical JSON across two runs of the tool
  * hand-count         -- synthetic fixtures whose component count is known
                          match the tool's reported integer exactly, including
                          a diagonal-adjacency case that would go wrong under
                          4-connectivity
  * scene agreement    -- calibration/scenes.py's multipart_object reports
                          exactly 3 components above the calibrated floor
  * empty-mask honesty -- a fully-transparent image reports 0 components and
                          the empty-mask flag, never a crash
  * floor obedience    -- a single sub-floor stray pixel does not inflate
                          the count
  * no scipy           -- the tool file contains no scipy import (grep-checked)
  * rejection hygiene  -- every documented bad-input path exits 2 with
                          byte-empty stdout
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from conftest import SCRIPTS, REPO_ROOT

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO_ROOT / "calibration"))

import pil_components  # noqa: E402
import scenes  # noqa: E402


def run_reject(*args):
    """Invoke pil_components.py directly, without the run_tool zero-exit check."""
    cmd = [sys.executable, str(SCRIPTS / "pil_components.py"), *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True)


def _rgba_from_mask(mask, colour=(200, 60, 60)):
    """Build a small RGBA image whose alpha is 255 exactly where mask is True.

    Using alpha for the mask sidesteps the border-median colour path so
    hand-counted tests measure the labeller, not the mask estimator.
    """
    h, w = mask.shape
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[mask, 0] = colour[0]
    arr[mask, 1] = colour[1]
    arr[mask, 2] = colour[2]
    arr[mask, 3] = 255
    return Image.fromarray(arr, "RGBA")


class TestLabellerHandCount:
    """Truth is a literal integer, per docs/phase3-handoff.md D3 and the plan."""

    def test_three_well_separated_blobs_report_three_components(self, tool, tmp_img):
        # Arrange: three axis-aligned 5x5 blocks with wide gaps between them.
        mask = np.zeros((20, 40), dtype=bool)
        mask[5:10, 2:7] = True
        mask[5:10, 15:20] = True
        mask[5:10, 30:35] = True
        img = _rgba_from_mask(mask)
        src = tmp_img(img, "three_blobs.png")

        # Act
        payload, _raw = tool(
            "pil_components.py", src, "--min-blob-area-fraction", "0"
        )

        # Assert
        assert payload["component_count"] == 3
        # Every reported area is exactly 25 pixels -- the fixture is hand-counted.
        assert sorted(c["area_pixels"] for c in payload["components"]) == [25, 25, 25]

    def test_diagonal_touching_pixels_are_one_component_under_8_connectivity(
        self, tool, tmp_img
    ):
        """The plan calls this case out specifically: 4-connectivity would
        return 2 here and 8-connectivity returns 1. Asserting a literal 1
        pins the choice and would go red if it were flipped."""
        # Arrange: two 3x3 blocks touching only at a diagonal corner.
        mask = np.zeros((16, 16), dtype=bool)
        mask[3:6, 3:6] = True
        mask[6:9, 6:9] = True
        img = _rgba_from_mask(mask)
        src = tmp_img(img, "diagonal_touch.png")

        # Act (default connectivity is 8)
        payload, _ = tool("pil_components.py", src, "--min-blob-area-fraction", "0")

        # Assert
        assert payload["parameters"]["connectivity"] == 8
        assert payload["component_count"] == 1
        assert payload["components"][0]["area_pixels"] == 18

    def test_same_diagonal_pair_reports_two_components_under_4_connectivity(
        self, tool, tmp_img
    ):
        """The knob works: the same fixture flips to 2 blobs when the caller
        explicitly asks for the stricter rule."""
        mask = np.zeros((16, 16), dtype=bool)
        mask[3:6, 3:6] = True
        mask[6:9, 6:9] = True
        img = _rgba_from_mask(mask)
        src = tmp_img(img, "diagonal_touch4.png")

        payload, _ = tool(
            "pil_components.py",
            src,
            "--min-blob-area-fraction",
            "0",
            "--connectivity",
            "4",
        )

        assert payload["component_count"] == 2
        assert sorted(c["area_pixels"] for c in payload["components"]) == [9, 9]

    def test_c_shape_two_prongs_are_one_component(self, tool, tmp_img):
        """A C-shape has two 'arms' that a naive labeller might split; a
        correct union-find recognises they share the spine."""
        # Arrange: an outline of a 'C' -- top bar, left bar, bottom bar.
        mask = np.zeros((14, 14), dtype=bool)
        mask[2:4, 2:12] = True
        mask[2:12, 2:4] = True
        mask[10:12, 2:12] = True
        img = _rgba_from_mask(mask)
        src = tmp_img(img, "c_shape.png")

        payload, _ = tool("pil_components.py", src, "--min-blob-area-fraction", "0")

        assert payload["component_count"] == 1

    def test_single_stray_pixel_below_floor_is_excluded(self, tool, tmp_img):
        """The whole reason the floor exists: an isolated single pixel of
        anti-aliasing residue must not read as an extra object."""
        mask = np.zeros((100, 100), dtype=bool)
        mask[10:60, 10:60] = True  # 2500-pixel primary
        mask[90, 90] = True  # one stray pixel, 0.0001 of a 10000-pixel frame
        img = _rgba_from_mask(mask)
        src = tmp_img(img, "stray.png")

        # min-blob-area 0.001 => 10 pixels; the stray is well below it.
        payload, _ = tool(
            "pil_components.py", src, "--min-blob-area-fraction", "0.001"
        )

        assert payload["component_count"] == 1
        assert payload["excluded_below_floor"]["count"] == 1
        assert payload["excluded_below_floor"]["total_pixels"] == 1


class TestSceneAgreement:
    """calibration/scenes.py has a builder that is 3 components by construction."""

    def test_multipart_object_reports_three_components_above_the_shipping_floor(
        self, tool, tmp_path
    ):
        # Arrange: build the scene the tool will run over, save through PIL as
        # PNG, and run the shipping default floor.
        img = scenes.multipart_object(seed=101)
        path = tmp_path / "multipart.png"
        img.save(path)

        payload, _ = tool("pil_components.py", path)

        assert payload["component_count"] == 3

    def test_blob_object_reports_one_component_above_the_shipping_floor(
        self, tool, tmp_path
    ):
        img = scenes.blob_object(seed=101)
        path = tmp_path / "blob.png"
        img.save(path)

        payload, _ = tool("pil_components.py", path)

        assert payload["component_count"] == 1


class TestForegroundFlags:
    def test_fully_transparent_image_reports_zero_components_and_the_empty_mask_flag(
        self, tool, tmp_img
    ):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        src = tmp_img(img, "empty.png")

        payload, _ = tool("pil_components.py", src)

        assert payload["component_count"] == 0
        assert payload["components"] == []
        assert "foreground_mask_empty" in payload["flags"]
        assert payload["foreground"]["bbox_pixel"] is None

    def test_tiny_foreground_still_returns_the_count_but_flags_it(
        self, tool, tmp_img
    ):
        # A 3x3 blob on a large opaque frame: an OKLab-estimated mask.
        img = Image.new("RGB", (200, 200), (24, 26, 30))
        ImageDraw.Draw(img).rectangle([2, 2, 4, 4], fill=(180, 80, 80))
        src = tmp_img(img, "tiny.png")

        payload, _ = tool(
            "pil_components.py", src, "--min-blob-area-fraction", "0"
        )

        # 3x3 = 9 px out of 40000 = 0.000225 << FOREGROUND_MIN_FRACTION (0.022)
        assert "foreground_too_small" in payload["flags"]
        # The count is not suppressed by the flag -- the flag is a warning,
        # not a censor.
        assert payload["component_count"] >= 1


class TestDeterminism:
    def test_two_runs_produce_byte_identical_stdout(self, tool, tmp_img):
        img = scenes.multipart_object(seed=101)
        src = tmp_img(img, "det.png")

        _, raw_a = tool("pil_components.py", src)
        _, raw_b = tool("pil_components.py", src)

        assert raw_a == raw_b


class TestScaleHonesty:
    def test_interpretation_limits_flags_that_count_is_not_scale_invariant(
        self, tool, tmp_img
    ):
        img = scenes.multipart_object(seed=101)
        src = tmp_img(img, "scale.png")

        payload, _ = tool("pil_components.py", src)

        joined = " ".join(payload["interpretation_limits"]).lower()
        assert "not scale-invariant" in joined or "not scale invariant" in joined


class TestRejectionHygiene:
    def test_missing_source_file_is_a_clean_reject(self, tmp_path):
        proc = run_reject(tmp_path / "does_not_exist.png")
        assert proc.returncode == 2
        assert proc.stdout == ""

    def test_out_of_range_min_blob_area_fraction_is_a_clean_reject(self, tmp_img):
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        src = tmp_img(img, "src.png")

        proc = run_reject(src, "--min-blob-area-fraction", "1.5")
        assert proc.returncode == 2
        assert proc.stdout == ""

        proc = run_reject(src, "--min-blob-area-fraction", "-0.1")
        assert proc.returncode == 2
        assert proc.stdout == ""

    def test_negative_background_delta_is_a_clean_reject(self, tmp_img):
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        src = tmp_img(img, "src.png")

        proc = run_reject(src, "--background-delta", "-0.001")
        assert proc.returncode == 2
        assert proc.stdout == ""


class TestNoScipy:
    def test_tool_source_contains_no_scipy_import(self):
        text = (SCRIPTS / "pil_components.py").read_text(encoding="utf-8")
        # A literal grep for an actual import statement: the plan's D7 style,
        # so a future refactor that reaches for scipy fails here rather than
        # at runtime. Matched against import lines specifically, not the
        # whole file text, because the module's own docstring/comments
        # legitimately say "no scipy" to document the constraint.
        import_lines = [
            line for line in text.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        assert not any("scipy" in line.lower() for line in import_lines)


class TestManifestVersion:
    def test_tool_version_matches_the_manifests(self):
        # Guarded generally by test_packaging_conformance; asserted here so
        # the components file's own version is pinned even if the packaging
        # glob is ever narrowed.
        assert pil_components.TOOL_VERSION == "0.8.0"
