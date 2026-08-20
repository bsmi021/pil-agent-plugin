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
    python pil_palette_diff.py "view_a.png" "view_b.png" --foreground
    python pil_palette_diff.py "ref.png" "render.png" --accent-space lch

Use --foreground for object renders on a shared preview background: full-frame
statistics on such frames mostly describe the background.

Colour distances are reported twice: as CIEDE2000 over D65 CIELAB, which is the
signal to read, and as Euclidean RGB, kept for continuity with phase 1 and
demoted. --accent-space lch switches the accent mask from HSV saturation/value
to perceptual chroma/lightness floors; it is not yet the default because those
floors are reasoned inputs awaiting calibration.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_color import LAB_REFERENCE  # noqa: E402
from pil_common import (  # noqa: E402
    ACCENT_AREA_SMALL_FRACTION,
    ACCENT_SPACES,
    ALPHA_FOREGROUND_MIN,
    BACKGROUND_DOMINANT_MAX,
    DEFAULT_ACCENT_CHROMA_MIN,
    DEFAULT_ACCENT_LIGHTNESS_MIN,
    DEFAULT_ACCENT_SAT_MIN,
    DEFAULT_ACCENT_SPACE,
    DEFAULT_ACCENT_VAL_MIN,
    DEFAULT_BACKGROUND_DELTA,
    FOREGROUND_MIN_FRACTION,
    HUE_PRESENCE_MIN_FRACTION,
    HUE_PRESENCE_MIN_PIXELS,
    accent_subset,
    entropy_of,
    foreground_estimate,
    foreground_mask,
    hue_families,
    load_rgb_alpha,
    luminance_stats,
    masked_strip,
    palette_distance,
    palette_distance_de2000,
    quantize_palette,
    saturation_stats,
)

TOOL_VERSION = "0.3.0"

# A hue family counts as shifted only when its share of accent pixels moves by
# both an absolute and a relative margin, which keeps resampling noise from
# tripping the detector while still catching a real recolour.
HUE_SHIFT_MIN_ABSOLUTE = 0.02
HUE_SHIFT_MIN_RELATIVE = 0.30

INTERPRETATION_LIMITS = [
    "Base-palette coverage is area-weighted: a visually dominant accent that "
    "occupies few pixels will rank low or be absent. Read accent_palette for "
    "perceptual identity, base_palette for bulk tone.",
    "base_palette_distance_de2000 and accent_palette_distance_de2000 are the "
    "primary colour-distance signal: the same coverage-weighted symmetric "
    "Chamfer construction, but measured with CIEDE2000 over D65 CIELAB. Read "
    "these first.",
    "base_palette_distance and accent_palette_distance are Euclidean in RGB, "
    "which is not perceptually uniform. They are kept for continuity with "
    "phase 1 and are now demoted to supporting detail; where the two disagree, "
    "the de2000 figure is the one to trust.",
    "dE2000 has NO authoritative perceptibility bands. The widely-copied "
    "0-1 / 1-2 / 2-10 banding table is disclaimed by its own author, and "
    "'dE 1.0 = just noticeable difference' has no traceable primary source, so "
    "this tool emits no verbal band. The numbers are raw: dE2000 is not a "
    "percentage and is not bounded at 100 (black vs white is exactly 100.0, "
    "but the sRGB maximum measured is 119.22), so never normalise it. A "
    "decision threshold has to come from calibration, not from this scale.",
    "Accent membership is a hard threshold in the space named by accent_space, "
    "with both spaces' thresholds echoed in accent_thresholds; only the pair "
    "belonging to accent_space is active. Colours near the boundary may flip "
    "between the two palettes. Hue-family bucketing runs on the HSV hue "
    "channel in both spaces -- no authoritative CIELAB hue-angle boundaries "
    "for basic colour names exist to port to.",
    "accent_palette is itself area-weighted, so a dominant accent hue can crowd "
    "out smaller ones. Use hue_families to establish which hues are present at "
    "all, and hue_families_lost/gained to detect a dropped accent -- a hue can "
    "matter semantically at well under 1% of the frame.",
    "In full-frame mode every statistic includes the background. When "
    "background_dominant or accent_support_low is flagged, hue and palette "
    "numbers mostly describe background and noise; re-run with --foreground "
    "before letting them influence a verdict or a ranking.",
    "In --foreground mode all fractions (accent_pixel_fraction, hue_families "
    "fraction_of_frame) are shares of the foreground, not of the frame. The "
    "foreground block records how the mask was derived.",
]


