#!/usr/bin/env python
"""Projection-profile alignment plus WCAG 2.x contrast-ratio arithmetic.

Two halves living in one CLI, and they are deliberately not the same kind of
measurement -- readers should not assume they were graded the same way:

*   The alignment half is *DEMOTED*. Calibration by
    ``calibration/alignment_gate.py`` measured the sub-pixel-jitter noise floor
    on this repository's no-op control set (rescale_roundtrip, jpeg_reencode,
    small gaussian_blur, small add_noise) across the four synthetic scenes.
    The bootstrap-CI upper bound came out at 64.05 px (ceiled to 65 px), which
    exceeds the 8 px useful ceiling a UI-alignment tool needs. The evidence
    bundle at ``runs/2026-08-20-alignment-discrimination/`` also shows the
    tool reports ``max_pixel_delta = 0`` for every real 1/2/4/8 px translation
    applied to the "busy" and "dark_accent" scenes -- it fails to detect real
    shifts on cluttered or dark-background inputs. See that bundle for n,
    alpha, the observed distribution, and the demotion criterion.

    The alignment subcommand still runs and still emits raw baselines, margins,
    and per-axis pixel/fractional deltas as diagnostic data (those numbers are
    real). The ``aligned_within_tolerance_px`` verdict is emitted alongside a
    ``demoted: true`` flag, the honest 65 px default tolerance derived from
    calibration, and ``interpretation_limits`` that explicitly say the verdict
    is not to be trusted for tight precision. A caller who explicitly passes
    ``--tolerance-px`` below the noise floor is claiming more than the
    calibration measured; the payload flags that with
    ``tolerance_below_noise_floor: true`` rather than silently agreeing.

*   The contrast half is a *fixed public formula* and ships normally.
    Relative luminance from sRGB
    and the ratio ``(L1 + 0.05) / (L2 + 0.05)`` are the WCAG 2.x definition
    (https://www.w3.org/TR/WCAG21/#dfn-relative-luminance,
    https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio); there is nothing to
    calibrate. It is verified against the published landmarks the standard
    itself pins (black vs white = 21.0 exactly) and against the same formula
    hand-computed inside ``tests/test_alignment.py``, the same way
    ``pil_color.py``'s CIEDE2000 is verified against the 34 Sharma reference
    values rather than statistically thresholded.

Usage:
    pil_alignment.py alignment IMAGE_A IMAGE_B [--tolerance-px N]
    pil_alignment.py contrast --colors HEX1 HEX2
    pil_alignment.py contrast --image PATH --region1 R1 --region2 R2
    pil_alignment.py contrast --image PATH --foreground-vs-background

Alignment runs at NATIVE resolution per view (pixel-level alignment is the
point). Both a pixel margin and a frame-fractional margin are reported. When
the two images being compared have DIFFERENT pixel dimensions on the compared
axis, the pixel-tolerance verdict is REFUSED (never silently computed against
mismatched pixel scales); the frame-fractional margins are still reported so a
caller has something to inspect.

Exit codes: 0 on success. 2 on any rejection -- unreadable inputs, malformed
regions or colours, unsupported image modes -- with byte-empty stdout and no
partial output, so a caller reading stdout can never mistake a rejection for a
partial answer.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_common import (  # noqa: E402
    DEFAULT_BACKGROUND_DELTA,
    edge_magnitude,
    foreground_mask,
    load_rgb_alpha,
)
from pil_region import (  # noqa: E402
    RegionError,
    parse_fractional_bbox,
    resolve_pixel_rect,
)

TOOL_VERSION = "0.5.0"

# Default fraction of the projection-profile maximum a row/column must reach to
# count as a baseline. Fixed here rather than left to the caller because the
# gate below measured the noise floor at this fraction; changing it invalidates
# that measurement. Exposed on the CLI as --profile-threshold so someone
# investigating a specific case can inspect the response, at the cost of no
# longer being answered inside the pinned tolerance.
DEFAULT_PROFILE_THRESHOLD = 0.05

# Default pixel tolerance for the "aligned within N px" verdict. Set to the
# honest sub-pixel-jitter noise-floor derived by calibration/alignment_gate.py
# against this repository's no-op control set (rescale_roundtrip,
# jpeg_reencode small-quality-loss, small gaussian_blur, small add_noise) on
# the four synthetic scenes in calibration/scenes.py. The gate publishes the
# bootstrap-CI upper bound on the largest observed absolute margin delta at
# the shipping profile threshold, at alpha = alpha_for(n). Rounded UP to the
# next whole pixel so the tool never claims a tighter tolerance than the
# calibration measured.
#
# 65 is the DEMOTED default. The gate's own useful ceiling for a UI-alignment
# tool is 8 px (roughly one line of body text); 65 px is over 8x that. See
# runs/2026-08-20-alignment-discrimination/derived-thresholds.json for the
# CI numbers (n=160, alpha=0.05, ci_upper=64.05, observed_max=68.0) and for
# the translation-accuracy table showing the tool fails to detect real 1/2/4/
# 8 px shifts on "busy" and "dark_accent" scenes. The alignment half of this
# CLI is therefore demoted (ALIGNMENT_DEMOTED = True); the verdict is emitted
# alongside a demoted flag and interpretation limits that say plainly the
# verdict is not to be trusted for tight precision. The contrast half ships
# normally.
DEFAULT_ALIGNMENT_TOLERANCE_PX = 65

# Whether the alignment half is demoted, mirrored into the payload as
# comparison["demoted"] and prepended to ALIGNMENT_LIMITS. Load-bearing: a
# reader who ignores the docstring but reads the payload still sees it.
ALIGNMENT_DEMOTED = True
ALIGNMENT_DEMOTION_REASON = (
    "Calibration gate runs/2026-08-20-alignment-discrimination/"
    "derived-thresholds.json measured a sub-pixel-jitter noise floor of 65 px "
    "(bootstrap CI upper bound 64.05, observed max 68.0, n=160, alpha=0.05), "
    "over 8x the useful ceiling for UI-alignment work (8 px, ~one line of "
    "body text). The same bundle's translation_accuracy table also shows the "
    "tool fails to recover real 1/2/4/8 px shifts on the 'busy' and "
    "'dark_accent' scenes. The 'aligned_within_tolerance_px' verdict is "
    "therefore not to be trusted for tight precision; raw baselines, margins "
    "and pixel/fractional deltas remain useful diagnostic data."
)

ALIGNMENT_LIMITS = [
    "DEMOTED: the alignment half of this tool is demoted per "
    "runs/2026-08-20-alignment-discrimination/derived-thresholds.json. The "
    "sub-pixel-jitter noise floor derived under no-op perturbations is 65 px "
    "(bootstrap CI upper bound 64.05, observed max 68.0, n=160, alpha=0.05), "
    "which exceeds the 8 px useful ceiling for a UI-alignment tool. The "
    "'aligned_within_tolerance_px' verdict is still emitted (with demoted: "
    "true) so callers can see it, but is not to be trusted for tight "
    "precision; setting --tolerance-px below 65 will flag "
    "tolerance_below_noise_floor: true rather than silently agree.",
    "DEMOTED: the same bundle's translation_accuracy table shows the tool "
    "reports max_pixel_delta = 0 for every 1/2/4/8 px translation applied to "
    "the 'busy' and 'dark_accent' scenes -- it fails to detect real shifts on "
    "cluttered or dark-background inputs. Real shifts on 'structured' and "
    "'thin_object' scenes are recovered.",
    "Alignment margins are read from edge-map projection profiles at NATIVE "
    "resolution per view; the compared axis's pixel dimensions must match for "
    "'aligned within N px' to be meaningful. When they do not, that verdict is "
    "REFUSED and only the frame-fractional margins are reported.",
    "Baseline positions record the outermost rows and columns whose edge-map "
    "projection reaches DEFAULT_PROFILE_THRESHOLD * max(profile). They are a "
    "silhouette bracket, not an object-part detector; changing "
    "--profile-threshold changes what the tool considers a baseline. "
    "pil_common.edge_magnitude applies a Gaussian pre-blur before gradient, "
    "so baselines can sit a few pixels outside the true object edge -- see "
    "tests/test_alignment.py for the observed spread on a known fixture.",
    "An empty projection profile (fully flat image, or one whose edge map "
    "collapses to zero) reports null baselines/margins with the "
    "profile_empty flag set. This is not a rejection; a caller reading the "
    "payload sees the mask reason.",
]

CONTRAST_LIMITS = [
    "Contrast ratios are the WCAG 2.x formula "
    "(https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio): relative luminance "
    "from sRGB with the WCAG piecewise linearisation "
    "(threshold 0.03928, not the sRGB standard's 0.04045 -- WCAG pins the "
    "historical rounded value), then (L1 + 0.05) / (L2 + 0.05) with the "
    "brighter side as L1. The ratio range is [1.0, 21.0]; 21.0 is exact for "
    "black-versus-white.",
    "Region-mean colours are the linear mean of the pixel RGB values inside "
    "each region at NATIVE resolution. This is a working colour, not the "
    "region's dominant palette entry -- a bimodal region reports the average "
    "of the two modes, which is neither. Use --colors for a caller-supplied "
    "pair when the region is unlikely to be unimodal.",
    "Foreground-versus-background uses pil_common.foreground_mask -- alpha "
    "when the file carries real transparency, the border-median colour "
    "otherwise. On an image with no foreground (fully transparent or "
    "border-median-dominant) or no background (fully covered), the pair is "
    "REFUSED with the foreground_mask_empty or background_mask_empty flag "
    "rather than reporting a fabricated ratio.",
    "WCAG contrast is a pixel-colour property and carries no scale-invariance "
    "question; nothing here depends on image dimensions matching.",
]

# Every rejection path in the tool raises this and only this. main() is the
# single place that catches, writes stderr, and returns exit 2 with byte-empty
# stdout and no partial file, so the rejection contract holds by construction
# rather than by convention repeated at each call site.


class AlignmentRejected(Exception):
    """Mark a failure that must exit 2 with byte-empty stdout."""


HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _load_image_bytes(path, display_path):
    """Read a source file into memory; used for provenance sha256 and for a
    single decoder pass. Rejects unreadable files as AlignmentRejected."""
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise AlignmentRejected(
            f"cannot read image {display_path!r}: {exc}"
        ) from exc


def _decode_rgb_only(source_bytes, display_path):
    """Decode source bytes to an RGB PIL image at native resolution.

    Alpha is dropped onto black (the same rule as pil_common.load_rgb): the
    alignment half only cares about visible structure, and having two decoder
    paths (with/without alpha) would double the surface for silent behaviour
    drift.
    """
    try:
        img = Image.open(io.BytesIO(source_bytes))
        img.load()
    except (OSError, ValueError) as exc:
        raise AlignmentRejected(
            f"cannot decode image {display_path!r}: {exc}"
        ) from exc
    if img.mode == "F" or img.mode == "I" or img.mode.startswith("I;"):
        raise AlignmentRejected(
            f"unsupported high-bit-depth image mode {img.mode!r}; "
            "pil_alignment accepts 8-bit pixels only and will not itself "
            "clip or rescale them"
        )
    return img.convert("RGB")


def _decode_rgba_with_alpha(path, display_path):
    """Load an RGB image and its optional alpha channel for the fg/bg contrast
    variant. Delegates to pil_common.load_rgb_alpha so the foreground mask
    definition here matches every other tool's."""
    try:
        return load_rgb_alpha(path)
    except (OSError, ValueError) as exc:
        raise AlignmentRejected(
            f"cannot read image {display_path!r}: {exc}"
        ) from exc


