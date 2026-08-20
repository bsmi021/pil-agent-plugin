"""Shared measurement primitives for the pil-agent-plugin tools.

Design constraints, all of them load-bearing:

*   **Determinism.** Every metric must produce byte-identical JSON across runs.
    Quantisation is pinned to MEDIANCUT (deterministic) with dithering off, and
    every palette is sorted by coverage then hex so ordering never floats.
*   **Scale invariance.** Structural metrics run on a fixed-size working copy and
    a *fractional* grid, so the same layout at two resolutions compares equal.
*   **Accent visibility.** A single global quantisation of a dark image spends its
    whole colour budget on near-black tones and never reports the vivid accents
    that carry the image's perceptual identity. Palettes are therefore always
    extracted twice: once over all pixels, once over a chroma-masked subset.
*   **Foreground honesty.** On an asset render, a shared preview background can
    be ~98% of both frames, so full-frame metrics score two different objects
    as near-identical. Every metric therefore accepts an optional foreground
    mask, derived from alpha when the file carries real transparency and from a
    border-median background colour in OKLab otherwise -- the same definition of
    "visible pixel" as the synty_asset_index descriptor, so the two codebases
    never disagree about what the foreground is.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from pil_color import ciede2000, rgb_to_lab_array, rgb_to_lch_array

# Working size for structural metrics. Both inputs are resampled to this long
# edge so that a rescaled copy of an image yields the same cell statistics.
WORKING_LONG_EDGE = 256

# Blur applied before edge extraction. Without it, edge density is dominated by
# resampling aliasing rather than by real image structure, and scale invariance
# fails for fine repeating detail.
EDGE_PREBLUR_RADIUS = 1.0

# Chroma thresholds (HSV, 0-255) separating "vivid accent" from "dark base".
# Tunable via CLI and always echoed into the output so a reader knows what
# definition of "accent" produced the numbers.
DEFAULT_ACCENT_SAT_MIN = 100
DEFAULT_ACCENT_VAL_MIN = 60

# LCh(ab) alternative to the HSV accent gate: accent iff C* >= chroma floor AND
# L* >= lightness floor. Implemented but NOT yet the default -- selecting it is
# --accent-space lch, and the default flips only once WP2 has calibrated these
# numbers.
#
# These are the research document's C_MIN and L_MIN. They are *reasoned inputs
# pending WP2 calibration*, not measured thresholds: genuine UI neutrals were
# measured at C* ~ 9-13 so a floor of 20 clears them with margin, and an L*
# floor of 20 excludes the dim maroon (#401010, L* = 12.6) that HSV wrongly
# admits as a vivid accent. No upper L* bound: vivid yellow is L* = 97.14 with
# C* = 96.91, so a cap would discard the most saturated colour in sRGB.
#
# Why the measurand changes rather than the tuning: HSV S is max-minus-min over
# max of the *companded* channels, so it stays high as a colour goes to black,
# and HSV V treats "as bright as possible" identically for yellow (L* = 97) and
# blue (L* = 32). There is no monotone relationship between HSV S and C* at all,
# so no choice of sat_min could have fixed either failure.
DEFAULT_ACCENT_CHROMA_MIN = 20.0
DEFAULT_ACCENT_LIGHTNESS_MIN = 20.0

# The accent-mask spaces this module knows how to build. "hsv" is the phase-1
# behaviour and remains the default.
ACCENT_SPACES = ("hsv", "lch")
DEFAULT_ACCENT_SPACE = "hsv"

# Per-pixel luminance delta above which a pixel counts as "changed".
# WP2-calibrated (runs/2026-08-19-phase2-calibration, n=400, alpha=0.01):
# smallest gate whose bootstrap CI upper bound on the controls' changed-area
# stays below 0.001. The phase-1 guess of 10 let re-encode and resample noise
# through as "change".
CHANGE_THRESHOLD = 20

# Accent coverage below this fraction is flagged as very small: too few vivid
# pixels for hue statistics to be trustworthy on their own.
ACCENT_AREA_SMALL_FRACTION = 0.005

# --- Foreground separation ---------------------------------------------------
# Constants deliberately mirror tools/synty_asset_index/palette.py so both
# codebases share one definition of "foreground".

# Pixels with alpha below this are background when the file carries real
# transparency (any alpha < 255).
ALPHA_FOREGROUND_MIN = 8

# Cap on how far un-premultiplying a working-resolution pixel can amplify its
# colour. Derived from ALPHA_FOREGROUND_MIN rather than invented: nothing below
# the membership floor is ever read by a foreground statistic, so the guard
# need only bound amplification at 255/8 ~= 32x for the pixels that ARE read.
# Tying it to the existing constant means this fix adds no new magic number.
UNPREMULTIPLY_COVERAGE_MIN = ALPHA_FOREGROUND_MIN / 255.0

# OKLab distance from the border-median colour within which an opaque pixel
# counts as background.
DEFAULT_BACKGROUND_DELTA = 0.035

# Foreground below this fraction of the frame earns foreground_too_small: thin
# assets leave so few pixels that regional and hue statistics are noisy.
# WP2-calibrated (foreground-fraction sweep, n=6 grid points -- coarse):
# smallest measured share at which foreground-mode control noise stays under
# its own thresholds; below it the mask is unstable and the flag must fire.
FOREGROUND_MIN_FRACTION = 0.022588

# Estimated foreground below this fraction earns background_dominant in default
# (full-frame) mode: the frame's metrics mostly describe the background.
BACKGROUND_DOMINANT_MAX = 0.10

# Minimum support for a hue family to count as *present* in the lost/gained
# verdict logic. Both bounds must hold: the fraction keeps the gate scale
# invariant, the absolute pixel floor keeps a handful of anti-aliased edge
# pixels from flipping the verdict on small foregrounds. The census itself is
# never gated -- these bound only verdict participation.
#
# WP2 calibration confirmed the fraction (joint sweep, n=400, alpha=0.01) and
# recommended lowering the pixel floor to 1. That recommendation is REJECTED,
# with the reason recorded: the synthetic controls are same-scene re-encodes,
# so cross-render anti-aliasing jitter -- the production failure this floor
# exists for, pinned by test_single_stray_pixel_does_not_flip_the_verdict --
# never appears in the negative class, and the sweep measured no benefit to
# lowering (identical false-alarm rate and detection limits at both settings).
HUE_PRESENCE_MIN_FRACTION = 0.0002
HUE_PRESENCE_MIN_PIXELS = 10

# Minimum foreground pixels for a grid cell to participate in the structural
# similarity score (counted on the fixed-size working copy, so resolution does
# not move it). WP2-calibrated (904 control cell pairs bucketed by support):
# smallest bucket whose p99 divergence stays within 2x the best-supported
# bucket's. The phase-0.2.0 guess of 16 admitted cells whose statistics were
# still dominated by sampling noise.
CELL_MIN_SUPPORT_PIXELS = 64


def load_rgba_straight(path):
    """Open an image; return (composited_rgb, straight_rgb, alpha).

    Exists because the two RGB forms answer different questions and the tools
    need both: the composited form is the rendered appearance that hashes and
    pixel diffs must see (alpha-flattened onto black, byte-identical to what
    load_rgb_alpha has always returned), the straight form is the object's own
    un-composited colour that every colour statistic must see. A PNG stores
    straight (non-premultiplied) alpha, so the RGB channels of
    Image.open(path).convert("RGBA") already ARE the object's true colour at
    every partial-coverage pixel -- reading them costs nothing and is exact,
    unlike un-premultiplying the flattened form, which cannot recover the
    colour compositing rounded away (see calibration/alpha_truth.py's
    unpremultiply_recovery for the measured cost of that alternative).

    alpha is a uint8 HxW array, or None when the file is fully opaque -- a file
    that carries an alpha channel but uses no transparency provides no
    foreground information. When alpha is None, straight_rgb IS composited_rgb
    (the same object, not a copy), so a caller can assert that identity.
    """
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        alpha = np.asarray(img.getchannel("A"), dtype=np.uint8)
        flat = Image.new("RGBA", img.size, (0, 0, 0, 255))
        flat.alpha_composite(img)
        composited = flat.convert("RGB")
        if not bool((alpha < 255).any()):
            return composited, composited, None
        straight = img.convert("RGB")
        return composited, straight, alpha
    rgb = img.convert("RGB")
    return rgb, rgb, None


def load_rgb_alpha(path):
    """Open an image; return (rgb, alpha).

    A two-element projection of load_rgba_straight, kept because full-frame
    metrics must never see the straight colour: a straight-RGB full-frame read
    would expose whatever a producer stored under fully-transparent pixels, and
    the composited form is what every full-frame statistic has always used. rgb
    is byte-identical to what this function has always returned.
    """
    composited, _straight, alpha = load_rgba_straight(path)
    return composited, alpha


def load_rgb(path):
    """Open an image and force RGB, dropping any alpha onto black."""
    return load_rgb_alpha(path)[0]


def _srgb_to_linear(channel):
    return np.where(
        channel <= 0.04045, channel / 12.92, ((channel + 0.055) / 1.055) ** 2.4
    )


def rgb_to_oklab_array(rgb_array):
    """Vectorised sRGB (0-255) -> OKLab over a trailing axis of 3.

    The coefficients match synty_asset_index's rgb_to_oklab exactly, so "near
    the background colour" means the same thing to both tools.
    """
    lin = _srgb_to_linear(np.asarray(rgb_array, dtype=np.float64) / 255.0)
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    l_val = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_val = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_val = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_root, m_root, s_root = np.cbrt(l_val), np.cbrt(m_val), np.cbrt(s_val)
    return np.stack(
        [
            0.2104542553 * l_root + 0.7936177850 * m_root - 0.0040720468 * s_root,
            1.9779984951 * l_root - 2.4285922050 * m_root + 0.4505937099 * s_root,
            0.0259040371 * l_root + 0.7827717662 * m_root - 0.8086757660 * s_root,
        ],
        axis=-1,
    )


def _border_samples(rgb):
    """The 8 border sample points (corners + edge midpoints) as an Nx3 array."""
    arr = np.asarray(rgb, dtype=np.uint8)
    h, w = arr.shape[:2]
    coords = sorted(
        {
            (0, 0),
            (w - 1, 0),
            (0, h - 1),
            (w - 1, h - 1),
            (w // 2, 0),
            (w // 2, h - 1),
            (0, h // 2),
            (w - 1, h // 2),
        }
    )
    return np.array([arr[y, x] for x, y in coords], dtype=np.float64)


def estimate_background(rgb):
    """(OKLab, hex) of the median border colour, per synty_asset_index."""
    samples = _border_samples(rgb)
    lab = np.median(rgb_to_oklab_array(samples), axis=0)
    rgb_median = np.median(samples, axis=0)
    return lab, to_hex(tuple(int(round(v)) for v in rgb_median))


def foreground_mask(rgb, alpha, background_delta):
    """Boolean HxW foreground mask, plus provenance.

    Returns (mask, source, background_hex). Real transparency is authoritative
    when present; otherwise opaque pixels within background_delta (OKLab) of the
    border-median colour are background. background_hex is None on the alpha
    path, where no colour estimate is involved.
    """
    if alpha is not None:
        return alpha >= ALPHA_FOREGROUND_MIN, "alpha", None
    lab = rgb_to_oklab_array(np.asarray(rgb, dtype=np.float64))
    background_lab, background_hex = estimate_background(rgb)
    distance = np.sqrt(((lab - background_lab) ** 2).sum(axis=-1))
    return distance > background_delta, "background_estimate", background_hex


def foreground_estimate(rgb, alpha, background_delta):
    """Cheap foreground-fraction hint: (fraction, source, background_hex).

    The alpha path is exact. The colour path runs on the working-size copy --
    good enough for a dominance flag without a full-resolution OKLab pass.
    """
    if alpha is not None:
        mask = alpha >= ALPHA_FOREGROUND_MIN
        return round(float(mask.mean()), 6), "alpha", None
    mask, source, background_hex = foreground_mask(
        to_working(rgb), None, background_delta
    )
    return round(float(mask.mean()), 6), source, background_hex


def mask_bbox(mask):
    """Tight (left, top, right, bottom) around True pixels, or None if empty."""
    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        return None
    return int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1


def masked_strip(img, mask):
    """Masked pixels as a 1xN image so the quantiser can run over a subset."""
    selected = np.asarray(img)[mask]
    if selected.size == 0:
        return None
    return Image.fromarray(selected.reshape(1, -1, 3).astype(np.uint8), "RGB")


def apply_mask(img, mask):
    """Copy of img with background pixels forced to black, so hashes and pixel
    diffs reflect the foreground alone."""
    arr = np.asarray(img).copy()
    arr[~mask] = 0
    return Image.fromarray(arr, "RGB")


def resize_mask(mask, size):
    """Resize a boolean mask to (width, height) without inventing new values."""
    strip = Image.fromarray(mask.astype(np.uint8) * 255, "L")
    return np.asarray(strip.resize(size, Image.NEAREST)) >= 128


def to_working(img):
    """Resample to a fixed long edge, preserving aspect ratio."""
    w, h = img.size
    scale = WORKING_LONG_EDGE / float(max(w, h))
    if scale >= 1.0:
        target = (max(1, round(w * scale)), max(1, round(h * scale)))
    else:
        target = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(target, Image.LANCZOS)


def resize_coverage(alpha, size):
    """NEAREST-resample an alpha channel to working size.

    NEAREST, and not an averaging kernel, because membership at working
    resolution must remain identical to resize_mask's -- both select the same
    source pixel, so `resize_coverage(alpha, s) >= ALPHA_FOREGROUND_MIN` is
    provably the same boolean array as `resize_mask(alpha >= ALPHA_FOREGROUND_MIN, s)`.
    That equality is what keeps the alpha and border-median paths selecting the
    same pixels on a hard-edged object, and is pinned by an acceptance test
    rather than assumed.
    """
    img = Image.fromarray(np.asarray(alpha, dtype=np.uint8), mode="L")
    return np.asarray(img.resize(size, Image.NEAREST), dtype=np.uint8)


def _resample_alpha_lanczos(alpha, size):
    """LANCZOS-resample an alpha channel to working size, as float64.

    Exists only to invert the same kernel that produced the resampled
    premultiplied colour: resize_coverage's NEAREST output answers "is this
    working pixel a member of the mask", this answers "how much does this
    working pixel's colour need dividing down by" -- two different questions
    that must not share one array. float64 throughout, per this module's
    determinism contract: float32 anywhere in this path is a documented failure
    mode (compounding rounding differences across platforms).
    """
    channel = Image.fromarray(np.asarray(alpha, dtype=np.float32), mode="F")
    return np.asarray(channel.resize(size, Image.LANCZOS), dtype=np.float64)


def working_straight_and_weights(composited_subject, alpha_subject, size):
    """(working_straight_rgb, working_coverage) at `size`, from a full-res crop.

    The working copy is a LANCZOS resample, so resampling the straight RGB
    directly would average the object's colour against the transparent
    region's stored RGB and bake a dark halo into the content. The correct
    operation is to resample the *premultiplied* colour -- which is exactly
    what `composited_subject` (composited onto black) already is -- resample
    the coverage with the same kernel, and divide.

    The clip to [0, 255] before dividing is required because LANCZOS has
    negative lobes: the filtered premultiplied colour can overshoot its own
    coverage near a hard edge. The division is guarded by
    UNPREMULTIPLY_COVERAGE_MIN rather than by the raw coverage, so a
    near-zero-coverage working pixel cannot blow the recovered colour up
    arbitrarily; see that constant's comment for the amplification bound this
    buys, and the working-resolution error-bound note in
    docs/aaa-build-plan.md#3.4 for the ~0.5/w_bar code-value cost it does not
    remove.
    """
    working_premultiplied = composited_subject.resize(size, Image.LANCZOS)
    coverage = np.clip(_resample_alpha_lanczos(alpha_subject, size), 0.0, 255.0) / 255.0
    premult_arr = np.asarray(working_premultiplied, dtype=np.float64)
    denom = np.maximum(coverage, UNPREMULTIPLY_COVERAGE_MIN)
    straight = np.clip(premult_arr / denom[..., None], 0.0, 255.0)
    straight_img = Image.fromarray(np.rint(straight).astype(np.uint8), "RGB")
    return straight_img, coverage


def to_hex(rgb):
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def luminance_array(img):
    """ITU-R 601-2 luma, matching PIL's own RGB->L conversion."""
    return np.asarray(img.convert("L"), dtype=np.float64)