def _foreground_analysis(rgb, alpha, background_delta, flags):
    """Derive the foreground mask and its reporting block; returns (mask, block).

    mask is None when it could not be derived (empty), in which case analysis
    falls back to full-frame with a flag rather than crashing or emitting
    statistics over nothing.
    """
    mask, source, background_hex = foreground_mask(rgb, alpha, background_delta)
    fraction = float(mask.mean())
    block = {
        "applied": bool(mask.any()),
        "source": source,
        "fraction_of_frame": round(fraction, 6),
        "background_estimate": background_hex,
    }
    if not mask.any():
        flags.append("foreground_mask_empty")
        return None, block
    if fraction < FOREGROUND_MIN_FRACTION:
        flags.append("foreground_too_small")
    return mask, block


def analyse(
    path,
    n_colors,
    sat_min,
    val_min,
    foreground,
    background_delta,
    accent_space=DEFAULT_ACCENT_SPACE,
):
    rgb, alpha = load_rgb_alpha(path)
    flags = []
    within = None

    if foreground:
        within, foreground_block = _foreground_analysis(
            rgb, alpha, background_delta, flags
        )
    else:
        fraction, source, background_hex = foreground_estimate(
            rgb, alpha, background_delta
        )
        foreground_block = {
            "applied": False,
            "source": source,
            "fraction_of_frame": fraction,
            "background_estimate": background_hex,
        }
        if fraction < BACKGROUND_DOMINANT_MAX:
            # The frame is mostly background: full-frame colour statistics
            # describe the backdrop, not the subject.
            flags.append("background_dominant")

    if within is None:
        base_palette = quantize_palette(rgb, n_colors)
    else:
        base_palette = quantize_palette(masked_strip(rgb, within), n_colors)

    accent_img, accent_fraction = accent_subset(
        rgb, sat_min, val_min, within=within, space=accent_space
    )

    if accent_img is None:
        accent_palette = []
        flags.append("no_accent_pixels")
    else:
        accent_palette = quantize_palette(accent_img, n_colors)
        if accent_fraction < ACCENT_AREA_SMALL_FRACTION:
            flags.append("accent_area_very_small")

    return {
        "path": str(path),
        "size": list(rgb.size),
        "mode": rgb.mode,
        "aspect_ratio": round(rgb.width / rgb.height, 4),
        "base_palette": base_palette,
        "accent_palette": accent_palette,
        "hue_families": hue_families(
            rgb, sat_min, val_min, within=within, space=accent_space
        ),
        "accent_pixel_fraction": round(accent_fraction, 6),
        "luminance": luminance_stats(rgb, mask=within),
        "saturation": saturation_stats(rgb, mask=within),
        "entropy": entropy_of(rgb, mask=within),
        "foreground": foreground_block,
        "flags": flags,
    }


def _supported_families(families):
    """Hue families with enough pixels to participate in the verdict.

    Bare presence (pixels > 0) is not evidence: a single anti-aliased edge pixel
    lands in some hue family on almost every render, and letting it into the
    lost/gained sets flips accent_hue_shift_detected on noise. The census still
    reports unsupported families -- only the verdict ignores them.
    """
    return {
        name
        for name, v in families.items()
        if v["pixels"] >= HUE_PRESENCE_MIN_PIXELS
        and v["fraction_of_frame"] >= HUE_PRESENCE_MIN_FRACTION
    }