# --- Projection-profile alignment --------------------------------------------


def profile_from_image(img, threshold_fraction):
    """Compute the row/column projection profiles and their baseline positions.

    Returns a dict with baselines, margins, and diagnostic profile maxima, or
    None when the profile has no signal at all (fully flat image, edge map
    collapsed to zero, or a degenerate 1-pixel dimension).

    edge_magnitude is reused verbatim from pil_common; that function's own
    pre-blur radius and gradient kernel are load-bearing to every metric that
    reads edges, and reimplementing the operator here would let this tool
    diverge from pil_structure_diff about what an edge is.
    """
    edges = edge_magnitude(img)
    h, w = edges.shape
    if h < 2 or w < 2:
        return None
    row_prof = edges.sum(axis=1)
    col_prof = edges.sum(axis=0)
    row_max = float(row_prof.max())
    col_max = float(col_prof.max())
    if row_max <= 0.0 or col_max <= 0.0:
        return None
    row_hits = np.where(row_prof >= row_max * threshold_fraction)[0]
    col_hits = np.where(col_prof >= col_max * threshold_fraction)[0]
    if row_hits.size == 0 or col_hits.size == 0:
        return None
    top = int(row_hits[0])
    bottom = int(row_hits[-1])
    left = int(col_hits[0])
    right = int(col_hits[-1])
    return {
        "width_px": int(w),
        "height_px": int(h),
        "top_baseline_row": top,
        "bottom_baseline_row": bottom,
        "left_baseline_col": left,
        "right_baseline_col": right,
        "top_margin_px": top,
        "bottom_margin_px": int(h - 1 - bottom),
        "left_margin_px": left,
        "right_margin_px": int(w - 1 - right),
        "top_margin_fractional": round(top / h, 6),
        "bottom_margin_fractional": round((h - 1 - bottom) / h, 6),
        "left_margin_fractional": round(left / w, 6),
        "right_margin_fractional": round((w - 1 - right) / w, 6),
        "row_profile_max": round(row_max, 4),
        "col_profile_max": round(col_max, 4),
    }


