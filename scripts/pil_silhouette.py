#!/usr/bin/env python
"""Silhouette shape descriptors on the working-resolution foreground mask.

Emits JSON on stdout for one image. Three descriptors, all measured on the
same working-resolution boolean foreground mask that pil_structure_diff's
grid statistics use (see pil_common.WORKING_LONG_EDGE, resize_mask,
foreground_mask):

*   ``fill_ratio``                   -- mask area / axis-aligned tight bbox area
*   ``perimeter_squared_over_area``  -- P^2 / A, dimensionless
*   ``orientation_histogram``        -- weighted gradient-orientation census
                                        at boundary pixels

Every descriptor degrades to ``null`` -- never a fabricated number -- under
any of the pil_common mask-quality flags ``foreground_mask_empty``,
``foreground_too_small`` and ``background_dominant``. The thresholds those
flags encode (ALPHA_FOREGROUND_MIN, FOREGROUND_MIN_FRACTION,
BACKGROUND_DOMINANT_MAX) are reused verbatim from pil_common; they are not
re-derived here.

SCOPE. This tool measures a rendered silhouette. It does NOT measure
geometry: perimeter and area are pixel counts on the raster mask, not
polygon or vertex counts. See README.md's scope-limit note and the
``geometry.*`` predicate family in pil_contract_verdict.py -- those
questions belong to a Blender scene, not to a render.

CALIBRATION. Whether an observed descriptor delta between two renders
usefully exceeds pose/rotation/resampling noise is a question the tool
itself cannot answer -- the empirical noise floor lives in the
calibration bundle at ``runs/2026-08-20-silhouette-discrimination/`` and
the demotion decisions it records. Read the bundle before asserting that
two renders have "the same shape" or "a different shape".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_common import (  # noqa: E402
    ALPHA_FOREGROUND_MIN,
    BACKGROUND_DOMINANT_MAX,
    DEFAULT_BACKGROUND_DELTA,
    FOREGROUND_MIN_FRACTION,
    WORKING_LONG_EDGE,
    foreground_mask,
    load_rgba_straight,
    mask_bbox,
    resize_mask,
    to_working,
)

TOOL_VERSION = "0.5.0"

# 8 equal-width bins over [0, 180) degrees. Orientation is 180-periodic
# (a rising and falling edge trace the same underlying line direction),
# so folding to [0, 180) is exact rather than an approximation.
ORIENTATION_BINS = 8

_SUPPRESSING_FLAGS = ("foreground_mask_empty", "foreground_too_small", "background_dominant")

INTERPRETATION_LIMITS = [
    "All three shape descriptors report null when any of the mask-quality "
    "flags foreground_mask_empty, foreground_too_small, or "
    "background_dominant is set. The mask is unreliable in those regimes "
    "and computing a shape descriptor from noise is worse than reporting "
    "nothing. The three thresholds are reused verbatim from pil_common "
    "(ALPHA_FOREGROUND_MIN, FOREGROUND_MIN_FRACTION, "
    "BACKGROUND_DOMINANT_MAX); they are not re-derived here.",
    "This tool derives all three mask-quality flags from ONE full-"
    "resolution foreground fraction (mean of the full-resolution mask). "
    "pil_structure_diff computes background_dominant from a working-size "
    "cheap estimate (foreground_estimate) and foreground_too_small from "
    "the full-resolution mask; the deviation here is deliberate -- "
    "silhouette descriptors are fundamentally foreground-based, so a "
    "single provenance for their gating flags reads more consistently "
    "than mixing two sources. The threshold VALUES are identical to "
    "pil_structure_diff's; only the source of the fraction differs.",
    "fill_ratio is the mask's foreground pixel count divided by the "
    f"area of the mask's tight axis-aligned bounding box, both counted on "
    f"the working-resolution copy (long edge {WORKING_LONG_EDGE}px). It "
    "is scale-invariant in principle; the working resample smooths sub-"
    "pixel jitter. The bbox is axis-aligned, so an elongated shape "
    "rotated off-axis reads a substantially different fill_ratio -- read "
    "orientation_histogram before interpreting a fill_ratio delta as a "
    "shape change. Suppressed by foreground_mask_empty, "
    "foreground_too_small, background_dominant.",
    "perimeter_squared_over_area (P^2/A) counts a boundary pixel as any "
    "mask pixel with at least one 4-connectivity axis-neighbour that is "
    "background (the frame edge counts as background). This is a PIXEL "
    "count, not a geometric arc length: a 45-degree edge is undercounted "
    "by roughly a factor of sqrt(2) compared with its continuum length. "
    "The continuum P^2/A minimum of a filled disc is 4*pi (~12.57); the "
    "raster measurement approximates it and is only comparable between "
    "two renders resampled to the same working resolution "
    f"({WORKING_LONG_EDGE}px long edge). Suppressed by "
    "foreground_mask_empty, foreground_too_small, background_dominant.",
    "orientation_histogram bins the local gradient orientation at each "
    f"mask boundary pixel into {ORIENTATION_BINS} equal-width bins "
    "spanning [0, 180) degrees (edge orientation is 180-periodic), "
    "weighted by gradient magnitude, then normalised to sum to 1. Bin 0 "
    "is [0, 22.5) degrees measured from the +x axis of the gradient. It "
    "is a coarse silhouette-orientation signature; do NOT read it as an "
    "object-pose measurement -- the field trial "
    "(runs/2026-08-18-skeleton-warrior-asset-review) deliberately "
    "declined general pose / proportion measurement as too easily "
    "misleading outside single-figure-on-flat-backdrop cases. Rotating "
    "the input rotates the histogram, so any real rotation between two "
    "renders moves this vector -- consult the calibration bundle "
    "(runs/2026-08-20-silhouette-discrimination) before interpreting a "
    "delta. Suppressed by foreground_mask_empty, foreground_too_small, "
    "background_dominant.",
    "This tool does NOT measure geometry. Perimeter and area are pixel "
    "counts on a rendered silhouette, not polygon or vertex counts; a "
    "polygon question is answered by scene mesh statistics, not by this "
    "tool. See README.md's scope-limit note.",
]


def _fill_ratio(mask):
    """mask area / axis-aligned tight bbox area, both in pixels."""
    bbox = mask_bbox(mask)
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    bbox_area = (right - left) * (bottom - top)
    if bbox_area <= 0:
        return None
    return round(float(int(mask.sum()) / float(bbox_area)), 6)


def _boundary_and_perimeter(mask):
    """(boundary_pixel_mask, perimeter_pixel_count, mask_pixel_count).

    4-connectivity: a foreground pixel is a boundary pixel iff at least
    one of its four axis-neighbours is background. Padding outside the
    array is background too, so a mask pixel touching the frame edge is
    counted as a boundary pixel.
    """
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    up = padded[:-2, 1:-1]
    down = padded[2:, 1:-1]
    left = padded[1:-1, :-2]
    right = padded[1:-1, 2:]
    interior = up & down & left & right
    boundary = mask & ~interior
    return boundary, int(boundary.sum()), int(mask.sum())


def _perimeter_squared_over_area(perimeter, area):
    if area <= 0 or perimeter <= 0:
        return None
    return round(float(perimeter) ** 2 / float(area), 6)


def _orientation_histogram(mask, boundary):
    """Gradient-orientation histogram at boundary pixels, weighted by magnitude.

    Returns a list of ``ORIENTATION_BINS`` floats summing to 1.0 (within
    rounding), or None when the boundary carries no gradient (a
    degenerate one-pixel-thick mask, or a mask that fills the frame with
    no boundary at all).

    Angles are folded to [0, 180): a horizontal edge (gradient along y)
    and its 180-degree twin both land in the same bin, because a
    silhouette edge has no signed direction. The +x axis is bin 0.
    """
    if not boundary.any():
        return None
    mask_f = mask.astype(np.float64)
    gy, gx = np.gradient(mask_f)
    magnitude = np.hypot(gx, gy)
    weights = magnitude * boundary
    total = float(weights.sum())
    if total <= 0.0:
        return None
    angle_deg = np.degrees(np.arctan2(gy, gx)) % 180.0
    counts, _ = np.histogram(
        angle_deg,
        bins=ORIENTATION_BINS,
        range=(0.0, 180.0),
        weights=weights,
    )
    return [round(float(x) / total, 6) for x in counts]


def analyse(path, background_delta):
    """Compute per-image silhouette descriptors and their gating flags.

    Returns the ``image`` block of the payload, with descriptors set to
    None whenever any suppressing flag fires. The full-resolution mask is
    what the flags are derived from (one provenance, three flags -- see
    interpretation_limits); the descriptors themselves are computed on the
    NEAREST-resampled working-resolution mask, the same pixel-selection
    rule pil_structure_diff uses (resize_mask in pil_common).
    """
    composited, _straight, alpha = load_rgba_straight(path)
    full_mask, source, _background_hex = foreground_mask(
        composited, alpha, background_delta
    )
    fraction = float(full_mask.mean())

    flags = []
    if not bool(full_mask.any()):
        flags.append("foreground_mask_empty")
    else:
        if fraction < FOREGROUND_MIN_FRACTION:
            flags.append("foreground_too_small")
        if fraction < BACKGROUND_DOMINANT_MAX:
            flags.append("background_dominant")

    working = to_working(composited)
    working_mask = resize_mask(full_mask, working.size)
    working_area = int(working_mask.sum())

    suppressed = any(flag in _SUPPRESSING_FLAGS for flag in flags)
    if suppressed or working_area == 0:
        if working_area == 0 and "foreground_mask_empty" not in flags:
            # The full-res mask was non-empty but NEAREST-resample to
            # working size dropped every pixel. Report the empty-mask
            # flag rather than silently null-ing without a stated reason.
            flags.append("foreground_mask_empty")
        descriptors = {
            "fill_ratio": None,
            "perimeter_squared_over_area": None,
            "orientation_histogram": None,
        }
        working_stats = {
            "mask_pixels": working_area,
            "bbox_pixel_rect": None,
            "perimeter_pixels": None,
        }
    else:
        boundary, perimeter, area = _boundary_and_perimeter(working_mask)
        bbox = mask_bbox(working_mask)
        descriptors = {
            "fill_ratio": _fill_ratio(working_mask),
            "perimeter_squared_over_area": _perimeter_squared_over_area(
                perimeter, area
            ),
            "orientation_histogram": _orientation_histogram(
                working_mask, boundary
            ),
        }
        working_stats = {
            "mask_pixels": area,
            "bbox_pixel_rect": list(bbox) if bbox else None,
            "perimeter_pixels": perimeter,
        }

    return {
        "path": str(path),
        "size": list(composited.size),
        "working_size": list(working.size),
        "foreground": {
            "source": source,
            "fraction_of_frame": round(fraction, 6),
        },
        "working_mask": working_stats,
        "descriptors": descriptors,
        "flags": sorted(flags),
    }


def main(argv=None):
    """Own the CLI shell and JSON emission.

    Any rejection path exits 2 with **empty stdout** and writes no partial
    file, matching every other tool in this tree (docs/phase3-handoff.md
    §3, D5 in docs/aaa-build-plan.md). argparse's own invalid-invocation
    path already prints nothing to stdout on exit 2; the image-read path
    is guarded explicitly.
    """
    parser = argparse.ArgumentParser(
        description="Silhouette shape descriptors on the working-resolution "
        "foreground mask."
    )
    parser.add_argument("image", help="image path")
    parser.add_argument(
        "--background-delta",
        type=float,
        default=DEFAULT_BACKGROUND_DELTA,
        help="OKLab distance from the border-median colour within which an "
        f"opaque pixel counts as background (default {DEFAULT_BACKGROUND_DELTA}); "
        "used only when the source has no real alpha channel",
    )
    args = parser.parse_args(argv)

    try:
        image_block = analyse(args.image, args.background_delta)
    except (OSError, ValueError) as exc:
        print(
            f"pil_silhouette: cannot read image {args.image!r}: {exc}",
            file=sys.stderr,
        )
        return 2

    payload = {
        "tool": "pil_silhouette",
        "version": TOOL_VERSION,
        "parameters": {
            "background_delta": args.background_delta,
            "working_long_edge": WORKING_LONG_EDGE,
            "alpha_foreground_min": ALPHA_FOREGROUND_MIN,
            "foreground_min_fraction": FOREGROUND_MIN_FRACTION,
            "background_dominant_max": BACKGROUND_DOMINANT_MAX,
            "orientation_bins": ORIENTATION_BINS,
        },
        "image": image_block,
        "interpretation_limits": INTERPRETATION_LIMITS,
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
