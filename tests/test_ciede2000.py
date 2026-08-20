"""RED tests for the perceptual colour chain in scripts/pil_color.py.

The 34 pairs below are the Sharma, Wu & Dalal (2005) supplementary test data,
transcribed from docs/research-phase2-colour-and-calibration.md -- where the
markdown table was itself machine-checked float-for-float against the file
downloaded from the first author's server (34 rows parsed, 0 mismatches). They
are asserted to 4 decimal places because that is the precision at which the
reference values are published: no tighter, no looser.

Those rows are not a smoke test. They are constructed to catch specific,
well-known implementation errors that otherwise produce plausible numbers:

*   rows 1-6   -- deep blues, the R_T region. A dropped minus sign on R_T
                  changes only blue-region pairs, so a test set without blues
                  would pass a broken implementation.
*   rows 7-8   -- one member is the pure neutral (50, 0, 0), exercising the
                  C'1*C'2 = 0 branch; the same pair in both orders, so they
                  also pin symmetry.
*   rows 9-16  -- sixth-figure perturbations of near-zero a*/b*. dE00 jumps
                  from 7.1792 to 7.2195 for a change of 0.0001 in b2, because
                  H_bar' flips across its three-case boundary. This is a
                  documented property of the formula, so never write a test
                  asserting dE00 is continuous, and never smooth the H_bar'
                  cases to make the jump go away. Rows 13-15 additionally pin
                  that b* is left unmodified by G and that h' is taken from a'
                  rather than a*.
*   rows 17-20 -- gross differences across all three axes.
*   rows 21-24 -- four different pairs all on the unit dE00 iso-surface.
*   rows 25-30 -- realistic mid-chroma pairs; these resemble real image colours.
*   rows 31-32 -- near-neutral light colours.
*   rows 33-34 -- very dark colours, directly relevant to this project.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from pil_color import (  # noqa: E402
    DE2000_BLACK_VS_WHITE,
    DE2000_MAX_SRGB_CUBE_CORNERS,
    POW_25_7,
    ciede2000,
    g_compression,
    lab_to_lch_array,
    rgb_to_lab_array,
    rgb_to_lch_array,
)

# (index, L1, a1, b1, L2, a2, b2, published dE00)
SHARMA_PAIRS = [
    (1, 50.0000, 2.6772, -79.7751, 50.0000, 0.0000, -82.7485, 2.0425),
    (2, 50.0000, 3.1571, -77.2803, 50.0000, 0.0000, -82.7485, 2.8615),
    (3, 50.0000, 2.8361, -74.0200, 50.0000, 0.0000, -82.7485, 3.4412),
    (4, 50.0000, -1.3802, -84.2814, 50.0000, 0.0000, -82.7485, 1.0000),
    (5, 50.0000, -1.1848, -84.8006, 50.0000, 0.0000, -82.7485, 1.0000),
    (6, 50.0000, -0.9009, -85.5211, 50.0000, 0.0000, -82.7485, 1.0000),
    (7, 50.0000, 0.0000, 0.0000, 50.0000, -1.0000, 2.0000, 2.3669),
    (8, 50.0000, -1.0000, 2.0000, 50.0000, 0.0000, 0.0000, 2.3669),
    (9, 50.0000, 2.4900, -0.0010, 50.0000, -2.4900, 0.0009, 7.1792),
    (10, 50.0000, 2.4900, -0.0010, 50.0000, -2.4900, 0.0010, 7.1792),
    (11, 50.0000, 2.4900, -0.0010, 50.0000, -2.4900, 0.0011, 7.2195),
    (12, 50.0000, 2.4900, -0.0010, 50.0000, -2.4900, 0.0012, 7.2195),
    (13, 50.0000, -0.0010, 2.4900, 50.0000, 0.0009, -2.4900, 4.8045),
    (14, 50.0000, -0.0010, 2.4900, 50.0000, 0.0010, -2.4900, 4.8045),
    (15, 50.0000, -0.0010, 2.4900, 50.0000, 0.0011, -2.4900, 4.7461),
    (16, 50.0000, 2.5000, 0.0000, 50.0000, 0.0000, -2.5000, 4.3065),
    (17, 50.0000, 2.5000, 0.0000, 73.0000, 25.0000, -18.0000, 27.1492),
    (18, 50.0000, 2.5000, 0.0000, 61.0000, -5.0000, 29.0000, 22.8977),
    (19, 50.0000, 2.5000, 0.0000, 56.0000, -27.0000, -3.0000, 31.9030),
    (20, 50.0000, 2.5000, 0.0000, 58.0000, 24.0000, 15.0000, 19.4535),
    (21, 50.0000, 2.5000, 0.0000, 50.0000, 3.1736, 0.5854, 1.0000),
    (22, 50.0000, 2.5000, 0.0000, 50.0000, 3.2972, 0.0000, 1.0000),
    (23, 50.0000, 2.5000, 0.0000, 50.0000, 1.8634, 0.5757, 1.0000),
    (24, 50.0000, 2.5000, 0.0000, 50.0000, 3.2592, 0.3350, 1.0000),
    (25, 60.2574, -34.0099, 36.2677, 60.4626, -34.1751, 39.4387, 1.2644),
    (26, 63.0109, -31.0961, -5.8663, 62.8187, -29.7946, -4.0864, 1.2630),
    (27, 61.2901, 3.7196, -5.3901, 61.4292, 2.2480, -4.9620, 1.8731),
    (28, 35.0831, -44.1164, 3.7933, 35.0232, -40.0716, 1.5901, 1.8645),
    (29, 22.7233, 20.0904, -46.6940, 23.0331, 14.9730, -42.5619, 2.0373),
    (30, 36.4612, 47.8580, 18.3852, 36.2715, 50.5065, 21.2231, 1.4146),
    (31, 90.8027, -2.0831, 1.4410, 91.1528, -1.6435, 0.0447, 1.4441),
    (32, 90.9257, -0.5406, -0.9208, 88.6381, -0.8985, -0.7239, 1.5381),
    (33, 6.7747, -0.2908, -2.4247, 5.8714, -0.0985, -2.2286, 0.6377),
    (34, 2.0776, 0.0795, -1.1350, 0.9033, -0.0636, -0.5514, 0.9082),
]

# Published precision: 4 dp. Assert exactly that band.
PUBLISHED_TOLERANCE = 5e-5

# Exact float Lab (D65) for the sRGB primaries, from the research document's
# measured table. Quoted to 2 dp there, so the tolerance is the rounding width
# with margin.
SRGB_D65_LAB = [
    ("#ff0000", (255, 0, 0), (53.24, 80.09, 67.20)),
    ("#00ff00", (0, 255, 0), (87.73, -86.18, 83.18)),
    ("#0000ff", (0, 0, 255), (32.30, 79.19, -107.86)),
    ("#ffff00", (255, 255, 0), (97.14, -21.55, 94.48)),
]

# Measured LCh for the colours the accent-gate discussion turns on.
SRGB_LCH = [
    ("dim maroon #401010", (0x40, 0x10, 0x10), (12.60, 26.37, 27.99)),
    ("dark slate #1e2430", (0x1E, 0x24, 0x30), (14.12, 8.81, 275.93)),
    ("pale pink #ffd0d0", (0xFF, 0xD0, 0xD0), (87.45, 17.65, 20.74)),
    ("pale mint #d0ffe4", (0xD0, 0xFF, 0xE4), (96.22, 21.60, 158.31)),
    ("vivid yellow #ffff00", (255, 255, 0), (97.14, 96.91, 102.85)),
    ("vivid blue #0000ff", (0, 0, 255), (32.30, 133.81, 306.28)),
    ("UI cyan #22b8cf", (0x22, 0xB8, 0xCF), (68.84, 36.39, 217.52)),
    ("brown #6b4a2f", (0x6B, 0x4A, 0x2F), (34.46, 24.10, 63.82)),
]


def _lab(row, side):
    """Pull (L, a, b) for side 0 or 1 out of a SHARMA_PAIRS row."""
    offset = 1 + 3 * side
    return np.array(row[offset : offset + 3], dtype=np.float64)


class TestPublishedVerificationData:
    """The gate WP1 was allowed to ship on."""

    @pytest.mark.parametrize(
        "row", SHARMA_PAIRS, ids=[f"pair{r[0]:02d}" for r in SHARMA_PAIRS]
    )
    def test_published_pair_reproduces_to_four_decimals(self, row):
        # Arrange
        expected = row[7]

        # Act
        got = float(ciede2000(_lab(row, 0), _lab(row, 1)))

        # Assert
        assert abs(got - expected) < PUBLISHED_TOLERANCE, (
            f"pair {row[0]}: published {expected}, computed {got:.6f}"
        )

    def test_every_pair_is_symmetric_under_swap(self):
        """Swapping standard and sample flips the signs of dL, dC and dH'
        simultaneously, and the R_T cross term carries dC*dH', so it cancels."""
        # Arrange / Act / Assert
        for row in SHARMA_PAIRS:
            forward = float(ciede2000(_lab(row, 0), _lab(row, 1)))
            reverse = float(ciede2000(_lab(row, 1), _lab(row, 0)))
            assert forward == pytest.approx(reverse, abs=1e-12), f"pair {row[0]}"

    def test_the_documented_discontinuity_is_preserved(self):
        """Rows 9-12 differ only in the sixth significant figure of b2, yet the
        published answer jumps. Pinned so nobody 'fixes' the H_bar' cases."""
        # Arrange
        rows = {r[0]: r for r in SHARMA_PAIRS}

        # Act
        low = float(ciede2000(_lab(rows[10], 0), _lab(rows[10], 1)))
        high = float(ciede2000(_lab(rows[11], 0), _lab(rows[11], 1)))

        # Assert
        assert round(low, 4) == 7.1792
        assert round(high, 4) == 7.2195