def compare_alignment(profile_a, profile_b, tolerance_px):
    """Diff two profiles and produce the "aligned within N px" verdict.

    The verdict is per-axis: left/right margins are on the width axis, top/
    bottom margins on the height axis. If the two images disagree on a compared
    axis's pixel dimension, that axis's pixel-tolerance verdict is REFUSED --
    a pixel delta between two coordinate systems of different physical extents
    is not a meaningful number. The fractional margins are always emitted
    (that comparison is dimension-independent), so a caller downgraded to
    frame-fractional still gets a per-side value.
    """
    if profile_a is None or profile_b is None:
        return {
            "verdict": None,
            "verdict_reason": "one or both projection profiles were empty; "
            "no baselines to compare",
            "profile_empty": True,
            "demoted": ALIGNMENT_DEMOTED,
            "demotion_reason": (
                ALIGNMENT_DEMOTION_REASON if ALIGNMENT_DEMOTED else None
            ),
            "tolerance_below_noise_floor": bool(
                int(tolerance_px) < DEFAULT_ALIGNMENT_TOLERANCE_PX
            ),
        }

    width_matches = profile_a["width_px"] == profile_b["width_px"]
    height_matches = profile_a["height_px"] == profile_b["height_px"]

    axis_details = {}
    for axis, sides, matches in (
        ("width", ("left", "right"), width_matches),
        ("height", ("top", "bottom"), height_matches),
    ):
        side_details = {}
        max_px_delta = 0
        max_frac_delta = 0.0
        for side in sides:
            px_key = f"{side}_margin_px"
            frac_key = f"{side}_margin_fractional"
            px_delta = int(abs(profile_a[px_key] - profile_b[px_key]))
            frac_delta = round(
                abs(profile_a[frac_key] - profile_b[frac_key]), 6
            )
            side_details[side] = {
                "margin_a_px": profile_a[px_key],
                "margin_b_px": profile_b[px_key],
                "margin_a_fractional": profile_a[frac_key],
                "margin_b_fractional": profile_b[frac_key],
                "pixel_delta": px_delta if matches else None,
                "fractional_delta": frac_delta,
            }
            if matches and px_delta > max_px_delta:
                max_px_delta = px_delta
            if frac_delta > max_frac_delta:
                max_frac_delta = frac_delta
        axis_details[axis] = {
            "dimensions_match": matches,
            "dimension_a_px": profile_a["width_px"]
            if axis == "width"
            else profile_a["height_px"],
            "dimension_b_px": profile_b["width_px"]
            if axis == "width"
            else profile_b["height_px"],
            "sides": side_details,
            "max_pixel_delta": max_px_delta if matches else None,
            "max_fractional_delta": round(max_frac_delta, 6),
            "aligned_within_tolerance_px": None
            if not matches
            else bool(max_px_delta <= tolerance_px),
            "pixel_tolerance_verdict_reason": None
            if matches
            else (
                f"refused: {axis} differs between images "
                f"({profile_a['width_px' if axis == 'width' else 'height_px']} "
                f"vs {profile_b['width_px' if axis == 'width' else 'height_px']}), "
                "pixel-tolerance comparison is not meaningful across mismatched "
                "pixel scales"
            ),
        }

    both_axes_comparable = width_matches and height_matches
    if both_axes_comparable:
        overall_px = max(
            axis_details["width"]["max_pixel_delta"],
            axis_details["height"]["max_pixel_delta"],
        )
        verdict = bool(overall_px <= tolerance_px)
        reason = f"max pixel delta {overall_px} vs tolerance {tolerance_px}"
    else:
        verdict = None
        differing = [a for a, matches in (
            ("width", width_matches), ("height", height_matches),
        ) if not matches]
        reason = (
            "refused: pixel-tolerance verdict is only meaningful when both "
            f"compared axes have equal pixel dimensions; differing: {differing}. "
            "Frame-fractional margins are still reported per axis."
        )

    return {
        "verdict": verdict,
        "verdict_reason": reason,
        "tolerance_px": int(tolerance_px),
        "tolerance_below_noise_floor": bool(
            int(tolerance_px) < DEFAULT_ALIGNMENT_TOLERANCE_PX
        ),
        "demoted": ALIGNMENT_DEMOTED,
        "demotion_reason": (
            ALIGNMENT_DEMOTION_REASON if ALIGNMENT_DEMOTED else None
        ),
        "max_pixel_delta": (
            max(
                axis_details["width"]["max_pixel_delta"] or 0,
                axis_details["height"]["max_pixel_delta"] or 0,
            )
            if both_axes_comparable
            else None
        ),
        "max_fractional_delta": round(
            max(
                axis_details["width"]["max_fractional_delta"],
                axis_details["height"]["max_fractional_delta"],
            ),
            6,
        ),
        "axes": axis_details,
        "profile_empty": False,
    }


