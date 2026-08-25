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

import numpy as np

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
    estimate_background,
    foreground_estimate,
    foreground_mask,
    hue_families,
    load_rgba_straight,
    luminance_stats,
    mask_bbox,
    masked_strip,
    palette_distance,
    palette_distance_de2000,
    quantize_palette,
    saturation_stats,
)
from pil_region import (  # noqa: E402
    DEFAULT_REGION_SPACE,
    REGION_SPACES,
    RegionError,
    parse_fractional_bbox,
    rect_to_fractional,
    resolve_pixel_rect,
)

TOOL_VERSION = "0.6.0"

# A hue family counts as shifted only when its share of accent pixels moves by
# both an absolute and a relative margin, which keeps resampling noise from
# tripping the detector while still catching a real recolour.
HUE_SHIFT_MIN_ABSOLUTE = 0.02
HUE_SHIFT_MIN_RELATIVE = 0.30

# Appended to INTERPRETATION_LIMITS only when at least one analysed image
# actually took the alpha-weighted code path (coverage_weighted True on some
# image). Full-frame runs and --foreground runs on opaque (border-median)
# input must stay byte-identical to the pre-0.4.0 tree (see docs/aaa-build-
# plan.md A1.1), and interpretation_limits is a static top-level payload field
# included in every invocation -- so these entries cannot be unconditional
# additions to the base list without breaking that byte-identity gate on every
# fixture, alpha-bearing or not. Gating on the code path actually exercised
# turns interpretation_limits into the payload's one dynamic field, deliberately.
ALPHA_INTERPRETATION_LIMITS = [
    "coverage_weighted (in the foreground block) is true when this image's "
    "colour statistics -- luminance, saturation, entropy, accent_pixel_fraction, "
    "base_palette/accent_palette coverage, hue_families coverage -- are "
    "alpha/255 coverage-weighted rather than counted at equal weight per masked "
    "pixel. base_palette/accent_palette cluster CENTRES remain chosen from an "
    "unweighted MEDIANCUT quantisation of the straight-RGB pixels; only each "
    "entry's reported coverage is reweighted afterwards. hue_families.pixels "
    "and fractional_cells.foreground_pixels stay integer counts throughout, "
    "because the gates built on them (HUE_PRESENCE_MIN_PIXELS, "
    "CELL_MIN_SUPPORT_PIXELS) are sampling-adequacy questions, not colour "
    "questions.",
    "partial_coverage_share is the fraction of this image's masked pixels with "
    "alpha < 255. It carries NO flag and NO threshold: an uncalibrated flag "
    "threshold is exactly what this repository refuses to ship. Read it "
    "yourself when deciding how much to trust a colour statistic on a "
    "partially-transparent object.",
    "foreground_source_mismatch fires when the two images' foreground.source "
    "differ (one alpha, one border-median) -- distinct from "
    "foreground_mask_mismatch, which fires when one side's mask fell back to "
    "full-frame. A source mismatch means one side's colours are coverage-"
    "weighted true object colour and the other side's are blended toward its "
    "own backdrop; the two are not commensurable, and a composited render's "
    "coverage information was destroyed at composite time -- there is no "
    "un-blending it. Measured on this repository's own calibration corpus, "
    "this residual reaches double digits in luminance and saturation code "
    "values on thin or highly transparent objects (see the W1 evidence "
    "bundle); it is not a rounding error.",
]

# Appended to INTERPRETATION_LIMITS unconditionally, unlike
# ALPHA_INTERPRETATION_LIMITS above: --region carries no pre-existing byte-
# identity obligation (it is new in this release), and A6.2/A6.3 require a
# --region run and an equivalent pre-cropped-file run to compare equal on
# this field, which only an unconditional entry can satisfy -- the same
# reasoning that makes source_size and parameters.region unconditional.
REGION_INTERPRETATION_LIMITS = [
    "--region crops BOTH images to the same fractional box at full "
    "resolution before any measurement runs -- never after, which is what "
    "keeps a --region measurement resolution-independent and identical to "
    "pre-cropping the file with pil_crop.py and measuring the result -- with "
    "exactly one exception: the flags list. region_background_estimate_"
    "diverged (below) can appear on a --region run and never on the "
    "equivalent pre-cropped-file run, because it compares the crop's own "
    "border-median colour against the ORIGINAL, un-cropped frame's -- a "
    "comparison a pre-cropped file structurally cannot make, having no frame "
    "left to compare against. Every other field, including every colour and "
    "structure statistic, is unaffected. Composed with --foreground, the "
    "order is: region crop first, then the foreground mask -- and, on the "
    "border-median path, the background estimate -- is re-derived from the "
    "crop's own pixels, never inherited from the whole frame. "
    "region_background_estimate_diverged fires when that re-derived estimate "
    "lands further than --background-delta (in OKLab) from the whole "
    "frame's own border-median colour -- the signature of a region that "
    "contains little or no backdrop, where the re-derived estimate is the "
    "more honest one but also the more surprising one.",
]


