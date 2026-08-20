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

import math

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


# --- RGBA / alpha corpus ------------------------------------------------------
# The scenes above are all opaque, so they exercise exactly one of the two
# foreground-mask paths: the border-median OKLab estimate. The scenes below
# exercise the other one -- real transparency -- and, more importantly, let the
# two be compared on *identical content*: every alpha scene can be emitted as
# RGBA on transparent film or alpha-composited onto a known flat backdrop, and
# the object is the same object either way. Any difference the tools report
# between the two forms is therefore attributable to mask provenance alone.
#
# Nothing here is seeded, because nothing here is random: the geometry is a
# closed-form function of its parameters, so determinism is unconditional rather
# than a property of a PRNG stream. ``ALPHA_CORPUS`` is the inventory;
# ``build_alpha`` is the single entry point.

# Square, and larger than SCENE_SIZE: perimeter-to-area is the governing
# variable of this corpus, and a 3px blade needs enough frame around it for its
# anti-aliased fringe to be a meaningful share of a meaningful object.
ALPHA_SCENE_SIZE = (512, 512)

# Supersample factor for the anti-aliased scenes. The object is drawn at 4x and
# LANCZOS-downsampled, so the fringe is a real resampling kernel's output rather
# than a hand-authored alpha ramp -- the alpha histogram of a genuine render.
ALPHA_SUPERSAMPLE = 4

# The flat backdrop the composited twin of every scene is composited onto.
# Deliberately PREVIEW_BG: the same backdrop the opaque thin_object scene uses,
# so a reader comparing the two corpora is looking at one preview background.
ALPHA_BACKDROP = PREVIEW_BG

# Un-premultiply guard. See _resolve_rgba for what it guards and why the result
# is clipped rather than left unbounded.
ALPHA_UNPREMULTIPLY_EPS = 1.0 / 255.0

# Alpha values of the graded bands in the ``alpha_ladder`` scene, in the order
# they are laid out left to right. Chosen to straddle ALPHA_FOREGROUND_MIN (8)
# closely and then to spread across the range, so a sweep of that constant
# removes a *known* set of bands at every step.
ALPHA_LADDER_STEPS = (0, 2, 4, 6, 8, 10, 12, 16, 24, 32, 48, 64, 96, 128, 160, 192, 224, 255)


def _lanczos_float(channel, size):
    """LANCZOS-resample one 8-bit channel and return it as float64.

    The resample runs in PIL's "F" (float32) mode rather than on uint8, so the
    premultiplied colour and the coverage are filtered by the same linear kernel
    without an intermediate quantisation between them. That equality is what
    makes the un-premultiply in _resolve_rgba exact.
    """
    source = Image.fromarray(np.asarray(channel, dtype=np.float32))
    return np.asarray(source.resize(size, Image.LANCZOS), dtype=np.float64)