def _alignment_payload(args, source_bytes_a, source_bytes_b, img_a, img_b,
                       profile_a, profile_b, comparison):
    return {
        "tool": "pil_alignment",
        "version": TOOL_VERSION,
        "mode": "alignment",
        "parameters": {
            "profile_threshold": round(args.profile_threshold, 6),
            "tolerance_px": int(args.tolerance_px),
        },
        "images": {
            "a": {
                "path": str(args.image_a),
                "size": list(img_a.size),
                "sha256": _sha256(source_bytes_a),
            },
            "b": {
                "path": str(args.image_b),
                "size": list(img_b.size),
                "sha256": _sha256(source_bytes_b),
            },
        },
        "profiles": {"a": profile_a, "b": profile_b},
        "comparison": comparison,
        "interpretation_limits": ALIGNMENT_LIMITS,
    }


def do_alignment(args):
    source_bytes_a = _load_image_bytes(args.image_a, args.image_a)
    source_bytes_b = _load_image_bytes(args.image_b, args.image_b)
    img_a = _decode_rgb_only(source_bytes_a, args.image_a)
    img_b = _decode_rgb_only(source_bytes_b, args.image_b)

    if args.profile_threshold <= 0.0 or args.profile_threshold >= 1.0:
        raise AlignmentRejected(
            f"--profile-threshold must be in (0.0, 1.0), got "
            f"{args.profile_threshold}"
        )
    if args.tolerance_px < 0:
        raise AlignmentRejected(
            f"--tolerance-px must be >= 0, got {args.tolerance_px}"
        )

    profile_a = profile_from_image(img_a, args.profile_threshold)
    profile_b = profile_from_image(img_b, args.profile_threshold)
    comparison = compare_alignment(profile_a, profile_b, args.tolerance_px)
    return _alignment_payload(
        args, source_bytes_a, source_bytes_b, img_a, img_b,
        profile_a, profile_b, comparison,
    )