def _apply_region(composited_rgb, straight_rgb, alpha, region, region_space, background_delta):
    """Crop all three full-resolution arrays to --region before anything else runs.

    Cropping here -- before the foreground mask is derived and before any
    statistic runs -- is what keeps a --region measurement resolution-
    independent and makes it identical to pre-cropping the file with
    pil_crop.py and measuring the result (A6.2): every statistic downstream
    simply treats whatever this function returns as "the image", so this is
    the only place region semantics live in this tool, and the equality
    with pil_crop is a consequence of sharing pil_region's rect arithmetic
    rather than something asserted separately.

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
    """Derive the foreground mask and its reporting block.

    Returns (mask, block, weights). mask is None when it could not be derived
    (empty), in which case analysis falls back to full-frame with a flag
    rather than crashing or emitting statistics over nothing. weights is the
    coverage-weight array (alpha/255, full-frame shape) when this image
    carries real alpha and the mask is non-empty, else None -- the single
    switch every downstream weighted call is gated on.

    coverage_weighted, coverage_fraction_of_frame and partial_coverage_share
    are added to the block ONLY when weights is returned non-None: adding them
    unconditionally would change the JSON shape of every --foreground
    invocation, including on opaque (border-median) input, which breaks the
    byte-identity gate that path is held to (docs/aaa-build-plan.md A1.1).
    """
    mask, source, background_hex = foreground_mask(rgb, alpha, background_delta)
    fraction = float(mask.mean())
    block = {
        "applied": bool(mask.any()),
        "source": source,
        "fraction_of_frame": round(fraction, 6),
        "background_estimate": background_hex,
    }
    weights = None
    if alpha is not None and mask.any():
        weights = np.asarray(alpha, dtype=np.float64) / 255.0
        partial = mask & (alpha < 255)
        block["coverage_weighted"] = True
        block["coverage_fraction_of_frame"] = round(
            float(weights[mask].sum()) / float(mask.size), 6
        )
        block["partial_coverage_share"] = round(
            float(partial.sum()) / float(mask.sum()), 6
        )
    if not mask.any():
        flags.append("foreground_mask_empty")
        return None, block, None
    if fraction < FOREGROUND_MIN_FRACTION:
        flags.append("foreground_too_small")
    return mask, block, weights


def analyse(
    path,
    n_colors,
    sat_min,
    val_min,
    foreground,
    background_delta,
    accent_space=DEFAULT_ACCENT_SPACE,
    region=None,
    region_space=DEFAULT_REGION_SPACE,
):
    composited_rgb, straight_rgb, alpha = load_rgba_straight(path)
    frame_rgb = composited_rgb
    composited_rgb, straight_rgb, alpha, region_block, source_size = _apply_region(
        composited_rgb, straight_rgb, alpha, region, region_space, background_delta
    )
    flags = []
    within = None
    weights = None

    if foreground:
        within, foreground_block, weights = _foreground_analysis(
            composited_rgb, alpha, background_delta, flags
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
    else:
        fraction, source, background_hex = foreground_estimate(
            composited_rgb, alpha, background_delta
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

    # colour_rgb is the straight (un-composited) image only when the alpha
    # path was actually exercised for THIS image (foreground requested, mask
    # non-empty, alpha present); every other case -- full-frame, opaque
    # foreground, empty-mask fallback -- keeps the composited image exactly as
    # before, so those paths stay byte-identical (docs/aaa-build-plan.md A1.1).
    colour_rgb = straight_rgb if weights is not None else composited_rgb

    if within is None:
        base_palette = quantize_palette(colour_rgb, n_colors)
    else:
        strip_weights = weights[within] if weights is not None else None
        base_palette = quantize_palette(
            masked_strip(colour_rgb, within), n_colors, weights=strip_weights
        )

    accent_img, accent_fraction, accent_weights = accent_subset(
        colour_rgb, sat_min, val_min, within=within, weights=weights, space=accent_space
    )

    if accent_img is None:
        accent_palette = []
        flags.append("no_accent_pixels")
    else:
        accent_palette = quantize_palette(accent_img, n_colors, weights=accent_weights)
        if accent_fraction < ACCENT_AREA_SMALL_FRACTION:
            flags.append("accent_area_very_small")

    return {
        "path": str(path),
        "size": list(composited_rgb.size),
        # source_size is the file's own size, unconditionally: it equals
        # size when no --region was given, and lets a caller with only the
        # payload in hand tell the crop's size from the file's (A6, #8.2).
        "source_size": source_size,
        "region": region_block,
        "mode": composited_rgb.mode,
        "aspect_ratio": round(composited_rgb.width / composited_rgb.height, 4),
        "base_palette": base_palette,
        "accent_palette": accent_palette,
        "hue_families": hue_families(
            colour_rgb, sat_min, val_min, within=within, weights=weights, space=accent_space
        ),
        "accent_pixel_fraction": round(accent_fraction, 6),
        "luminance": luminance_stats(colour_rgb, mask=within, weights=weights),
        "saturation": saturation_stats(colour_rgb, mask=within, weights=weights),
        "entropy": entropy_of(colour_rgb, mask=within, weights=weights),
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


def build_diff(a, b, foreground=False):
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
    if foreground and a["foreground"]["source"] != b["foreground"]["source"]:
        # Distinct from foreground_mask_mismatch above: both masks may have
        # applied fine, but one side's colours are coverage-weighted true
        # object colour (source "alpha") and the other's are blended toward
        # its own backdrop (source "background_estimate") -- not commensurable.
        # The composited side's coverage information was destroyed at
        # composite time, so this is not recoverable by re-reading either file.
        # Gated to --foreground mode only (per docs/aaa-build-plan.md #3.8):
        # in full-frame mode this same source disagreement is normal and
        # already unflagged pre-0.4.0, and flagging it there would change the
        # byte-identical full-frame output A1.1 holds every fixture to.
        flags.append("foreground_source_mismatch")
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
    parser.add_argument(
        "--region",
        default=None,
        help="fractional bbox 'L,T,R,B' or JSON '[L,T,R,B]' (same parser and "
        "half-up rounding rule as pil_crop.py); crops BOTH images to this "
        "box at full resolution before any measurement runs, so the "
        "reported palettes and statistics describe only the region",
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

    if args.colors < 2:
        parser.error("--colors must be >= 2")

    try:
        region_box = parse_fractional_bbox(args.region) if args.region is not None else None
    except RegionError as exc:
        print(f"pil_palette_diff: {exc}", file=sys.stderr)
        return 2

    def run(path):
        return analyse(
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

    try:
        images = {"a": run(args.image_a)}
        diff = None
        if args.image_b:
            images["b"] = run(args.image_b)
            diff = build_diff(images["a"], images["b"], foreground=args.foreground)
    except RegionError as exc:
        print(f"pil_palette_diff: {exc}", file=sys.stderr)
        return 2

    # interpretation_limits is a static payload field on every other run, but
    # the alpha-related entries only apply -- and must only appear -- when the
    # alpha-weighted path was actually exercised for at least one image, so
    # full-frame and opaque-input --foreground output stay byte-identical to
    # the pre-0.4.0 tree (docs/aaa-build-plan.md A1.1). REGION_INTERPRETATION_
    # LIMITS is NOT gated the same way: unlike the alpha entries it carries no
    # pre-existing byte-identity obligation to protect (--region is new in
    # this release), and A6.2/A6.3 (docs/aaa-build-plan.md #8.3) require a
    # --region run and an equivalent pre-cropped-file run to compare equal on
    # this field too -- only possible if it is unconditional, the same way
    # source_size and parameters.region are unconditional per #8.2.
    alpha_path_used = any(
        img["foreground"].get("coverage_weighted") for img in images.values()
    )
    interpretation_limits = list(INTERPRETATION_LIMITS)
    if alpha_path_used:
        interpretation_limits += ALPHA_INTERPRETATION_LIMITS
    interpretation_limits += REGION_INTERPRETATION_LIMITS

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
