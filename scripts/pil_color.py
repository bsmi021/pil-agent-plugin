"""Perceptual colour science: sRGB -> CIELAB(D65) -> LCh, and CIEDE2000.

Pure numpy, no dependency beyond the ones the plugin already ships. Everything
here is vectorised over a trailing axis of 3, so the same functions serve a
single colour, a palette of eight, or a whole HxW image.

Design constraints, all of them load-bearing and all of them measured rather
than assumed (see docs/research-phase2-colour-and-calibration.md):

*   **D65, hand-rolled.** Never use Pillow's ``convert("LAB")``. It works, but
    it is D50-referenced and 8-bit quantised, and its byte encoding is
    undocumented. Reading it as D65 Lab costs up to 7.5 dE00 -- roughly seven
    times the conventional perceptibility threshold -- in smooth, hue-dependent,
    entirely plausible-looking errors. The illuminant is therefore named in this
    module and echoed into the tools' JSON.
*   **float64 throughout.** The ``C**7`` terms in CIEDE2000 reach ~6e15 for
    C* = 180, and ``25**7 = 6103515625`` already exceeds float32's
    exact-integer range.
*   **dE00 is never normalised.** It is not bounded at 100. Black versus white
    happens to give exactly 100, which invites the error, but the maximum over
    the sRGB cube corners is 111.41 and over a 216-colour sRGB grid 119.22.
    Report raw values; round only at JSON emission, and only to 4 dp -- the
    precision at which the reference data is published.
*   **No verbal bands.** dE00 carries no authoritative perceptibility banding.
    The widely-copied 0-1 / 1-2 / 2-10 table is disclaimed by its own author,
    and "dE 1.0 = just noticeable difference" has no traceable primary source.
    This module emits numbers; the decision threshold is WP2's job.
*   **Hue needs a chroma gate.** The published sRGB->XYZ matrix rounds to seven
    decimals, so its Y row sums to 1.0000001 and a neutral grey lands at
    C* ~ 1e-05 rather than 0 -- with a completely meaningless hue angle of
    158.20 degrees. Never bucket a hue without first requiring chroma.

The CIEDE2000 formulation below is transcribed from the authors' own reference
MATLAB (Sharma, Wu & Dalal 2005, ``deltaE2000.m``) in its radian-internal
convention, and reproduces all 34 published test values to 4 decimal places --
see tests/test_ciede2000.py.
"""

from __future__ import annotations

import numpy as np

# Named in the tools' output so a reader never has to guess which Lab this is.
# ICC/Photoshop Lab -- and Pillow's own LAB mode -- are D50; sRGB and the
# CIEDE2000 literature are D65. Mixing them is worth up to 7.5 dE00.
LAB_ILLUMINANT = "D65"
LAB_OBSERVER = "CIE 1931 2 degree"
LAB_REFERENCE = f"{LAB_ILLUMINANT} ({LAB_OBSERVER} observer)"

# Lindbloom's sRGB/D65 matrix. Its row sums are the D65 white point below,
# which is the strongest available confirmation that matrix and white point are
# the same pair and have not been mixed across sources.
SRGB_TO_XYZ_D65 = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)

D65_WHITE = np.array([0.95047, 1.00000, 1.08883], dtype=np.float64)

# Rational forms, not the rounded 0.008856 / 903.3 decimals: these make the two
# branches of f() join continuously in both value and first derivative.
LAB_EPS = 216.0 / 24389.0
LAB_KAPPA = 24389.0 / 27.0

# 25**7, spelled out because mistyping it as 25e7 is a known failure mode.
POW_25_7 = 6103515625.0

TWO_PI = 2.0 * np.pi

