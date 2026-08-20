#!/usr/bin/env python
"""Parameterised perturbations with exact ground-truth magnitude.

WP2's method note: a corpus of real images cannot calibrate a threshold, because
it carries no labels -- there is no ground truth for "how different" two
arbitrary images are. Synthetic perturbation supplies exact ground truth, so
every function here takes a magnitude in a stated unit and the calibration
records that magnitude alongside whatever the metric reported.

Two rules the whole module obeys:

*   **Monotone magnitude axis.** Each perturbation family exposes a single
    scalar ``magnitude`` that increases with severity, even where the natural
    parameter does not (saturation scaling is split into ``saturation_down`` and
    ``saturation_up`` rather than sweeping a factor through 1.0, and rescale is
    reported as ``1 - factor`` rather than as the factor). Response curves are
    checked for monotonicity in that scalar; a non-monotone parameterisation
    would make the check meaningless.
*   **Determinism.** The only stochastic perturbation is additive noise, drawn
    from ``numpy.random.Generator(PCG64(seed))`` with the seed derived up front
    from the scene seed, so the same plan yields the same pixels on every run.

Magnitude x extent: ``hue_rotation`` and ``region_recolour`` carry a second
axis (the fraction of the frame the edit covers), because a large change to a
small area and a small change to a large area are exactly the two cases a
single scalar threshold conflates.
"""

from __future__ import annotations

import hashlib
import io

import numpy as np
from PIL import Image, ImageFilter

# Fill colour for partial-region edits. A single fixed vivid colour keeps the
# ground truth simple: the edit is "this exact fraction of the frame became this
# exact colour".
REGION_FILL = (255, 0, 200)

# JPEG chroma subsampling is pinned to 4:2:0 across every quality level. It is
# the realistic setting, and pinning it stops Pillow's quality-dependent default
# from putting a discontinuity in the middle of the response curve.
JPEG_SUBSAMPLING = 2


def derive_seed(*parts):
    """Stable 32-bit seed from arbitrary parts.

    Uses sha256 rather than ``hash()``, whose string hashing is randomised per
    process and would silently break run-to-run determinism.
    """
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


# --- Perturbation primitives -------------------------------------------------


def hue_rotate(img, degrees, extent=1.0):
    """Rotate hue by ``degrees`` over the top ``extent`` fraction of rows.

    Runs through PIL's own 8-bit HSV conversion, which is what the measurement
    tools see, so the perturbation and the measurement agree about what a hue
    is. That round trip is itself slightly lossy; ``hue_rotate(img, 0)`` is
    included in the control set precisely to measure how lossy.
    """
    hsv = np.asarray(img.convert("HSV")).copy()
    height = hsv.shape[0]
    rows = max(1, int(round(height * extent)))
    shift = int(round(degrees * 255.0 / 360.0))
    band = hsv[:rows, :, 0].astype(np.int16)
    hsv[:rows, :, 0] = ((band + shift) % 256).astype(np.uint8)
    return Image.fromarray(hsv, "HSV").convert("RGB")


def saturation_scale(img, factor):
    """Multiply the HSV saturation channel by ``factor`` and clip."""
    hsv = np.asarray(img.convert("HSV")).astype(np.float64)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * factor, 0.0, 255.0)
    return Image.fromarray(hsv.round().astype(np.uint8), "HSV").convert("RGB")


def exposure_shift(img, delta):
    """Add ``delta`` (in 0-255 code values) to every channel and clip."""
    arr = np.asarray(img).astype(np.int16) + int(delta)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def gaussian_blur(img, radius):
    return img.filter(ImageFilter.GaussianBlur(radius))


def add_noise(img, sigma, seed):
    """Additive zero-mean gaussian noise, seeded."""
    arr = np.asarray(img).astype(np.float64)
    rng = np.random.Generator(np.random.PCG64(int(seed)))
    noise = rng.normal(0.0, float(sigma), size=arr.shape)
    return Image.fromarray(np.clip(arr + noise, 0, 255).round().astype(np.uint8), "RGB")


def translate(img, fraction):
    """Shift right by ``fraction`` of the width, replicating the left edge.

    Edge replication rather than wrap-around: a wrapped strip would introduce a
    hard artificial seam whose contribution to every metric would swamp the
    translation itself.
    """
    width, _ = img.size
    dx = int(round(width * fraction))
    if dx <= 0:
        return img.copy()
    arr = np.asarray(img)
    out = np.empty_like(arr)
    out[:, dx:] = arr[:, : width - dx]
    out[:, :dx] = arr[:, :1]
    return Image.fromarray(out, "RGB")


def rescale_roundtrip(img, factor):
    """Downscale (or upscale) by ``factor`` and back, both LANCZOS.

    The tools claim scale invariance; mild round trips therefore sit in the
    control set. The grid sweeps the severe end, where real detail is destroyed
    and the claim has to break somewhere.
    """
    width, height = img.size
    small = img.resize(
        (max(1, int(round(width * factor))), max(1, int(round(height * factor)))),
        Image.LANCZOS,
    )
    return small.resize((width, height), Image.LANCZOS)


