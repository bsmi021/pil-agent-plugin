#!/usr/bin/env python
"""Structural / layout measurement and comparison for one or two images.

Emits JSON on stdout. Provides both a fuzzy similarity path (per-cell statistics
over a fractional grid, plus perceptual hash distance) and a near-exact path
(changed-region bounding box), so a caller can ask either "is this the same
layout and style" or "what specifically changed between these two".

Scope guard: edge density and entropy are 2D image-complexity proxies. They are
NOT measurements of 3D geometry. Do not read them as polygon counts -- query the
Blender scene's own mesh statistics for that.

Usage:
    python pil_structure_diff.py "ref.png"
    python pil_structure_diff.py "ref.png" "render.png" --grid 4x3
    python pil_structure_diff.py "view_a.png" "view_b.png" --foreground

Use --foreground for object renders on a shared preview background. Full-frame
similarity over two such frames is arithmetically dominated by the identical
background (a 1.5%-of-frame object cannot move the score more than a few
percent); foreground mode masks the background, crops to the object's bounding
box, and scores only grid cells with real foreground support.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_common import (  # noqa: E402
    ALPHA_FOREGROUND_MIN,
    BACKGROUND_DOMINANT_MAX,
    CELL_MIN_SUPPORT_PIXELS,
    CHANGE_THRESHOLD,
    DEFAULT_BACKGROUND_DELTA,
    FOREGROUND_MIN_FRACTION,
    WORKING_LONG_EDGE,
    ahash,
    apply_mask,
    dhash,
    entropy_of,
    foreground_estimate,
    foreground_mask,
    fractional_cells,
    hamming,
    load_rgb_alpha,
    luminance_array,
    luminance_stats,
    mask_bbox,
    parse_grid,
    resize_mask,
    symmetry_scores,
    to_working,
)

TOOL_VERSION = "0.2.0"

# Feature scales used to normalise each per-cell statistic into roughly 0..1
# before differencing, so no single feature dominates the similarity score.
FEATURE_SCALES = {
    "luminance_mean": 255.0,
    "luminance_std": 128.0,
    "edge_mean": 64.0,
    "entropy": 8.0,
}

INTERPRETATION_LIMITS = [
    "edge_mean and entropy are 2D image-complexity proxies, NOT geometry: they "
    "do not measure polygon count, mesh density or topology. Shading, normal "
    "maps, lighting and camera angle all move these numbers independently of "
    "the underlying model. For polygon or topology questions, query the 3D "
    "scene's mesh statistics directly instead of analysing a render.",
    "The grid is rectangular and ignores real object boundaries, so a cell may "
    "mix unrelated content; per-cell numbers describe pixel neighbourhoods, not "
    "semantic regions.",
    "structural_similarity is a normalised mean absolute difference over cell "
    "statistics. It is sensitive to global lighting and exposure shifts, which "
    "can lower the score even when layout is unchanged.",
    "Perceptual hash distance detects near-duplicates and survives rescaling, "
    "but is weak at distinguishing images that differ only in fine detail.",
    "In full-frame mode structural_similarity includes the background. Two "
    "different objects on the same large background will score near 1.0 -- the "
    "shared background is doing the scoring, not the objects. When the "
    "background_dominant flag appears (or similarity is high while "
    "changed_area_fraction is tiny), re-run with --foreground before drawing "
    "any conclusion about the subjects.",
    "In --foreground mode both images are cropped to their foreground bounding "
    "boxes before gridding, so cells correspond object-to-object rather than "
    "frame-to-frame, and cells without foreground support are excluded from "
    "the score (see cells_compared / cells_skipped_low_support).",
    "Foreground statistics of a THIN object degrade across resolutions: when "
    "the object is a few pixels wide, most of its pixels are edge-blended with "
    "the background, so a rescaled copy reads moderately different even though "
    "dhash still reports the same layout. Compare like-resolution renders "
    "where possible; rely on relative ranking otherwise.",
    "entropy_delta is DEMOTED: WP2 calibration (runs/2026-08-19-phase2-"
    "calibration) found it unable to resolve 24 of 26 perturbation families "
    "at any tested magnitude, with 9 non-monotonic response curves. It is "
    "kept for continuity; do not let it decide anything.",
]


def analyse(path, cols, rows, foreground=False, background_delta=DEFAULT_BACKGROUND_DELTA):
    rgb, alpha = load_rgb_alpha(path)
    flags = []
    subject = rgb
    subject_mask = None

    if foreground:
        full_mask, source, background_hex = foreground_mask(
            rgb, alpha, background_delta
        )
        fraction = float(full_mask.mean())
        bbox = mask_bbox(full_mask)
        if bbox is None:
            # Nothing survived the mask; measuring an empty set would be
            # fabrication, so fall back to full-frame and say so.
            flags.append("foreground_mask_empty")
            foreground_block = {
                "applied": False,
                "source": source,
                "fraction_of_frame": round(fraction, 6),
                "background_estimate": background_hex,
            }
        else:
            if fraction < FOREGROUND_MIN_FRACTION:
                flags.append("foreground_too_small")
            left, top, right, bottom = bbox
            subject = rgb.crop(bbox)
            subject_mask = full_mask[top:bottom, left:right]
            foreground_block = {
                "applied": True,
                "source": source,
                "fraction_of_frame": round(fraction, 6),
                "bbox_fractional": [
                    round(left / rgb.width, 4),
                    round(top / rgb.height, 4),
                    round(right / rgb.width, 4),
                    round(bottom / rgb.height, 4),
                ],
                "bbox_aspect_ratio": round((right - left) / (bottom - top), 4),
                "background_estimate": background_hex,
            }
    else:
        fraction, source, background_hex = foreground_estimate(
            rgb, alpha, background_delta
        )
        if fraction < BACKGROUND_DOMINANT_MAX:
            # The frame is mostly background; full-frame similarity will mostly
            # measure the background.
            flags.append("background_dominant")
        foreground_block = {
            "applied": False,
            "source": source,
            "fraction_of_frame": fraction,
            "background_estimate": background_hex,
        }

    working = to_working(subject)
    if subject_mask is not None:
        working_mask = resize_mask(subject_mask, working.size)
        # Background forced to black so hashes, symmetry and pixel diffs
        # reflect the foreground alone.
        working_pixels = apply_mask(working, working_mask)
    else:
        working_mask = None
        working_pixels = working

    return {
        "path": str(path),
        "size": list(rgb.size),
        "working_size": list(working.size),
        "aspect_ratio": round(rgb.width / rgb.height, 4),
        # Full-resolution statistics: these describe the actual asset (the
        # foreground crop, in foreground mode).
        "luminance": luminance_stats(subject, mask=subject_mask),
        "entropy": entropy_of(subject, mask=subject_mask),
        # Structural statistics: computed on the fixed-size working copy so that
        # a rescaled duplicate produces the same numbers.
        "symmetry": symmetry_scores(working_pixels),
        "grid": {"cols": cols, "rows": rows},
        "cells": fractional_cells(working, cols, rows, mask=working_mask),
        "foreground": foreground_block,
        "flags": flags,
    }, working_pixels


def cell_similarity(cells_a, cells_b):
    """Normalised mean absolute difference across corresponding cells.

    Cells correspond by (row, col) rather than by pixel position, which is what
    makes the comparison scale invariant.

    Foreground mode adds a support rule per cell pair:

    *   Both sides below CELL_MIN_SUPPORT_PIXELS: skipped. Two background-only
        cells scoring "identical" is exactly the failure this mode prevents.
    *   One side supported, the other not: that IS the shape difference --
        object present here in one image, absent in the other -- so the pair
        scores its foreground-occupancy divergence rather than being skipped.
        (An early version skipped these; on two mirrored diagonal blades it
        discarded 9 of 12 cells and reported 0.95.)
    *   Both supported: the four statistics compare as usual, with occupancy
        divergence joining them as a fifth feature.

    Returns (similarity, per_cell, cells_compared, cells_skipped_low_support).
    """
    index_b = {(c["row"], c["col"]): c for c in cells_b}
    diffs = []
    per_cell = []
    skipped = 0

    for cell in cells_a:
        other = index_b.get((cell["row"], cell["col"]))
        if other is None:
            continue
        support_a = cell.get("foreground_pixels")
        support_b = other.get("foreground_pixels")
        masked = support_a is not None and support_b is not None

        if masked:
            ok_a = support_a >= CELL_MIN_SUPPORT_PIXELS
            ok_b = support_b >= CELL_MIN_SUPPORT_PIXELS
            if not ok_a and not ok_b:
                skipped += 1
                continue
            occ_a = cell["foreground_fraction"]
            occ_b = other["foreground_fraction"]
            reference = max(occ_a, occ_b)
            occupancy_div = (abs(occ_a - occ_b) / reference) if reference else 0.0
            if ok_a and ok_b:
                cell_diffs = [
                    min(1.0, abs(cell[f] - other[f]) / scale)
                    for f, scale in FEATURE_SCALES.items()
                ]
                cell_diffs.append(min(1.0, occupancy_div))
            else:
                # Statistics over the under-supported side would be noise, so
                # this pair is scored on occupancy alone -- which, with one
                # side near zero, is rightly close to maximal.
                cell_diffs = [min(1.0, occupancy_div)]
        else:
            cell_diffs = [
                min(1.0, abs(cell[f] - other[f]) / scale)
                for f, scale in FEATURE_SCALES.items()
            ]

        diffs.extend(cell_diffs)
        per_cell.append(
            {
                "row": cell["row"],
                "col": cell["col"],
                "bounds_fractional": cell["bounds_fractional"],
                "divergence": round(float(np.mean(cell_diffs)), 4),
            }
        )

    if not diffs:
        return None, [], 0, skipped

    similarity = 1.0 - float(np.mean(diffs))
    per_cell.sort(key=lambda c: (-c["divergence"], c["row"], c["col"]))
    return round(max(0.0, min(1.0, similarity)), 6), per_cell, len(per_cell), skipped


def changed_region(working_a, working_b):
    """Fractional bounding box of pixels that differ beyond CHANGE_THRESHOLD.

    Returns None when the images are effectively identical. The box is expressed
    as fractions of the frame so it stays meaningful across resolutions.
    """
    if working_a.size != working_b.size:
        working_b = working_b.resize(working_a.size, Image.LANCZOS)

    delta = ImageChops.difference(working_a, working_b).convert("L")
    mask = delta.point(lambda v: 255 if v > CHANGE_THRESHOLD else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return None

    width, height = working_a.size
    left, top, right, bottom = bbox
    return [
        round(left / width, 4),
        round(top / height, 4),
        round(right / width, 4),
        round(bottom / height, 4),
    ]


def changed_area_fraction(working_a, working_b):
    if working_a.size != working_b.size:
        working_b = working_b.resize(working_a.size, Image.LANCZOS)
    delta = np.abs(luminance_array(working_a) - luminance_array(working_b))
    return round(float((delta > CHANGE_THRESHOLD).mean()), 6)


def build_diff(a, b, working_a, working_b):
    similarity, per_cell, compared, skipped = cell_similarity(a["cells"], b["cells"])

    flags = []
    if abs(a["aspect_ratio"] - b["aspect_ratio"]) > 0.01:
        # Never silently squash: mismatched aspect makes grid cells
        # non-corresponding and quietly invalidates every per-cell number.
        flags.append("aspect_ratio_mismatch")
    if a["size"] != b["size"]:
        flags.append("resolution_mismatch")
    if "background_dominant" in a["flags"] and "background_dominant" in b["flags"]:
        # Both frames are mostly background, so a high similarity here mostly
        # certifies that the backgrounds match. Re-run with --foreground before
        # concluding anything about the subjects.
        flags.append("background_dominant")

    fg_a, fg_b = a["foreground"], b["foreground"]
    if fg_a["applied"] != fg_b["applied"]:
        # One side's mask came up empty and fell back to full-frame: the two
        # analyses no longer measure comparable things.
        flags.append("foreground_mask_mismatch")
    if fg_a["applied"] and fg_b["applied"]:
        ratio_a = fg_a["bbox_aspect_ratio"]
        ratio_b = fg_b["bbox_aspect_ratio"]
        if abs(ratio_a - ratio_b) / max(ratio_a, ratio_b) > 0.10:
            # The two foregrounds have very different proportions, so the
            # bbox-registered grids do not correspond cell-to-cell.
            flags.append("foreground_aspect_mismatch")
        if similarity is None:
            flags.append("foreground_support_insufficient")

    return {
        "structural_similarity": similarity,
        "dhash_distance": hamming(dhash(working_a), dhash(working_b)),
        "ahash_distance": hamming(ahash(working_a), ahash(working_b)),
        "changed_region_bbox_fractional": changed_region(working_a, working_b),
        "changed_area_fraction": changed_area_fraction(working_a, working_b),
        "most_divergent_cells": per_cell[:5],
        "cells_compared": compared,
        "cells_skipped_low_support": skipped,
        "luminance_mean_delta": round(
            b["luminance"]["mean"] - a["luminance"]["mean"], 3
        ),
        "entropy_delta": round(b["entropy"] - a["entropy"], 4),
        "flags": flags,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Measure and compare image structure and layout."
    )
    parser.add_argument("image_a", help="reference image path")
    parser.add_argument("image_b", nargs="?", help="optional comparison image path")
    parser.add_argument(
        "--grid", default="4x3", help="analysis grid as COLSxROWS (default 4x3)"
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="mask the background out (alpha when present, else border-median "
        "colour), crop to the foreground bounding box, and score only cells "
        "with real foreground support -- required for meaningful numbers on "
        "object renders over a shared preview background",
    )
    parser.add_argument(
        "--background-delta",
        type=float,
        default=DEFAULT_BACKGROUND_DELTA,
        help="OKLab distance from the border-median colour within which an "
        f"opaque pixel counts as background (default {DEFAULT_BACKGROUND_DELTA})",
    )
    args = parser.parse_args(argv)

    cols, rows = parse_grid(args.grid)

    analysis_a, working_a = analyse(
        args.image_a, cols, rows, args.foreground, args.background_delta
    )
    images = {"a": analysis_a}
    diff = None

    if args.image_b:
        analysis_b, working_b = analyse(
            args.image_b, cols, rows, args.foreground, args.background_delta
        )
        images["b"] = analysis_b
        diff = build_diff(analysis_a, analysis_b, working_a, working_b)

    payload = {
        "tool": "pil_structure_diff",
        "version": TOOL_VERSION,
        "parameters": {
            "grid": {"cols": cols, "rows": rows},
            "working_long_edge": WORKING_LONG_EDGE,
            "change_threshold": CHANGE_THRESHOLD,
            "foreground": args.foreground,
            "background_delta": args.background_delta,
            "alpha_foreground_min": ALPHA_FOREGROUND_MIN,
            "cell_min_support_pixels": CELL_MIN_SUPPORT_PIXELS,
        },
        "images": images,
        "diff": diff,
        "interpretation_limits": INTERPRETATION_LIMITS,
    }

    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
