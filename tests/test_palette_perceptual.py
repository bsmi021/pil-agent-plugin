"""RED tests for WP1's two additions to pil_palette_diff: perceptual palette
distance, and the LCh accent gate.

Both are additive. The RGB palette distances and the HSV accent mask keep
producing exactly the numbers they produced in phase 1 -- the de2000 fields sit
alongside them, and the LCh mask is opt-in behind --accent-space lch. The
default flips only once WP2 has calibrated the chroma and lightness floors, so
"the default is still hsv" is itself asserted here.
"""

import pytest
from PIL import Image

from conftest import dark_base_with_accent, preview_render

RED = (220, 30, 30)
CYAN = (30, 220, 220)

# Pale but unmistakably coloured. HSV saturation is 89 -- under the sat_min of
# 100 -- so the phase-1 gate rejects it, while C* is ~29, comfortably over the
# LCh floor of 20. This is failure mode 2 from the research document: HSV ranks
# light, low-saturation-but-clearly-coloured pixels backwards relative to
# perceived colourfulness. (The document's own measured example, pale mint
# #d0ffe4 at C* = 21.60 with HSV S = 47, makes the same point with less margin;
# its exact LCh values are pinned in test_ciede2000.py.)
PALE_PINK = (200, 130, 130)

# The mirror-image failure: HSV saturation 191 and value 64 admit this as a
# "vivid accent" at L* = 12.6, a near-black maroon carrying no perceptual
# identity. HSV S stays high as a colour goes to black because it is relative.
DIM_MAROON = (0x40, 0x10, 0x10)


