"""RED tests for pil_structure_diff.

Contracts:
  * scale invariance   -- a resized copy is still the same layout
  * aspect honesty     -- never silently squash mismatched aspect ratios
  * exact-match path   -- perceptual hash distance + changed-region bboxes
  * determinism        -- byte-identical JSON across runs
"""

import pytest
from PIL import Image, ImageDraw

from conftest import (
    REFERENCE_EXPECTED,
    REFERENCE_IMAGE,
    dark_base_with_accent,
    detailed_vs_flat,
)


def _layout_image(size=(600, 400)):
    """A structured scene: bright block top-left, striped block bottom-right."""
    img = Image.new("RGB", size, (15, 15, 20))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 260, 160], fill=(240, 240, 240))
    for x in range(320, 580, 4):
        draw.line([x, 240, x, 380], fill=(200, 60, 60))
    return img


class TestScaleInvariance:
    def test_resized_copy_remains_highly_similar(self, tool, tmp_img):
        # Arrange: the same scene at two very different pixel scales.
        original = _layout_image((600, 400))
        scaled = original.resize((300, 200), Image.LANCZOS)
        a = tmp_img(original, "a.png")
        b = tmp_img(scaled, "b.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, b)

        # Assert: requires a fixed internal working size and a fractional grid.
        assert result["diff"]["structural_similarity"] > 0.90, (
            "a pure rescale must not read as a layout change; got "
            f"{result['diff']['structural_similarity']}"
        )

    def test_resized_copy_has_near_zero_hash_distance(self, tool, tmp_img):
        # Arrange
        original = _layout_image((600, 400))
        a = tmp_img(original, "a.png")
        b = tmp_img(original.resize((300, 200), Image.LANCZOS), "b.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, b)

        # Assert: perceptual hashes are scale-invariant by construction.
        assert result["diff"]["dhash_distance"] <= 4


class TestAspectHonesty:
    def test_mismatched_aspect_ratio_is_flagged(self, tool, tmp_img):
        # Arrange: 3:2 versus 1:1 -- a Blender render rarely matches a reference sheet.
        a = tmp_img(_layout_image((600, 400)), "a.png")
        b = tmp_img(_layout_image((400, 400)), "b.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, b)

        # Assert: surfaced explicitly, because squashing makes grid cells
        # non-corresponding and every per-cell number becomes a lie.
        assert "aspect_ratio_mismatch" in result["diff"]["flags"]

    def test_matching_aspect_ratio_is_not_flagged(self, tool, tmp_img):
        # Arrange
        a = tmp_img(_layout_image((600, 400)), "a.png")
        b = tmp_img(_layout_image((300, 200)), "b.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, b)

        # Assert
        assert "aspect_ratio_mismatch" not in result["diff"]["flags"]


class TestChangedRegionLocalisation:
    def test_bbox_localises_a_planted_edit(self, tool, tmp_img):
        # Arrange: identical scenes except one patch added bottom-right.
        base = _layout_image((600, 400))
        edited = base.copy()
        ImageDraw.Draw(edited).rectangle([480, 300, 560, 360], fill=(0, 255, 0))
        a = tmp_img(base, "a.png")
        b = tmp_img(edited, "b.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, b)

        # Assert: answers "what changed", not merely "how much".
        bbox = result["diff"]["changed_region_bbox_fractional"]
        assert bbox is not None
        left, top, right, bottom = bbox
        assert left > 0.5 and top > 0.5, f"edit was bottom-right, bbox={bbox}"
        assert right <= 1.0 and bottom <= 1.0

    def test_identical_images_report_no_changed_region(self, tool, tmp_img):
        # Arrange
        img = _layout_image()
        a = tmp_img(img, "a.png")
        b = tmp_img(img, "b.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, b)

        # Assert
        assert result["diff"]["changed_region_bbox_fractional"] is None
        assert result["diff"]["dhash_distance"] == 0
        assert result["diff"]["structural_similarity"] == pytest.approx(1.0, abs=1e-6)


class TestDissimilarity:
    def test_unrelated_images_score_low(self, tool, tmp_img):
        # Arrange
        a = tmp_img(_layout_image((600, 400)), "a.png")
        b = tmp_img(dark_base_with_accent((220, 30, 30), (600, 400)), "b.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, b)

        # Assert: the metric must actually discriminate, not saturate near 1.
        assert result["diff"]["structural_similarity"] < 0.85
        assert result["diff"]["dhash_distance"] > 4


class TestEdgeDensity:
    def test_detailed_half_reads_denser_than_flat_half(self, tool, tmp_img):
        # Arrange: left half finely striped, right half flat.
        a = tmp_img(detailed_vs_flat((400, 300)), "a.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, "--grid", "2x1")
        cells = result["images"]["a"]["cells"]

        # Assert
        left = next(c for c in cells if c["col"] == 0)
        right = next(c for c in cells if c["col"] == 1)
        assert left["edge_mean"] > right["edge_mean"] * 2


class TestDeterminism:
    def test_repeated_runs_produce_byte_identical_json(self, tool, tmp_img):
        # Arrange
        a = tmp_img(_layout_image((600, 400)), "a.png")
        b = tmp_img(_layout_image((300, 200)), "b.png")

        # Act
        _, raw1 = tool("pil_structure_diff.py", a, b)
        _, raw2 = tool("pil_structure_diff.py", a, b)

        # Assert
        assert raw1 == raw2


class TestSingleImageMode:
    def test_one_argument_reports_cells_without_a_diff(self, tool, tmp_img):
        # Arrange
        a = tmp_img(_layout_image(), "a.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a)

        # Assert
        assert result["diff"] is None
        assert len(result["images"]["a"]["cells"]) == 12  # default 4x3 grid

    def test_grid_argument_controls_cell_count(self, tool, tmp_img):
        # Arrange
        a = tmp_img(_layout_image(), "a.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, "--grid", "3x2")

        # Assert
        assert len(result["images"]["a"]["cells"]) == 6


class TestScopeGuard:
    def test_output_disclaims_geometry_inference(self, tool, tmp_img):
        """Edge density is a 2D proxy and must never be read as polygon count.

        The disclaimer travels with the data so an agent reading only the JSON
        cannot mistake complexity for topology.
        """
        # Arrange
        a = tmp_img(_layout_image(), "a.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a)

        # Assert
        note = result["interpretation_limits"]
        assert any("polygon" in n.lower() for n in note)


@pytest.mark.skipif(
    not REFERENCE_IMAGE.exists(), reason="reference image not present on this machine"
)
class TestReferenceImageSanity:
    def test_luminance_matches_prototype_measurement(self, tool):
        # Arrange / Act
        result, _ = tool("pil_structure_diff.py", REFERENCE_IMAGE)
        img = result["images"]["a"]

        # Assert: computed on the full-resolution image, not the working copy.
        assert img["luminance"]["mean"] == pytest.approx(
            REFERENCE_EXPECTED["luminance_mean"], abs=0.5
        )

    def test_image_is_asymmetric_left_to_right(self, tool):
        # Arrange / Act
        result, _ = tool("pil_structure_diff.py", REFERENCE_IMAGE)

        # Assert: title text left, pipeline graphic right -> strongly asymmetric.
        assert result["images"]["a"]["symmetry"]["left_right_diff"] > 20