def _resolve_rgba(colour_ss, coverage_ss, size):
    """Downsample a supersampled (premultiplied colour, coverage) pair to RGBA.

    ``colour_ss`` carries the object's colour inside the silhouette and black
    outside it, and ``coverage_ss`` is 255 inside and 0 outside -- so at
    supersample resolution ``colour_ss`` is already the premultiplied form of
    ``coverage_ss``. Downsampling both with one linear kernel preserves that
    relationship, and un-premultiplying recovers the object's *true* colour at
    every fringe pixel.

    This is the whole point of building the corpus this way. Resampling a
    straight-alpha RGBA channel-by-channel (the obvious approach) averages the
    object's colour against the transparent region's RGB, which bakes a dark
    halo into the content itself; the fringe would then be genuinely dark and
    both mask paths would agree about it. Premultiplied resampling is what real
    renderers do, and it leaves the fringe's true colour equal to the object's.

    The division is ``clip(premultiplied / max(coverage, eps), 0, 255)``:

    *   ``eps`` is one alpha code value (1/255). Below that the stored 8-bit
        alpha rounds to zero, so the recovered colour is never read; the guard
        exists to cap the amplification at 255x instead of dividing by ~0.
    *   The clip is needed because LANCZOS has negative lobes: near a hard edge
        the filtered premultiplied colour can overshoot its own coverage, which
        would otherwise recover a colour outside the sRGB byte range.
    *   Coverage here is the *float* result of the resample, not the rounded
        8-bit alpha. They differ by up to half a code value, and dividing by the
        rounded value would inject up to +/-50% colour error at alpha=1 --
        reconstruction noise that would then be indistinguishable from the
        measurement bias this corpus exists to isolate.
    """
    coverage = np.clip(_lanczos_float(coverage_ss, size), 0.0, 255.0) / 255.0
    premultiplied = np.stack(
        [_lanczos_float(channel, size) for channel in colour_ss.split()], axis=-1
    )
    straight = np.clip(
        premultiplied / np.maximum(coverage, ALPHA_UNPREMULTIPLY_EPS)[..., None],
        0.0,
        255.0,
    )
    alpha = np.rint(coverage * 255.0).astype(np.uint8)
    rgb = np.rint(straight).astype(np.uint8)
    # Canonical fully-transparent pixel, so two builds cannot differ in bytes
    # nobody can see.
    rgb[alpha == 0] = 0
    return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")


def _harden(rgba):
    """Force alpha to strictly 0 or 255, keeping the recovered straight colour.

    The edge-type axis of the corpus: same geometry, same colours, no partial
    coverage anywhere. A hard-edged scene and its composited twin contain
    literally the same visible pixels, so any disagreement the tools report on
    *this* scene is the alpha mask path itself rather than the fringe.
    """
    arr = np.asarray(rgba).copy()
    opaque = arr[:, :, 3] >= 128
    arr[:, :, 3] = np.where(opaque, 255, 0).astype(np.uint8)
    arr[~opaque, :3] = 0
    return Image.fromarray(arr, "RGBA")


