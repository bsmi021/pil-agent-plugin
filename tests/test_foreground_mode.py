"""RED tests for foreground separation and support gating.

The production failure these pin: two immutable views of the same sword shared a
dark preview background covering ~98.5% of both frames. Full-frame structural
similarity read 0.991 because the backgrounds were identical, and the accent
verdict fired off a handful of pixels while both images carried
accent_area_very_small. The fixes under test:

  * background_dominant   -- default mode must say when the frame is mostly
                             background, instead of letting the caller trust an
                             inflated score.
  * --foreground          -- masked, bbox-registered measurement of the object
                             itself (alpha when real, border-median OKLab
                             otherwise, matching the synty_asset_index
                             definition of a visible pixel).
  * support gating        -- hue families need minimum support before they can
                             flip accent_hue_shift_detected; grid cells need
                             minimum foreground support before they can score.
"""

import pytest
from PIL import Image

from conftest import preview_render, preview_render_rgba

CYAN = (30, 200, 200)
RED = (220, 30, 30)


def _sim(result):
    return result["diff"]["structural_similarity"]


class TestBackgroundDominance:
    """Default mode must confess when the background is doing the scoring."""

    def test_structure_flags_a_background_dominated_frame(self, tool, tmp_img):
        # Arrange: two clearly different objects on the same large background.
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(mirrored=True), "b.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, b)

        # Assert: the score is inflated -- and every layer of the payload says so.
        assert _sim(result) > 0.90, "background should inflate full-frame similarity"
        assert "background_dominant" in result["images"]["a"]["flags"]
        assert "background_dominant" in result["images"]["b"]["flags"]
        assert "background_dominant" in result["diff"]["flags"]

    def test_palette_flags_a_background_dominated_frame(self, tool, tmp_img):
        # Arrange
        a = tmp_img(preview_render(), "a.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)

        # Assert
        img = result["images"]["a"]
        assert "background_dominant" in img["flags"]
        assert img["foreground"]["applied"] is False
        assert img["foreground"]["fraction_of_frame"] < 0.10

    def test_a_full_content_image_is_not_flagged(self, tool, tmp_img):
        # Arrange: a frame that is mostly content -- the UI-screenshot case for
        # which full-frame analysis is the correct default.
        from conftest import synthetic_reference

        a = tmp_img(synthetic_reference(), "ref.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)

        # Assert: dominance detection must not fire on legitimate dark scenes
        # whose content spans the frame.
        assert "background_dominant" not in result["images"]["a"]["flags"]


class TestForegroundDiscrimination:
    """The smoke-test regression: foreground mode must separate objects that
    full-frame mode scores as near-identical."""

    def test_foreground_mode_discriminates_different_objects(self, tool, tmp_img):
        # Arrange
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(mirrored=True), "b.png")

        # Act
        full, _ = tool("pil_structure_diff.py", a, b)
        fg, _ = tool("pil_structure_diff.py", a, b, "--foreground")

        # Assert: the discriminating gap is the whole point of the mode.
        assert _sim(full) > 0.90
        assert _sim(fg) < 0.80, f"foreground similarity {_sim(fg)} not discriminating"
        assert _sim(fg) < _sim(full) - 0.15

    def test_identical_object_shifted_in_frame_scores_identical(self, tool, tmp_img):
        # Arrange: the same object, translated. Bbox registration must make the
        # comparison position-independent.
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(offset=(30, 10)), "b.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, b, "--foreground")

        # Assert
        assert _sim(result) == pytest.approx(1.0, abs=1e-6)
        assert result["diff"]["changed_area_fraction"] == pytest.approx(0.0, abs=1e-6)
        assert result["diff"]["dhash_distance"] == 0

    def test_identical_object_on_different_background(self, tool, tmp_img):
        # Arrange: same sword, repainted backdrop.
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(bg=(70, 72, 78)), "b.png")

        # Act
        full, _ = tool("pil_structure_diff.py", a, b)
        fg, _ = tool("pil_structure_diff.py", a, b, "--foreground")

        # Assert: full-frame reads a near-total change; foreground reads the
        # truth -- the object did not change.
        assert full["diff"]["changed_area_fraction"] > 0.90
        assert fg["diff"]["changed_area_fraction"] < 0.05
        assert _sim(fg) > 0.90

    def test_rescaled_copy_ranks_above_a_different_object(self, tool, tmp_img):
        # Arrange: thin objects lose edge fidelity across resolutions (most of
        # their pixels are edge-blended), so exact invariance is not on offer;
        # the ordering contract is. A rescale of the same object must beat a
        # genuinely different object.
        original = preview_render()
        a = tmp_img(original, "a.png")
        rescaled = tmp_img(original.resize((200, 150), Image.LANCZOS), "small.png")
        different = tmp_img(preview_render(mirrored=True), "mirrored.png")

        # Act
        same, _ = tool("pil_structure_diff.py", a, rescaled, "--foreground")
        other, _ = tool("pil_structure_diff.py", a, different, "--foreground")

        # Assert
        assert _sim(same) > _sim(other) + 0.10
        assert same["diff"]["dhash_distance"] <= 4


