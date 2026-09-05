#!/usr/bin/env python
"""One-call maximal image profile: file facts, colour, structure, fingerprints.

Composes the three measurement layers that previously required three separate
invocations -- pil_image_info (file facts), pil_palette_diff (colour) and
pil_structure_diff (structure/layout) -- into a single deterministic JSON
payload, and adds the signals none of them emitted for a single image:

*   Persistable perceptual-hash fingerprints (dhash/ahash as hex strings).
    The pairwise tools only ever reported hash DISTANCES, so a single image
    could not be fingerprinted for later identification. Hex hashes from two
    separate runs -- of this tool, on different days, on different files --
    are directly comparable with a Hamming distance.
*   Tonal statistics beyond mean/std: exact luminance percentiles, min/max,
    and clipped/near-clipped fractions for exposure characterisation.
*   Channel statistics: per-channel mean/std, an exact all-channels-equal
    (true-greyscale) fact, and an exact distinct-colour count.
*   Global detail diagnostics: edge-magnitude statistics and Laplacian
    variance on the working copy. These are UNCALIBRATED 2D complexity
    proxies, clearly labelled as such -- never geometry, never a verdict.

With two images it additionally emits the full pairwise diff of both
underlying tools plus fingerprint distances, so a complete comparison is one
invocation instead of three.

The composed sub-tools are invoked through their own public analyse()
functions, so their outputs here are byte-identical in content to running
them standalone, and their own byte-identity obligations are untouched.

Usage:
    python pil_image_analyze.py "image.png"
    python pil_image_analyze.py "ref.png" "render.png"
    python pil_image_analyze.py "render.png" --foreground
    python pil_image_analyze.py "ref.png" --region "0.1,0.4,0.3,0.9"

Use --foreground for object renders on a shared preview background, exactly
as with the underlying tools; the colour and structure blocks then measure
the foreground, and the subject fingerprint hashes the masked, bbox-cropped
object rather than the frame.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pil_image_info  # noqa: E402
import pil_palette_diff  # noqa: E402
import pil_structure_diff  # noqa: E402
from pil_common import (  # noqa: E402
    ACCENT_SPACES,
    DEFAULT_ACCENT_SAT_MIN,
    DEFAULT_ACCENT_SPACE,
    DEFAULT_ACCENT_VAL_MIN,
    DEFAULT_BACKGROUND_DELTA,
    ahash,
    dhash,
    edge_magnitude,
    load_rgba_straight,
    luminance_array,
    parse_grid,
    to_working,
)
from pil_region import (  # noqa: E402
    DEFAULT_REGION_SPACE,
    REGION_SPACES,
    RegionError,
    parse_fractional_bbox,
)

TOOL_VERSION = "0.8.0"

# Luminance code values at or below/above which a pixel counts as
# near-clipped. These are reporting conventions echoed in parameters, not
# calibrated decision thresholds: the exact ==0 / ==255 fractions are also
# reported, so a caller who disagrees with the convention still has the
# uninterpreted facts.
NEAR_BLACK_MAX = 5
NEAR_WHITE_MIN = 250

TONAL_PERCENTILES = (1, 5, 25, 50, 75, 95, 99)

INTERPRETATION_LIMITS = [
    "This tool composes pil_image_info, pil_palette_diff and "
    "pil_structure_diff through their own analyse functions: the file, "
    "colour and structure blocks match those tools' standalone output "
    "content for the same options, and every limit those tools publish "
    "(reproduced below) applies unchanged to the matching block here.",
    "fingerprints are 64-bit perceptual hashes serialised as 16 hex "
    "characters. They are stable across runs and machines for the same "
    "Pillow resampling implementation, and two fingerprints -- from "
    "different runs, files, or days -- compare by Hamming distance over "
    "their bits. They detect near-duplicates and survive rescaling; they "
    "are luminance-based and therefore blind to hue (a pure recolour can "
    "measure distance 0), and weak at fine-detail differences. "
    "full_frame hashes the analysis frame; subject hashes the same pixels "
    "the structure block scored (the masked, bbox-cropped foreground in "
    "--foreground mode; identical to full_frame otherwise).",
    "tonal percentiles, min/max and clipped fractions are exact order "
    "statistics of the analysis frame's full-resolution luminance "
    "(ITU-R 601-2, the same conversion every other tool here uses). "
    "clipped_black/white_fraction count exactly 0 and exactly 255; "
    "near_black/white_fraction use the reporting thresholds echoed in "
    "parameters, which are conventions, not calibrated limits. In "
    "--foreground mode these remain FRAME statistics (of the region crop "
    "when --region is given): background pixels are included. Read the "
    "colour block's luminance for foreground-masked figures.",
    "channels.unique_colours counts exact distinct RGB byte triples of the "
    "analysis frame after alpha compositing onto black -- a decoder-level "
    "fact, not a perceptual colour count: anti-aliasing and gradients "
    "inflate it without adding perceptually distinct colours. "
    "channels.all_channels_equal is an exact per-pixel R==G==B test -- "
    "true means the decoded image is arithmetically greyscale, but false "
    "does not mean it LOOKS colourful; read the colour block's saturation "
    "for that.",
    "detail.edge_magnitude_* and detail.laplacian_variance are UNCALIBRATED "
    "2D image-complexity diagnostics computed on the working-resolution "
    "copy. No discrimination gate has validated either as a sharpness or "
    "detail verdict, so treat them as relative signals between "
    "like-resolution, like-content images -- never as an absolute quality "
    "score. They are NOT geometry: shading, normal maps, lighting and "
    "camera angle all move them independently of any underlying model, "
    "exactly as the structure block's own limits state.",
]


def _bits_to_hex(bits):
    """Serialise a 64-bit hash array to 16 hex characters, row-major."""
    return np.packbits(np.asarray(bits, dtype=bool).ravel()).tobytes().hex()


def hex_hamming(hex_a, hex_b):
    """Hamming distance between two equal-length hex fingerprints."""
    if len(hex_a) != len(hex_b):
        raise ValueError("fingerprints differ in length")
    return int(bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1"))


def _fingerprints(frame_working, subject_working):
    block = {
        "full_frame": {
            "dhash": _bits_to_hex(dhash(frame_working)),
            "ahash": _bits_to_hex(ahash(frame_working)),
        }
    }
    block["subject"] = {
        "dhash": _bits_to_hex(dhash(subject_working)),
        "ahash": _bits_to_hex(ahash(subject_working)),
    }
    return block


def _tonal_stats(frame_rgb):
    lum = luminance_array(frame_rgb)
    total = float(lum.size)
    percentiles = np.percentile(lum, TONAL_PERCENTILES)
    return {
        "min": int(lum.min()),
        "max": int(lum.max()),
        "percentiles": {
            f"p{p:02d}": round(float(v), 3)
            for p, v in zip(TONAL_PERCENTILES, percentiles)
        },
        "clipped_black_fraction": round(float((lum == 0).sum() / total), 6),
        "clipped_white_fraction": round(float((lum == 255).sum() / total), 6),
        "near_black_fraction": round(float((lum <= NEAR_BLACK_MAX).sum() / total), 6),
        "near_white_fraction": round(float((lum >= NEAR_WHITE_MIN).sum() / total), 6),
    }


def _channel_stats(frame_rgb):
    arr = np.asarray(frame_rgb, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 3:
        arr = np.asarray(frame_rgb.convert("RGB"), dtype=np.uint8)
    flat = arr.reshape(-1, 3)
    packed = (
        flat[:, 0].astype(np.uint32) << 16
    ) | (flat[:, 1].astype(np.uint32) << 8) | flat[:, 2].astype(np.uint32)
    channels = arr.astype(np.float64)
    names = ("r", "g", "b")
    return {
        "unique_colours": int(np.unique(packed).size),
        "all_channels_equal": bool(
            (flat[:, 0] == flat[:, 1]).all() and (flat[:, 1] == flat[:, 2]).all()
        ),
        "mean": {
            name: round(float(channels[:, :, i].mean()), 3)
            for i, name in enumerate(names)
        },
        "std": {
            name: round(float(channels[:, :, i].std()), 3)
            for i, name in enumerate(names)
        },
    }


def _detail_stats(frame_working):
    edges = edge_magnitude(frame_working)
    lum = luminance_array(frame_working)
    if lum.shape[0] >= 3 and lum.shape[1] >= 3:
        lap = (
            lum[:-2, 1:-1]
            + lum[2:, 1:-1]
            + lum[1:-1, :-2]
            + lum[1:-1, 2:]
            - 4.0 * lum[1:-1, 1:-1]
        )
        laplacian_variance = round(float(lap.var()), 3)
    else:
        laplacian_variance = None
    return {
        "edge_magnitude_mean": round(float(edges.mean()), 3),
        "edge_magnitude_std": round(float(edges.std()), 3),
        "edge_magnitude_p95": round(float(np.percentile(edges, 95)), 3),
        "laplacian_variance": laplacian_variance,
    }


def _analysis_frame(path, region, region_space, background_delta):
    """The region-cropped composited frame the new signal blocks describe.

    Region semantics are pil_structure_diff's own _apply_region, so a
    --region here means exactly what it means on the composed tools.
    """
    composited_rgb, straight_rgb, alpha = load_rgba_straight(path)
    composited_rgb, _straight, _alpha, _block, _size = pil_structure_diff._apply_region(
        composited_rgb, straight_rgb, alpha, region, region_space, background_delta
    )
    return composited_rgb


def profile_image(path, args, region_box):
    """Build the full profile for one image.

    Returns (profile, palette_analysis, structure_analysis, structure_working)
    so the pairwise diff can reuse the exact analysis dicts rather than
    re-deriving them.
    """
    file_block = pil_image_info.inspect_image(path)
    if not file_block.get("readable"):
        profile = {
            "path": str(path),
            "file": file_block,
            "colour": None,
            "structure": None,
            "fingerprints": None,
            "tonal": None,
            "channels": None,
            "detail": None,
            "flags": ["unreadable"],
        }
        return profile, None, None, None

    colour = pil_palette_diff.analyse(
        path,
        args.colors,
        args.accent_sat,
        args.accent_val,
        args.foreground,
        args.background_delta,
        args.accent_space,
        region_box,
        args.region_space,
    )
    cols, rows = parse_grid(args.grid)
    structure, structure_working = pil_structure_diff.analyse(
        path,
        cols,
        rows,
        args.foreground,
        args.background_delta,
        region_box,
        args.region_space,
    )

    frame_rgb = _analysis_frame(path, region_box, args.region_space, args.background_delta)
    frame_working = to_working(frame_rgb)

    flags = sorted(
        set(file_block.get("flags") or [])
        | set(colour["flags"])
        | set(structure["flags"])
    )

    profile = {
        "path": str(path),
        "file": file_block,
        "colour": colour,
        "structure": structure,
        "fingerprints": _fingerprints(frame_working, structure_working),
        "tonal": _tonal_stats(frame_rgb),
        "channels": _channel_stats(frame_rgb),
        "detail": _detail_stats(frame_working),
        "flags": flags,
    }
    return profile, colour, structure, structure_working


def build_diff(profiles, analyses):
    """Pairwise diff over both composed tools plus fingerprint distances."""
    (colour_a, structure_a, working_a) = analyses["a"]
    (colour_b, structure_b, working_b) = analyses["b"]
    file_a, file_b = profiles["a"]["file"], profiles["b"]["file"]
    fp_a, fp_b = profiles["a"]["fingerprints"], profiles["b"]["fingerprints"]

    diff = {
        "file": {
            "identical_bytes": file_a["sha256"] == file_b["sha256"],
            "format_match": file_a["format"] == file_b["format"],
            "mode_match": file_a["mode"] == file_b["mode"],
            "size_match": file_a["size"] == file_b["size"],
        },
        "colour": pil_palette_diff.build_diff(
            colour_a, colour_b, foreground=analyses["foreground"]
        ),
        "structure": pil_structure_diff.build_diff(
            structure_a,
            structure_b,
            working_a,
            working_b,
            foreground=analyses["foreground"],
        ),
        "fingerprints": {
            "full_frame_dhash_distance": hex_hamming(
                fp_a["full_frame"]["dhash"], fp_b["full_frame"]["dhash"]
            ),
            "full_frame_ahash_distance": hex_hamming(
                fp_a["full_frame"]["ahash"], fp_b["full_frame"]["ahash"]
            ),
            "subject_dhash_distance": hex_hamming(
                fp_a["subject"]["dhash"], fp_b["subject"]["dhash"]
            ),
            "subject_ahash_distance": hex_hamming(
                fp_a["subject"]["ahash"], fp_b["subject"]["ahash"]
            ),
        },
    }
    diff["flags"] = sorted(set(diff["colour"]["flags"]) | set(diff["structure"]["flags"]))
    return diff


def _composed_limits(profiles):
    """Aggregate every composed tool's interpretation limits, mirroring their
    own alpha-path gating, plus this tool's additions -- exact duplicate
    strings collapse, order is preserved."""
    alpha_path_used = any(
        p["colour"] is not None and p["colour"]["foreground"].get("coverage_weighted")
        for p in profiles.values()
    )

    palette_limits = list(pil_palette_diff.INTERPRETATION_LIMITS)
    if alpha_path_used:
        palette_limits += pil_palette_diff.ALPHA_INTERPRETATION_LIMITS
    palette_limits += pil_palette_diff.REGION_INTERPRETATION_LIMITS

    structure_limits = list(pil_structure_diff.INTERPRETATION_LIMITS)
    if alpha_path_used:
        structure_limits[pil_structure_diff._THIN_OBJECT_CAVEAT_INDEX] = (
            pil_structure_diff._ALPHA_THIN_OBJECT_CAVEAT
        )
        structure_limits += pil_structure_diff.ALPHA_INTERPRETATION_LIMITS
    structure_limits += pil_structure_diff.REGION_INTERPRETATION_LIMITS

    combined = (
        list(INTERPRETATION_LIMITS)
        + palette_limits
        + structure_limits
        + list(pil_image_info.INTERPRETATION_LIMITS)
    )
    seen = set()
    unique = []
    for entry in combined:
        if entry not in seen:
            seen.add(entry)
            unique.append(entry)
    return unique


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Emit a maximal single-image profile (file facts, colour, "
        "structure, fingerprints, tonal/channel/detail statistics) or a full "
        "two-image comparison, in one deterministic JSON payload."
    )
    parser.add_argument("image_a", help="image path")
    parser.add_argument("image_b", nargs="?", help="optional comparison image path")
    parser.add_argument(
        "--colors", type=int, default=8, help="palette size per image (default 8)"
    )
    parser.add_argument(
        "--grid", default="4x3", help="structure analysis grid as COLSxROWS (default 4x3)"
    )
    parser.add_argument(
        "--accent-sat",
        type=int,
        default=DEFAULT_ACCENT_SAT_MIN,
        help=f"minimum HSV saturation for accent pixels (default {DEFAULT_ACCENT_SAT_MIN})",
    )
    parser.add_argument(
        "--accent-val",
        type=int,
        default=DEFAULT_ACCENT_VAL_MIN,
        help=f"minimum HSV value for accent pixels (default {DEFAULT_ACCENT_VAL_MIN})",
    )
    parser.add_argument(
        "--accent-space",
        choices=ACCENT_SPACES,
        default=DEFAULT_ACCENT_SPACE,
        help="space in which 'vivid accent' is defined (see pil_palette_diff)",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="mask the background out and measure the foreground in the "
        "colour and structure blocks; the subject fingerprint then hashes "
        "the masked, bbox-cropped object",
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
        help="fractional bbox 'L,T,R,B'; crops the analysis to this box at "
        "full resolution before any measurement runs, with the same "
        "semantics as the composed tools",
    )
    parser.add_argument(
        "--region-space",
        choices=REGION_SPACES,
        default=DEFAULT_REGION_SPACE,
        help=f"resolve --region against frame or foreground bbox (default {DEFAULT_REGION_SPACE})",
    )
    args = parser.parse_args(argv)

    if args.colors < 2:
        parser.error("--colors must be >= 2")
    cols, rows = parse_grid(args.grid)

    try:
        region_box = parse_fractional_bbox(args.region) if args.region is not None else None
    except RegionError as exc:
        print(f"pil_image_analyze: {exc}", file=sys.stderr)
        return 2

    profiles = {}
    analyses = {"foreground": args.foreground}
    try:
        for key, path in (("a", args.image_a), ("b", args.image_b)):
            if path is None:
                continue
            profile, colour, structure, working = profile_image(path, args, region_box)
            profiles[key] = profile
            analyses[key] = (colour, structure, working)
    except RegionError as exc:
        print(f"pil_image_analyze: {exc}", file=sys.stderr)
        return 2

    diff = None
    all_readable = all(p["file"].get("readable") for p in profiles.values())
    if "b" in profiles and all_readable:
        diff = build_diff(profiles, analyses)

    payload = {
        "tool": "pil_image_analyze",
        "version": TOOL_VERSION,
        "parameters": {
            "colors": args.colors,
            "grid": {"cols": cols, "rows": rows},
            "accent_space": args.accent_space,
            "accent_thresholds": {
                "saturation_min": args.accent_sat,
                "value_min": args.accent_val,
            },
            "foreground": args.foreground,
            "background_delta": args.background_delta,
            "region": region_box,
            "region_space": args.region_space,
            "near_black_max": NEAR_BLACK_MAX,
            "near_white_min": NEAR_WHITE_MIN,
            "tonal_percentiles": list(TONAL_PERCENTILES),
        },
        "images": profiles,
        "diff": diff,
        "interpretation_limits": _composed_limits(profiles),
    }

    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0 if all_readable else 1


if __name__ == "__main__":
    raise SystemExit(main())
