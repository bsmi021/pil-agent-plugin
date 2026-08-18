"""RED tests for pil_palette_diff.

The central contract: an accent-hue change between two images that share an
identical dark base MUST surface as a difference. The prototyping phase proved
that a single global quantisation misses this entirely, so the tool reports a
base palette and a chroma-masked accent palette separately.
"""

import pytest

from conftest import (
    HUE_SAMPLES,
    REFERENCE_EXPECTED,
    REFERENCE_IMAGE,
    dark_base_with_accent,
    synthetic_reference,
)

RED = (220, 30, 30)
CYAN = (30, 220, 220)


def _hues(palette):
    """Crude hue bucket for each palette entry, for asserting 'is there a red'."""
    out = []
    for entry in palette:
        r, g, b = entry["rgb"]
        if r > g + 40 and r > b + 40:
            out.append("red")
        elif b > r + 40 and g > r + 40:
            out.append("cyan")
        else:
            out.append("other")
    return out


class TestAccentBlindness:
    """The flaw this tool exists to avoid."""

    def test_accent_hue_change_is_reported_as_a_difference(self, tool, tmp_img):
        # Arrange: identical near-black base, one red accent vs one cyan accent.
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert: the accent channel must register a large distance.
        assert result["diff"]["accent_palette_distance"] > 100, (
            "accent hue change must not be washed out; got "
            f"{result['diff']['accent_palette_distance']}"
        )

    def test_accent_distance_exceeds_base_distance(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert: this is the discriminating evidence -- the shared dark base
        # makes the base palettes look alike while the accents differ sharply.
        diff = result["diff"]
        assert diff["accent_palette_distance"] > diff["base_palette_distance"]

    def test_accent_palette_surfaces_the_vivid_colour(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert: each accent palette actually contains its accent hue.
        assert "red" in _hues(result["images"]["a"]["accent_palette"])
        assert "cyan" in _hues(result["images"]["b"]["accent_palette"])

    def test_accent_pixel_fraction_approximates_the_planted_area(self, tool, tmp_img):
        # Arrange: 5% of the canvas is vivid.
        a = tmp_img(dark_base_with_accent(RED, accent_frac=0.05), "a.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)

        # Assert: within a loose tolerance -- the mask is a threshold, not exact.
        frac = result["images"]["a"]["accent_pixel_fraction"]
        assert 0.02 < frac < 0.10, f"expected ~0.05, got {frac}"


class TestIdentity:
    def test_identical_images_report_zero_distance(self, tool, tmp_img):
        # Arrange
        img = dark_base_with_accent(RED)
        a = tmp_img(img, "a.png")
        b = tmp_img(img, "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert
        assert result["diff"]["base_palette_distance"] == pytest.approx(0.0, abs=1e-6)
        assert result["diff"]["accent_palette_distance"] == pytest.approx(0.0, abs=1e-6)


class TestDeterminism:
    def test_repeated_runs_produce_byte_identical_json(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        _, raw1 = tool("pil_palette_diff.py", a, b)
        _, raw2 = tool("pil_palette_diff.py", a, b)

        # Assert: the whole value proposition is reproducible, diffable output.
        assert raw1 == raw2


class TestNoAccentCase:
    def test_image_with_no_vivid_pixels_is_flagged_not_crashed(self, tool, tmp_img):
        # Arrange: a fully desaturated grey image has no accent pixels at all.
        from PIL import Image

        a = tmp_img(Image.new("RGB", (100, 100), (60, 60, 60)), "grey.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)

        # Assert
        img = result["images"]["a"]
        assert img["accent_pixel_fraction"] == pytest.approx(0.0, abs=1e-6)
        assert img["accent_palette"] == []
        assert "no_accent_pixels" in img["flags"]


class TestSingleImageMode:
    def test_one_argument_analyses_without_a_diff_block(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)

        # Assert
        assert result["diff"] is None
        assert "b" not in result["images"]
        assert result["images"]["a"]["base_palette"]


class TestHueFamilies:
    """Second-order accent blindness, found by measuring the real image.

    Masking for chroma fixes the dark-base problem but not the *dominant accent*
    problem: on the reference image the accent palette spent ~7 of 8 slots on
    red/orange and never surfaced cyan, green, blue or purple -- even though
    those hues encode the semantic status language of the graphic. A hue-family
    histogram enumerates every hue present regardless of relative area.
    """

    def test_small_secondary_hue_survives_a_dominant_accent(self, tool, tmp_img):
        # Arrange: a large red accent plus a deliberately tiny cyan one.
        from PIL import ImageDraw

        img = dark_base_with_accent(RED, size=(400, 300), accent_frac=0.20)
        ImageDraw.Draw(img).rectangle([0, 290, 400, 300], fill=CYAN)  # ~0.8% of frame
        a = tmp_img(img, "a.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)
        families = result["images"]["a"]["hue_families"]

        # Assert: quantisation would drop this; the histogram must not.
        assert families["red"]["fraction_of_accents"] > 0.5
        assert families["cyan"]["fraction_of_accents"] > 0.0, (
            "a small secondary hue must still be reported; families=" f"{families}"
        )

    def test_hue_family_shift_is_reported_in_the_diff(self, tool, tmp_img):
        # Arrange: same dark base and dominant red, but one image loses its cyan.
        from PIL import ImageDraw

        with_cyan = dark_base_with_accent(RED, size=(400, 300), accent_frac=0.20)
        ImageDraw.Draw(with_cyan).rectangle([0, 290, 400, 300], fill=CYAN)
        without_cyan = dark_base_with_accent(RED, size=(400, 300), accent_frac=0.20)
        a = tmp_img(with_cyan, "a.png")
        b = tmp_img(without_cyan, "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert: a dropped accent hue is the exact revision-loop question
        # ("did the render keep the reference's colour scheme?").
        assert "cyan" in result["diff"]["hue_families_lost"]


class TestPartialHueShift:
    """A *partial* accent shift, which is what real revisions actually produce.

    Measured on the reference image, a cyan->red recolour left a residue of cyan
    pixels near the saturation threshold, so a set-membership test for a "lost"
    family never fired. Detection must therefore be magnitude-based, not
    presence-based.
    """

    def test_diminished_family_is_detected_without_full_removal(self, tool, tmp_img):
        # Arrange: cyan shrinks from a large share to a sliver but never hits zero.
        from PIL import ImageDraw

        def scene(cyan_height):
            img = dark_base_with_accent(RED, size=(400, 300), accent_frac=0.20)
            ImageDraw.Draw(img).rectangle(
                [0, 300 - cyan_height, 400, 300], fill=CYAN
            )
            return img

        a = tmp_img(scene(60), "a.png")  # cyan clearly present
        b = tmp_img(scene(2), "b.png")   # cyan reduced to a residue, still nonzero

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)
        diff = result["diff"]

        # Assert: presence-based detection is not enough on its own.
        assert result["images"]["b"]["hue_families"]["cyan"]["pixels"] > 0
        assert "cyan" not in diff["hue_families_lost"]
        assert "cyan" in diff["hue_families_diminished"]
        assert diff["accent_hue_shift_detected"] is True

    def test_rescale_alone_does_not_trip_the_shift_detector(self, tool, tmp_img):
        # Arrange: identical content, different resolution -- must read as no shift.
        from PIL import Image, ImageDraw

        img = dark_base_with_accent(RED, size=(400, 300), accent_frac=0.20)
        ImageDraw.Draw(img).rectangle([0, 240, 400, 300], fill=CYAN)
        a = tmp_img(img, "a.png")
        b = tmp_img(img.resize((200, 150), Image.LANCZOS), "b.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)

        # Assert: guards against a detector that fires on everything.
        assert result["diff"]["accent_hue_shift_detected"] is False


class TestSyntheticReferenceRegressions:
    """Machine-independent copies of the strongest regressions.

    The equivalents in TestReferenceImageSanity skip on any machine lacking the
    real image, which would let the suite report green with its most important
    assertions unguarded. These run everywhere.
    """

    def test_all_hue_families_are_enumerated(self, tool, tmp_img):
        # Arrange: dominant red plus seven sub-1% secondary hues.
        a = tmp_img(synthetic_reference(), "synth.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)
        families = result["images"]["a"]["hue_families"]

        # Assert: area-weighted extraction drops these; the census must not.
        for hue in ("red", "orange", "yellow", "green", "cyan", "blue", "purple"):
            assert families[hue]["pixels"] > 0, (
                f"{hue} was planted but not reported; "
                f"got { {k: v['pixels'] for k, v in families.items()} }"
            )

    def test_secondary_hues_are_absent_from_the_area_weighted_palettes(
        self, tool, tmp_img
    ):
        """Pins the failure mode itself, so the census cannot be removed silently."""
        # Arrange
        a = tmp_img(synthetic_reference(), "synth.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)
        img = result["images"]["a"]

        # Assert: cyan is reported by the census but not by the base palette --
        # evidence that the two mechanisms are not redundant.
        assert img["hue_families"]["cyan"]["pixels"] > 0
        base_has_cyan = any(
            b > r + 40 and g > r + 40 for r, g, b in (e["rgb"] for e in img["base_palette"])
        )
        assert not base_has_cyan, (
            "base palette unexpectedly surfaced the sub-1% cyan; the fixture no "
            "longer reproduces the area-weighting blind spot"
        )

    def test_dropped_cyan_is_detected_between_two_variants(self, tool, tmp_img):
        # Arrange: identical scenes except one omits the cyan sliver entirely.
        a = tmp_img(synthetic_reference(include_cyan=True), "with.png")
        b = tmp_img(synthetic_reference(include_cyan=False), "without.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a, b)
        diff = result["diff"]

        # Assert
        assert diff["accent_hue_shift_detected"] is True
        assert "cyan" in diff["hue_families_lost"]

    def test_red_family_wrap_branch_is_counted_as_red(self, tool, tmp_img):
        """The red family spans H 0-10 and 246-255; the upper branch needs cover."""
        # Arrange: a single vivid patch at H=250, nothing else saturated.
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (200, 200), (5, 5, 8))
        ImageDraw.Draw(img).rectangle(
            [0, 0, 200, 40], fill=HUE_SAMPLES["red_wrap"]
        )
        a = tmp_img(img, "wrap.png")

        # Act
        result, _ = tool("pil_palette_diff.py", a)
        families = result["images"]["a"]["hue_families"]

        # Assert
        assert families["red"]["pixels"] > 0, "H=250 must count as red, not magenta"
        assert families["magenta"]["pixels"] == 0


class TestArgumentOrder:
    """Direction words are relative to A; the overall verdict is not."""

    def test_verdict_is_symmetric_but_direction_words_flip(self, tool, tmp_img):
        # Arrange
        a = tmp_img(synthetic_reference(include_cyan=True), "with.png")
        b = tmp_img(synthetic_reference(include_cyan=False), "without.png")

        # Act
        forward, _ = tool("pil_palette_diff.py", a, b)
        reverse, _ = tool("pil_palette_diff.py", b, a)

        # Assert: a caller may swap reference and candidate; the verdict must not
        # depend on which way round they did it.
        assert forward["diff"]["accent_hue_shift_detected"] is True
        assert reverse["diff"]["accent_hue_shift_detected"] is True
        # ...but the words describe what B did relative to A, so they invert.
        assert "cyan" in forward["diff"]["hue_families_lost"]
        assert "cyan" in reverse["diff"]["hue_families_gained"]

    def test_deltas_negate_under_swap(self, tool, tmp_img):
        # Arrange
        a = tmp_img(synthetic_reference(include_cyan=True), "with.png")
        b = tmp_img(synthetic_reference(include_cyan=False), "without.png")

        # Act
        forward, _ = tool("pil_palette_diff.py", a, b)
        reverse, _ = tool("pil_palette_diff.py", b, a)

        # Assert
        for hue, delta in forward["diff"]["hue_family_fraction_deltas"].items():
            assert reverse["diff"]["hue_family_fraction_deltas"][hue] == pytest.approx(
                -delta, abs=1e-6
            )

    def test_palette_distances_are_order_independent(self, tool, tmp_img):
        # Arrange
        a = tmp_img(dark_base_with_accent(RED), "a.png")
        b = tmp_img(dark_base_with_accent(CYAN), "b.png")

        # Act
        forward, _ = tool("pil_palette_diff.py", a, b)
        reverse, _ = tool("pil_palette_diff.py", b, a)

        # Assert: the Chamfer distance is symmetrised by construction.
        assert forward["diff"]["accent_palette_distance"] == pytest.approx(
            reverse["diff"]["accent_palette_distance"], abs=1e-6
        )


@pytest.mark.skipif(
    not REFERENCE_IMAGE.exists(), reason="reference image not present on this machine"
)
class TestReferenceImageSanity:
    """Re-derive the numbers the prototyping agent reported, to catch metric bugs.

    Those ad-hoc scripts were deleted; these assertions are the only surviving
    check that the production implementation computes the same quantities.
    """

    def test_metrics_match_prototype_measurements(self, tool):
        # Arrange / Act
        result, _ = tool("pil_palette_diff.py", REFERENCE_IMAGE)
        img = result["images"]["a"]

        # Assert
        assert tuple(img["size"]) == REFERENCE_EXPECTED["size"]
        assert img["luminance"]["mean"] == pytest.approx(
            REFERENCE_EXPECTED["luminance_mean"], abs=0.5
        )
        assert img["luminance"]["std"] == pytest.approx(
            REFERENCE_EXPECTED["luminance_std"], abs=0.5
        )
        assert img["entropy"] == pytest.approx(REFERENCE_EXPECTED["entropy"], abs=0.05)
        assert img["saturation"]["mean"] == pytest.approx(
            REFERENCE_EXPECTED["saturation_mean"], abs=1.0
        )

    def test_accent_mask_recovers_hues_the_global_palette_missed(self, tool):
        """The discriminating check the advisor called for.

        Global quantisation of this image returned no vivid entries. If the
        accent mask also returns none, the thresholds are wrong.
        """
        # Arrange / Act
        result, _ = tool("pil_palette_diff.py", REFERENCE_IMAGE)
        img = result["images"]["a"]

        # Assert
        base_hues = set(_hues(img["base_palette"]))
        accent_hues = set(_hues(img["accent_palette"]))
        assert img["accent_palette"], "accent mask found nothing on a vivid image"
        assert accent_hues - {"other"}, (
            "accent palette must contain a genuine hue, not just dark tones; "
            f"base={base_hues} accent={accent_hues}"
        )

    def test_real_cyan_to_red_recolour_is_detected(self, tool, tmp_img):
        """Regression for the empirically-observed miss.

        A cyan->red hue rotation on the reference image is invisible to
        structural similarity (0.9990 vs 0.9996 for a mere rescale), to both
        perceptual hashes (distance 0), and to base palette distance (0.50).
        The hue-family magnitude comparison is the only thing that catches it,
        so it is pinned here.
        """
        # Arrange: rotate only the cyan family to red, leave all else untouched.
        import numpy as np
        from PIL import Image

        original = Image.open(REFERENCE_IMAGE).convert("RGB")
        hsv = np.asarray(original.convert("HSV")).copy()
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        cyan_mask = (sat > 100) & (val > 60) & (hue >= 96) & (hue <= 130)
        hsv[:, :, 0] = np.where(cyan_mask, 5, hue)
        recoloured = Image.fromarray(hsv, "HSV").convert("RGB")
        variant = tmp_img(recoloured, "recoloured.png")

        # Act
        result, _ = tool("pil_palette_diff.py", REFERENCE_IMAGE, variant)
        diff = result["diff"]

        # Assert
        assert diff["accent_hue_shift_detected"] is True
        assert "cyan" in diff["hue_families_diminished"]
        assert diff["hue_family_fraction_deltas"]["cyan"] < -0.02
        assert diff["hue_family_fraction_deltas"]["red"] > 0.02

    def test_status_colour_language_is_fully_enumerated(self, tool):
        """The regression this whole hue-family feature exists to prevent.

        The graphic encodes pipeline state as colour (red=open, amber=developing,
        cyan=testing, green=done, plus blue/purple lane labels). Cyan is only
        ~0.5% of the frame, so quantisation drops it -- yet losing it would mean
        losing the image's semantic content. All of these must be reported.
        """
        # Arrange / Act
        result, _ = tool("pil_palette_diff.py", REFERENCE_IMAGE)
        families = result["images"]["a"]["hue_families"]

        # Assert
        for hue in ("red", "orange", "yellow", "green", "cyan", "blue", "purple"):
            assert families[hue]["fraction_of_accents"] > 0.0, (
                f"{hue} is visually present but was not reported; "
                f"families={ {k: v['fraction_of_accents'] for k, v in families.items()} }"
            )