def _weighted_mean_std(values, weights):
    w = np.asarray(weights, dtype=np.float64)
    total = float(w.sum())
    mean = float((values * w).sum() / total)
    std = float(np.sqrt((w * (values - mean) ** 2).sum() / total))
    return mean, std


def luminance_stats(img, mask=None, weights=None):
    """Mean/std luminance, coverage-weighted when `weights` is given.

    Takes the existing unweighted branch verbatim when weights is None, so the
    border-median path (and every full-frame call) is untouched -- a
    ones-weighted mean computed as sum(1*x)/sum(1) can differ from x.mean() in
    the last ULP, which is enough to break byte-identical JSON.
    """
    lum = luminance_array(img)
    if mask is not None:
        lum = lum[mask]
        if weights is not None:
            weights = np.asarray(weights, dtype=np.float64)[mask]
        if lum.size == 0:
            return {"mean": None, "std": None}
    if weights is not None:
        mean, std = _weighted_mean_std(lum, weights)
        return {"mean": round(mean, 3), "std": round(std, 3)}
    return {"mean": round(float(lum.mean()), 3), "std": round(float(lum.std()), 3)}


def saturation_stats(img, mask=None, weights=None):
    """Mean/std HSV saturation, coverage-weighted when `weights` is given.

    Same verbatim-when-unweighted guarantee as luminance_stats, for the same
    reason.
    """
    sat = np.asarray(img.convert("HSV"), dtype=np.float64)[:, :, 1]
    if mask is not None:
        sat = sat[mask]
        if weights is not None:
            weights = np.asarray(weights, dtype=np.float64)[mask]
        if sat.size == 0:
            return {"mean": None, "std": None}
    if weights is not None:
        mean, std = _weighted_mean_std(sat, weights)
        return {"mean": round(mean, 3), "std": round(std, 3)}
    return {"mean": round(float(sat.mean()), 3), "std": round(float(sat.std()), 3)}


