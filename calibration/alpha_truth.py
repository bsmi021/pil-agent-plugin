#!/usr/bin/env python
"""Exact alpha-weighted ground truth for the RGBA calibration corpus.

Every number here is computed from the corpus image itself, not estimated from
it. Each scene in ``scenes.ALPHA_CORPUS`` is built as RGBA, and its stored
straight (un-premultiplied) RGB together with its stored alpha *is* the truth:
alpha/255 is the fraction of the pixel the object covers, and the RGB channels
are the object's colour at full strength. No fitting, no sampling, no tolerance.

WEIGHTING CONVENTION AND ITS PROVENANCE
---------------------------------------
The convention mirrored here is the one implemented by ``_visible_pixels`` in

    C:\\Projects\\tms-heim\\tools\\synty_asset_index\\palette.py

which lives in a **different repository** (the tms-heim workspace) and is not
importable from this project -- it is a convergence target, not a dependency.
The claims below were established by reading that file on 2026-08-20; they are
pinned to that reading and could drift if the file changes. Specifically
confirmed in ``_visible_pixels`` and its caller:

*   A pixel is visible iff ``alpha >= alpha_threshold``, with the threshold
    defaulting to 8 -- the same floor and the same inclusive comparison as
    pil_common.ALPHA_FOREGROUND_MIN.
*   Each visible pixel enters the accumulators with weight ``alpha / 255.0``
    (the fourth element of every tuple ``_visible_pixels`` returns), so partial
    coverage contributes partially. ``_quantized_groups`` multiplies each
    colour by that weight and divides by the summed weight -- a coverage-
    weighted mean, and ``_silhouette_descriptor`` weights occupancy the same way.
*   The colour weighted is the pixel's own stored RGB, taken straight from the
    PNG by ``_decode_pixel`` and converted by ``rgb_to_oklab``. It is never
    composited against a background first, so the value carried is the object's
    true colour rather than a darkened one.
*   ``has_transparency = any(alpha < 255 ...)`` disables background subtraction
    entirely for a file with real transparency, which is the same precedence
    pil_common.foreground_mask applies when it treats alpha as authoritative.

Honest limit on that provenance: palette.py applies this convention to *palette
extraction* (OKLab cluster centres and silhouette occupancy). It does not
compute luminance, saturation or an accent fraction. Extending the same
weighting to those three statistics is this module's own step, taken so the
reference and the tools under test differ in exactly one respect -- the
weighting -- rather than in the statistic as well.

WHAT EACH RECORD CONTAINS
-------------------------
For every scene, three readings of the same pixels:

*   ``truth``     -- the true colour, coverage-weighted, over the visible mask.
*   ``tool_path`` -- the PRE-0.4.0 unweighted reading: the colour after
    pil_common.load_rgb_alpha flattens onto black, averaged with every masked
    pixel at equal weight. Since 0.4.0 (the W1 alpha/coverage fix,
    docs/aaa-build-plan.md #3) this is no longer what the bundled tools
    compute on the alpha path -- they now read the straight (un-premultiplied)
    colour and weight it by coverage, which is exactly ``truth`` below to
    within the tolerance tests/test_alpha_weighting.py holds it to. This field
    is retained, unrenamed, as the fixed reference point for what the fix
    removed; every ``bias`` entry it feeds is now a historical measurement of
    the pre-fix defect, not a live reading of the tools.
*   ``bias``      -- tool_path minus truth, per statistic. Pre-0.4.0 this was
    the defect's magnitude; post-0.4.0 it is the pre-fix defect's magnitude,
    unchanged, since neither operand it is computed from has moved.

PIL's own conversions do the colour maths on both sides (``convert("L")`` for
luminance, ``convert("HSV")`` for saturation, pil_common.accent_mask for the
accent gate), so the two readings differ only in weighting and in whether the
colour was darkened by compositing -- never in the formula.

Run ``python calibration/alpha_truth.py`` to dump the corpus inventory as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

CALIBRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = CALIBRATION_DIR.parent
SCRIPTS = REPO_ROOT / "scripts"

for _path in (CALIBRATION_DIR, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import scenes  # noqa: E402
from pil_common import (  # noqa: E402
    ALPHA_FOREGROUND_MIN,
    DEFAULT_ACCENT_SAT_MIN,
    DEFAULT_ACCENT_VAL_MIN,
    accent_mask,
)

# Alpha floors swept in the inventory. Bracket ALPHA_FOREGROUND_MIN (8) all the
# way to "nearly opaque only", which is the question a maintainer reaching for
# recalibration will ask first: does raising the floor fix the bias?
ALPHA_FLOOR_SWEEP = (8, 16, 32, 64, 96, 128, 160, 192, 224, 250)


def truth_arrays(rgba):
    """(true RGB uint8 HxWx3, alpha uint8 HxW) for a built RGBA scene.

    The RGB channels of a straight-alpha image already *are* the true colour;
    reading them is the whole extraction. scenes._resolve_rgba is what makes
    that so -- it resamples premultiplied and un-premultiplies once, so a fringe
    pixel stores the object's colour rather than a coverage-darkened version.
    """
    arr = np.asarray(rgba.convert("RGBA"), dtype=np.uint8)
    return arr[:, :, :3].copy(), arr[:, :, 3].copy()


def unpremultiply(composited_rgb, alpha, eps=scenes.ALPHA_UNPREMULTIPLY_EPS):
    """Recover straight RGB from a black-composited image and its alpha.

    ``clip(composited / max(alpha/255, eps), 0, 255)``, the inverse of
    compositing ``colour * coverage`` onto black.

    *   ``eps`` is one alpha code value (1/255), the smallest coverage an 8-bit
        alpha channel can represent. Any pixel below it stores alpha 0 and is
        outside every foreground mask, so its recovered colour is never read;
        the guard is there to bound the amplification at 255x rather than to
        produce a meaningful value in that region.
    *   The clip is not cosmetic. Compositing quantises ``colour * coverage`` to
        a byte, so dividing by a small coverage magnifies up to half a code
        value of rounding error by ``255 / alpha`` -- at alpha 8 that is a
        possible 16-code-value overshoot, which can push a bright channel past
        255. Clipping keeps the result inside sRGB; it does not recover the
        information the quantisation destroyed.

    This is the operator a *fix* would need if it worked from the flattened RGB
    the current loader produces. The inventory records how much colour it fails
    to recover (``unpremultiply_recovery``) precisely so that cost is on the
    record: the corpus's own stored RGB is exact, this recovery is not.
    """
    coverage = np.asarray(alpha, dtype=np.float64) / 255.0
    recovered = np.asarray(composited_rgb, dtype=np.float64) / np.maximum(
        coverage, eps
    )[..., None]
    return np.clip(recovered, 0.0, 255.0)


def composite_on_black(rgba):
    """The RGB pil_common.load_rgb_alpha hands every metric, computed in-process.

    Byte-identical to what the loader does: RGBA alpha-composited onto opaque
    black by PIL, then converted to RGB. Reproduced here rather than reading a
    file back so the inventory needs no temporary PNGs; the tests exercise the
    real loader on real files.
    """
    base = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    return Image.alpha_composite(base, rgba.convert("RGBA")).convert("RGB")


def _luminance(rgb_u8):
    return np.asarray(Image.fromarray(rgb_u8, "RGB").convert("L"), dtype=np.float64)


def _saturation(rgb_u8):
    hsv = np.asarray(Image.fromarray(rgb_u8, "RGB").convert("HSV"), dtype=np.float64)
    return hsv[:, :, 1]


def _accent(rgb_u8):
    return accent_mask(
        Image.fromarray(rgb_u8, "RGB"), DEFAULT_ACCENT_SAT_MIN, DEFAULT_ACCENT_VAL_MIN
    )


def _round(value, digits=4):
    return None if value is None else round(float(value), digits)


def weighted_stats(rgb_u8, alpha, alpha_min=ALPHA_FOREGROUND_MIN):
    """Coverage-weighted luminance, saturation and accent share over the mask.

    The reference reading. Membership is ``alpha >= alpha_min`` and weight is
    ``alpha / 255`` -- palette.py's convention, applied to statistics palette.py
    does not itself compute (see the module docstring).
    """
    mask = np.asarray(alpha) >= alpha_min
    if not mask.any():
        return {
            "luminance_mean": None,
            "saturation_mean": None,
            "accent_fraction": None,
            "pixels": 0,
            "coverage_sum": 0.0,
        }
    weights = np.asarray(alpha, dtype=np.float64)[mask] / 255.0
    total = float(weights.sum())
    return {
        "luminance_mean": _round((_luminance(rgb_u8)[mask] * weights).sum() / total),
        "saturation_mean": _round((_saturation(rgb_u8)[mask] * weights).sum() / total),
        "accent_fraction": _round(
            (_accent(rgb_u8)[mask].astype(np.float64) * weights).sum() / total, 6
        ),
        "pixels": int(mask.sum()),
        "coverage_sum": _round(total, 3),
    }


def unweighted_stats(rgb_u8, alpha, alpha_min=ALPHA_FOREGROUND_MIN):
    """The PRE-0.4.0 tools' reading: every masked pixel at equal weight.

    Since the W1 alpha/coverage fix (docs/aaa-build-plan.md #3) this is no
    longer what the bundled tools compute on the alpha path -- see tool_path's
    docstring in scene_truth. Kept, unrenamed, as the fixed pre-fix reference.

    Same mask, same colour maths, same statistics as weighted_stats -- only the
    weights differ, and the RGB handed in is the flattened one. That is the
    single-variable comparison the whole corpus is built around.
    """
    mask = np.asarray(alpha) >= alpha_min
    if not mask.any():
        return {
            "luminance_mean": None,
            "saturation_mean": None,
            "accent_fraction": None,
            "pixels": 0,
        }
    return {
        "luminance_mean": _round(_luminance(rgb_u8)[mask].mean()),
        "saturation_mean": _round(_saturation(rgb_u8)[mask].mean()),
        "accent_fraction": _round(float(_accent(rgb_u8)[mask].mean()), 6),
        "pixels": int(mask.sum()),
    }


def scene_truth(label, scene, params):
    """Full ground-truth record for one corpus scene.

    Everything a test or a report needs about this scene, computed exactly:
    what is in it, what the truth is, what the tools read instead, and how the
    gap moves when the alpha floor is raised.
    """
    rgba = scenes.build_alpha(scene, **params)
    true_rgb, alpha = truth_arrays(rgba)
    flattened = np.asarray(composite_on_black(rgba), dtype=np.uint8)

    visible = alpha >= ALPHA_FOREGROUND_MIN
    partial = visible & (alpha < 255)
    truth = weighted_stats(true_rgb, alpha)
    tool = unweighted_stats(flattened, alpha)

    record = {
        "label": label,
        "scene": scene,
        "params": dict(params),
        "size": list(rgba.size),
        "alpha_foreground_min": ALPHA_FOREGROUND_MIN,
        "pixels": {
            "frame": int(alpha.size),
            "foreground": int(visible.sum()),
            "opaque": int((alpha == 255).sum()),
            "partial": int(partial.sum()),
            "below_floor": int(((alpha > 0) & (alpha < ALPHA_FOREGROUND_MIN)).sum()),
        },
        "partial_share_of_foreground": _round(
            partial.sum() / visible.sum() if visible.any() else 0.0, 6
        ),
        # How many distinct alpha values the partial-coverage pixels take.
        # Recorded next to the share because the two answer different
        # questions: the share says how much of the object is fringe, this says
        # how varied that fringe is. A low count means the edge repeats one
        # sub-pixel phase along its length -- an axis-aligned or 45-degree edge
        # -- so its per-pixel errors are correlated rather than averaging out.
        # It is a signature of that correlation, NOT a predictor of the bias's
        # magnitude: measured here, the 45-degree blade collapses to 48 distinct
        # values against 78-80 off-axis while reading a *smaller* delta than the
        # off-axis angles at the same placement. See alpha_blade's docstring for
        # why placement, not nominal angle, is the variable that moves the bias.
        "distinct_partial_alphas": int(np.unique(alpha[partial]).size),
        "foreground_fraction_of_frame": _round(visible.mean(), 6),
        "truth": truth,
        "tool_path": tool,
        "bias": {
            key: _round(tool[key] - truth[key])
            for key in ("luminance_mean", "saturation_mean", "accent_fraction")
            if truth[key] is not None and tool[key] is not None
        },
        "accent_gate": _accent_gate_record(true_rgb, flattened, visible, partial),
        "unpremultiply_recovery": _recovery_record(true_rgb, flattened, alpha, visible),
        "alpha_floor_sweep": _floor_sweep(true_rgb, flattened, alpha),
    }
    if scene == "alpha_ladder":
        # The one scene whose alpha histogram is known by construction rather
        # than measured; record the construction so a sweep can be checked
        # against it instead of merely observed.
        record["ladder_bands"] = [
            {"alpha": int(step), "pixels": int((alpha == step).sum())}
            for step in scenes.ALPHA_LADDER_STEPS
        ]
    return record


def _accent_gate_record(true_rgb, flattened, visible, partial):
    """How the HSV accent gate treats partial-coverage pixels versus opaque ones.

    The gate is ``S > 100 and V > 60`` on the flattened RGB. Compositing a
    partial pixel onto black scales all three channels by its coverage, which
    leaves HSV S unchanged -- it is a ratio -- but scales V down proportionally.
    So the gate's failure on fringe pixels is a V failure specifically, and the
    pass rates below separate the two populations to show it.
    """
    opaque = visible & ~partial
    true_pass = _accent(true_rgb)
    tool_pass = _accent(flattened)
    eligible = visible & true_pass

    def rate(mask):
        return _round(float(tool_pass[mask].mean()), 6) if mask.any() else None

    return {
        "true_accent_pixels": int(eligible.sum()),
        "tool_pass_rate_partial": rate(partial & true_pass),
        "tool_pass_rate_opaque": rate(opaque & true_pass),
        "admitted_share_of_true_accents": rate(eligible),
        "wrongly_rejected_pixels": int((eligible & ~tool_pass).sum()),
    }


def _recovery_record(true_rgb, flattened, alpha, visible):
    """Colour error left over after un-premultiplying the tools' flattened RGB.

    Quantifies what the compositing step destroys: compositing rounds
    ``colour * coverage`` to a byte, and no division recovers the discarded
    fraction. Weighted by coverage as well as reported at its worst, because the
    worst case lands on the pixels that matter least.
    """
    if not visible.any():
        return {"max_abs_error": None, "coverage_weighted_mean_abs_error": None}
    recovered = unpremultiply(flattened, alpha)
    error = np.abs(recovered - true_rgb.astype(np.float64)).max(axis=-1)
    weights = alpha.astype(np.float64)[visible] / 255.0
    return {
        "max_abs_error": _round(error[visible].max(), 3),
        "coverage_weighted_mean_abs_error": _round(
            (error[visible] * weights).sum() / weights.sum(), 3
        ),
    }


def _floor_sweep(true_rgb, flattened, alpha):
    """Tool reading versus truth at each candidate ALPHA_FOREGROUND_MIN.

    The evidence for the claim that recalibrating the floor is not the fix: it
    shows both what raising the floor does to the bias and what it costs in
    object pixels, at every step, on the same scene.
    """
    sweep = []
    for floor in ALPHA_FLOOR_SWEEP:
        truth = weighted_stats(true_rgb, alpha, alpha_min=floor)
        tool = unweighted_stats(flattened, alpha, alpha_min=floor)
        bias = (
            None
            if truth["luminance_mean"] is None or tool["luminance_mean"] is None
            else _round(tool["luminance_mean"] - truth["luminance_mean"])
        )
        sweep.append(
            {
                "alpha_floor": floor,
                "foreground_pixels": tool["pixels"],
                "tool_luminance_mean": tool["luminance_mean"],
                "truth_luminance_mean": truth["luminance_mean"],
                "luminance_bias": bias,
            }
        )
    return sweep


def inventory(corpus=scenes.ALPHA_CORPUS):
    """Ground-truth records for the whole corpus, in ALPHA_CORPUS order."""
    return [scene_truth(label, scene, params) for label, scene, params in corpus]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Dump exact alpha-weighted ground truth for the RGBA corpus."
    )
    parser.add_argument("--out", default=None, help="write JSON here instead of stdout")
    args = parser.parse_args(argv)

    payload = {
        "alpha_foreground_min": ALPHA_FOREGROUND_MIN,
        "accent_thresholds": {
            "sat_min": DEFAULT_ACCENT_SAT_MIN,
            "val_min": DEFAULT_ACCENT_VAL_MIN,
        },
        "backdrop": list(scenes.ALPHA_BACKDROP),
        "supersample": scenes.ALPHA_SUPERSAMPLE,
        "scenes": inventory(),
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