# Landmarks, for orientation only -- NOT thresholds and NOT a normaliser.
# Black vs white is exactly 100.0 at kL=1 because L_bar' = 50 makes S_L = 1 and
# every chroma and hue term vanishes. The sRGB maxima above it are the reason
# dividing by 100 would be wrong.
DE2000_BLACK_VS_WHITE = 100.0
DE2000_MAX_SRGB_CUBE_CORNERS = 111.41
DE2000_MAX_SRGB_216_GRID = 119.22


def srgb_to_linear(channel):
    """Inverse sRGB companding, the piecewise function rather than gamma 2.2.

    A plain 2.2 gamma is wrong, and wrong by the most in the dark tones --
    exactly where this project's images live.
    """
    channel = np.asarray(channel, dtype=np.float64)
    return np.where(
        channel <= 0.04045, channel / 12.92, ((channel + 0.055) / 1.055) ** 2.4
    )


def srgb_to_xyz_array(rgb_array):
    """Vectorised sRGB (0-255) -> XYZ (D65), Y = 1 for white, over a trailing
    axis of 3."""
    lin = srgb_to_linear(np.asarray(rgb_array, dtype=np.float64) / 255.0)
    return lin @ SRGB_TO_XYZ_D65.T


def _lab_f(t):
    """CIELAB's compression function. np.cbrt, not t ** (1/3), so numerical
    noise producing a slightly negative input cannot yield NaN."""
    return np.where(t > LAB_EPS, np.cbrt(t), (LAB_KAPPA * t + 16.0) / 116.0)