def _blade_geometry(size, supersample, angle_deg=None):
    """Endpoints of the blade axis at supersample resolution.

    Held well clear of the frame border on purpose: the composited twin's mask
    comes from the border-median colour, so an object touching an edge would
    poison the background estimate and confound a mask-provenance comparison
    with a background-estimation failure.

    The default slope is -0.658 rather than the obvious -1, and that choice is
    deliberate in BOTH directions. A 45-degree edge on a supersample grid
    repeats the same sub-pixel phase at every step, so the downsampled fringe
    collapses onto a handful of alpha values: measured on a square frame, a
    45-degree blade produced 17 distinct fringe alphas in two tight clusters,
    while this slope produces 59 spread across the range. The default is the
    broad, incoherent fringe a renderer usually produces, so the width sweep
    measures perimeter-to-area rather than a phase artefact.

    ``angle_deg`` overrides that slope, rotating the blade about the midpoint of
    the default axis at constant length. It exists because the fringe's severity
    depends on how the edge lands on the pixel grid, not only on the object's
    perimeter-to-area ratio, and an AXIS-ALIGNED edge is the extreme: every
    point along it shares one sub-pixel offset, so whatever error that offset
    produces applies uniformly instead of averaging out along the edge.

    At ONE fixed placement, RGBA-versus-composited saturation delta by angle on
    a neutral 5px blade reads: 0deg 5.267, 10deg 2.453, 20deg 2.410, 30deg
    2.189, 45deg 2.050, 60deg 2.274, 75deg 2.285, 90deg 1.724. Read alone that
    table says axis-aligned is about twice the off-axis angles and 45 degrees is
    unremarkable. Read alone it is misleading, because a single placement is a
    single draw from a distribution -- see ``alpha_blade``'s ``phase_px``.

    What the placement sweep shows is that the two GRID-COMMENSURATE angles, 0
    and 45 degrees, are the ones whose reading depends on where the edge falls,
    and the off-axis angles are the ones where it does not. Sweeping a
    quarter-pixel perpendicular translation of the same blade, the measured
    foreground luminance moves over a range of 21.56 code values at 0 degrees
    and 16.70 at 45 degrees, against 0.75 at 20 degrees and 0.94 at 60. The
    object itself barely moves across the same sweep: alpha-weighted truth
    ranges 3.82 / 0.06 / 0.79 / 0.07 respectively.

    So the honest statement is neither "45 degrees is the worst case" nor "45
    degrees is unremarkable". Axis-aligned is the worst STABLE case -- it sits
    high wherever you put it -- while 45 degrees is the UNSTABLE one: at some
    placements it reads better than any off-axis angle and at others it is the
    worst thing in the corpus. Its RGBA-versus-composited luminance delta swings
    4.063 .. 35.681 across placements, an 8.8x range that crosses the calibrated
    foreground threshold of 34.129, while off-axis angles swing only 1.1-1.3x.

    That instability, not the magnitude at any one placement, is the finding:
    it means a half-pixel re-render of an unchanged asset can cross a calibrated
    threshold, which is the false-positive-on-a-re-render failure this plugin
    exists to prevent. Diagonal edges are common in exactly the art this
    measures -- blades, chevrons, isometric tiles, UI carets -- and the sword
    fixture in tests/conftest.py is itself near-diagonal.

    The caution that follows is unchanged and load-bearing: do NOT build a
    threshold or an ordering assertion on nominal angle. Any such assertion pins
    the placement it happened to sample rather than the mechanism. Assert on the
    SPREAD across placements instead; tests/test_alpha_foreground.py does.

    ``angle_deg=None`` reproduces the original endpoints exactly, so every
    pre-existing scene stays byte-identical.
    """
    w, h = size
    scale = supersample
    default = (
        (0.09 * w * scale, 0.80 * h * scale),
        (0.91 * w * scale, 0.26 * h * scale),
    )
    if angle_deg is None:
        return default

    (ax, ay), (bx, by) = default
    cx, cy = (ax + bx) / 2.0, (ay + by) / 2.0
    length = math.hypot(bx - ax, by - ay)
    radians = math.radians(float(angle_deg))
    # Screen y grows downward, so a positive angle tilts the blade up-and-right,
    # matching the default axis's direction of travel.
    dx = math.cos(radians) * length / 2.0
    dy = -math.sin(radians) * length / 2.0
    return ((cx - dx, cy - dy), (cx + dx, cy + dy))