# --- WCAG contrast -----------------------------------------------------------


def _parse_hex_colour(text, argname):
    """Parse a 6-hex-digit sRGB colour. Refuses shorthand and CSS names -- one
    accepted spelling keeps the payload deterministic and the failure mode
    obvious."""
    if not isinstance(text, str) or not HEX_RE.match(text):
        raise AlignmentRejected(
            f"{argname}: expected a 6-hex-digit sRGB colour "
            f"(e.g. '#a1b2c3' or 'a1b2c3'), got {text!r}"
        )
    stripped = text.lstrip("#")
    return (
        int(stripped[0:2], 16),
        int(stripped[2:4], 16),
        int(stripped[4:6], 16),
    )


def _to_hex(rgb):
    r, g, b = (int(round(v)) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def wcag_channel_linear(c_srgb_01):
    """WCAG 2.x linearisation of one sRGB channel already normalised to
    [0, 1].

    The threshold is 0.03928 exactly as pinned by
    https://www.w3.org/TR/WCAG21/#dfn-relative-luminance. The sRGB *standard*
    uses 0.04045; WCAG never re-derived that constant from the newer piecewise
    definition, and reproducing WCAG's own worked examples requires WCAG's own
    threshold. Reusing pil_color.srgb_to_linear here would silently switch to
    the sRGB threshold and drift by a fraction of a hundredth in the deep
    shadows, which is small in absolute terms but breaks the "matches the
    published table" test.
    """
    if c_srgb_01 <= 0.03928:
        return c_srgb_01 / 12.92
    return ((c_srgb_01 + 0.055) / 1.055) ** 2.4


def wcag_relative_luminance(rgb):
    """WCAG relative luminance L for an sRGB colour in 0-255 (per-channel)."""
    r, g, b = (float(c) / 255.0 for c in rgb)
    r_lin = wcag_channel_linear(r)
    g_lin = wcag_channel_linear(g)
    b_lin = wcag_channel_linear(b)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def wcag_contrast_ratio(rgb_a, rgb_b):
    """Contrast ratio (L1 + 0.05) / (L2 + 0.05), brighter side as L1."""
    l_a = wcag_relative_luminance(rgb_a)
    l_b = wcag_relative_luminance(rgb_b)
    if l_a >= l_b:
        return (l_a + 0.05) / (l_b + 0.05), l_a, l_b
    return (l_b + 0.05) / (l_a + 0.05), l_b, l_a


def _region_mean_colour(img, region_text, argname):
    """Mean RGB of a fractional-bbox region at native resolution."""
    try:
        box = parse_fractional_bbox(region_text)
    except RegionError as exc:
        raise AlignmentRejected(f"{argname}: {exc}") from exc
    try:
        rect = resolve_pixel_rect(box, img.size)
    except RegionError as exc:
        raise AlignmentRejected(f"{argname}: {exc}") from exc
    crop = img.crop(rect)
    arr = np.asarray(crop, dtype=np.float64)
    if arr.size == 0:
        raise AlignmentRejected(
            f"{argname}: region resolved to a zero-pixel rectangle"
        )
    mean_rgb = arr.reshape(-1, 3).mean(axis=0)
    return tuple(float(c) for c in mean_rgb), list(rect), box


def _foreground_and_background_mean(path):
    """Mean fg and mean bg RGB of one image, per pil_common's mask rule.

    Rejects (rather than fabricates) when either side is empty.
    """
    rgb, alpha = _decode_rgba_with_alpha(path, path)
    mask, source, background_hex = foreground_mask(
        rgb, alpha, DEFAULT_BACKGROUND_DELTA,
    )
    arr = np.asarray(rgb, dtype=np.float64)
    fg_pixels = arr[mask]
    bg_pixels = arr[~mask]
    if fg_pixels.size == 0:
        raise AlignmentRejected(
            "foreground_mask_empty: no foreground pixels in image, "
            "cannot compute foreground/background contrast"
        )
    if bg_pixels.size == 0:
        raise AlignmentRejected(
            "background_mask_empty: no background pixels in image, "
            "cannot compute foreground/background contrast"
        )
    fg_mean = tuple(float(c) for c in fg_pixels.mean(axis=0))
    bg_mean = tuple(float(c) for c in bg_pixels.mean(axis=0))
    return {
        "foreground_rgb": [round(c, 3) for c in fg_mean],
        "background_rgb": [round(c, 3) for c in bg_mean],
        "foreground_hex": _to_hex(fg_mean),
        "background_hex": _to_hex(bg_mean),
        "foreground_pixels": int(fg_pixels.shape[0]),
        "background_pixels": int(bg_pixels.shape[0]),
        "mask_source": source,
        "background_estimated_hex": background_hex,
    }, fg_mean, bg_mean


def _contrast_payload(variant, inputs, colour_a, colour_b, ratio, l_bright,
                      l_dark, hex_a=None, hex_b=None):
    return {
        "tool": "pil_alignment",
        "version": TOOL_VERSION,
        "mode": "contrast",
        "parameters": {"variant": variant, **inputs},
        "colours": {
            "a": {
                "rgb": [round(c, 3) for c in colour_a],
                "hex": hex_a or _to_hex(colour_a),
                "relative_luminance": round(
                    wcag_relative_luminance(colour_a), 8
                ),
            },
            "b": {
                "rgb": [round(c, 3) for c in colour_b],
                "hex": hex_b or _to_hex(colour_b),
                "relative_luminance": round(
                    wcag_relative_luminance(colour_b), 8
                ),
            },
        },
        "contrast_ratio": round(float(ratio), 6),
        "brighter_relative_luminance": round(float(l_bright), 8),
        "darker_relative_luminance": round(float(l_dark), 8),
        "wcag_thresholds": {
            "AA_normal_text": 4.5,
            "AA_large_text": 3.0,
            "AAA_normal_text": 7.0,
            "AAA_large_text": 4.5,
            "AA_non_text": 3.0,
        },
        "meets": {
            "AA_normal_text": bool(ratio >= 4.5),
            "AA_large_text": bool(ratio >= 3.0),
            "AAA_normal_text": bool(ratio >= 7.0),
            "AAA_large_text": bool(ratio >= 4.5),
            "AA_non_text": bool(ratio >= 3.0),
        },
        "interpretation_limits": CONTRAST_LIMITS,
    }


def do_contrast(args):
    if args.colors:
        if args.image or args.region1 or args.region2 or args.foreground_vs_background:
            raise AlignmentRejected(
                "contrast --colors is exclusive with --image and its options"
            )
        if len(args.colors) != 2:
            raise AlignmentRejected(
                f"contrast --colors takes exactly two hex colours, got "
                f"{len(args.colors)}"
            )
        rgb_a = _parse_hex_colour(args.colors[0], "--colors first")
        rgb_b = _parse_hex_colour(args.colors[1], "--colors second")
        ratio, l_bright, l_dark = wcag_contrast_ratio(rgb_a, rgb_b)
        return _contrast_payload(
            "colors",
            {"color_a": args.colors[0], "color_b": args.colors[1]},
            rgb_a, rgb_b, ratio, l_bright, l_dark,
            hex_a=_to_hex(rgb_a), hex_b=_to_hex(rgb_b),
        )

    if not args.image:
        raise AlignmentRejected(
            "contrast requires either --colors HEX1 HEX2, or "
            "--image PATH with --region1/--region2 or "
            "--foreground-vs-background"
        )

    source_bytes = _load_image_bytes(args.image, args.image)
    img = _decode_rgb_only(source_bytes, args.image)

    if args.foreground_vs_background:
        if args.region1 or args.region2:
            raise AlignmentRejected(
                "contrast --foreground-vs-background is exclusive with "
                "--region1/--region2"
            )
        fg_bg, fg_rgb, bg_rgb = _foreground_and_background_mean(args.image)
        ratio, l_bright, l_dark = wcag_contrast_ratio(fg_rgb, bg_rgb)
        payload = _contrast_payload(
            "foreground_vs_background",
            {
                "image": str(args.image),
                "background_delta": DEFAULT_BACKGROUND_DELTA,
            },
            fg_rgb, bg_rgb, ratio, l_bright, l_dark,
            hex_a=_to_hex(fg_rgb), hex_b=_to_hex(bg_rgb),
        )
        payload["foreground_background"] = fg_bg
        payload["images"] = {
            "path": str(args.image),
            "size": list(img.size),
            "sha256": _sha256(source_bytes),
        }
        return payload

    if not (args.region1 and args.region2):
        raise AlignmentRejected(
            "contrast --image requires --region1 R1 --region2 R2 (or "
            "--foreground-vs-background)"
        )
    rgb_a, rect_a, box_a = _region_mean_colour(img, args.region1, "--region1")
    rgb_b, rect_b, box_b = _region_mean_colour(img, args.region2, "--region2")
    ratio, l_bright, l_dark = wcag_contrast_ratio(rgb_a, rgb_b)
    payload = _contrast_payload(
        "regions",
        {
            "image": str(args.image),
            "region1": args.region1,
            "region2": args.region2,
        },
        rgb_a, rgb_b, ratio, l_bright, l_dark,
        hex_a=_to_hex(rgb_a), hex_b=_to_hex(rgb_b),
    )
    payload["regions"] = {
        "region1": {
            "requested_fractional": box_a,
            "resolved_pixel_rect": rect_a,
        },
        "region2": {
            "requested_fractional": box_b,
            "resolved_pixel_rect": rect_b,
        },
    }
    payload["images"] = {
        "path": str(args.image),
        "size": list(img.size),
        "sha256": _sha256(source_bytes),
    }
    return payload


# --- CLI ---------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Projection-profile alignment and WCAG contrast ratio. "
            "The alignment half is discrimination-gated; the contrast half is "
            "the fixed WCAG 2.x formula."
        ),
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    align = sub.add_parser(
        "alignment",
        help="Compare projection-profile margins between two images.",
    )
    align.add_argument("image_a", help="first image path")
    align.add_argument("image_b", help="second image path")
    align.add_argument(
        "--tolerance-px",
        type=int,
        default=DEFAULT_ALIGNMENT_TOLERANCE_PX,
        help=(
            f"pixel tolerance for the 'aligned within N px' verdict "
            f"(DEMOTED default {DEFAULT_ALIGNMENT_TOLERANCE_PX}, derived by "
            "runs/2026-08-20-alignment-discrimination/derived-thresholds.json; "
            "the alignment half is demoted -- see comparison.demotion_reason "
            "in the payload. Setting this below the demoted default flags "
            "tolerance_below_noise_floor: true rather than silently agreeing.)"
        ),
    )
    align.add_argument(
        "--profile-threshold",
        type=float,
        default=DEFAULT_PROFILE_THRESHOLD,
        help=(
            f"fraction of the projection-profile maximum a row/column must "
            f"reach to count as a baseline (default {DEFAULT_PROFILE_THRESHOLD})"
        ),
    )

    contrast = sub.add_parser(
        "contrast", help="WCAG 2.x contrast ratio for two colours or regions.",
    )
    contrast.add_argument(
        "--colors",
        nargs=2,
        metavar=("HEX1", "HEX2"),
        help="two 6-hex-digit sRGB colours (e.g. '#ffffff' '#000000')",
    )
    contrast.add_argument(
        "--image",
        help="image path for region- or foreground-based contrast",
    )
    contrast.add_argument(
        "--region1",
        help="fractional bbox for the first region (with --image)",
    )
    contrast.add_argument(
        "--region2",
        help="fractional bbox for the second region (with --image)",
    )
    contrast.add_argument(
        "--foreground-vs-background",
        action="store_true",
        help=(
            "compute contrast between the mean foreground and mean "
            "background colour of --image, per pil_common.foreground_mask"
        ),
    )

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    try:
        if args.mode == "alignment":
            payload = do_alignment(args)
        elif args.mode == "contrast":
            payload = do_contrast(args)
        else:  # pragma: no cover -- argparse rejects unknown subcommands
            raise AlignmentRejected(f"unknown mode {args.mode!r}")
    except AlignmentRejected as exc:
        print(f"pil_alignment: {exc}", file=sys.stderr)
        return 2

    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
