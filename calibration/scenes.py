#!/usr/bin/env python
"""Deterministic base scenes for WP2 threshold calibration.

Four scenes, chosen because the bundled tools measurably behave differently on
each of them:

*   ``dark_accent``  -- near-black bulk with small vivid accents. The case that
    defeats area-weighted quantisation (phase 1's central finding) and the only
    one where the hue census carries the verdict.
*   ``structured``   -- blocks plus stripes. Gives the fractional grid real
    per-cell contrast, so ``structural_similarity`` has something to lose.
*   ``thin_object``  -- a sword-like object on a flat preview backdrop at ~2% of
    the frame. The foreground-separation case: full-frame metrics here are
    arithmetically dominated by the shared background.
*   ``busy``         -- full-frame high-entropy content. The opposite extreme:
    every metric has a large, noisy operating point.

Every scene is a pure function of ``(size, seed, ...)``. Randomness comes from
the conftest-style LCG or from ``numpy.random.Generator(PCG64(seed))`` -- never
from ``random`` global state -- so two runs of the calibration produce
byte-identical PNGs and therefore byte-identical measurements.

Sizes are held at 384x288 (long edge 384 <= 512) to stay inside WP2's runtime
budget. The structural tools resample to a 256px long edge internally, so the
extra resolution would buy nothing anyway.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

# Long edge 384: under the 512 budget, comfortably above the tools' 256px
# working size so the resample step is exercised rather than skipped.
SCENE_SIZE = (384, 288)

# Base-scene seeds. Five for the control set (so the noise floor is estimated
# from 5 independent draws per scene rather than 1), two for the perturbation
# grid (where the magnitude axis, not the seed axis, carries the information).
CONTROL_SEEDS = (101, 211, 337, 449, 563)
GRID_SEEDS = (101, 211)

# Hue representatives copied verbatim from tests/conftest.py, where each was
# verified against PIL's own HSV conversion. Reusing them keeps the calibration
# corpus and the test corpus talking about the same hue families.
HUE_SAMPLES = {
    "red": (200, 30, 30),  # H=0
    "red_wrap": (255, 0, 30),  # H=250, upper branch of the wrapped red family
    "orange": (255, 128, 0),  # H=21
    "yellow": (255, 212, 0),  # H=35
    "green": (0, 255, 0),  # H=85
    "cyan": (0, 255, 255),  # H=127
    "blue": (0, 128, 255),  # H=148
    "purple": (128, 0, 255),  # H=191
    "magenta": (255, 0, 200),  # H=221
}

# Order used when laying out accent patches: red is the dominant family (as in
# the real reference image), the rest are secondaries.
ACCENT_DOMINANT = "red"
ACCENT_SECONDARIES = (
    "orange",
    "yellow",
    "green",
    "cyan",
    "blue",
    "purple",
    "magenta",
)

PREVIEW_BG = (24, 26, 30)
SWORD_BLADE = (168, 172, 180)
SWORD_GRIP = (90, 60, 25)


def lcg(seed):
    """Tiny deterministic PRNG, identical to tests/conftest.py's ``_lcg``.

    Kept byte-compatible with the test fixture on purpose: a scene built here
    with the same seed and parameters is the same image the suite would build.
    """
    state = seed
    while True:
        state = (1103515245 * state + 12345) % (2**31)
        yield state / (2**31)


def _rng(seed):
    """Seeded numpy generator. PCG64 explicitly, so the stream is pinned by the
    algorithm as well as by the seed."""
    return np.random.Generator(np.random.PCG64(seed))


def _dark_bands(draw, size, seed, band_h=4, ceiling=40):
    """Near-black horizontal bands spanning many distinct dark shades.

    This is what makes a global 8-colour quantisation spend its entire budget on
    dark tones -- the failure phase 1 discovered on the real reference image.
    """
    rnd = lcg(seed)
    for y in range(0, size[1], band_h):
        shade = int(next(rnd) * ceiling)
        draw.rectangle([0, y, size[0], y + band_h], fill=(shade, shade, shade + 3))


def dark_accent(size=SCENE_SIZE, seed=101, accent_fraction=0.06):
    """Near-black base with vivid accent patches covering ``accent_fraction``.

    The dominant hue takes half the accent budget and the seven secondaries
    share the rest, reproducing the reference image's shape: one accent that
    survives quantisation and several that only the hue census can see.

    ``accent_fraction`` is the knob the ACCENT_AREA_SMALL_FRACTION sweep turns.
    At the small end the secondary patches fall to a handful of pixels, which is
    exactly the regime HUE_PRESENCE_MIN_PIXELS exists to police.
    """
    img = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(img)
    _dark_bands(draw, size, seed)

    total = size[0] * size[1]
    budget = accent_fraction * total
    areas = [(ACCENT_DOMINANT, budget * 0.5)]
    share = (budget * 0.5) / len(ACCENT_SECONDARIES)
    areas.extend((name, share) for name in ACCENT_SECONDARIES)

    # Patches are squares laid left to right so the layout is a pure function of
    # the requested fraction; a band layout cannot represent fractions small
    # enough to reach sub-row heights.
    rnd = lcg(seed + 1)
    x = 4 + int(next(rnd) * 6)
    y = 4 + int(next(rnd) * 6)
    row_height = 0
    for name, area in areas:
        side = max(1, int(round(area**0.5)))
        if x + side >= size[0] - 2:
            x = 4
            y += row_height + 2
            row_height = 0
        draw.rectangle([x, y, x + side - 1, y + side - 1], fill=HUE_SAMPLES[name])
        x += side + 2
        row_height = max(row_height, side)
    return img


# Block palette for the structured scene: mid-tone, moderately saturated, so
# per-cell luminance and edge statistics all have headroom in both directions.
_BLOCK_COLOURS = (
    (176, 64, 64),
    (64, 128, 176),
    (96, 160, 80),
    (200, 168, 72),
    (128, 96, 176),
    (72, 160, 160),
    (208, 120, 56),
    (112, 112, 128),
    (160, 80, 128),
    (88, 144, 96),
    (192, 192, 176),
    (56, 72, 104),
)


def structured(size=SCENE_SIZE, seed=101):
    """A 4x3 block layout with a striped band -- a layout that can actually
    change.

    Structural similarity compares per-cell statistics on a fractional grid;
    against flat or uniformly noisy content it has almost no dynamic range. The
    blocks give each cell a distinct luminance and the stripes give the middle
    row a high edge density, so translation, blur and rescale all move the score
    in a readable way.
    """
    img = Image.new("RGB", size, (32, 32, 36))
    draw = ImageDraw.Draw(img)
    w, h = size
    rnd = lcg(seed)
    rotation = int(next(rnd) * len(_BLOCK_COLOURS))

    cols, rows = 4, 3
    for row in range(rows):
        for col in range(cols):
            idx = (row * cols + col + rotation) % len(_BLOCK_COLOURS)
            x0, x1 = int(w * col / cols), int(w * (col + 1) / cols)
            y0, y1 = int(h * row / rows), int(h * (row + 1) / rows)
            draw.rectangle([x0 + 3, y0 + 3, x1 - 4, y1 - 4], fill=_BLOCK_COLOURS[idx])

    # Vertical stripes across the middle row: fine repeating detail, which is
    # what blur and downscale destroy first.
    stripe_top, stripe_bottom = int(h / 3) + 6, int(2 * h / 3) - 7
    for x in range(0, w, 6):
        draw.rectangle([x, stripe_top, x + 2, stripe_bottom], fill=(236, 236, 240))

    # A couple of seeded diagonals so the scene is not perfectly axis-aligned;
    # perfectly axis-aligned content makes sub-pixel translation degenerate.
    for i in range(3):
        x0 = int(next(rnd) * w)
        draw.line([x0, 0, x0 + 40, h], fill=(20, 20, 24), width=2)
    return img


# Object geometry in fractions of the frame, taken from tests/conftest.py's
# preview_render (400x300) so the calibration object matches the fixture the
# foreground work was built against.
_BLADE_FRAC = (0.075, 0.900, 0.825, 0.133)
_GRIP_FRAC = (0.050, 0.873, 0.120, 0.950)
_ACCENT_FRAC = (0.110, 0.833, 0.140, 0.873)


def _scaled(frac_pt, size, scale, offset):
    """Map a fractional point to pixels, scaled about the frame centre."""
    w, h = size
    fx, fy = frac_pt
    x = 0.5 * w + (fx - 0.5) * w * scale + offset[0]
    y = 0.5 * h + (fy - 0.5) * h * scale + offset[1]
    return int(round(x)), int(round(y))


def thin_object(
    size=SCENE_SIZE,
    seed=101,
    scale=1.0,
    thickness=5,
    blade=SWORD_BLADE,
    accent=None,
    offset=(0, 0),
    mirrored=False,
):
    """A thin sword-like object on a flat preview backdrop.

    At the default ``scale``/``thickness`` the object covers roughly 2.4% of the
    frame -- the production case the foreground work exists for, where a shared
    backdrop is ~97% of both frames and full-frame similarity certifies the
    backdrop rather than the subject.

    ``scale`` and ``thickness`` are the knobs the foreground-fraction sweep
    turns. At the top of that sweep the object stops looking like a sword; the
    sweep is about *foreground share*, not realism, and the measured
    ``foreground.fraction_of_frame`` reported by the tools is what gets recorded
    -- never the nominal value.
    """
    # Colours may arrive as lists (they travel through JSON image specs); PIL's
    # ink resolution wants tuples.
    blade = tuple(blade)
    offset = tuple(offset)
    if accent is not None:
        accent = tuple(accent)

    rnd = lcg(seed)
    # The accent draw is consumed whether or not it is used, so that passing an
    # explicit accent colour does not shift the jitter draw underneath it. The
    # foreground sweep compares a scene against a recoloured copy of itself and
    # would otherwise be comparing two differently-positioned objects.
    names = ACCENT_SECONDARIES
    default_accent = HUE_SAMPLES[names[int(next(rnd) * len(names)) % len(names)]]
    if accent is None:
        accent = default_accent
    jitter = (int(next(rnd) * 9) - 4, int(next(rnd) * 9) - 4)
    total_offset = (offset[0] + jitter[0], offset[1] + jitter[1])

    img = Image.new("RGB", size, PREVIEW_BG)
    draw = ImageDraw.Draw(img)

    bx0, by0 = _scaled(_BLADE_FRAC[:2], size, scale, total_offset)
    bx1, by1 = _scaled(_BLADE_FRAC[2:], size, scale, total_offset)
    if mirrored:
        bx0, bx1 = bx1, bx0
        by0, by1 = by1, by0
    draw.line([bx0, by0, bx1, by1], fill=blade, width=max(1, int(round(thickness))))

    gx0, gy0 = _scaled(_GRIP_FRAC[:2], size, scale, total_offset)
    gx1, gy1 = _scaled(_GRIP_FRAC[2:], size, scale, total_offset)
    draw.rectangle([gx0, gy0, gx1, gy1], fill=SWORD_GRIP)

    ax0, ay0 = _scaled(_ACCENT_FRAC[:2], size, scale, total_offset)
    ax1, ay1 = _scaled(_ACCENT_FRAC[2:], size, scale, total_offset)
    draw.rectangle([ax0, ay0, ax1, ay1], fill=accent)
    return img


def busy(size=SCENE_SIZE, seed=101):
    """Full-frame high-entropy content: overlapping rectangles, lines, noise.

    The opposite extreme from ``thin_object``: nothing here is background, every
    metric sits at a large operating point, and the question a threshold has to
    answer is whether a perturbation is visible *above* that operating point.
    """
    w, h = size
    rng = _rng(seed)

    # Smooth two-axis gradient base, so the scene is not uniform before the
    # clutter goes on top.
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    base = np.stack(
        [
            60 + 120 * xx / max(1, w - 1),
            50 + 130 * yy / max(1, h - 1),
            70 + 100 * (xx + yy) / max(1, w + h - 2),
        ],
        axis=-1,
    )
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img)

    rects = rng.integers(0, 2**31 - 1, size=(56, 6))
    for x0, y0, dw, dh, colour_seed, alpha_seed in rects:
        x0 = int(x0 % w)
        y0 = int(y0 % h)
        dw = int(dw % (w // 4)) + 6
        dh = int(dh % (h // 4)) + 6
        colour = (
            int(colour_seed % 256),
            int((colour_seed // 256) % 256),
            int((colour_seed // 65536) % 256),
        )
        if alpha_seed % 3 == 0:
            draw.ellipse([x0, y0, x0 + dw, y0 + dh], fill=colour)
        else:
            draw.rectangle([x0, y0, x0 + dw, y0 + dh], fill=colour)

    lines = rng.integers(0, 2**31 - 1, size=(24, 5))
    for x0, y0, x1, y1, colour_seed in lines:
        draw.line(
            [int(x0 % w), int(y0 % h), int(x1 % w), int(y1 % h)],
            fill=(
                int(colour_seed % 256),
                int((colour_seed // 256) % 256),
                int((colour_seed // 65536) % 256),
            ),
            width=1 + int(colour_seed % 3),
        )

    # Fine grain on top, so downscale and blur have detail to destroy.
    grain = rng.normal(0.0, 10.0, size=(h, w, 3))
    arr = np.asarray(img, dtype=np.float64) + grain
    return Image.fromarray(np.clip(arr, 0, 255).round().astype(np.uint8), "RGB")


SCENE_BUILDERS = {
    "dark_accent": dark_accent,
    "structured": structured,
    "thin_object": thin_object,
    "busy": busy,
}

# Scenes for which running the tools in --foreground mode is meaningful. On the
# other three the "background" is the subject, so foreground mode would be
# measuring an artefact of the border-median estimate rather than an object.
FOREGROUND_SCENES = ("thin_object",)


def build(scene, **params):
    """Build a named scene. ``params`` are forwarded to the builder."""
    try:
        builder = SCENE_BUILDERS[scene]
    except KeyError:
        raise SystemExit(f"unknown scene {scene!r}; expected one of {sorted(SCENE_BUILDERS)}")
    return builder(**params)


# --- Sweep configurations ----------------------------------------------------
# These drive the constant-specific experiments in measure.py. Nominal values
# are documented for orientation only; every derivation uses the *measured*
# fraction reported by the tools.

# Foreground-fraction sweep for BACKGROUND_DOMINANT_MAX / FOREGROUND_MIN_FRACTION.
# (scale, thickness) pairs chosen to bracket both constants (~0.005 .. ~0.20).
FOREGROUND_SWEEP = (
    {"label": "fg_tiny", "scale": 0.45, "thickness": 3, "nominal_fraction": 0.003},
    {"label": "fg_small", "scale": 0.64, "thickness": 4, "nominal_fraction": 0.008},
    {"label": "fg_default", "scale": 1.0, "thickness": 5, "nominal_fraction": 0.024},
    {"label": "fg_mid", "scale": 1.0, "thickness": 15, "nominal_fraction": 0.058},
    {"label": "fg_large", "scale": 1.0, "thickness": 28, "nominal_fraction": 0.103},
    {"label": "fg_huge", "scale": 1.0, "thickness": 56, "nominal_fraction": 0.199},
)

# Accent-area sweep for ACCENT_AREA_SMALL_FRACTION, bracketing 0.005 by an
# order of magnitude either side.
ACCENT_SWEEP = (0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05)