def entropy_of(img, mask=None, weights=None):
    """Shannon entropy in bits of the 256-bin luminance histogram.

    Coverage-weighted (each pixel contributes alpha/255 of a count to its bin)
    when `weights` is given; takes the existing unweighted branch verbatim
    otherwise, including PIL's own fast `.histogram()` in the no-mask case, so
    the border-median and full-frame paths are untouched.
    """
    if mask is None:
        if weights is None:
            hist = np.asarray(img.convert("L").histogram(), dtype=np.float64)
        else:
            lum = np.asarray(img.convert("L"), dtype=np.float64)
            w = np.asarray(weights, dtype=np.float64)
            hist = np.histogram(
                lum.ravel(), bins=256, range=(0, 256), weights=w.ravel()
            )[0].astype(np.float64)
    else:
        lum = np.asarray(img.convert("L"), dtype=np.float64)[mask]
        if lum.size == 0:
            return 0.0
        if weights is None:
            hist = np.histogram(lum, bins=256, range=(0, 256))[0].astype(np.float64)
        else:
            w = np.asarray(weights, dtype=np.float64)[mask]
            hist = np.histogram(lum, bins=256, range=(0, 256), weights=w)[0].astype(
                np.float64
            )
    total = hist.sum()
    if total <= 0:
        return 0.0
    p = hist / total
    p = p[p > 0]
    return round(float(-(p * np.log2(p)).sum()), 4)