class TestVectorisation:
    """One code path serves a colour, a palette and an image."""

    def test_vectorised_call_matches_scalar_calls(self):
        # Arrange: all 34 pairs stacked into two (34, 3) arrays.
        lab1 = np.array([_lab(r, 0) for r in SHARMA_PAIRS])
        lab2 = np.array([_lab(r, 1) for r in SHARMA_PAIRS])

        # Act
        batched = ciede2000(lab1, lab2)
        one_at_a_time = np.array(
            [float(ciede2000(_lab(r, 0), _lab(r, 1))) for r in SHARMA_PAIRS]
        )

        # Assert
        assert batched.shape == (len(SHARMA_PAIRS),)
        assert np.allclose(batched, one_at_a_time, rtol=0.0, atol=1e-12)

    def test_broadcasting_produces_the_full_pairwise_matrix(self):
        """The shape the palette Chamfer distance relies on."""
        # Arrange
        lab1 = np.array([_lab(r, 0) for r in SHARMA_PAIRS[:5]])
        lab2 = np.array([_lab(r, 1) for r in SHARMA_PAIRS[:5]])

        # Act
        matrix = ciede2000(lab1[:, None, :], lab2[None, :, :])

        # Assert: the diagonal is the elementwise result.
        assert matrix.shape == (5, 5)
        assert np.allclose(
            np.diag(matrix), ciede2000(lab1, lab2), rtol=0.0, atol=1e-12
        )

    def test_identical_colours_are_zero(self):
        # Arrange
        lab = np.array([_lab(r, 0) for r in SHARMA_PAIRS])

        # Act / Assert
        assert np.all(ciede2000(lab, lab) == 0.0)


