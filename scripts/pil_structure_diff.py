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
    estimate_background,
    foreground_estimate,
    foreground_mask,
    fractional_cells,
    hamming,
    load_rgba_straight,
    luminance_array,
    luminance_stats,
    mask_bbox,
    parse_grid,
    resize_mask,
    symmetry_scores,
    to_working,
    working_straight_and_weights,
)
from pil_region import (  # noqa: E402
    DEFAULT_REGION_SPACE,
    REGION_SPACES,
    RegionError,
    parse_fractional_bbox,
    rect_to_fractional,
    resolve_pixel_rect,
)

TOOL_VERSION = "0.4.0"

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

# Index of the "Foreground statistics of a THIN object..." entry above, so the
# alpha-path payload can swap it for the corrected wording rather than delete
# it -- it is still true on the border-median path (most production input).
_THIN_OBJECT_CAVEAT_INDEX = 6

_ALPHA_THIN_OBJECT_CAVEAT = (
    "Foreground statistics of a THIN object degrade across resolutions ON THE "
    "BORDER-MEDIAN PATH (opaque input, no real alpha channel): most of its "
    "pixels are edge-blended with the background, so a rescaled copy reads "
    "moderately different even though dhash still reports the same layout. "
    "On the alpha path (real transparency, coverage_weighted is true) this is "
    "NO LONGER TRUE: every working-resolution pixel's colour is "
    "un-premultiplied before it is read and "
    "weighted by its own coverage, so its influence on the mean is exactly its "
    "coverage share rather than a full vote diluted by a fringe blend. The "
    "sub-pixel placement stability this buys is measured, not assumed -- see "
    "the W1 evidence bundle's sub-pixel excursion table. A small residual "
    "remains from resampling arithmetic (compositing rounds colour*coverage to "
    "a byte before the working copy is built), bounded at roughly 0.5/mean-"
    "coverage code values per cell -- see UNPREMULTIPLY_COVERAGE_MIN's comment "
    "in pil_common.py."
)

# Appended to INTERPRETATION_LIMITS only when at least one analysed image
# actually took the alpha-weighted code path (coverage_weighted True on some
# image). interpretation_limits is a static top-level payload field included
# in every invocation; full-frame runs and --foreground runs on opaque input
# must stay byte-identical to the pre-0.4.0 tree (docs/aaa-build-plan.md
# A1.1), so these entries -- and the caveat rewrite above -- cannot be
# unconditional.
ALPHA_INTERPRETATION_LIMITS = [
    "coverage_weighted (in the foreground block) is true when this image's "
    "luminance/entropy per cell and at full resolution are alpha/255 "
    "coverage-weighted over an un-premultiplied (straight) colour read, rather "
    "than an unweighted mean over the composited-onto-black colour. "
    "edge_mean is NEVER coverage-weighted -- gradients are read from the "
    "still-composited image on purpose, because a silhouette edge is real "
    "object structure and damping it by coverage would make a thin object "
    "read as progressively less edgy the thinner it gets. dhash, ahash, "
    "symmetry and changed_region_bbox_fractional are computed on "
    "apply_mask(working, working_mask), the composited-on-black working copy, "
    "unconditionally and unchanged -- compositing onto black IS "
    "premultiplication by coverage, which is exactly right for a pixel-domain "
    "appearance metric.",
    "foreground_coverage_fraction (per cell) is Sum(alpha/255) over the cell "
    "divided by the cell's pixel count; it replaces foreground_fraction as the "
    "occupancy feature in structural_similarity when present, because total "
    "coverage is conserved under a sub-pixel translation while pixel COUNT is "
    "not -- foreground_fraction stays available and unchanged in meaning.",
    "foreground_source_mismatch fires when the two images' foreground.source "
    "differ (one alpha, one border-median) -- distinct from "
    "foreground_mask_mismatch, which fires when one side's mask fell back to "
    "full-frame. A source mismatch means one side's colours are coverage-"
    "weighted true object colour and the other side's are blended toward its "
    "own backdrop; the two are not commensurable, and a composited render's "
    "coverage information was destroyed at composite time -- there is no "
    "un-blending it.",
]

