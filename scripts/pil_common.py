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

# Per-pixel luminance delta above which a pixel counts as "changed".
CHANGE_THRESHOLD = 10

# Accent coverage below this fraction is flagged as very small: too few vivid
# pixels for hue statistics to be trustworthy on their own.
ACCENT_AREA_SMALL_FRACTION = 0.005

# --- Foreground separation ---------------------------------------------------
# Constants deliberately mirror tools/synty_asset_index/palette.py so both
# codebases share one definition of "foreground".

# Pixels with alpha below this are background when the file carries real
# transparency (any alpha < 255).
ALPHA_FOREGROUND_MIN = 8

# OKLab distance from the border-median colour within which an opaque pixel
# counts as background.
DEFAULT_BACKGROUND_DELTA = 0.035

# Foreground below this fraction of the frame earns foreground_too_small: thin
# assets leave so few pixels that regional and hue statistics are noisy.
FOREGROUND_MIN_FRACTION = 0.02

# Estimated foreground below this fraction earns background_dominant in default
# (full-frame) mode: the frame's metrics mostly describe the background.
BACKGROUND_DOMINANT_MAX = 0.10

# Minimum support for a hue family to count as *present* in the lost/gained
# verdict logic. Both bounds must hold: the fraction keeps the gate scale
# invariant, the absolute pixel floor keeps a handful of anti-aliased edge
# pixels from flipping the verdict on small foregrounds. The census itself is
# never gated -- these bound only verdict participation.
HUE_PRESENCE_MIN_FRACTION = 0.0002
HUE_PRESENCE_MIN_PIXELS = 10

# Minimum foreground pixels for a grid cell to participate in the structural
# similarity score (counted on the fixed-size working copy, so resolution does
# not move it).
CELL_MIN_SUPPORT_PIXELS = 16


def load_rgb_alpha(path):
    """Open an image; return (rgb, alpha).

    rgb is alpha-flattened onto black, byte-identical to what load_rgb returns,
    so full-frame metrics are unchanged by this loader. alpha is a uint8 HxW
    array, or None when the file is fully opaque -- a file that carries an alpha
    channel but uses no transparency provides no foreground information.
    """
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        alpha = np.asarray(img.getchannel("A"), dtype=np.uint8)
        flat = Image.new("RGBA", img.size, (0, 0, 0, 255))
        flat.alpha_composite(img)
        if not bool((alpha < 255).any()):
            alpha = None
        return flat.convert("RGB"), alpha
    return img.convert("RGB"), None


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


def to_hex(rgb):
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def luminance_array(img):
    """ITU-R 601-2 luma, matching PIL's own RGB->L conversion."""
    return np.asarray(img.convert("L"), dtype=np.float64)


def luminance_stats(img, mask=None):
    lum = luminance_array(img)
    if mask is not None:
        lum = lum[mask]
        if lum.size == 0:
            return {"mean": None, "std": None}
    return {"mean": round(float(lum.mean()), 3), "std": round(float(lum.std()), 3)}


def saturation_stats(img, mask=None):
    sat = np.asarray(img.convert("HSV"), dtype=np.float64)[:, :, 1]
    if mask is not None:
        sat = sat[mask]
        if sat.size == 0:
            return {"mean": None, "std": None}
    return {"mean": round(float(sat.mean()), 3), "std": round(float(sat.std()), 3)}


def entropy_of(img, mask=None):
    """Shannon entropy in bits of the 256-bin luminance histogram."""
    if mask is None:
        hist = np.asarray(img.convert("L").histogram(), dtype=np.float64)
    else:
        lum = np.asarray(img.convert("L"), dtype=np.float64)[mask]
        if lum.size == 0:
            return 0.0
        hist = np.histogram(lum, bins=256, range=(0, 256))[0].astype(np.float64)
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