class TestPerceptualPaletteDistance:
    """dE2000 over D65 CIELAB, as the primary colour-distance signal."""

    def test_identical_images_report_zero_perceptual_distance(self, tool, tmp_img):
        # Arrange
        img = dark_base_with_accent(RED)
        a = tmp_img(img, "a.png")
        b = tmp_img(img, "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert
        diff = result["diff"]
        assert diff["base_palette_distance_de2000"] == pytest.approx(0.0, abs=1e-6)
        assert diff["accent_palette_distance_de2000"] == pytest.approx(0.0, abs=1e-6)

    def test_accent_recolour_registers_a_large_perceptual_distance(
        self, tool, tmp_img
    ):
        # Arrange: identical near-black base, one red accent vs one cyan accent.
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)
        diff = result["diff"]

        # Assert: the same discriminating evidence as the RGB pair -- the shared
        # dark base makes the base palettes look alike while the accents differ.
        assert diff["accent_palette_distance_de2000"] > 30.0
        assert (
            diff["accent_palette_distance_de2000"]
            > diff["base_palette_distance_de2000"]
        )

    def test_perceptual_distance_is_order_independent(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        forward, _ = tool("pil_palette_diff.py", a, b)
        reverse, _ = tool("pil_palette_diff.py", b, a)

        # Assert: symmetrised by construction, exactly like the RGB Chamfer.
        assert forward["diff"]["accent_palette_distance_de2000"] == pytest.approx(
            reverse["diff"]["accent_palette_distance_de2000"], abs=1e-6
        )
        assert forward["diff"]["base_palette_distance_de2000"] == pytest.approx(
            reverse["diff"]["base_palette_distance_de2000"], abs=1e-6
        )

    def test_black_versus_white_lands_on_the_hundred_landmark(self, tool, tmp_img):
        """The scale check that makes a normalisation bug visible.

        Black vs white is exactly 100 -- a landmark, not a maximum, and not a
        percentage. If someone ever divides by 100 this reads 1.0; if they clamp
        to a 0-1 range it reads 1.0 as well. Either way the assertion fails.
        """
        # Arrange
        a = tmp_img(Image.new("RGB", (100, 100), (0, 0, 0)), "black.png")
        b = tmp_img(Image.new("RGB", (100, 100), (255, 255, 255)), "white.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)
        diff = result["diff"]

        # Assert
        assert diff["base_palette_distance_de2000"] == pytest.approx(100.0, abs=0.001)
        # The RGB distance is on a completely different scale (~441 here), which
        # is why the two are reported as separate fields and never blended.
        assert diff["base_palette_distance"] > 400.0

    def test_missing_palette_yields_null_not_zero(self, tool, tmp_img):
        """A colour distance that could not be computed must not read as
        'identical'. Both sides here have no vivid pixels at all."""
        # Arrange
        a = tmp_img(Image.new("RGB", (100, 100), (60, 60, 60)), "grey_a.png")
        b = tmp_img(Image.new("RGB", (100, 100), (70, 70, 70)), "grey_b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)
        diff = result["diff"]

        # Assert
        assert diff["accent_palette_distance_de2000"] is None
        assert diff["accent_palette_distance"] is None
        assert "accent_comparison_unavailable" in diff["flags"]

    def test_rgb_distances_are_retained_alongside_the_perceptual_ones(
        self, tool, tmp_img
    ):
        """Demoted, not removed: phase-1 outputs stay comparable."""
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert
        for field in (
            "base_palette_distance",
            "accent_palette_distance",
            "base_palette_distance_de2000",
            "accent_palette_distance_de2000",
        ):
            assert field in result["diff"], f"{field} missing from the diff block"

    def test_output_names_its_illuminant(self, tool, tmp_img):
        """dE2000 without its illuminant is not interpretable: ICC/Photoshop Lab
        and Pillow's own LAB mode are D50, and reading one as the other is worth
        up to 7.5 dE00."""
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)

        # Assert
        assert "D65" in result["parameters"]["lab_reference"]

    def test_no_verbal_perceptibility_band_is_emitted(self, tool, tmp_img):
        """dE2000 has no authoritative banding table -- the popular one is
        disclaimed by its own author -- so the tool must emit numbers only."""
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert: every de2000 field is numeric or null, never a word.
        for field in (
            "base_palette_distance_de2000",
            "accent_palette_distance_de2000",
        ):
            value = result["diff"][field]
            assert value is None or isinstance(value, (int, float)), (
                f"{field} must be a raw number, got {value!r}"
            )


class TestAccentSpaceDefault:
    """The default must not have flipped: WP2 calibrates the LCh floors, and
    only the integrator flips the default once it has."""

    def test_default_accent_space_is_hsv(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)

        # Assert
        assert result["parameters"]["accent_space"] == "hsv"

    def test_explicit_hsv_is_byte_identical_to_the_bare_invocation(
        self, tool, tmp_img
    ):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        _, bare = tool("pil_palette_diff.py", a, b)
        _, explicit = tool("pil_palette_diff.py", a, b, "--accent-space", "hsv")

        # Assert: selecting the default must be a no-op, byte for byte.
        assert bare == explicit


class TestLchAccentGate:
    """The measured failure modes of the HSV gate, and their fix."""

    def test_hsv_misses_a_pale_but_clearly_coloured_accent(self, tool, tmp_img):
        """Half of the evidence: pinning the failure so it cannot quietly go
        away and take the reason for --accent-space lch with it."""
        # Arrange
        a = tmp_img(dark_base_with_accent(PALE_PINK), "pale.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)
        img = result["images"]["a"]

        # Assert
        assert img["accent_pixel_fraction"] == pytest.approx(0.0, abs=1e-6)
        assert img["accent_palette"] == []
        assert "no_accent_pixels" in img["flags"]

    def test_lch_catches_the_pale_accent_hsv_missed(self, tool, tmp_img):
        # Arrange: the same image, the same planted 5% patch.
        a = tmp_img(dark_base_with_accent(PALE_PINK), "pale.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, "--accent-space", "lch")
        img = result["images"]["a"]

        # Assert
        assert img["accent_pixel_fraction"] > 0.02, (
            "C* ~ 29 clears the chroma floor of 20; the patch must be found"
        )
        assert img["accent_palette"], "accent palette empty despite a vivid patch"
        assert img["hue_families"]["red"]["pixels"] > 0

    def test_hsv_admits_a_near_black_maroon_as_a_vivid_accent(self, tool, tmp_img):
        """The other half: HSV saturation is relative, so it stays high as a
        colour goes to black. This is the phase-1 false positive."""
        # Arrange
        a = tmp_img(dark_base_with_accent(DIM_MAROON), "maroon.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)
        img = result["images"]["a"]

        # Assert
        assert img["accent_pixel_fraction"] > 0.02

    def test_lch_rejects_the_near_black_maroon(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(DIM_MAROON), "maroon.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, "--accent-space", "lch")
        img = result["images"]["a"]

        # Assert: L* = 12.6 is below the lightness floor of 20.
        assert img["accent_pixel_fraction"] == pytest.approx(0.0, abs=1e-6)
        assert "no_accent_pixels" in img["flags"]

    def test_hue_bucketing_still_runs_on_the_hsv_hue_channel(self, tool, tmp_img):
        """LCh selects *which* pixels are accents; it does not move the family
        boundaries. No authoritative CIELAB hue-angle boundary set for basic
        colour names exists, so deriving them is WP2's job."""
        # Arrange
        a = tmp_img(dark_base_with_accent(CYAN), "cyan.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, "--accent-space", "lch")
        families = result["images"]["a"]["hue_families"]

        # Assert: the same eight families, with the same names, as in HSV mode.
        assert set(families) == {
            "red",
            "orange",
            "yellow",
            "green",
            "cyan",
            "blue",
            "purple",
            "magenta",
        }
        assert families["cyan"]["pixels"] > 0


class TestLchParameterEcho:
    """A reader must be able to tell from the JSON alone which definition of
    'accent' produced the numbers."""

    def test_lch_run_echoes_its_space_and_active_thresholds(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, "--accent-space", "lch")
        params = result["parameters"]

        # Assert
        assert params["accent_space"] == "lch"
        assert params["accent_thresholds"]["chroma_min"] == 20.0
        assert params["accent_thresholds"]["lightness_min"] == 20.0

    def test_hsv_thresholds_are_still_echoed_in_both_spaces(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")

        # Act
        hsv, _ = tool("pil_palette_diff.py", a)
        lch, _ = tool("pil_palette_diff.py", a, "--accent-space", "lch")

        # Assert: the schema does not change shape with the flag; accent_space
        # is what says which pair is live.
        assert hsv["parameters"]["accent_thresholds"] == (
            lch["parameters"]["accent_thresholds"]
        )
        assert hsv["parameters"]["accent_thresholds"]["saturation_min"] == 100
        assert hsv["parameters"]["accent_thresholds"]["value_min"] == 60


class TestLchDeterminism:
    def test_repeated_lch_runs_produce_byte_identical_json(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        _, raw1 = tool("pil_palette_diff.py", a, b, "--accent-space", "lch")
        _, raw2 = tool("pil_palette_diff.py", a, b, "--accent-space", "lch")

        # Assert: the canary for the whole additive claim.
        assert raw1 == raw2


class TestLchWithForeground:
    """The foreground `within` mask has to intersect correctly in both spaces."""

    def test_foreground_mode_works_in_lch_space(self, tool, tmp_img):
        # Arrange: a thin asset on a shared preview background.
        a = tmp_img(preview_render(), "sword.png")

        # Act
        full, _ = tool("pil_palette_diff.py", a, "--accent-space", "lch")
        fg, _ = tool(
            "pil_palette_diff.py", a, "--accent-space", "lch", "--foreground"
        )

        # Assert: fractions become shares of the foreground, so the same accent
        # pixels represent a far larger share once the backdrop is excluded.
        assert fg["images"]["a"]["foreground"]["applied"] is True
        assert fg["images"]["a"]["accent_pixel_fraction"] > (
            full["images"]["a"]["accent_pixel_fraction"]
        )
        assert fg["images"]["a"]["accent_palette"]

    def test_foreground_diff_reports_perceptual_distance_in_lch_space(
        self, tool, tmp_img
    ):
        # Arrange: same asset, different accent colour on the pommel.
        a = tmp_img(preview_render(accent=(30, 200, 200)), "cyan.png")
        b = tmp_img(preview_render(accent=(200, 40, 40)), "red.png")

        # Act
        result, _ = tool(
            "pil_palette_diff.py", a, b, "--accent-space", "lch", "--foreground"
        )
        diff = result["diff"]

        # Assert: the pommel is a minority of the accent mass (the grip is
        # unchanged and outweighs it), so the coverage-weighted figure is well
        # below the raw dE00 between the two pommel colours -- but it must still
        # clearly exceed the identical-image floor of zero.
        assert diff["accent_palette_distance_de2000"] is not None
        assert diff["accent_palette_distance_de2000"] > 2.0