def alpha_blade(
    width=12,
    edge="aa",
    size=ALPHA_SCENE_SIZE,
    blade=SWORD_BLADE,
    accent=None,
    supersample=ALPHA_SUPERSAMPLE,
    angle_deg=None,
    phase_px=0.0,
):
    """A diagonal blade with a vivid gem, on transparent film.

    ``width`` is the blade's thickness in final-image pixels and is the
    perimeter-to-area knob: the object's area falls linearly with it while its
    perimeter barely moves, so the anti-aliased fringe goes from a rim to most
    of the object. That ratio, not the alpha values themselves, is what governs
    how badly an unweighted foreground mask misreads the object.

    ``edge="hard"`` emits the same geometry with alpha strictly 0 or 255. It is
    the control: it isolates the fringe from the alpha mask path.

    The gem exists so the scene carries a genuinely vivid hue whose fringe can
    be tested against the accent gate. It is sized from ``width`` and clamped,
    so it stays a modest share of the object at every blade width rather than
    swamping the thin end of the sweep. Pass ``accent`` equal to ``blade`` to
    suppress it, which the angle axis does so that a rotating edge is measured
    against a constant, axis-aligned gem rather than through it.

    ``angle_deg`` rotates the blade axis; see _blade_geometry for what the angle
    axis does and does not show.

    ``phase_px`` translates the whole blade perpendicular to its own axis by a
    fraction of a final-image pixel, changing where the edge falls on the grid
    while changing nothing about the object -- same colour, same width, same
    length, same angle, same area. It is the axis that separates a property of
    the object from an accident of its placement, and it is the one that
    exposes the instability: a quarter-pixel translation moves the measured
    foreground luminance by 16.70 code values at 45 degrees and 21.56 at 0
    degrees, while moving alpha-weighted truth by only 0.79 and 3.82. Off-axis
    the same translation moves the reading by under one code value.

    Translating perpendicular to the axis rather than along it is deliberate:
    along the axis a straight edge maps onto itself and the phase is unchanged,
    so only the perpendicular component is a phase at all.

    ``phase_px=0.0`` is a no-op that leaves the endpoints untouched, so every
    pre-existing scene stays byte-identical.
    """
    if edge not in ("aa", "hard"):
        raise SystemExit(f"unknown edge {edge!r}; expected 'aa' or 'hard'")
    blade = tuple(blade)
    accent = tuple(accent) if accent is not None else HUE_SAMPLES["cyan"]

    w, h = size
    ss = (w * supersample, h * supersample)
    colour = Image.new("RGB", ss, (0, 0, 0))
    coverage = Image.new("L", ss, 0)
    colour_draw, coverage_draw = ImageDraw.Draw(colour), ImageDraw.Draw(coverage)

    (x0, y0), (x1, y1) = _blade_geometry(size, supersample, angle_deg)
    if phase_px:
        # Unit normal to the blade axis, then the offset in supersample units.
        span_x, span_y = x1 - x0, y1 - y0
        length = math.hypot(span_x, span_y) or 1.0
        offset = float(phase_px) * supersample
        shift_x, shift_y = span_y / length * offset, -span_x / length * offset
        x0, y0, x1, y1 = x0 + shift_x, y0 + shift_y, x1 + shift_x, y1 + shift_y
    line = [x0, y0, x1, y1]
    stroke = max(1, int(round(width * supersample)))
    colour_draw.line(line, fill=blade, width=stroke)
    coverage_draw.line(line, fill=255, width=stroke)

    # Gem centred on the blade axis, a sixth of the way up from the hilt.
    gem = max(6, min(28, 2 * int(round(width)))) * supersample
    gx = x0 + (x1 - x0) / 6.0
    gy = y0 + (y1 - y0) / 6.0
    box = [gx - gem / 2, gy - gem / 2, gx + gem / 2, gy + gem / 2]
    colour_draw.rectangle(box, fill=accent)
    coverage_draw.rectangle(box, fill=255)

    rgba = _resolve_rgba(colour, coverage, size)
    return _harden(rgba) if edge == "hard" else rgba


def alpha_interior(
    interior_alpha=128,
    size=ALPHA_SCENE_SIZE,
    bezel=SWORD_BLADE,
    interior=None,
    bezel_width=12,
):
    """A hard-edged panel whose interior is a constant partial alpha.

    Glass, ghosts and VFX quads: large regions that are *uniformly* semi-
    transparent. Edge-only scenes never reach this case, and it is the one where
    the difference between "in the mask" and "how much of the object is here" is
    unmistakable -- an interior at alpha 128 is admitted at full weight by an
    unweighted mask while contributing half its colour to the composite.

    Drawn axis-aligned at final resolution with no supersampling, so alpha takes
    exactly three values (0, ``interior_alpha``, 255) and there is no fringe at
    all to confound the reading.
    """
    if not 0 <= int(interior_alpha) <= 255:
        raise SystemExit(f"interior_alpha {interior_alpha!r} must be 0-255")
    bezel = tuple(bezel)
    interior = tuple(interior) if interior is not None else HUE_SAMPLES["cyan"]

    w, h = size
    rgb = Image.new("RGB", size, (0, 0, 0))
    alpha = Image.new("L", size, 0)
    rgb_draw, alpha_draw = ImageDraw.Draw(rgb), ImageDraw.Draw(alpha)

    outer = [int(0.25 * w), int(0.25 * h), int(0.75 * w) - 1, int(0.75 * h) - 1]
    rgb_draw.rectangle(outer, fill=bezel)
    alpha_draw.rectangle(outer, fill=255)

    inner = [
        outer[0] + bezel_width,
        outer[1] + bezel_width,
        outer[2] - bezel_width,
        outer[3] - bezel_width,
    ]
    rgb_draw.rectangle(inner, fill=interior)
    alpha_draw.rectangle(inner, fill=int(interior_alpha))

    arr = np.dstack([np.asarray(rgb, dtype=np.uint8), np.asarray(alpha, dtype=np.uint8)])
    arr[arr[:, :, 3] == 0, :3] = 0
    return Image.fromarray(arr, "RGBA")


