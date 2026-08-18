#!/usr/bin/env python
"""Exact colour-palette measurement and comparison for one or two images.

Emits JSON on stdout. With one image it reports that image's palettes and colour
statistics; with two it adds a diff block.

Why two palettes: on a dark image, a single global quantisation spends its whole
colour budget on near-black tones and returns no vivid entries at all -- so two
images with completely different accent hues would compare as a match. The
chroma-masked accent palette is what actually answers "did the colour scheme
change".

Usage:
    python pil_palette_diff.py "ref.png"
    python pil_palette_diff.py "ref.png" "render.png" --colors 8
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_common import (  # noqa: E402
    DEFAULT_ACCENT_SAT_MIN,
    DEFAULT_ACCENT_VAL_MIN,
    accent_subset,
    entropy_of,
    hue_families,
    load_rgb,
    luminance_stats,
    palette_distance,
    quantize_palette,
    saturation_stats,
)

TOOL_VERSION = "0.1.0"

# A hue family counts as shifted only when its share of accent pixels moves by
# both an absolute and a relative margin, which keeps resampling noise from
# tripping the detector while still catching a real recolour.
HUE_SHIFT_MIN_ABSOLUTE = 0.02
HUE_SHIFT_MIN_RELATIVE = 0.30

INTERPRETATION_LIMITS = [
    "Base-palette coverage is area-weighted: a visually dominant accent that "
    "occupies few pixels will rank low or be absent. Read accent_palette for "
    "perceptual identity, base_palette for bulk tone.",
    "Palette distance is Euclidean in RGB, which is not perceptually uniform; "
    "treat it as a relative signal between comparable images, not an absolute "
    "perceptual delta.",
    "Accent membership is a hard HSV threshold, echoed in accent_thresholds. "
    "Colours near the boundary may flip between the two palettes.",
    "accent_palette is itself area-weighted, so a dominant accent hue can crowd "
    "out smaller ones. Use hue_families to establish which hues are present at "
    "all, and hue_families_lost/gained to detect a dropped accent -- a hue can "
    "matter semantically at well under 1% of the frame.",
]


def analyse(path, n_colors, sat_min, val_min):
    img = load_rgb(path)
    flags = []

    base_palette = quantize_palette(img, n_colors)
    accent_img, accent_fraction = accent_subset(img, sat_min, val_min)

    if accent_img is None:
        accent_palette = []
        flags.append("no_accent_pixels")
    else:
        accent_palette = quantize_palette(accent_img, n_colors)
        if accent_fraction < 0.005:
            flags.append("accent_area_very_small")

    return {
        "path": str(path),
        "size": list(img.size),
        "mode": img.mode,
        "aspect_ratio": round(img.width / img.height, 4),
        "base_palette": base_palette,
        "accent_palette": accent_palette,
        "hue_families": hue_families(img, sat_min, val_min),
        "accent_pixel_fraction": round(accent_fraction, 6),
        "luminance": luminance_stats(img),
        "saturation": saturation_stats(img),
        "entropy": entropy_of(img),
        "flags": flags,
    }


def build_diff(a, b):
    base = palette_distance(a["base_palette"], b["base_palette"])
    accent = palette_distance(a["accent_palette"], b["accent_palette"])

    flags = []
    if accent is None:
        flags.append("accent_comparison_unavailable")
    if abs(a["aspect_ratio"] - b["aspect_ratio"]) > 0.01:
        flags.append("aspect_ratio_mismatch")

    present_a = {k for k, v in a["hue_families"].items() if v["pixels"] > 0}
    present_b = {k for k, v in b["hue_families"].items() if v["pixels"] > 0}

    deltas = {
        name: round(
            b["hue_families"][name]["fraction_of_accents"]
            - a["hue_families"][name]["fraction_of_accents"],
            6,
        )
        for name in a["hue_families"]
    }

    # Magnitude-based detection. Presence/absence alone is brittle: a recolour
    # leaves residue pixels near the saturation threshold, so a family that has
    # effectively vanished still reports pixels > 0 and never registers as lost.
    diminished, amplified = [], []
    for name, delta in deltas.items():
        before = a["hue_families"][name]["fraction_of_accents"]
        after = b["hue_families"][name]["fraction_of_accents"]
        if abs(delta) < HUE_SHIFT_MIN_ABSOLUTE:
            continue
        reference = max(before, after)
        if reference <= 0:
            continue
        if abs(delta) / reference < HUE_SHIFT_MIN_RELATIVE:
            continue
        (diminished if delta < 0 else amplified).append(name)

    return {
        "base_palette_distance": base,
        "accent_palette_distance": accent,
        # A hue family present in the reference but absent from the comparison is
        # a colour-scheme regression even when it is area-negligible.
        "hue_families_lost": sorted(present_a - present_b),
        "hue_families_gained": sorted(present_b - present_a),
        "hue_families_diminished": sorted(diminished),
        "hue_families_amplified": sorted(amplified),
        "hue_family_fraction_deltas": deltas,
        # The headline colour-scheme verdict. Measured to be the only reliable
        # detector of an accent recolour: structural similarity, both perceptual
        # hashes and base palette distance are all blind to it.
        "accent_hue_shift_detected": bool(
            diminished
            or amplified
            or (present_a - present_b)
            or (present_b - present_a)
        ),
        "accent_fraction_delta": round(
            b["accent_pixel_fraction"] - a["accent_pixel_fraction"], 6
        ),
        "luminance_mean_delta": round(
            b["luminance"]["mean"] - a["luminance"]["mean"], 3
        ),
        "saturation_mean_delta": round(
            b["saturation"]["mean"] - a["saturation"]["mean"], 3
        ),
        "entropy_delta": round(b["entropy"] - a["entropy"], 4),
        "flags": flags,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Measure and compare image colour palettes."
    )
    parser.add_argument("image_a", help="reference image path")
    parser.add_argument("image_b", nargs="?", help="optional comparison image path")
    parser.add_argument(
        "--colors", type=int, default=8, help="palette size per image (default 8)"
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
    args = parser.parse_args(argv)

    if args.colors < 2:
        parser.error("--colors must be >= 2")

    images = {
        "a": analyse(args.image_a, args.colors, args.accent_sat, args.accent_val)
    }
    diff = None
    if args.image_b:
        images["b"] = analyse(
            args.image_b, args.colors, args.accent_sat, args.accent_val
        )
        diff = build_diff(images["a"], images["b"])

    payload = {
        "tool": "pil_palette_diff",
        "version": TOOL_VERSION,
        "parameters": {
            "colors": args.colors,
            "accent_thresholds": {
                "saturation_min": args.accent_sat,
                "value_min": args.accent_val,
            },
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