def build_diff(a, b):
    base = palette_distance(a["base_palette"], b["base_palette"])
    accent = palette_distance(a["accent_palette"], b["accent_palette"])
    base_de2000 = palette_distance_de2000(a["base_palette"], b["base_palette"])
    accent_de2000 = palette_distance_de2000(
        a["accent_palette"], b["accent_palette"]
    )

    flags = []
    if accent is None:
        flags.append("accent_comparison_unavailable")
    if abs(a["aspect_ratio"] - b["aspect_ratio"]) > 0.01:
        flags.append("aspect_ratio_mismatch")
    if a["foreground"]["applied"] != b["foreground"]["applied"]:
        # One side's mask fell back to full-frame: its fractions are
        # frame-relative while the other side's are foreground-relative, so the
        # deltas below compare incommensurable quantities.
        flags.append("foreground_mask_mismatch")
    if (
        a["accent_pixel_fraction"] < ACCENT_AREA_SMALL_FRACTION
        or b["accent_pixel_fraction"] < ACCENT_AREA_SMALL_FRACTION
    ):
        # Too few vivid pixels on at least one side for hue statistics to rank
        # anything. The verdict below is still emitted, but a caller feeding it
        # into a ranking must treat this flag as disqualifying.
        flags.append("accent_support_low")

    present_a = _supported_families(a["hue_families"])
    present_b = _supported_families(b["hue_families"])

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
    # A family unsupported on both sides is skipped outright -- with a tiny
    # accent area the fraction denominators are so small that these ratios are
    # pure noise.
    diminished, amplified = [], []
    for name, delta in deltas.items():
        if name not in present_a and name not in present_b:
            continue
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
        # The perceptual pair leads: same Chamfer construction as the RGB
        # distances below, measured with CIEDE2000 over D65 CIELAB. Raw dE00 --
        # never normalised, never banded.
        "base_palette_distance_de2000": base_de2000,
        "accent_palette_distance_de2000": accent_de2000,
        # Retained for continuity with phase 1 and demoted to supporting
        # detail: Euclidean RGB is not perceptually uniform.
        "base_palette_distance": base,
        "accent_palette_distance": accent,
        # A hue family with supported presence in the reference but not in the
        # comparison is a colour-scheme regression even when it is
        # area-negligible. "Supported" is the operative word: presence below the
        # support floor never enters these sets (see _supported_families).
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
    parser.add_argument(
        "--accent-space",
        choices=ACCENT_SPACES,
        default=DEFAULT_ACCENT_SPACE,
        help="space in which 'vivid accent' is defined: hsv uses --accent-sat/"
        "--accent-val (default, phase-1 behaviour); lch uses the perceptual "
        f"floors C* >= {DEFAULT_ACCENT_CHROMA_MIN} and "
        f"L* >= {DEFAULT_ACCENT_LIGHTNESS_MIN}, which are reasoned inputs "
        "awaiting calibration rather than measured thresholds",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="mask the background out (alpha when present, else border-median "
        "colour) and measure the foreground only -- required for meaningful "
        "numbers on object renders over a shared preview background",
    )
    parser.add_argument(
        "--background-delta",
        type=float,
        default=DEFAULT_BACKGROUND_DELTA,
        help="OKLab distance from the border-median colour within which an "
        f"opaque pixel counts as background (default {DEFAULT_BACKGROUND_DELTA})",
    )
    args = parser.parse_args(argv)

    if args.colors < 2:
        parser.error("--colors must be >= 2")

    def run(path):
        return analyse(
            path,
            args.colors,
            args.accent_sat,
            args.accent_val,
            args.foreground,
            args.background_delta,
            args.accent_space,
        )

    images = {"a": run(args.image_a)}
    diff = None
    if args.image_b:
        images["b"] = run(args.image_b)
        diff = build_diff(images["a"], images["b"])

    payload = {
        "tool": "pil_palette_diff",
        "version": TOOL_VERSION,
        "parameters": {
            "colors": args.colors,
            # Which pair of thresholds is live is decided by accent_space; both
            # are echoed so a reader can see what the alternative would have
            # been without re-running the tool.
            "accent_space": args.accent_space,
            "accent_thresholds": {
                "saturation_min": args.accent_sat,
                "value_min": args.accent_val,
                "chroma_min": DEFAULT_ACCENT_CHROMA_MIN,
                "lightness_min": DEFAULT_ACCENT_LIGHTNESS_MIN,
            },
            # dE2000 is meaningless without its illuminant: ICC/Photoshop Lab
            # and Pillow's own LAB mode are D50, and reading one as the other
            # is worth up to 7.5 dE00.
            "lab_reference": LAB_REFERENCE,
            "foreground": args.foreground,
            "background_delta": args.background_delta,
            "alpha_foreground_min": ALPHA_FOREGROUND_MIN,
            "hue_presence_min": {
                "fraction": HUE_PRESENCE_MIN_FRACTION,
                "pixels": HUE_PRESENCE_MIN_PIXELS,
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