def alpha_ladder(
    size=ALPHA_SCENE_SIZE,
    colour=SWORD_BLADE,
    steps=ALPHA_LADDER_STEPS,
    band_width=24,
    band_height=256,
):
    """One colour at every alpha in ``steps``, in bands of known pixel count.

    The alpha histogram of this scene is known by construction rather than
    measured: band *i* is exactly ``band_width * band_height`` pixels at exactly
    ``steps[i]``. That makes it the instrument for an ALPHA_FOREGROUND_MIN
    sweep -- raising the floor deletes a set of bands whose size and colour
    contribution were both known in advance, so what the sweep does to the
    measured statistics can be predicted and checked rather than merely observed.

    A solid opaque bar sits above the bands so the object still has a body when
    the floor is raised past every graded band.
    """
    colour = tuple(colour)
    w, h = size
    rgb = Image.new("RGB", size, (0, 0, 0))
    alpha = Image.new("L", size, 0)
    rgb_draw, alpha_draw = ImageDraw.Draw(rgb), ImageDraw.Draw(alpha)

    span = len(steps) * band_width
    x0 = (w - span) // 2
    top = (h - band_height) // 2 + 32

    rgb_draw.rectangle([x0, top - 96, x0 + span - 1, top - 33], fill=colour)
    alpha_draw.rectangle([x0, top - 96, x0 + span - 1, top - 33], fill=255)

    for index, value in enumerate(steps):
        left = x0 + index * band_width
        box = [left, top, left + band_width - 1, top + band_height - 1]
        rgb_draw.rectangle(box, fill=colour)
        alpha_draw.rectangle(box, fill=int(value))

    arr = np.dstack([np.asarray(rgb, dtype=np.uint8), np.asarray(alpha, dtype=np.uint8)])
    arr[arr[:, :, 3] == 0, :3] = 0
    return Image.fromarray(arr, "RGBA")


def alpha_empty(size=ALPHA_SCENE_SIZE):
    """Fully transparent film: the degenerate case where the alpha mask is empty.

    The tools must fall back to full-frame with foreground_mask_empty rather
    than computing statistics over nothing.
    """
    return Image.new("RGBA", size, (0, 0, 0, 0))


def alpha_opaque(size=ALPHA_SCENE_SIZE, backdrop=ALPHA_BACKDROP, width=12):
    """An RGBA file carrying an alpha channel that is entirely 255.

    A file can be RGBA and still provide no foreground information at all. This
    is the case load_rgb_alpha's ``alpha = None`` branch exists for: without it
    the mask would be "every pixel", which is not a foreground.
    """
    flattened = composite_over(alpha_blade(width=width, edge="hard", size=size), backdrop)
    arr = np.asarray(flattened, dtype=np.uint8)
    opaque = np.full(arr.shape[:2] + (1,), 255, dtype=np.uint8)
    return Image.fromarray(np.concatenate([arr, opaque], axis=-1), "RGBA")