class TestScaleLandmarks:
    """dE00's scale, and the normalisation error it invites."""

    def test_black_versus_white_is_exactly_one_hundred(self):
        """In Lab space this is exact: L_bar' = 50 makes S_L = 1 and every
        chroma and hue term vanishes, leaving dL = 100."""
        # Arrange
        black = np.array([0.0, 0.0, 0.0])
        white = np.array([100.0, 0.0, 0.0])

        # Act
        got = float(ciede2000(black, white))

        # Assert
        assert got == DE2000_BLACK_VS_WHITE

    def test_srgb_black_versus_white_lands_on_the_landmark_after_rounding(self):
        """Through the sRGB chain it is 100.000004, not 100 exactly: the
        published matrix rounds to 7 decimals so its Y row sums to 1.0000001.
        Emission rounds at 4 dp, which is where the landmark appears."""
        # Arrange
        lab = rgb_to_lab_array(np.array([[0, 0, 0], [255, 255, 255]], dtype=np.float64))

        # Act
        got = float(ciede2000(lab[0], lab[1]))

        # Assert
        assert round(got, 4) == DE2000_BLACK_VS_WHITE

    def test_dE00_is_not_bounded_at_one_hundred(self):
        """Black-vs-white is a landmark, not a maximum. This is the assertion
        that makes normalising by 100 -- or presenting dE00 as a percentage --
        visibly wrong."""
        # Arrange: the sRGB cube corners' worst pair.
        green = np.array([0, 255, 0], dtype=np.float64)
        magenta = np.array([255, 0, 255], dtype=np.float64)

        # Act
        got = float(ciede2000(rgb_to_lab_array(green), rgb_to_lab_array(magenta)))

        # Assert
        assert got > DE2000_BLACK_VS_WHITE
        assert got == pytest.approx(DE2000_MAX_SRGB_CUBE_CORNERS, abs=0.05)


class TestGCompression:
    """The G term, which the published data does not obviously exercise.

    G must be computed from the arithmetic mean of the two *original* chromas
    C*_ab. Computing it from C' would be circular, and the research document
    flags it as a failure mode the 34 rows do not clearly catch -- so it gets
    its own assertions here.
    """

    def test_g_is_one_half_at_zero_chroma(self):
        assert float(g_compression(0.0)) == 0.5

    def test_g_at_the_twenty_five_crossover(self):
        # At C_bar = 25 the ratio is exactly 1/2, so G = 0.5 * (1 - sqrt(0.5)).
        expected = 0.5 * (1.0 - (0.5**0.5))
        assert float(g_compression(25.0)) == pytest.approx(expected, abs=1e-12)

    def test_g_decays_monotonically_with_chroma(self):
        chroma = np.array([0.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0])
        g = g_compression(chroma)
        assert np.all(np.diff(g) < 0.0)
        assert g[-1] < 0.01

    def test_the_twenty_five_to_the_seventh_constant(self):
        """Mistyping this as 25e7 is a documented failure mode."""
        assert POW_25_7 == 25.0**7
        assert POW_25_7 == 6103515625.0