# Appended to INTERPRETATION_LIMITS unconditionally, unlike
# ALPHA_INTERPRETATION_LIMITS above: --region carries no pre-existing byte-
# identity obligation (it is new in this release), and A6.2/A6.3 require a
# --region run and an equivalent pre-cropped-file run to compare equal on
# this field, which only an unconditional entry can satisfy -- the same
# reasoning that makes source_size and parameters.region unconditional.
REGION_INTERPRETATION_LIMITS = [
    "--region crops BOTH images to the same fractional box at full "
    "resolution before any measurement runs, including the working-"
    "resolution resample -- never after, which is what keeps a --region "
    "measurement resolution-independent and identical to pre-cropping the "
    "file with pil_crop.py and measuring the result -- with exactly one "
    "exception: the flags list. region_background_estimate_diverged (below) "
    "can appear on a --region run and never on the equivalent pre-cropped-"
    "file run, because it compares the crop's own border-median colour "
    "against the ORIGINAL, un-cropped frame's -- a comparison a pre-cropped "
    "file structurally cannot make, having no frame left to compare "
    "against. Every other field, including every structure and similarity "
    "statistic, is unaffected. Composed with --foreground, the order is: "
    "region crop first, then the foreground mask -- and, on the border-"
    "median path, the background estimate -- is re-derived from the crop's "
    "own pixels, never inherited from the whole frame. "
    "region_background_estimate_diverged fires when that re-derived "
    "estimate lands further than --background-delta (in OKLab) from the "
    "whole frame's own border-median colour -- the signature of a region "
    "that contains little or no backdrop, where the re-derived estimate is "
    "the more honest one but also the more surprising one.",
]


def _apply_region(composited_rgb, straight_rgb, alpha, region, region_space, background_delta):
    """Crop all three full-resolution arrays to --region before anything else runs.

    Cropping here -- before the foreground mask is derived and before the
    working-resolution resample -- is what keeps a --region measurement
    resolution-independent and makes it identical to pre-cropping the file
    with pil_crop.py and measuring the result (A6.2): every statistic
    downstream simply treats whatever this function returns as "the image",
    so this is the only place region semantics live in this tool, and the
    equality with pil_crop is a consequence of sharing pil_region's rect
    arithmetic rather than something asserted separately.

    Returns (composited_rgb, straight_rgb, alpha, region_block, source_size).
    region_block is None and source_size equals the untouched image's own
    size when region is None, so a caller that never asks for a region pays
    nothing beyond the size lookup.
    """
    width, height = composited_rgb.size
    source_size = [width, height]
    if region is None:
        return composited_rgb, straight_rgb, alpha, None, source_size

    reference_rect = None
    if region_space == "foreground":
        full_mask, _source, _bg_hex = foreground_mask(composited_rgb, alpha, background_delta)
        bbox = mask_bbox(full_mask)
        if bbox is None:
            raise RegionError(
                "region-space foreground: the foreground mask is empty, so "
                "there is no foreground bounding box to resolve --region against"
            )
        ref_left, ref_top, ref_right, ref_bottom = bbox
        size = (ref_right - ref_left, ref_bottom - ref_top)
        origin = (ref_left, ref_top)
        reference_rect = list(bbox)
    else:
        size = (width, height)
        origin = (0, 0)

    rect = resolve_pixel_rect(region, size, origin=origin)
    resolved_fractional = rect_to_fractional(rect, (width, height))
    left, top, right, bottom = rect

    composited_rgb = composited_rgb.crop(rect)
    if alpha is not None:
        straight_rgb = straight_rgb.crop(rect)
        alpha = alpha[top:bottom, left:right]
        if not bool((alpha < 255).any()):
            # Mirror load_rgba_straight's own rule (pil_common.py): an
            # all-opaque alpha carries no foreground information and
            # normalises to None. Without this, a region cropped out of an
            # all-opaque part of the alpha array stays non-None while the
            # equivalent pre-cropped FILE would load with alpha=None,
            # putting the two on different mask paths and breaking the
            # region-equals-pre-crop identity this function exists to keep.
            alpha = None
            straight_rgb = composited_rgb
    else:
        straight_rgb = composited_rgb

    region_block = {
        "requested_fractional": region,
        "resolved_pixel_rect": list(rect),
        "resolved_fractional": resolved_fractional,
        "space": region_space,
        "reference_rect": reference_rect,
    }
    return composited_rgb, straight_rgb, alpha, region_block, source_size


