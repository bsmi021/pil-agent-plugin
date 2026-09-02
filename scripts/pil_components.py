#!/usr/bin/env python
"""Connected-component instance counting over a native-resolution foreground mask.

Emits JSON on stdout. For each blob in the foreground mask the tool reports
pixel area, frame-fractional area, frame-fractional centroid, and both the
pixel-space and frame-fractional tight bounding box. Blobs whose frame-fraction
area falls below a calibrated noise floor are excluded from the count so
anti-aliasing residue and single stray pixels cannot inflate it.

Usage:
    python pil_components.py render.png
    python pil_components.py render.png --min-blob-area-fraction 0.0005
    python pil_components.py render.png --connectivity 4

Why native resolution: downsampling risks merging (or splitting) blobs that
are genuinely separate at the file's own resolution, which would corrupt the
one thing this metric answers. The consequence, stated in interpretation
limits: component count and pixel area are NOT scale-invariant; centroid and
bbox are additionally reported as frame-fractional coordinates so that only
position (not size) is comparable across renders of different resolutions.

The labeller is a pure-numpy two-pass union-find with 8-connectivity by
default: a diagonal-touching pixel pair reads as ONE component, which is how
a human count sees them. No scipy import is used or permitted -- Pillow and
numpy are the only runtime dependencies.

Exit codes: 0 on success. 2 on any rejection -- an unreadable source, an
unsupported bit depth, an invalid argument -- with nothing at all written to
stdout so a caller can never mistake a rejected run for a partial answer.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_common import (  # noqa: E402
    DEFAULT_BACKGROUND_DELTA,
    FOREGROUND_MIN_FRACTION,
    foreground_mask,
    load_rgb_alpha,
    mask_bbox,
)

TOOL_VERSION = "0.7.0"

# Calibrated noise floor: the smallest blob-area frame-fraction that the
# no-op control set of runs/2026-08-20-components-discrimination/ could not
# spuriously produce at alpha=0.05 (n=60, bootstrap CI upper bound). Blobs
# below this fraction are excluded from the count, so anti-aliasing fringes
# and single stray pixels near an object's edge cannot inflate it. See that
# bundle's README for the derivation and its CI. This constant is scale
# invariant by construction (a fraction rather than a pixel count); the
# equivalent pixel floor at a given resolution is reported as
# min_blob_area_pixels_applied in the output.
MIN_BLOB_AREA_FRACTION = 0.0001

# Connectivity choices. 8 (default) treats diagonal-touching pixels as one
# blob, matching how a human visual count reads them; 4 treats only
# axis-aligned neighbours as connected. Exposed because a caller who is
# counting deliberately axis-aligned tiles may want the stricter rule; the
# default is what "how many separate objects are there" almost always means.
CONNECTIVITIES = (4, 8)
DEFAULT_CONNECTIVITY = 8

INTERPRETATION_LIMITS = [
    "component count and area_pixels are NOT scale-invariant and are only "
    "comparable between renders of the same pixel dimensions. Centroid and "
    "bbox are additionally reported as frame-fractional coordinates for "
    "cross-resolution comparison of position, not of size.",
    "connectivity=8 (default) treats a diagonal-touching pixel pair as ONE "
    "component; connectivity=4 treats only axis-aligned neighbours as "
    "connected. The reported count depends on which is chosen and the "
    "parameter is echoed into the payload so a reader knows which rule "
    "produced the number.",
    "min_blob_area_fraction is a calibrated noise floor (see "
    "runs/2026-08-20-components-discrimination/): blobs at or below the "
    "equivalent pixel count for this image are excluded from `components` "
    "and from `component_count`, but still contribute to the total mask "
    "area reported under `foreground`. A caller sweeping the floor lower "
    "should expect the count to become dominated by anti-aliasing residue.",
    "the foreground mask follows pil_common's rule: alpha when the file "
    "carries real transparency (exact), the border-median OKLab estimate "
    "otherwise (inherits that estimate's error). An empty mask reports zero "
    "components with `foreground_mask_empty` set, never a fabricated count "
    "and never a crash. A very small mask still returns its counts but with "
    "`foreground_too_small` set, because on such a mask the components are "
    "themselves sub-1%-of-frame and their identities are noisy.",
    "the labeller is a pure-numpy two-pass union-find. No scipy import is "
    "used or permitted; a repo-wide check enforces this.",
]


class ComponentsRejected(Exception):
    """Mark a failure that must exit 2 with empty stdout and nothing written.

    Every rejection this tool can produce -- an unreadable source, an
    unsupported bit depth, an out-of-range argument -- is raised as this one
    type so main() has a single place to enforce "nothing was printed" rather
    than re-deriving that guarantee at each call site.
    """


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _load_source(path):
    """Decode the source at full resolution; return (rgb, alpha, has_alpha, mode).

    Reuses pil_common.load_rgb_alpha's flatten-onto-black composite for RGB
    (so the mask path is byte-identical to what every other foreground tool
    sees) but reads the source's own mode and rejects the high-bit-depth
    modes for which no display-transfer policy is defined here -- the same
    boundary pil_crop.py holds.
    """
    try:
        with Image.open(path) as probe:
            probe.load()
            source_mode = probe.mode
    except OSError as exc:
        raise ComponentsRejected(f"cannot read image {path!r}: {exc}") from exc
    if source_mode == "I" or source_mode == "F" or source_mode.startswith("I;"):
        raise ComponentsRejected(
            f"unsupported high-bit-depth image mode {source_mode!r}; "
            "pil_components accepts 8-bit pixels only and will not itself "
            "clip or rescale them."
        )
    rgb, alpha = load_rgb_alpha(path)
    has_alpha = alpha is not None
    return rgb, alpha, has_alpha, source_mode


def label_components(mask, connectivity=DEFAULT_CONNECTIVITY):
    """Two-pass union-find labeller. Returns int32 HxW label array (0 = background).

    Pass 1 walks foreground pixels in row-major order and assigns provisional
    labels by looking at already-labelled neighbours (up-left, up, up-right,
    left for 8-connectivity; up and left for 4-connectivity). Equivalences
    discovered when a pixel touches two previously-distinct labels are
    recorded in a union-find with path compression and union-by-smaller-root
    -- the smaller-root rule is what makes the final labelling a function of
    the pixel geometry alone, so two runs on the same input produce
    byte-identical labels rather than merely a byte-identical count.

    Pass 2 flattens every provisional label to its union-find root and
    renumbers the roots consecutively in ascending numeric order, so the
    label ids are contiguous 1..K.

    Pure-numpy and pure-python (the union-find bookkeeping is a small list of
    ints, not a numpy array, because path compression under numpy indexing is
    slower than a python loop when the number of provisional labels is
    modest). No scipy import.
    """
    if connectivity not in CONNECTIVITIES:
        raise ComponentsRejected(
            f"connectivity must be one of {CONNECTIVITIES}, got {connectivity!r}"
        )

    m = np.ascontiguousarray(mask, dtype=bool)
    h, w = m.shape
    labels = np.zeros((h, w), dtype=np.int32)
    if not m.any():
        return labels

    parent = [0]

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    next_label = 1
    ys, xs = np.nonzero(m)
    ys_list = ys.tolist()
    xs_list = xs.tolist()
    diagonal = connectivity == 8

    for y, x in zip(ys_list, xs_list):
        candidates = []
        if y > 0:
            up = labels[y - 1, x]
            if up:
                candidates.append(int(up))
            if diagonal:
                if x > 0:
                    ul = labels[y - 1, x - 1]
                    if ul:
                        candidates.append(int(ul))
                if x + 1 < w:
                    ur = labels[y - 1, x + 1]
                    if ur:
                        candidates.append(int(ur))
        if x > 0:
            left = labels[y, x - 1]
            if left:
                candidates.append(int(left))

        if not candidates:
            labels[y, x] = next_label
            parent.append(next_label)
            next_label += 1
            continue

        root = min(candidates)
        labels[y, x] = root
        for other in candidates:
            if other != root:
                union(root, other)

    # Pass 2: flatten to roots and renumber consecutively.
    roots = np.zeros(next_label, dtype=np.int32)
    for i in range(1, next_label):
        roots[i] = find(i)
    flat = roots[labels]
    unique = np.unique(flat)
    unique = unique[unique != 0]
    if unique.size == 0:
        return np.zeros((h, w), dtype=np.int32)
    remap = np.zeros(int(unique.max()) + 1, dtype=np.int32)
    for new_id, old_root in enumerate(unique.tolist(), start=1):
        remap[int(old_root)] = new_id
    return remap[flat].astype(np.int32)


def _per_component_stats(labels, size):
    """(area_pixels, sum_y, sum_x, bbox) per label id, vectorised where possible.

    Returns a list of dicts, one per id 1..K in provisional order (before any
    application-level sort). Uses np.bincount for area and centroid moments;
    the bbox loop is per-label because bbox is order-sensitive rather than
    additive, but the labels are dense int32 so the loop is bounded by the
    component count, not by the pixel count.
    """
    width, height = size
    total_labels = int(labels.max())
    if total_labels == 0:
        return []

    flat = labels.ravel()
    areas = np.bincount(flat, minlength=total_labels + 1).astype(np.int64)

    yy, xx = np.mgrid[0:height, 0:width]
    sy = np.bincount(flat, weights=yy.ravel().astype(np.float64), minlength=total_labels + 1)
    sx = np.bincount(flat, weights=xx.ravel().astype(np.float64), minlength=total_labels + 1)

    out = []
    for label_id in range(1, total_labels + 1):
        area = int(areas[label_id])
        if area <= 0:
            continue
        rows, cols = np.nonzero(labels == label_id)
        left = int(cols.min())
        top = int(rows.min())
        right = int(cols.max()) + 1
        bottom = int(rows.max()) + 1
        cy = float(sy[label_id]) / area
        cx = float(sx[label_id]) / area
        out.append(
            {
                "area_pixels": area,
                "centroid_pixel_y": cy,
                "centroid_pixel_x": cx,
                "bbox_pixel": [left, top, right, bottom],
            }
        )
    return out


def _make_component_entries(raw, size, min_area_pixels):
    """Filter, sort, and shape per-component records for the JSON payload.

    Sort order is descending area (largest first), tiebroken by bbox top then
    bbox left, so the ordering is deterministic and matches the convention
    quantize_palette uses for its own entries.
    """
    width, height = size
    frame_pixels = float(width * height)
    kept = [rec for rec in raw if rec["area_pixels"] >= min_area_pixels]
    kept.sort(
        key=lambda rec: (
            -rec["area_pixels"],
            rec["bbox_pixel"][1],
            rec["bbox_pixel"][0],
        )
    )
    entries = []
    for new_id, rec in enumerate(kept, start=1):
        left, top, right, bottom = rec["bbox_pixel"]
        entries.append(
            {
                "id": new_id,
                "area_pixels": rec["area_pixels"],
                "area_fraction_of_frame": round(rec["area_pixels"] / frame_pixels, 8),
                "centroid_fractional": [
                    round(rec["centroid_pixel_x"] / width, 8),
                    round(rec["centroid_pixel_y"] / height, 8),
                ],
                "bbox_pixel": [left, top, right, bottom],
                "bbox_fractional": [
                    round(left / width, 8),
                    round(top / height, 8),
                    round(right / width, 8),
                    round(bottom / height, 8),
                ],
            }
        )
    return entries, kept


def measure(args):
    """Assemble the payload for one image.

    Every input-validation check runs before any expensive work, so a
    rejected request never spends time labelling. Returns the payload dict;
    main() is the only place that writes it.
    """
    if not (0.0 <= args.min_blob_area_fraction <= 1.0):
        raise ComponentsRejected(
            "--min-blob-area-fraction must be in [0, 1], got "
            f"{args.min_blob_area_fraction!r}"
        )
    if args.background_delta < 0.0:
        raise ComponentsRejected(
            "--background-delta must be non-negative, got "
            f"{args.background_delta!r}"
        )
    if args.connectivity not in CONNECTIVITIES:
        raise ComponentsRejected(
            f"--connectivity must be one of {CONNECTIVITIES}, got "
            f"{args.connectivity!r}"
        )

    source_path = Path(args.image)
    try:
        source_bytes = source_path.read_bytes()
    except OSError as exc:
        raise ComponentsRejected(f"cannot read image {args.image!r}: {exc}") from exc

    rgb, alpha, has_alpha, source_mode = _load_source(source_path)
    width, height = rgb.size
    frame_pixels = width * height

    mask, mask_source, background_hex = foreground_mask(
        rgb, alpha, args.background_delta
    )
    mask_pixels = int(mask.sum())
    fraction = mask_pixels / frame_pixels if frame_pixels else 0.0
    bbox_pixels = mask_bbox(mask)

    flags = []
    if mask_pixels == 0:
        flags.append("foreground_mask_empty")
    elif fraction < FOREGROUND_MIN_FRACTION:
        flags.append("foreground_too_small")

    min_area_pixels = int(np.ceil(args.min_blob_area_fraction * frame_pixels))
    # The floor is a strict lower bound: a blob whose area equals the floor
    # is BELOW the noise band and excluded, mirroring the > convention every
    # other calibrated pixel gate in this codebase uses.
    if min_area_pixels < 1:
        min_area_pixels = 1

    if mask_pixels == 0:
        labels = np.zeros((height, width), dtype=np.int32)
        raw = []
    else:
        labels = label_components(mask, connectivity=args.connectivity)
        raw = _per_component_stats(labels, (width, height))

    components, kept = _make_component_entries(
        raw, (width, height), min_area_pixels
    )
    excluded_count = len(raw) - len(kept)
    excluded_area = sum(rec["area_pixels"] for rec in raw if rec["area_pixels"] < min_area_pixels)

    foreground_block = {
        "source": mask_source,
        "background_estimate": background_hex,
        "mask_pixels": mask_pixels,
        "fraction_of_frame": round(fraction, 8),
        "bbox_pixel": list(bbox_pixels) if bbox_pixels else None,
        "bbox_fractional": (
            [
                round(bbox_pixels[0] / width, 8),
                round(bbox_pixels[1] / height, 8),
                round(bbox_pixels[2] / width, 8),
                round(bbox_pixels[3] / height, 8),
            ]
            if bbox_pixels
            else None
        ),
        "flags": list(flags),
    }

    payload = {
        "tool": "pil_components",
        "version": TOOL_VERSION,
        "parameters": {
            "image": str(args.image),
            "background_delta": args.background_delta,
            "min_blob_area_fraction": args.min_blob_area_fraction,
            "connectivity": args.connectivity,
        },
        "source": {
            "path": str(args.image),
            "size": [width, height],
            "mode": source_mode,
            "has_alpha": has_alpha,
            "sha256": _sha256(source_bytes),
        },
        "foreground": foreground_block,
        "components": components,
        "component_count": len(components),
        "min_blob_area_pixels_applied": min_area_pixels,
        "excluded_below_floor": {
            "count": excluded_count,
            "total_pixels": int(excluded_area),
        },
        "flags": list(flags),
        "interpretation_limits": INTERPRETATION_LIMITS,
    }
    return payload


def build_parser():
    parser = argparse.ArgumentParser(
        description="Connected-component instance counting over the native-resolution foreground mask."
    )
    parser.add_argument("image", help="source image path")
    parser.add_argument(
        "--min-blob-area-fraction",
        type=float,
        default=MIN_BLOB_AREA_FRACTION,
        help=(
            "minimum blob area as a frame fraction; blobs at or below the "
            "equivalent pixel count are excluded from the count. Default "
            f"{MIN_BLOB_AREA_FRACTION} is calibrated in "
            "runs/2026-08-20-components-discrimination/."
        ),
    )
    parser.add_argument(
        "--connectivity",
        type=int,
        default=DEFAULT_CONNECTIVITY,
        choices=CONNECTIVITIES,
        help=(
            f"neighbourhood rule; {DEFAULT_CONNECTIVITY} treats a "
            "diagonal-touching pixel pair as one component (default), 4 "
            "treats only axis-aligned neighbours as connected."
        ),
    )
    parser.add_argument(
        "--background-delta",
        type=float,
        default=DEFAULT_BACKGROUND_DELTA,
        help=(
            "OKLab distance from the border-median colour within which an "
            "opaque pixel counts as background, used only when the file "
            "carries no real alpha channel (default "
            f"{DEFAULT_BACKGROUND_DELTA})."
        ),
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        payload = measure(args)
    except ComponentsRejected as exc:
        print(f"pil_components: {exc}", file=sys.stderr)
        return 2
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
