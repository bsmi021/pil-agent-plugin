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


def load_rgb(path):
    """Open an image and force RGB, dropping any alpha onto black."""
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGBA", img.size, (0, 0, 0, 255))
        flat.alpha_composite(img)
        return flat.convert("RGB")
    return img.convert("RGB")


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


def luminance_stats(img):
    lum = luminance_array(img)
    return {"mean": round(float(lum.mean()), 3), "std": round(float(lum.std()), 3)}


def saturation_stats(img):
    sat = np.asarray(img.convert("HSV"), dtype=np.float64)[:, :, 1]
    return {"mean": round(float(sat.mean()), 3), "std": round(float(sat.std()), 3)}


def entropy_of(img):
    """Shannon entropy in bits of the 256-bin luminance histogram."""
    hist = np.asarray(img.convert("L").histogram(), dtype=np.float64)
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


def accent_subset(img, sat_min, val_min):
    """Return (accent-pixels-as-image, fraction-of-frame) for vivid pixels.

    The returned image is a 1xN strip containing only the masked pixels, which
    lets the same quantiser run over the accent subset alone.
    """
    hsv = np.asarray(img.convert("HSV"))
    rgb = np.asarray(img)
    mask = (hsv[:, :, 1] > sat_min) & (hsv[:, :, 2] > val_min)
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


def hue_families(img, sat_min, val_min):
    """Per-hue-family census of the chroma-masked pixels.

    Complements the palettes: quantisation reports *which* colours dominate by
    area, this reports *which hues are present at all*. Both are needed -- an
    accent hue vanishing between two renders is a colour-scheme change even when
    it occupies a fraction of a percent of the frame.
    """
    hsv = np.asarray(img.convert("HSV"))
    hue = hsv[:, :, 0].astype(np.int16)
    mask = (hsv[:, :, 1] > sat_min) & (hsv[:, :, 2] > val_min)

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
            "fraction_of_frame": round(count / total_px, 6),
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


def fractional_cells(img, cols, rows):
    """Per-cell statistics over a fractional grid.

    The grid is defined as fractions of the image's own dimensions, so two
    images of different pixel sizes still yield corresponding cells.
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
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "bounds_fractional": [
                        round(col / cols, 4),
                        round(row / rows, 4),
                        round((col + 1) / cols, 4),
                        round((row + 1) / rows, 4),
                    ],
                    "luminance_mean": round(float(lum_cell.mean()), 3),
                    "luminance_std": round(float(lum_cell.std()), 3),
                    "edge_mean": round(float(edge_cell.mean()), 3),
                    "entropy": _cell_entropy(lum_cell),
                }
            )
    return cells


def _cell_entropy(lum_cell):
    hist, _ = np.histogram(lum_cell, bins=256, range=(0, 256))
    total = hist.sum()
    if total <= 0:
        return 0.0
    p = hist.astype(np.float64) / total
    p = p[p > 0]
    return round(float(-(p * np.log2(p)).sum()), 4)