def analyse(
    path,
    cols,
    rows,
    foreground=False,
    background_delta=DEFAULT_BACKGROUND_DELTA,
    region=None,
    region_space=DEFAULT_REGION_SPACE,
):
    composited_rgb, straight_rgb, alpha = load_rgba_straight(path)
    frame_rgb = composited_rgb
    composited_rgb, straight_rgb, alpha, region_block, source_size = _apply_region(
        composited_rgb, straight_rgb, alpha, region, region_space, background_delta
    )
    flags = []
    subject = composited_rgb
    subject_straight = composited_rgb
    subject_mask = None
    subject_alpha = None

    if foreground:
        full_mask, source, background_hex = foreground_mask(
            composited_rgb, alpha, background_delta
        )
        if region_block is not None and alpha is None:
            # --region composed with --foreground re-derives the background
            # estimate from the crop's own borders rather than inheriting
            # the frame's (docs/aaa-build-plan.md #8.1) -- flag when that
            # choice actually matters. Gated to the border-median path
            # (alpha is None): the alpha path never uses a background
            # estimate to derive its mask, so there is no inherited-vs-
            # re-derived ambiguity for it to surface.
            frame_lab, _frame_hex = estimate_background(frame_rgb)
            crop_lab, _crop_hex = estimate_background(composited_rgb)
            divergence = float(np.sqrt(np.sum((crop_lab - frame_lab) ** 2)))
            if divergence > background_delta:
                flags.append("region_background_estimate_diverged")
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
            subject = composited_rgb.crop(bbox)
            subject_mask = full_mask[top:bottom, left:right]
            foreground_block = {
                "applied": True,
                "source": source,
                "fraction_of_frame": round(fraction, 6),
                "bbox_fractional": [
                    round(left / composited_rgb.width, 4),
                    round(top / composited_rgb.height, 4),
                    round(right / composited_rgb.width, 4),
                    round(bottom / composited_rgb.height, 4),
                ],
                "bbox_aspect_ratio": round((right - left) / (bottom - top), 4),
                "background_estimate": background_hex,
            }
            if alpha is not None:
                # coverage_weighted etc. added ONLY here -- foreground
                # requested, mask non-empty, real alpha present -- so
                # full-frame output and opaque (border-median) --foreground
                # output never gain these keys and stay byte-identical to the
                # pre-0.4.0 tree (docs/aaa-build-plan.md A1.1).
                full_weights_frame = np.asarray(alpha, dtype=np.float64) / 255.0
                partial = full_mask & (alpha < 255)
                foreground_block["coverage_weighted"] = True
                foreground_block["coverage_fraction_of_frame"] = round(
                    float(full_weights_frame[full_mask].sum()) / float(full_mask.size),
                    6,
                )
                foreground_block["partial_coverage_share"] = round(
                    float(partial.sum()) / float(full_mask.sum()), 6
                )
                subject_straight = straight_rgb.crop(bbox)
                subject_alpha = alpha[top:bottom, left:right]
    else:
        fraction, source, background_hex = foreground_estimate(
            composited_rgb, alpha, background_delta
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

    # working is the composited working-resolution copy, computed exactly as
    # before -- dhash/ahash/symmetry/changed_region_bbox_fractional all read
    # from it (via working_pixels) unconditionally, because compositing onto
    # black IS premultiplication by coverage, which is the right thing for a
    # pixel-domain appearance metric (see the alpha interpretation_limits
    # entry above).
    working = to_working(subject)
    if subject_mask is not None:
        working_mask = resize_mask(subject_mask, working.size)
        # Background forced to black so hashes, symmetry and pixel diffs
        # reflect the foreground alone.
        working_pixels = apply_mask(working, working_mask)
    else:
        working_mask = None
        working_pixels = working

    if subject_alpha is not None:
        # The alpha path only: recover the working-resolution straight colour
        # and its continuous coverage weight by inverting the same LANCZOS
        # kernel that produced `working` (see working_straight_and_weights).
        working_straight, working_weights = working_straight_and_weights(
            subject, subject_alpha, working.size
        )
        full_weights = np.asarray(subject_alpha, dtype=np.float64) / 255.0
        colour_subject = subject_straight
    else:
        working_straight = None
        working_weights = None
        full_weights = None
        colour_subject = subject

    return {
        "path": str(path),
        "size": list(composited_rgb.size),
        # source_size is the file's own size, unconditionally: it equals
        # size when no --region was given, and lets a caller with only the
        # payload in hand tell the crop's size from the file's (A6, #8.2).
        "source_size": source_size,
        "region": region_block,
        "working_size": list(working.size),
        "aspect_ratio": round(composited_rgb.width / composited_rgb.height, 4),
        # Full-resolution statistics: these describe the actual asset (the
        # foreground crop, in foreground mode). Coverage-weighted over the
        # straight (un-premultiplied) colour when the alpha path is active;
        # otherwise byte-identical to the pre-0.4.0 reading.
        "luminance": luminance_stats(colour_subject, mask=subject_mask, weights=full_weights),
        "entropy": entropy_of(colour_subject, mask=subject_mask, weights=full_weights),
        # Structural statistics: computed on the fixed-size working copy so that
        # a rescaled duplicate produces the same numbers.
        "symmetry": symmetry_scores(working_pixels),
        "grid": {"cols": cols, "rows": rows},
        "cells": fractional_cells(
            working,
            cols,
            rows,
            mask=working_mask,
            weights=working_weights,
            colour_img=working_straight,
        ),
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
            # Coverage form when both sides carry it (the alpha path): total
            # coverage is conserved under a sub-pixel translation while pixel
            # COUNT is not, so the coverage form is what makes cell occupancy
            # placement-stable. Falls back to the count form otherwise -- on
            # the border-median path the two are identical by construction, so
            # this never changes that path's output.
            if "foreground_coverage_fraction" in cell and "foreground_coverage_fraction" in other:
                occ_a = cell["foreground_coverage_fraction"]
                occ_b = other["foreground_coverage_fraction"]
            else:
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


def build_diff(a, b, working_a, working_b, foreground=False):
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
    if foreground and fg_a["source"] != fg_b["source"]:
        # Distinct from foreground_mask_mismatch above: one side's colours are
        # coverage-weighted true object colour (source "alpha"), the other's
        # are blended toward its own backdrop (source "background_estimate") --
        # not commensurable, and the composited side's coverage information
        # was destroyed at composite time. Gated to --foreground mode (per
        # docs/aaa-build-plan.md #3.8): in full-frame mode this same source
        # disagreement is normal and already unflagged pre-0.4.0, so flagging
        # it there would change the byte-identical full-frame output A1.1
        # holds every fixture to.
        flags.append("foreground_source_mismatch")
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
    parser.add_argument(
        "--region",
        default=None,
        help="fractional bbox 'L,T,R,B' or JSON '[L,T,R,B]' (same parser and "
        "half-up rounding rule as pil_crop.py); crops BOTH images to this "
        "box at full resolution before any measurement runs, including the "
        "working-resolution resample, so the reported numbers describe only "
        "the region",
    )
    parser.add_argument(
        "--region-space",
        choices=REGION_SPACES,
        default=DEFAULT_REGION_SPACE,
        help="resolve --region against the whole frame or the foreground's "
        f"own bounding box (default {DEFAULT_REGION_SPACE}). Composed with "
        "--foreground: --region crops first, and the foreground mask -- and, "
        "on the border-median path, the background estimate -- is then "
        "re-derived from the crop's own pixels rather than inherited from "
        "the whole frame; see region_background_estimate_diverged",
    )
    args = parser.parse_args(argv)

    cols, rows = parse_grid(args.grid)

    try:
        region_box = parse_fractional_bbox(args.region) if args.region is not None else None
    except RegionError as exc:
        print(f"pil_structure_diff: {exc}", file=sys.stderr)
        return 2

    try:
        analysis_a, working_a = analyse(
            args.image_a,
            cols,
            rows,
            args.foreground,
            args.background_delta,
            region_box,
            args.region_space,
        )
        images = {"a": analysis_a}
        diff = None

        if args.image_b:
            analysis_b, working_b = analyse(
                args.image_b,
                cols,
                rows,
                args.foreground,
                args.background_delta,
                region_box,
                args.region_space,
            )
            images["b"] = analysis_b
            diff = build_diff(
                analysis_a, analysis_b, working_a, working_b, foreground=args.foreground
            )
    except RegionError as exc:
        print(f"pil_structure_diff: {exc}", file=sys.stderr)
        return 2

    # interpretation_limits is a static payload field on every other run, but
    # the alpha-related entries (and the thin-object caveat rewrite) only
    # apply -- and must only appear -- when the alpha-weighted path was
    # actually exercised for at least one image, so full-frame and
    # opaque-input --foreground output stay byte-identical to the pre-0.4.0
    # tree (docs/aaa-build-plan.md A1.1). REGION_INTERPRETATION_LIMITS is NOT
    # gated the same way: unlike the alpha entries, it carries no pre-existing
    # byte-identity obligation to protect (--region is new in this release),
    # and A6.2/A6.3 (docs/aaa-build-plan.md #8.3) require a --region run and
    # an equivalent pre-cropped-file run to compare equal on this field too --
    # only possible if it is unconditional, the same way source_size and
    # parameters.region are unconditional per #8.2.
    alpha_path_used = any(
        img["foreground"].get("coverage_weighted") for img in images.values()
    )
    interpretation_limits = list(INTERPRETATION_LIMITS)
    if alpha_path_used:
        interpretation_limits[_THIN_OBJECT_CAVEAT_INDEX] = _ALPHA_THIN_OBJECT_CAVEAT
        interpretation_limits += ALPHA_INTERPRETATION_LIMITS
    interpretation_limits += REGION_INTERPRETATION_LIMITS

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
            "region": region_box,
            "region_space": args.region_space,
        },
        "images": images,
        "diff": diff,
        "interpretation_limits": interpretation_limits,
    }

    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