def xyz_to_lab_array(xyz_array):
    """Vectorised XYZ (D65, Y = 1 for white) -> CIELAB over a trailing axis."""
    ratio = np.asarray(xyz_array, dtype=np.float64) / D65_WHITE
    f = _lab_f(ratio)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    return np.stack(
        [116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=-1
    )


def rgb_to_lab_array(rgb_array):
    """Vectorised sRGB (0-255) -> CIELAB (D65) over a trailing axis of 3."""
    return xyz_to_lab_array(srgb_to_xyz_array(rgb_array))


def lab_to_lch_array(lab_array):
    """Vectorised CIELAB -> LCh(ab), h in [0, 360).

    atan2(b, a) -- that argument order. Hue is undefined at C* = 0 and
    meaningless near it; callers must gate on chroma before bucketing hue.
    """
    lab = np.asarray(lab_array, dtype=np.float64)
    lightness, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    chroma = np.hypot(a, b)
    hue = np.degrees(np.arctan2(b, a)) % 360.0
    return np.stack([lightness, chroma, hue], axis=-1)


def rgb_to_lch_array(rgb_array):
    """Vectorised sRGB (0-255) -> LCh(ab) over a trailing axis of 3."""
    return lab_to_lch_array(rgb_to_lab_array(rgb_array))


def g_compression(c_bar_ab):
    """CIEDE2000's G term, which stretches a* in the low-chroma region.

    Exposed because getting its input wrong is a documented failure mode that
    the published test data does not catch: G must be computed from the
    arithmetic mean of the two *original* chromas C*_ab, never from C'.
    """
    c_bar = np.asarray(c_bar_ab, dtype=np.float64)
    c7 = c_bar**7
    return 0.5 * (1.0 - np.sqrt(c7 / (c7 + POW_25_7)))


def ciede2000(lab1, lab2, k_l=1.0, k_c=1.0, k_h=1.0):
    """CIEDE2000 colour difference, vectorised over a trailing axis of 3.

    ``lab1`` and ``lab2`` are broadcast against each other, so an (N, 1, 3)
    against a (1, M, 3) yields the full N x M pairwise matrix.

    Returns raw dE00. Do not normalise it, do not divide it by 100, and do not
    attach a verbal band to it -- see the module docstring.
    """
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)
    l1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    l2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    # Step 1 -- original chroma and the G compression term.
    c1_ab = np.hypot(a1, b1)
    c2_ab = np.hypot(a2, b2)
    g = g_compression((c1_ab + c2_ab) / 2.0)

    # Step 2 -- a' and C'. b* is NOT modified; C' is recomputed from the
    # modified a' and the original b*.
    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p = np.hypot(a1p, b1)
    c2p = np.hypot(a2p, b2)
    cp_prod = c1p * c2p
    zero_chroma = cp_prod == 0.0

    # Step 3 -- h' from (a', b*), mapped into [0, 2pi), with the zero case set
    # explicitly rather than relying on a library's atan2(0, 0) convention.
    h1p = np.arctan2(b1, a1p)
    h1p = np.where(h1p < 0.0, h1p + TWO_PI, h1p)
    h1p = np.where(np.abs(a1p) + np.abs(b1) == 0.0, 0.0, h1p)
    h2p = np.arctan2(b2, a2p)
    h2p = np.where(h2p < 0.0, h2p + TWO_PI, h2p)
    h2p = np.where(np.abs(a2p) + np.abs(b2) == 0.0, 0.0, h2p)

    # Step 4 -- the three differences. dC and dH' are signed; the sign matters
    # only through the R_T cross term, but it matters absolutely there.
    d_l = l2 - l1
    d_cp = c2p - c1p

    dhp = h2p - h1p
    dhp = np.where(dhp > np.pi, dhp - TWO_PI, dhp)
    dhp = np.where(dhp < -np.pi, dhp + TWO_PI, dhp)
    dhp = np.where(zero_chroma, 0.0, dhp)
    d_hp = 2.0 * np.sqrt(cp_prod) * np.sin(dhp / 2.0)

    # Step 5 -- the means. L' = L, so L_bar' is a plain arithmetic mean. The
    # three lines for h_bar' are the reference implementation's compact form of
    # the paper's three-case table; the zero-chroma case is applied last.
    lp_bar = (l1 + l2) / 2.0
    cp_bar = (c1p + c2p) / 2.0

    hp_bar = (h1p + h2p) / 2.0
    hp_bar = np.where(np.abs(h1p - h2p) > np.pi, hp_bar - np.pi, hp_bar)
    hp_bar = np.where(hp_bar < 0.0, hp_bar + TWO_PI, hp_bar)
    hp_bar = np.where(zero_chroma, h1p + h2p, hp_bar)

    # Step 6 -- weighting functions. T's four coefficients alternate - + + -.
    q = (lp_bar - 50.0) ** 2
    s_l = 1.0 + 0.015 * q / np.sqrt(20.0 + q)
    s_c = 1.0 + 0.045 * cp_bar
    t = (
        1.0
        - 0.17 * np.cos(hp_bar - np.pi / 6.0)
        + 0.24 * np.cos(2.0 * hp_bar)
        + 0.32 * np.cos(3.0 * hp_bar + np.pi / 30.0)
        - 0.20 * np.cos(4.0 * hp_bar - 63.0 * np.pi / 180.0)
    )
    s_h = 1.0 + 0.015 * cp_bar * t

    # Step 7 -- the blue-region hue rotation. Its Gaussian is in degrees always,
    # which is the only place a degree value appears outside a trig call.
    d_theta = np.radians(30.0) * np.exp(
        -(((np.degrees(hp_bar) - 275.0) / 25.0) ** 2)
    )
    cp_bar7 = cp_bar**7
    r_c = 2.0 * np.sqrt(cp_bar7 / (cp_bar7 + POW_25_7))
    r_t = -np.sin(2.0 * d_theta) * r_c

    # Step 8 -- the result.
    term_l = d_l / (k_l * s_l)
    term_c = d_cp / (k_c * s_c)
    term_h = d_hp / (k_h * s_h)
    return np.sqrt(
        term_l**2 + term_c**2 + term_h**2 + r_t * term_c * term_h
    )


def rgb_ciede2000(rgb1, rgb2):
    """CIEDE2000 between two sRGB (0-255) colours or colour arrays."""
    return ciede2000(rgb_to_lab_array(rgb1), rgb_to_lab_array(rgb2))
