#!/usr/bin/env python
"""D65 sRGB -> CIELAB -> LCh, hand-rolled in numpy.

Two constraints from the phase 2 research, both load-bearing:

*   **Do not use Pillow's ``convert("LAB")``.** It works, but it is
    D50-referenced, 8-bit quantised, and its byte encoding is undocumented.
    Calibrating hue-angle boundaries against an undocumented encoding would
    produce boundaries nobody could reproduce.
*   **D65 throughout**, matching sRGB's own white point, so the L and C values
    here mean the same thing as the ones the research reasoned about when it
    suggested ``C_MIN = 20`` / ``L_MIN = 20`` as inputs to WP2.

This module deliberately does *not* implement CIEDE2000. That is WP1's
deliverable (``scripts/pil_color.py``), verified against the Sharma, Wu & Dalal
dataset; a second uncertified copy living in the calibration harness would be a
liability. WP2 reads whatever perceptual-distance fields the palette tool emits.
"""

from __future__ import annotations

import numpy as np

# sRGB (D65) -> XYZ. IEC 61966-2-1 matrix.
SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)

# D65 white point, 2 degree observer.
WHITE_D65 = np.array([0.95047, 1.00000, 1.08883], dtype=np.float64)

_DELTA = 6.0 / 29.0


def srgb_to_linear(channel):
    """sRGB transfer function inverse, on 0..1 floats."""
    return np.where(channel <= 0.04045, channel / 12.92, ((channel + 0.055) / 1.055) ** 2.4)


def rgb_to_xyz(rgb_uint8):
    """sRGB 0-255 (..., 3) -> XYZ (..., 3), D65 referenced."""
    linear = srgb_to_linear(np.asarray(rgb_uint8, dtype=np.float64) / 255.0)
    return linear @ SRGB_TO_XYZ.T


def _f(t):
    return np.where(t > _DELTA**3, np.cbrt(t), t / (3.0 * _DELTA**2) + 4.0 / 29.0)


def xyz_to_lab(xyz):
    """XYZ -> CIELAB, D65 referenced."""
    scaled = np.asarray(xyz, dtype=np.float64) / WHITE_D65
    fx, fy, fz = _f(scaled[..., 0]), _f(scaled[..., 1]), _f(scaled[..., 2])
    return np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=-1)


def rgb_to_lab(rgb_uint8):
    return xyz_to_lab(rgb_to_xyz(rgb_uint8))


def lab_to_lch(lab):
    """CIELAB -> (L, C, h degrees in [0, 360))."""
    lab = np.asarray(lab, dtype=np.float64)
    lightness = lab[..., 0]
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360.0
    return np.stack([lightness, chroma, hue], axis=-1)


def rgb_to_lch(rgb_uint8):
    return lab_to_lch(rgb_to_lab(rgb_uint8))


# --- Circular helpers for hue-angle statistics --------------------------------


def arc_delta(origin, angles):
    """Forward angular distance from ``origin`` to ``angles``, in [0, 360)."""
    return (np.asarray(angles, dtype=np.float64) - float(origin)) % 360.0


def circular_mean(angles):
    """Mean direction of a set of angles in degrees, or None when empty."""
    angles = np.asarray(angles, dtype=np.float64)
    if angles.size == 0:
        return None
    radians = np.radians(angles)
    return float(np.degrees(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean())) % 360.0)


def circular_spread(angles, low=1.0, high=99.0):
    """Percentile interval of angles taken about their own mean direction.

    Returns ``(low_angle, high_angle, width_degrees)``. Working relative to the
    mean is what makes this correct for the red family, whose angles straddle
    0/360 and whose naive percentiles would be 0 and 360.
    """
    angles = np.asarray(angles, dtype=np.float64)
    if angles.size == 0:
        return None, None, None
    mean = circular_mean(angles)
    # Signed offsets in (-180, 180] about the mean direction.
    offsets = (angles - mean + 180.0) % 360.0 - 180.0
    lo, hi = np.percentile(offsets, [low, high])
    return (mean + lo) % 360.0, (mean + hi) % 360.0, float(hi - lo)