def jpeg_reencode(img, quality):
    """Encode to JPEG at ``quality`` and decode back to RGB pixels."""
    buf = io.BytesIO()
    img.save(
        buf,
        format="JPEG",
        quality=int(quality),
        subsampling=JPEG_SUBSAMPLING,
        optimize=False,
        progressive=False,
    )
    buf.seek(0)
    with Image.open(buf) as decoded:
        return decoded.convert("RGB")


def region_recolour(img, region_fraction):
    """Fill a centred square covering ``region_fraction`` of the frame."""
    width, height = img.size
    side = max(1, int(round((region_fraction * width * height) ** 0.5)))
    side = min(side, width, height)
    x0 = (width - side) // 2
    y0 = (height - side) // 2
    out = img.copy()
    arr = np.asarray(out).copy()
    arr[y0 : y0 + side, x0 : x0 + side] = REGION_FILL
    return Image.fromarray(arr, "RGB")


OPERATIONS = {
    "hue_rotate": hue_rotate,
    "saturation_scale": saturation_scale,
    "exposure_shift": exposure_shift,
    "gaussian_blur": gaussian_blur,
    "add_noise": add_noise,
    "translate": translate,
    "rescale_roundtrip": rescale_roundtrip,
    "jpeg_reencode": jpeg_reencode,
    "region_recolour": region_recolour,
}


def apply(op, img, params):
    """Dispatch one perturbation by name."""
    try:
        fn = OPERATIONS[op]
    except KeyError:
        raise SystemExit(f"unknown perturbation {op!r}; expected one of {sorted(OPERATIONS)}")
    return fn(img, **params)


# --- The magnitude x extent grid ---------------------------------------------
# Coarse but monotonic: 5-6 levels per family, spanning roughly "barely there"
# to "obvious", chosen to bracket the current constants rather than to sample
# uniformly. Runtime budget forbids anything denser -- see the ledger.


def _levels(unit, entries):
    return [
        {
            "magnitude": round(float(magnitude), 6),
            "unit": unit,
            "extent": round(float(extent), 6),
            "op": op,
            "params": dict(params),
        }
        for magnitude, extent, op, params in entries
    ]


GRID = {
    "hue_rotation": _levels(
        "degrees",
        [
            (deg, extent, "hue_rotate", {"degrees": deg, "extent": extent})
            for extent in (0.05, 0.25, 1.0)
            for deg in (5, 15, 30, 60, 120)
        ],
    ),
    "saturation_down": _levels(
        "saturation_loss (1 - factor)",
        [
            (round(1.0 - f, 4), 1.0, "saturation_scale", {"factor": f})
            for f in (0.9, 0.75, 0.5, 0.25, 0.0)
        ],
    ),
    "saturation_up": _levels(
        "saturation_gain (factor - 1)",
        [
            (round(f - 1.0, 4), 1.0, "saturation_scale", {"factor": f})
            for f in (1.1, 1.25, 1.5, 2.0, 3.0)
        ],
    ),
    "exposure_up": _levels(
        "code values (+)",
        [(d, 1.0, "exposure_shift", {"delta": d}) for d in (4, 8, 16, 32, 64)],
    ),
    "exposure_down": _levels(
        "code values (-)",
        [(d, 1.0, "exposure_shift", {"delta": -d}) for d in (4, 8, 16, 32, 64)],
    ),
    "gaussian_blur": _levels(
        "radius px",
        [(r, 1.0, "gaussian_blur", {"radius": r}) for r in (0.5, 1.0, 2.0, 4.0, 8.0)],
    ),
    "additive_noise": _levels(
        "sigma code values",
        [
            (s, 1.0, "add_noise", {"sigma": s, "seed_offset": i})
            for i, s in enumerate((2, 4, 8, 16, 32))
        ],
    ),
    "translation": _levels(
        "fraction of width",
        [
            (round(px / 384.0, 6), 1.0, "translate", {"fraction": round(px / 384.0, 6)})
            for px in (1, 2, 4, 8, 16, 32)
        ],
    ),
    "rescale_roundtrip": _levels(
        "downscale loss (1 - factor)",
        [
            (round(1.0 - f, 4), 1.0, "rescale_roundtrip", {"factor": f})
            for f in (0.5, 0.35, 0.25, 0.15, 0.08)
        ],
    ),
    "jpeg_reencode": _levels(
        "quality loss (100 - quality)",
        [(100 - q, 1.0, "jpeg_reencode", {"quality": q}) for q in (95, 85, 70, 50, 30)],
    ),
    # Region fraction is this family's *magnitude*, so its extent field stays at
    # 1.0: response curves group by extent and sweep magnitude within a group,
    # and setting both to the same value would split the curve into singletons.
    "region_recolour": _levels(
        "region fraction of frame",
        [
            (f, 1.0, "region_recolour", {"region_fraction": f})
            for f in (0.001, 0.005, 0.02, 0.05, 0.15)
        ],
    ),
}

# A cut-down grid for --quick: one cheap level per family, used to smoke-test
# the harness end to end without paying for the full sweep.
QUICK_GRID = {name: [levels[0], levels[-1]] for name, levels in GRID.items()}


def grid_size(grid=None):
    return sum(len(levels) for levels in (grid or GRID).values())