def quantize_palette(img, n_colors):
    """Extract up to n_colors dominant colours with fractional coverage.

    Coverage is relative to the pixel count of the image passed in -- so for the
    chroma-masked accent image, coverage is a share of accent pixels, not of the
    whole frame.
    """
    if img.width * img.height == 0:
        return []
    quantized = img.quantize(
        colors=n_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    palette = quantized.getpalette() or []
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
    # Stable ordering: heaviest first, hex as tiebreak, so JSON never floats.
    entries.sort(key=lambda e: (-e["coverage"], e["hex"]))
    return entries


def accent_subset(img, sat_min, val_min, within=None):
    """Return (accent-pixels-as-image, fraction) for vivid pixels.

    The returned image is a 1xN strip containing only the masked pixels, which
    lets the same quantiser run over the accent subset alone. With a `within`
    foreground mask, only foreground pixels are considered and the fraction is
    a share of foreground rather than of the frame.
    """
    hsv = np.asarray(img.convert("HSV"))
    rgb = np.asarray(img)
    mask = (hsv[:, :, 1] > sat_min) & (hsv[:, :, 2] > val_min)
    if within is not None:
        mask &= within
        denominator = float(within.sum())
        fraction = (float(mask.sum()) / denominator) if denominator else 0.0
    else:
        fraction = float(mask.mean())
    selected = rgb[mask]
    if selected.size == 0:
        return None, 0.0
    strip = selected.reshape(1, -1, 3).astype(np.uint8)
    return Image.fromarray(strip, "RGB"), fraction


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


def hue_families(img, sat_min, val_min, within=None):
    """Per-hue-family census of the chroma-masked pixels.

    Complements the palettes: quantisation reports *which* colours dominate by
    area, this reports *which hues are present at all*. Both are needed -- an
    accent hue vanishing between two renders is a colour-scheme change even when
    it occupies a fraction of a percent of the frame.

    With a `within` foreground mask, only foreground pixels are considered and
    fraction_of_frame becomes a fraction of the foreground.
    """
    hsv = np.asarray(img.convert("HSV"))
    hue = hsv[:, :, 0].astype(np.int16)
    mask = (hsv[:, :, 1] > sat_min) & (hsv[:, :, 2] > val_min)

    if within is not None:
        mask &= within
        total_px = float(within.sum())
    else:
        total_px = float(hue.size)
    accent_px = float(mask.sum())
    masked_hue = hue[mask]

    out = {}
    for name, ranges in HUE_FAMILIES:
        count = 0
        for lo, hi in ranges:
            count += int(((masked_hue >= lo) & (masked_hue <= hi)).sum())
        frac_accents = (count / accent_px) if accent_px else 0.0
        out[name] = {
            "pixels": count,
            "fraction_of_accents": round(frac_accents, 6),
            "fraction_of_frame": round(count / total_px, 6) if total_px else 0.0,
            "negligible": bool(0.0 < frac_accents < HUE_FAMILY_NEGLIGIBLE),
        }
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


def fractional_cells(img, cols, rows, mask=None):
    """Per-cell statistics over a fractional grid.

    The grid is defined as fractions of the image's own dimensions, so two
    images of different pixel sizes still yield corresponding cells.

    With a foreground mask, statistics run over each cell's foreground pixels
    only and every cell carries its `foreground_pixels` support count; a cell
    with no foreground reports null statistics rather than fabricating numbers
    from pure background. Gradients are still taken on the unmasked image, so
    silhouette edges -- real object structure -- survive.
    """
    lum = luminance_array(img)
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
            cell.update(
                luminance_mean=round(float(lum_cell.mean()), 3),
                luminance_std=round(float(lum_cell.std()), 3),
                edge_mean=round(float(edge_cell.mean()), 3),
                entropy=_cell_entropy(lum_cell),
            )
            cells.append(cell)
    return cells


def _cell_entropy(lum_cell):
    hist, _ = np.histogram(lum_cell, bins=256, range=(0, 256))
    total = hist.sum()
    if total <= 0:
        return 0.0
    p = hist.astype(np.float64) / total
    p = p[p > 0]
    return round(float(-(p * np.log2(p)).sum()), 4)