def edge_magnitude(img):
    """Gradient magnitude of the pre-blurred luminance channel.

    A crude 2D complexity proxy. Deliberately NOT a geometry measurement -- see
    the interpretation limits emitted by the tools.
    """
    blurred = img.convert("L").filter(ImageFilter.GaussianBlur(EDGE_PREBLUR_RADIUS))
    lum = np.asarray(blurred, dtype=np.float64)
    if lum.shape[0] < 2 or lum.shape[1] < 2:
        return np.zeros_like(lum)
    gy, gx = np.gradient(lum)
    return np.hypot(gx, gy)


def quantize_palette(img, n_colors, weights=None):
    """Extract up to n_colors dominant colours with fractional coverage.

    Coverage is relative to the pixel count of the image passed in -- so for the
    chroma-masked accent image, coverage is a share of accent pixels, not of the
    whole frame.

    `weights`, when given, must be a 1D array in the same pixel order as `img`'s
    own flattened pixels (the same boolean-mask order `masked_strip`/
    `accent_subset` build their strips in). Image.quantize accepts no weights of
    its own, so the *choice* of cluster centres stays unweighted -- made from
    MEDIANCUT over the straight-RGB strip exactly as before, which is already
    most of the fix, because it means the centres are chosen from the object's
    true colours rather than darkened ones. Only each entry's *coverage* is
    reweighted afterwards, by reading the quantizer's own per-pixel index map
    and computing `bincount(idx, weights=w) / w.sum()` -- exact, one extra
    vectorised pass, no new constant. The residual this leaves (a large
    low-coverage region can still win a palette slot it would not win under
    true-weighted centre selection) is measured, not assumed -- see the W1
    evidence bundle's MEDIANCUT residual comparison.

    When weights is None this takes the existing code path verbatim, including
    `getcolors`, so opaque output stays byte-identical rather than merely
    numerically equal.
    """
    if img.width * img.height == 0:
        return []
    quantized = img.quantize(
        colors=n_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    palette = quantized.getpalette() or []

    if weights is None:
        counts = quantized.getcolors(maxcolors=n_colors * 8) or []
        total = float(img.width * img.height)
        entries = []
        for count, index in counts:
            rgb = tuple(palette[index * 3 : index * 3 + 3])
            if len(rgb) < 3:
                continue
            entries.append(
                {
                    "hex": to_hex(rgb),
                    "rgb": [int(c) for c in rgb],
                    "coverage": round(count / total, 6),
                }
            )
    else:
        idx = np.asarray(quantized).reshape(-1)
        w = np.asarray(weights, dtype=np.float64).reshape(-1)
        total_w = float(w.sum())
        entries = []
        if total_w > 0 and idx.size:
            n_slots = len(palette) // 3
            weighted_counts = np.bincount(idx, weights=w, minlength=n_slots)
            for index in np.unique(idx):
                rgb = tuple(palette[int(index) * 3 : int(index) * 3 + 3])
                if len(rgb) < 3:
                    continue
                entries.append(
                    {
                        "hex": to_hex(rgb),
                        "rgb": [int(c) for c in rgb],
                        "coverage": round(float(weighted_counts[index] / total_w), 6),
                    }
                )
    # Stable ordering: heaviest first, hex as tiebreak, so JSON never floats.
    entries.sort(key=lambda e: (-e["coverage"], e["hex"]))
    return entries


def accent_mask(
    img,
    sat_min,
    val_min,
    space=DEFAULT_ACCENT_SPACE,
    chroma_min=DEFAULT_ACCENT_CHROMA_MIN,
    lightness_min=DEFAULT_ACCENT_LIGHTNESS_MIN,
):
    """Boolean HxW mask of "vivid accent" pixels, in the requested space.

    "hsv" is phase 1's rule and stays the default, thresholds exclusive so the
    numbers it produces are unchanged. "lch" is the WP1 alternative: chroma and
    lightness floors on D65 CIELAB, thresholds inclusive because they are stated
    as `C* >= C_MIN and L* >= L_MIN`. The two are different measurands, not two
    tunings of one -- see DEFAULT_ACCENT_CHROMA_MIN.
    """
    if space == "lch":
        lch = rgb_to_lch_array(np.asarray(img, dtype=np.float64))
        return (lch[:, :, 1] >= chroma_min) & (lch[:, :, 0] >= lightness_min)
    if space != "hsv":
        raise ValueError(
            f"unknown accent space {space!r}; expected one of {ACCENT_SPACES}"
        )
    hsv = np.asarray(img.convert("HSV"))
    return (hsv[:, :, 1] > sat_min) & (hsv[:, :, 2] > val_min)


def accent_subset(
    img,
    sat_min,
    val_min,
    within=None,
    weights=None,
    space=DEFAULT_ACCENT_SPACE,
    chroma_min=DEFAULT_ACCENT_CHROMA_MIN,
    lightness_min=DEFAULT_ACCENT_LIGHTNESS_MIN,
):
    """Return (accent-pixels-as-image, fraction, accent_weights) for vivid pixels.

    The returned image is a 1xN strip containing only the masked pixels, which
    lets the same quantiser run over the accent subset alone. With a `within`
    foreground mask, only foreground pixels are considered and the fraction is
    a share of foreground rather than of the frame. `space` selects which
    definition of "vivid" applies; the foreground intersection is identical
    either way.

    `weights`, when given alongside `within`, makes `fraction` the coverage
    share `sum(w over accent&within) / sum(w over within)` instead of a pixel
    count ratio, matching alpha_truth.weighted_stats' accent_fraction
    convention. `accent_weights` is the same weights array sliced to the
    returned strip's pixel order (or None), for the caller to hand straight to
    quantize_palette.
    """
    rgb = np.asarray(img)
    mask = accent_mask(img, sat_min, val_min, space, chroma_min, lightness_min)
    if within is not None:
        mask &= within
        if weights is not None:
            w = np.asarray(weights, dtype=np.float64)
            denom = float(w[within].sum())
            fraction = (float(w[mask].sum()) / denom) if denom else 0.0
        else:
            denominator = float(within.sum())
            fraction = (float(mask.sum()) / denominator) if denominator else 0.0
    else:
        fraction = float(mask.mean())
    selected = rgb[mask]
    if selected.size == 0:
        return None, 0.0, None
    strip = selected.reshape(1, -1, 3).astype(np.uint8)
    accent_weights = (
        np.asarray(weights, dtype=np.float64)[mask] if weights is not None else None
    )
    return Image.fromarray(strip, "RGB"), fraction, accent_weights


# Hue-family buckets over PIL's 0-255 H channel. Enumerating families separately
# from quantisation is what keeps a small-but-semantically-critical accent (a
# cyan status column occupying 0.5% of the frame) from being swallowed by a
# dominant one (red at 4%+). Bounds are inclusive; red wraps both ends.
HUE_FAMILIES = (
    ("red", ((0, 10), (246, 255))),
    ("orange", ((11, 25),)),
    ("yellow", ((26, 42),)),
    ("green", ((43, 95),)),
    ("cyan", ((96, 130),)),
    ("blue", ((131, 165),)),
    ("purple", ((166, 200),)),
    ("magenta", ((201, 245),)),
)

# A family below this share of accent pixels is reported but marked negligible,
# so a caller can distinguish "absent" from "present but tiny".
HUE_FAMILY_NEGLIGIBLE = 0.01


def hue_families(
    img,
    sat_min,
    val_min,
    within=None,
    weights=None,
    space=DEFAULT_ACCENT_SPACE,
    chroma_min=DEFAULT_ACCENT_CHROMA_MIN,
    lightness_min=DEFAULT_ACCENT_LIGHTNESS_MIN,
):
    """Per-hue-family census of the chroma-masked pixels.

    Complements the palettes: quantisation reports *which* colours dominate by
    area, this reports *which hues are present at all*. Both are needed -- an
    accent hue vanishing between two renders is a colour-scheme change even when
    it occupies a fraction of a percent of the frame.

    With a `within` foreground mask, only foreground pixels are considered and
    fraction_of_frame becomes a fraction of the foreground.

    `space` selects which pixels count as accents. Bucketing itself always runs
    on the HSV hue channel, in both spaces: the research found no authoritative
    CIELAB hue-angle boundary set for basic colour names, and porting the
    current 0-255 HSV bounds into degrees would be worse than doing nothing.
    Deriving LCh boundaries by measurement is WP2's job, so the family names
    stay comparable with phase 1 until then.

    `pixels` is always an integer count, unweighted, because
    HUE_PRESENCE_MIN_PIXELS exists to stop a handful of anti-aliased edge
    pixels flipping a verdict and that gate is a count question. When `weights`
    is given, `fraction_of_accents` / `fraction_of_frame` become coverage
    shares and a `coverage` (sum of weights, float) key is added per family;
    without weights this function's return shape is unchanged from before.
    """
    hsv = np.asarray(img.convert("HSV"))
    hue = hsv[:, :, 0].astype(np.int16)
    mask = accent_mask(img, sat_min, val_min, space, chroma_min, lightness_min)

    if within is not None:
        mask &= within
        total_px = float(within.sum())
        total_w = float(np.asarray(weights, dtype=np.float64)[within].sum()) if weights is not None else None
    else:
        total_px = float(hue.size)
        total_w = float(np.asarray(weights, dtype=np.float64).sum()) if weights is not None else None
    accent_px = float(mask.sum())
    masked_hue = hue[mask]
    masked_weights = np.asarray(weights, dtype=np.float64)[mask] if weights is not None else None
    accent_w = float(masked_weights.sum()) if masked_weights is not None else None

    out = {}
    for name, ranges in HUE_FAMILIES:
        family_mask = np.zeros(masked_hue.shape, dtype=bool)
        for lo, hi in ranges:
            family_mask |= (masked_hue >= lo) & (masked_hue <= hi)
        count = int(family_mask.sum())
        entry = {"pixels": count}
        if masked_weights is not None:
            coverage = float(masked_weights[family_mask].sum())
            frac_accents = (coverage / accent_w) if accent_w else 0.0
            frac_frame = (coverage / total_w) if total_w else 0.0
            entry["coverage"] = round(coverage, 6)
        else:
            frac_accents = (count / accent_px) if accent_px else 0.0
            frac_frame = (count / total_px) if total_px else 0.0
        entry["fraction_of_accents"] = round(frac_accents, 6)
        entry["fraction_of_frame"] = round(frac_frame, 6)
        entry["negligible"] = bool(0.0 < frac_accents < HUE_FAMILY_NEGLIGIBLE)
        out[name] = entry
    return out


def palette_distance(pal_a, pal_b):
    """Coverage-weighted symmetric Chamfer distance in RGB space.

    For each colour in one palette, the distance to its nearest neighbour in the
    other, weighted by coverage; averaged in both directions so the result is
    order-independent. Range is roughly 0 (identical) to ~441 (black vs white).
    """
    if not pal_a or not pal_b:
        return None

    def one_way(src, dst):
        weighted = 0.0
        weight_total = 0.0
        for entry in src:
            ar, ag, ab = entry["rgb"]
            nearest = min(
                ((ar - br) ** 2 + (ag - bg) ** 2 + (ab - bb) ** 2)
                for br, bg, bb in (o["rgb"] for o in dst)
            )
            weighted += (nearest**0.5) * entry["coverage"]
            weight_total += entry["coverage"]
        return weighted / weight_total if weight_total else 0.0

    return round((one_way(pal_a, pal_b) + one_way(pal_b, pal_a)) / 2.0, 4)


def palette_distance_de2000(pal_a, pal_b):
    """The same coverage-weighted symmetric Chamfer distance, but with CIEDE2000
    as the pairwise metric instead of Euclidean RGB.

    Structurally identical to palette_distance -- nearest neighbour in the other
    palette for each entry, weighted by that entry's coverage, averaged in both
    directions so the result is order-independent. Only the distance function
    differs, and that is the whole point: RGB distance is not perceptually
    uniform, which is how phase 1 managed to score a genuinely recoloured image
    as *more* similar than an unchanged rescale.

    Rounded to 4 dp for emission and no further. The value is raw dE00: it is
    not a percentage, it is not bounded at 100, and it carries no verbal band.
    """
    if not pal_a or not pal_b:
        return None

    lab_a = rgb_to_lab_array(np.array([e["rgb"] for e in pal_a], dtype=np.float64))
    lab_b = rgb_to_lab_array(np.array([e["rgb"] for e in pal_b], dtype=np.float64))
    # (len(a), len(b)) pairwise matrix in one vectorised call.
    pairwise = ciede2000(lab_a[:, None, :], lab_b[None, :, :])

    def one_way(nearest, palette):
        weights = np.array([e["coverage"] for e in palette], dtype=np.float64)
        weight_total = weights.sum()
        if weight_total <= 0:
            return 0.0
        return float((nearest * weights).sum() / weight_total)

    a_to_b = one_way(pairwise.min(axis=1), pal_a)
    b_to_a = one_way(pairwise.min(axis=0), pal_b)
    return round((a_to_b + b_to_a) / 2.0, 4)


def _hash_bits(img, size, horizontal_diff):
    small = img.convert("L").resize(size, Image.LANCZOS)
    arr = np.asarray(small, dtype=np.float64)
    if horizontal_diff:
        return arr[:, 1:] > arr[:, :-1]
    return arr > arr.mean()


def dhash(img):
    """Difference hash: 64 bits comparing horizontally adjacent pixels."""
    return _hash_bits(img, (9, 8), True)


def ahash(img):
    """Average hash: 64 bits thresholded at the mean."""
    return _hash_bits(img, (8, 8), False)


def hamming(bits_a, bits_b):
    return int(np.count_nonzero(bits_a != bits_b))


def symmetry_scores(img):
    """Mean absolute difference between each half and its mirrored counterpart."""
    lum = luminance_array(img)
    h, w = lum.shape

    half_w = w // 2
    left = lum[:, :half_w]
    right = np.fliplr(lum[:, w - half_w :])
    lr = float(np.abs(left - right).mean()) if half_w else 0.0

    half_h = h // 2
    top = lum[:half_h, :]
    bottom = np.flipud(lum[h - half_h :, :])
    tb = float(np.abs(top - bottom).mean()) if half_h else 0.0

    return {"left_right_diff": round(lr, 3), "top_bottom_diff": round(tb, 3)}


def parse_grid(spec):
    """Parse a COLSxROWS grid spec, e.g. '4x3' -> (4, 3)."""
    try:
        cols, rows = spec.lower().split("x")
        cols, rows = int(cols), int(rows)
    except ValueError:
        raise SystemExit(f"invalid --grid {spec!r}; expected COLSxROWS such as 4x3")
    if cols < 1 or rows < 1:
        raise SystemExit(f"invalid --grid {spec!r}; dimensions must be >= 1")
    return cols, rows


def fractional_cells(img, cols, rows, mask=None, weights=None, colour_img=None):
    """Per-cell statistics over a fractional grid.

    The grid is defined as fractions of the image's own dimensions, so two
    images of different pixel sizes still yield corresponding cells.

    With a foreground mask, statistics run over each cell's foreground pixels
    only and every cell carries its `foreground_pixels` support count; a cell
    with no foreground reports null statistics rather than fabricating numbers
    from pure background. Gradients are always taken from `img`, unmasked, on
    purpose -- silhouette edges are real object structure, and damping them by
    coverage would make a thin object read as progressively less edgy the
    thinner it gets, which is backwards.

    `weights`, when given, coverage-weights luminance_mean/std and entropy
    (Sum(w*x)/Sum(w)) over each cell's masked pixels, and adds
    `foreground_coverage_fraction` (Sum(w)/cell size) beside `foreground_fraction`.
    `edge_mean` is never weighted -- see the docstring above -- so it is always
    read from `img`, the same source gradients always came from.

    `colour_img`, when given alongside `weights`, is the un-premultiplied
    working-resolution image that luminance/entropy are read from instead of
    `img`. It exists because weighting `img` itself (still composited onto
    black) would double-count the coverage discount: the composited value is
    already colour*coverage, so weighting it again biases the mean *downward
    more* than the unweighted reading (see docs/aaa-build-plan.md#3.11,
    "weighting without un-premultiplying").

    Passing neither `weights` nor `colour_img` takes the existing code path
    verbatim, so the border-median path is untouched.
    """
    lum_source = colour_img if colour_img is not None else img
    lum = luminance_array(lum_source)
    edges = edge_magnitude(img)
    h, w = lum.shape

    cells = []
    for row in range(rows):
        y0, y1 = int(h * row / rows), int(h * (row + 1) / rows)
        for col in range(cols):
            x0, x1 = int(w * col / cols), int(w * (col + 1) / cols)
            lum_cell = lum[y0:y1, x0:x1]
            edge_cell = edges[y0:y1, x0:x1]
            if lum_cell.size == 0:
                continue
            cell = {
                "row": row,
                "col": col,
                "bounds_fractional": [
                    round(col / cols, 4),
                    round(row / rows, 4),
                    round((col + 1) / cols, 4),
                    round((row + 1) / rows, 4),
                ],
            }
            if mask is not None:
                cell_mask = mask[y0:y1, x0:x1]
                support = int(cell_mask.sum())
                cell["foreground_pixels"] = support
                cell["foreground_fraction"] = round(support / lum_cell.size, 6)
                if weights is not None:
                    cell_weights = weights[y0:y1, x0:x1]
                    cell["foreground_coverage_fraction"] = round(
                        float(cell_weights.sum()) / lum_cell.size, 6
                    )
                if support == 0:
                    # No foreground here: emit nulls, never NaN (which json.dump
                    # would happily serialise as invalid JSON).
                    cell.update(
                        luminance_mean=None,
                        luminance_std=None,
                        edge_mean=None,
                        entropy=None,
                    )
                    cells.append(cell)
                    continue
                lum_cell = lum_cell[cell_mask]
                edge_cell = edge_cell[cell_mask]
                if weights is not None:
                    w_cell = weights[y0:y1, x0:x1][cell_mask]
                    lum_mean, lum_std = _weighted_mean_std(lum_cell, w_cell)
                    cell.update(
                        luminance_mean=round(lum_mean, 3),
                        luminance_std=round(lum_std, 3),
                        edge_mean=round(float(edge_cell.mean()), 3),
                        entropy=_cell_entropy(lum_cell, weights=w_cell),
                    )
                    cells.append(cell)
                    continue
            cell.update(
                luminance_mean=round(float(lum_cell.mean()), 3),
                luminance_std=round(float(lum_cell.std()), 3),
                edge_mean=round(float(edge_cell.mean()), 3),
                entropy=_cell_entropy(lum_cell),
            )
            cells.append(cell)
    return cells


def _cell_entropy(lum_cell, weights=None):
    if weights is None:
        hist, _ = np.histogram(lum_cell, bins=256, range=(0, 256))
    else:
        hist, _ = np.histogram(lum_cell, bins=256, range=(0, 256), weights=weights)
    total = hist.sum()
    if total <= 0:
        return 0.0
    p = hist.astype(np.float64) / total
    p = p[p > 0]
    return round(float(-(p * np.log2(p)).sum()), 4)