def composite_over(rgba, backdrop=ALPHA_BACKDROP):
    """Alpha-composite an RGBA scene onto a flat opaque backdrop; return RGB.

    The composited twin is made from the *built RGBA*, never re-rendered, so the
    two forms of a scene are guaranteed to contain the same object rather than
    two independent drawings of it. PIL's own alpha_composite does the blend --
    the same integer path pil_common.load_rgb_alpha uses to flatten onto black.

    The result is converted to RGB explicitly rather than left as RGBA with an
    all-255 alpha, because "RGBA with no transparency" is itself a case under
    test (see alpha_opaque) and the twin must not accidentally be it.
    """
    base = Image.new("RGBA", rgba.size, tuple(backdrop) + (255,))
    return Image.alpha_composite(base, rgba.convert("RGBA")).convert("RGB")


ALPHA_SCENE_BUILDERS = {
    "alpha_blade": alpha_blade,
    "alpha_interior": alpha_interior,
    "alpha_ladder": alpha_ladder,
    "alpha_empty": alpha_empty,
    "alpha_opaque": alpha_opaque,
}


def build_alpha(scene, composite=False, backdrop=ALPHA_BACKDROP, **params):
    """Build a named alpha scene; return a PIL image.

    ``composite=False`` returns the RGBA original on transparent film;
    ``composite=True`` returns the same object composited onto ``backdrop`` as
    an RGB image. Every scene is meaningful in both forms, and comparing the
    pair is the corpus's central experiment: identical content, two mask paths.
    """
    try:
        builder = ALPHA_SCENE_BUILDERS[scene]
    except KeyError:
        raise SystemExit(
            f"unknown alpha scene {scene!r}; expected one of {sorted(ALPHA_SCENE_BUILDERS)}"
        )
    img = builder(**params)
    return composite_over(img, backdrop) if composite else img


# (label, scene, params). The axes, in the order they are laid out:
#
#   1. PERIMETER-TO-AREA -- aa_blade_w40 .. aa_blade_w3, one object at four
#      thicknesses. The governing variable.
#   2. EDGE TYPE         -- hard_blade_* mirror two of those widths with alpha
#      strictly 0 or 255, isolating the fringe from the mask path.
#      vivid_blade_w5 is the same geometry again in a saturated colour: the
#      default blade is a near-neutral grey whose fringe never approaches the
#      accent gate, so the gate's treatment of partial coverage needs an object
#      that is vivid along its whole edge rather than only at its gem.
#   3. INTERIOR ALPHA    -- glass_a64/128/192, constant partial alpha over a
#      large area, with no fringe anywhere.
#   4. ALPHA HISTOGRAM   -- alpha_ladder, every alpha step at a known pixel
#      count, for sweeping the foreground alpha floor.
#   5. DEGENERATE        -- empty mask, an all-255 alpha channel, and a
#      one-pixel blade whose fringe outweighs its interior.
ALPHA_CORPUS = (
    ("aa_blade_w40", "alpha_blade", {"width": 40}),
    ("aa_blade_w12", "alpha_blade", {"width": 12}),
    ("aa_blade_w5", "alpha_blade", {"width": 5}),
    ("aa_blade_w3", "alpha_blade", {"width": 3}),
    ("hard_blade_w12", "alpha_blade", {"width": 12, "edge": "hard"}),
    ("hard_blade_w5", "alpha_blade", {"width": 5, "edge": "hard"}),
    (
        "vivid_blade_w5",
        "alpha_blade",
        {
            "width": 5,
            "blade": HUE_SAMPLES["cyan"],
            "accent": HUE_SAMPLES["magenta"],
        },
    ),
    ("glass_a64", "alpha_interior", {"interior_alpha": 64}),
    ("glass_a128", "alpha_interior", {"interior_alpha": 128}),
    ("glass_a192", "alpha_interior", {"interior_alpha": 192}),
    ("alpha_ladder", "alpha_ladder", {}),
    ("degenerate_empty", "alpha_empty", {}),
    ("degenerate_opaque", "alpha_opaque", {}),
    ("degenerate_hairline", "alpha_blade", {"width": 1}),
    #   6. EDGE ANGLE -- the same neutral 5px blade rotated, gem suppressed
    #      (accent == blade) so a rotating edge is not measured through an
    #      axis-aligned rectangle. The axis exists because fringe severity
    #      depends on how the edge meets the pixel grid, not only on
    #      perimeter-to-area: an axis-aligned edge shares one sub-pixel offset
    #      along its whole length, so its error applies uniformly rather than
    #      averaging out. At one fixed placement the saturation delta reads
    #      5.267 at 0deg against 2.050-2.274 at 20/45/60, which makes 45 degrees
    #      look unremarkable. It is not: it is UNSTABLE. Both 0 and 45 degrees
    #      are commensurate with the pixel grid, and a quarter-pixel translation
    #      moves their reading by 21.56 and 16.70 luminance code values against
    #      0.75 and 0.94 off-axis. The single-placement table above is one draw
    #      from that spread, which is why the axis carries angle_a45_phase as
    #      well. Characterisation data, never a threshold on nominal angle --
    #      see _blade_geometry.
    ("angle_a00", "alpha_blade", {"width": 5, "accent": SWORD_BLADE, "angle_deg": 0}),
    ("angle_a20", "alpha_blade", {"width": 5, "accent": SWORD_BLADE, "angle_deg": 20}),
    ("angle_a45", "alpha_blade", {"width": 5, "accent": SWORD_BLADE, "angle_deg": 45}),
    ("angle_a60", "alpha_blade", {"width": 5, "accent": SWORD_BLADE, "angle_deg": 60}),
    # The same 45-degree blade a quarter pixel over. Identical object, identical
    # angle -- only the sub-pixel placement differs, and that is enough to move
    # the measured foreground luminance from 147.203 to 130.504. It is in the
    # corpus so the inventory records the coherent state of the coin flip and
    # not only the benign one that angle_a45 happens to draw.
    (
        "angle_a45_phase",
        "alpha_blade",
        {"width": 5, "accent": SWORD_BLADE, "angle_deg": 45, "phase_px": 0.25},
    ),
)