class TestSrgbToLabChain:
    """The conversion is tested separately from the formula: the Sharma data is
    defined purely in Lab coordinates and is therefore illuminant-agnostic."""

    @pytest.mark.parametrize(
        "name,rgb,expected", SRGB_D65_LAB, ids=[c[0] for c in SRGB_D65_LAB]
    )
    def test_primaries_land_on_the_measured_d65_values(self, name, rgb, expected):
        # Act
        lab = rgb_to_lab_array(np.array(rgb, dtype=np.float64))

        # Assert
        assert lab[0] == pytest.approx(expected[0], abs=0.05), f"{name} L*"
        assert lab[1] == pytest.approx(expected[1], abs=0.05), f"{name} a*"
        assert lab[2] == pytest.approx(expected[2], abs=0.05), f"{name} b*"

    def test_blue_is_d65_and_not_silently_d50(self):
        """The single most valuable assertion in this class. Pillow's own LAB
        mode is D50; reading it as D65 costs up to 7.5 dE00 in smooth,
        plausible-looking errors. Under D50, blue's a* is 68.30; under D65 it is
        79.19, and nothing else about the number looks wrong."""
        # Act
        lab = rgb_to_lab_array(np.array([0, 0, 255], dtype=np.float64))

        # Assert
        assert lab[1] == pytest.approx(79.19, abs=0.05)
        assert abs(lab[1] - 68.30) > 5.0, "a* looks D50-referenced"

    def test_white_and_black_bracket_the_lightness_scale(self):
        # Act
        lab = rgb_to_lab_array(
            np.array([[0, 0, 0], [255, 255, 255]], dtype=np.float64)
        )

        # Assert
        assert lab[0][0] == pytest.approx(0.0, abs=1e-6)
        assert lab[1][0] == pytest.approx(100.0, abs=1e-4)

    def test_neutral_greys_have_no_meaningful_chroma(self):
        # Act
        greys = np.array([[v, v, v] for v in (64, 128, 192, 255)], dtype=np.float64)
        lch = rgb_to_lch_array(greys)

        # Assert: a* and b* are ~1e-05 rather than 0 because the published
        # matrix rounds to 7 decimals. Small, but not nothing.
        assert np.all(lch[:, 1] < 1e-4)

    def test_a_grey_still_produces_a_definite_and_meaningless_hue(self):
        """Why the chroma gate is a correctness requirement, not an
        optimisation: without it a greyscale image reports as uniformly
        green-cyan."""
        # Act
        lch = rgb_to_lch_array(np.array([128, 128, 128], dtype=np.float64))

        # Assert
        assert lch[1] < 1e-4
        assert float(lch[2]) == pytest.approx(158.20, abs=0.5)


class TestLchConversion:
    @pytest.mark.parametrize(
        "name,rgb,expected", SRGB_LCH, ids=[c[0] for c in SRGB_LCH]
    )
    def test_measured_lch_values_are_reproduced(self, name, rgb, expected):
        # Act
        lch = rgb_to_lch_array(np.array(rgb, dtype=np.float64))

        # Assert
        assert lch[0] == pytest.approx(expected[0], abs=0.05), f"{name} L*"
        assert lch[1] == pytest.approx(expected[1], abs=0.05), f"{name} C*"
        assert lch[2] == pytest.approx(expected[2], abs=0.05), f"{name} h_ab"

    def test_hue_uses_atan2_b_a_and_wraps_into_zero_to_360(self):
        """Swapping atan2's arguments reflects the wheel about 45 degrees and
        still produces plausible-looking numbers, so it needs pinning."""
        # Arrange: +a is ~0 degrees, +b is 90, -a is 180, -b is 270.
        lab = np.array(
            [
                [50.0, 10.0, 0.0],
                [50.0, 0.0, 10.0],
                [50.0, -10.0, 0.0],
                [50.0, 0.0, -10.0],
            ]
        )

        # Act
        lch = lab_to_lch_array(lab)

        # Assert
        assert np.allclose(lch[:, 2], [0.0, 90.0, 180.0, 270.0], atol=1e-9)
        assert np.allclose(lch[:, 1], 10.0, atol=1e-9)
