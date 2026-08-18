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
    CHANGE_THRESHOLD,
    WORKING_LONG_EDGE,
    ahash,
    dhash,
    entropy_of,
    fractional_cells,
    hamming,
    load_rgb,
    luminance_array,
    luminance_stats,
    parse_grid,
    symmetry_scores,
    to_working,
)

TOOL_VERSION = "0.1.0"

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
]


def analyse(path, cols, rows):
    img = load_rgb(path)
    working = to_working(img)
    return {
        "path": str(path),
        "size": list(img.size),
        "working_size": list(working.size),
        "aspect_ratio": round(img.width / img.height, 4),
        # Full-resolution statistics: these describe the actual asset.
        "luminance": luminance_stats(img),
        "entropy": entropy_of(img),
        # Structural statistics: computed on the fixed-size working copy so that
        # a rescaled duplicate produces the same numbers.
        "symmetry": symmetry_scores(working),
        "grid": {"cols": cols, "rows": rows},
        "cells": fractional_cells(working, cols, rows),
    }, working


def cell_similarity(cells_a, cells_b):
    """Normalised mean absolute difference across corresponding cells.

    Cells correspond by (row, col) rather than by pixel position, which is what
    makes the comparison scale invariant.
    """
    index_b = {(c["row"], c["col"]): c for c in cells_b}
    diffs = []
    per_cell = []

    for cell in cells_a:
        other = index_b.get((cell["row"], cell["col"]))
        if other is None:
            continue
        cell_diffs = []
        for feature, scale in FEATURE_SCALES.items():
            delta = abs(cell[feature] - other[feature]) / scale
            cell_diffs.append(min(1.0, delta))
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
        return None, []

    similarity = 1.0 - float(np.mean(diffs))
    per_cell.sort(key=lambda c: (-c["divergence"], c["row"], c["col"]))
    return round(max(0.0, min(1.0, similarity)), 6), per_cell


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
    similarity, per_cell = cell_similarity(a["cells"], b["cells"])

    flags = []
    if abs(a["aspect_ratio"] - b["aspect_ratio"]) > 0.01:
        # Never silently squash: mismatched aspect makes grid cells
        # non-corresponding and quietly invalidates every per-cell number.
        flags.append("aspect_ratio_mismatch")
    if a["size"] != b["size"]:
        flags.append("resolution_mismatch")

    return {
        "structural_similarity": similarity,
        "dhash_distance": hamming(dhash(working_a), dhash(working_b)),
        "ahash_distance": hamming(ahash(working_a), ahash(working_b)),
        "changed_region_bbox_fractional": changed_region(working_a, working_b),
        "changed_area_fraction": changed_area_fraction(working_a, working_b),
        "most_divergent_cells": per_cell[:5],
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
    args = parser.parse_args(argv)

    cols, rows = parse_grid(args.grid)

    analysis_a, working_a = analyse(args.image_a, cols, rows)
    images = {"a": analysis_a}
    diff = None

    if args.image_b:
        analysis_b, working_b = analyse(args.image_b, cols, rows)
        images["b"] = analysis_b
        diff = build_diff(analysis_a, analysis_b, working_a, working_b)

    payload = {
        "tool": "pil_structure_diff",
        "version": TOOL_VERSION,
        "parameters": {
            "grid": {"cols": cols, "rows": rows},
            "working_long_edge": WORKING_LONG_EDGE,
            "change_threshold": CHANGE_THRESHOLD,
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