class TestCellSupport:
    def test_cells_carry_foreground_support(self, tool, tmp_img):
        # Arrange
        a = tmp_img(preview_render(), "a.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, "--foreground")
        cells = result["images"]["a"]["cells"]

        # Assert: every cell declares its support; empty cells report null
        # statistics rather than fabricating numbers from pure background.
        assert all("foreground_pixels" in c for c in cells)
        empty = [c for c in cells if c["foreground_pixels"] == 0]
        assert empty, "a thin diagonal must leave some cells without foreground"
        assert all(c["luminance_mean"] is None for c in empty)

    def test_background_only_cell_pairs_are_skipped(self, tool, tmp_img):
        # Arrange
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(accent=RED), "b.png")

        # Act
        result, _ = tool("pil_structure_diff.py", a, b, "--foreground")
        diff = result["diff"]

        # Assert: background-only pairs are excluded rather than counted as
        # "identical", and the exclusion is visible in the payload.
        assert diff["cells_skipped_low_support"] >= 1
        assert diff["cells_compared"] >= 1
        assert diff["cells_compared"] + diff["cells_skipped_low_support"] <= 12


class TestForegroundMaskSources:
    def test_alpha_is_used_when_the_file_carries_transparency(self, tool, tmp_img):
        # Arrange
        a = tmp_img(preview_render_rgba(), "a.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, "--foreground")
        block = result["images"]["a"]["foreground"]

        # Assert
        assert block["applied"] is True
        assert block["source"] == "alpha"
        assert block["background_estimate"] is None
        assert 0.01 < block["fraction_of_frame"] < 0.05

    def test_opaque_files_fall_back_to_border_median_estimate(self, tool, tmp_img):
        # Arrange
        a = tmp_img(preview_render(), "a.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, "--foreground")
        block = result["images"]["a"]["foreground"]

        # Assert: the estimated backdrop is the actual backdrop colour.
        assert block["source"] == "background_estimate"
        assert block["background_estimate"] == "#181a1e"

    def test_empty_mask_falls_back_to_full_frame_with_a_flag(self, tool, tmp_img):
        # Arrange: a uniform image has no foreground at all.
        a = tmp_img(Image.new("RGB", (200, 200), (40, 40, 44)), "flat.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, "--foreground")
        img = result["images"]["a"]

        # Assert: fall back and say so -- never crash, never emit statistics
        # over an empty pixel set.
        assert "foreground_mask_empty" in img["flags"]
        assert img["foreground"]["applied"] is False
        assert img["base_palette"], "fallback must still analyse the frame"

    def test_mixed_mask_fallback_is_flagged_on_the_diff(self, tool, tmp_img):
        # Arrange: one image masks cleanly, the other has no foreground and
        # falls back -- their fractions are no longer commensurable.
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(Image.new("RGB", (400, 300), (40, 40, 44)), "flat.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b, "--foreground")

        # Assert
        assert "foreground_mask_empty" in result["images"]["b"]["flags"]
        assert "foreground_mask_mismatch" in result["diff"]["flags"]

    def test_thin_asset_is_flagged_foreground_too_small(self, tool, tmp_img):
        # Arrange: same object, much larger frame -> ~0.6% foreground.
        a = tmp_img(preview_render(size=(800, 600)), "a.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, "--foreground")
        img = result["images"]["a"]

        # Assert: measured anyway, but the warning travels with the numbers.
        assert "foreground_too_small" in img["flags"]
        assert img["base_palette"]


class TestForegroundPalette:
    def test_accent_share_is_foreground_relative(self, tool, tmp_img):
        # Arrange: the cyan gem is ~0.1% of the frame but a solid chunk of the
        # sword itself.
        a = tmp_img(preview_render(), "a.png")

        # Act
        full, _ = tool("pil_palette_diff.py", a)
        fg, _ = tool("pil_palette_diff.py", a, "--foreground")

        # Assert
        assert full["images"]["a"]["accent_pixel_fraction"] < 0.01
        assert fg["images"]["a"]["accent_pixel_fraction"] > 0.20
        assert "accent_area_very_small" not in fg["images"]["a"]["flags"]

    def test_gem_recolour_detected_in_foreground_mode(self, tool, tmp_img):
        # Arrange
        a = tmp_img(preview_render(accent=CYAN), "a.png")
        b = tmp_img(preview_render(accent=RED), "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b, "--foreground")
        diff = result["diff"]

        # Assert
        assert diff["accent_hue_shift_detected"] is True
        assert "cyan" in diff["hue_families_lost"]
        assert "red" in diff["hue_families_gained"]


class TestVerdictSupportGating:
    """accent_hue_shift_detected must not flip on unsupported presence."""

    def test_single_stray_pixel_does_not_flip_the_verdict(self, tool, tmp_img):
        # Arrange: identical images except one anti-aliasing-scale artefact --
        # a single vivid magenta pixel. Before support gating this flipped the
        # verdict via hue_families_gained.
        a = tmp_img(preview_render(), "a.png")
        speckled = preview_render()
        speckled.putpixel((200, 20), (255, 0, 200))
        b = tmp_img(speckled, "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)
        diff = result["diff"]

        # Assert
        assert diff["hue_families_gained"] == []
        assert diff["accent_hue_shift_detected"] is False
        # ...while the census still reports the pixel: gating hides nothing.
        assert result["images"]["b"]["hue_families"]["magenta"]["pixels"] >= 1

    def test_genuine_small_recolour_still_fires(self, tool, tmp_img):
        # Arrange: guards against over-gating -- a real 100+-pixel recolour must
        # keep firing even though the accent area is small.
        a = tmp_img(preview_render(accent=CYAN), "a.png")
        b = tmp_img(preview_render(accent=RED), "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert
        assert result["diff"]["accent_hue_shift_detected"] is True
        assert "cyan" in result["diff"]["hue_families_lost"]

    def test_low_accent_support_is_flagged_on_the_diff(self, tool, tmp_img):
        # Arrange: on a large frame the vivid pixels drop below the support
        # floor on both sides -- the smoke test's exact configuration.
        a = tmp_img(preview_render(size=(800, 600), accent=CYAN), "a.png")
        b = tmp_img(preview_render(size=(800, 600), accent=RED), "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert: a ranking caller must treat this flag as disqualifying.
        assert "accent_area_very_small" in result["images"]["a"]["flags"]
        assert "accent_support_low" in result["diff"]["flags"]


class TestDeterminism:
    def test_foreground_structure_runs_are_byte_identical(self, tool, tmp_img):
        # Arrange
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(mirrored=True), "b.png")

        # Act
        _, raw1 = tool("pil_structure_diff.py", a, b, "--foreground")
        _, raw2 = tool("pil_structure_diff.py", a, b, "--foreground")

        # Assert
        assert raw1 == raw2

    def test_foreground_palette_runs_are_byte_identical(self, tool, tmp_img):
        # Arrange
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(accent=RED), "b.png")

        # Act
        _, raw1 = tool("pil_palette_diff.py", a, b, "--foreground")
        _, raw2 = tool("pil_palette_diff.py", a, b, "--foreground")

        # Assert
        assert raw1 == raw2


class TestParameterEcho:
    """A reader must be able to tell from the JSON alone which definition of
    'foreground' produced the numbers."""

    def test_structure_parameters_echo_foreground_settings(self, tool, tmp_img):
        a = tmp_img(preview_render(), "a.png")
        result, _ = tool("pil_structure_diff.py", a, "--foreground")
        params = result["parameters"]
        assert params["foreground"] is True
        assert params["background_delta"] == 0.035
        assert params["cell_min_support_pixels"] == 16

    def test_palette_parameters_echo_support_floors(self, tool, tmp_img):
        a = tmp_img(preview_render(), "a.png")
        result, _ = tool("pil_palette_diff.py", a)
        params = result["parameters"]
        assert params["foreground"] is False
        assert params["hue_presence_min"]["pixels"] == 10
        assert params["hue_presence_min"]["fraction"] == 0.0002