# The blade widths of the perimeter-to-area axis, in the order the sweep reads
# them. Kept next to ALPHA_CORPUS so the two cannot drift apart.
ALPHA_BLADE_WIDTHS = (40, 12, 5, 3)

# --- Sub-pixel placement axis -------------------------------------------------
# Angles split by whether their slope is commensurate with the pixel grid.
# 0 and 45 degrees repeat one sub-pixel phase along the whole edge, so their
# per-pixel errors are correlated and the reading depends on placement; 20 and
# 60 degrees cycle through phases, so the errors average out and the reading
# does not. That split, not the angle's magnitude, is what the phase sweep
# measures -- see _blade_geometry.
ALPHA_COMMENSURATE_ANGLES = (0, 45)
ALPHA_OFF_AXIS_ANGLES = (20, 60)
ALPHA_PHASE_ANGLES = tuple(sorted(ALPHA_COMMENSURATE_ANGLES + ALPHA_OFF_AXIS_ANGLES))

# Perpendicular offsets in final-image pixels. Quarter-pixel steps, and three of
# them is the minimum that reaches both states of the 45-degree case: it reads
# 147.203 at 0.0, 130.504 at 0.25 and 147.203 again at 0.5.
ALPHA_PHASE_OFFSETS = (0.0, 0.25, 0.5)

# Blade parameters held fixed across the phase sweep, so the only thing that
# varies is where the edge lands. Gem suppressed (accent == blade) to keep an
# axis-aligned rectangle out of a measurement about edge placement.
ALPHA_PHASE_BLADE = {"width": 5, "accent": SWORD_BLADE}

# The edge-angle axis, in degrees. 0 is the axis-aligned extreme (one shared
# sub-pixel offset along the whole edge); the rest are the off-axis baseline.
ALPHA_BLADE_ANGLES = (0, 20, 45, 60)
ALPHA_AXIS_ALIGNED_ANGLE = 0
