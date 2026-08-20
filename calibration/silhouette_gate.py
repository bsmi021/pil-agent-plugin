#!/usr/bin/env python
"""Discrimination gate for scripts/pil_silhouette.py.

Self-contained per docs/phase3-build-plan.md §1: imports ``bootstrap_quantile``
and ``alpha_for`` from ``calibration/derive.py`` read-only, imports
``blob_object`` from ``calibration/scenes.py`` read-only, and never writes to
``scripts/detection_limits.json`` or to any other shared calibration file.

Method, mirroring phase 2's Neyman-Pearson methodology:

*   NO-OP CONTROL SET. blob_object rendered at CONTROL_SEEDS, then subjected
    to pose / resampling perturbations that the tools claim scale-invariance
    for -- rescale_roundtrip (mild), translate (a few pixels), gaussian_blur
    (small radius), jpeg_reencode (high quality), and ROTATION (implemented
    locally: perturb.py has no rotation op, and rotation is the exact class
    the build plan's own §3.2 names as a hard test for silhouette
    descriptors -- an elongated blob's axis-aligned bbox moves under
    rotation, so fill_ratio and orientation_histogram are provably sensitive
    to it and the noise floor for both must be measured against a rotating
    control, not sidestepped).

*   REAL-CHANGE FIXTURES. The same blob with a rectangular notch cut into
    it at five magnitudes (5%, 10%, 20%, 30%, 50% of the blob's area) --
    a genuine shape change with monotone ground-truth magnitude. The notch
    fixtures are BUILT LOCALLY in this file for the same reason A5-CC
    draws its own count fixtures locally: modifying calibration/scenes.py
    to add a ``notch`` parameter to blob_object is off-limits (see the
    file ownership register), and the fixture must sit on the same
    PREVIEW_BG so the border-median mask reads consistently.

*   THRESHOLDS. Per descriptor, the threshold is the upper bound of a
    bootstrap CI on Q(1-alpha) of the no-op delta distribution, at
    ``alpha_for(n)`` derived from the actual no-op sample size. Detection
    limit uses derive.py's "clears the threshold and stays clear" rule
    (never a first-crossing that dips later).

*   DEMOTION. If a descriptor's smallest reshape magnitude that clears its
    threshold is not meaningfully smaller than a shape change anyone
    would care about (an arbitrary ~5% area notch as a lower reference),
    the descriptor is DEMOTED with numbers -- exactly the sanctioned
    escape hatch docs/phase3-scope.md open question 3 and
    docs/phase3-handoff.md §5 both flag. The gate answers with numbers
    whether that outcome held; it does not decide policy.

CLI usage (from repo root):

    python calibration/silhouette_gate.py --outdir runs/2026-08-20-silhouette-discrimination

The tool is invoked as a subprocess by module name (never imported), per
docs/phase3-build-plan.md §1: "invokes its own new tool as a CLI subprocess,
the same way calibration/measure.py invokes the existing tools ... never by
importing the tool's functions".
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
CALIBRATION = REPO_ROOT / "calibration"
for _path in (SCRIPTS, CALIBRATION):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from derive import alpha_for, bootstrap_quantile  # noqa: E402  read-only import
from scenes import (  # noqa: E402  read-only import
    CONTROL_SEEDS,
    PREVIEW_BG,
    SCENE_SIZE,
    blob_object,
)
import perturb  # noqa: E402  read-only import

TOOL = SCRIPTS / "pil_silhouette.py"

# Reference shape-change magnitude, in fraction-of-blob-area removed. If a
# descriptor's detection limit sits at or above this, the descriptor is
# demoted: a 5% area notch is a modest but visible change to a compact
# silhouette; anything less discriminating than that is not shape-sensitive
# in any operationally useful sense.
REFERENCE_NOTCH_MAGNITUDE = 0.05

# No-op perturbation grid. Chosen to bracket the pose/resampling noise
# space the tools already promise scale-invariance for; magnitudes at the
# mild end where the noise floor gets measured, per phase 2 methodology.
NO_OP_PLAN = [
    ("rescale_roundtrip", {"factor": 0.5}),
    ("rescale_roundtrip", {"factor": 0.35}),
    ("translate", {"fraction": round(1.0 / 384.0, 6)}),      # ~1px on SCENE_SIZE width
    ("translate", {"fraction": round(2.0 / 384.0, 6)}),      # ~2px
    ("translate", {"fraction": round(4.0 / 384.0, 6)}),      # ~4px
    ("gaussian_blur", {"radius": 0.5}),
    ("gaussian_blur", {"radius": 1.0}),
    ("jpeg_reencode", {"quality": 95}),
    ("jpeg_reencode", {"quality": 85}),
    ("rotate_local", {"degrees": 3}),
    ("rotate_local", {"degrees": 6}),
    ("rotate_local", {"degrees": 12}),
]

# Reshape (real-change) magnitudes, expressed as fraction of the blob's
# area removed by a centred rectangular notch cut into the blob's right
# side. Monotone by construction.
NOTCH_MAGNITUDES = (0.05, 0.10, 0.20, 0.30, 0.50)


# --- Local rotation (perturb.py has no rotate op) ---------------------------


def rotate_local(img, degrees):
    """Rotate about the frame centre with the PREVIEW_BG backdrop as fill.

    Documented here rather than added to calibration/perturb.py: that file
    is on the no-touch list, and this gate is the only caller that needs
    rotation. Fill colour must be PREVIEW_BG so the border-median mask
    still classifies the rotation's exposed corners as background rather
    than as new "object" pixels -- a rotate that filled with black would
    fabricate a new blob at the frame corners on every step.
    """
    return img.rotate(
        float(degrees),
        resample=Image.BILINEAR,
        expand=False,
        fillcolor=PREVIEW_BG,
    )


def _apply(op, img, params):
    if op == "rotate_local":
        return rotate_local(img, **params)
    return perturb.apply(op, img, params)


# --- Reshape fixture --------------------------------------------------------


def blob_with_notch(size=SCENE_SIZE, seed=101, notch_area_fraction=0.20):
    """Same blob as scenes.blob_object, with a rectangular notch cut out.

    Reproduces blob_object's geometry EXACTLY (colour, box, radius, seeded
    placement offset) and then overpaints a centred rectangular notch cut
    into the blob's right side. Notch area is
    ``notch_area_fraction * blob_bbox_area`` -- a monotone magnitude axis
    for the response curve.

    Uses PREVIEW_BG as the fill so the notch reads as background under
    the same border-median mask rule the base scene does.
    """
    # ---- Reproduce blob_object exactly (do NOT import + modify) -----------
    from scenes import lcg  # local import, read-only, same behaviour as scenes

    w, h = size
    rnd = lcg(seed + 17)
    offset = (int(next(rnd) * 7) - 3, int(next(rnd) * 7) - 3)
    box_w, box_h = int(round(w * 0.38)), int(round(h * 0.43))
    left = int(round((w - box_w) / 2)) + offset[0]
    top = int(round((h - box_h) / 2)) + offset[1]
    right, bottom = left + box_w - 1, top + box_h - 1
    radius = max(8, int(round(min(box_w, box_h) * 0.28)))
    img = Image.new("RGB", size, PREVIEW_BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([left, top, right, bottom], radius=radius, fill=(150, 178, 210))

    # ---- Notch: centred vertically on the blob, cut into the right side ---
    blob_area = box_w * box_h
    notch_area = notch_area_fraction * blob_area
    # Aspect ratio 1:1 for the notch, clipped to fit the blob height.
    notch_side = max(2, int(round(notch_area ** 0.5)))
    notch_side = min(notch_side, box_h - 4, box_w - 4)
    notch_top = top + (box_h - notch_side) // 2
    notch_left = right - notch_side + 1
    draw.rectangle(
        [notch_left, notch_top, right + 1, notch_top + notch_side - 1],
        fill=PREVIEW_BG,
    )
    return img


# --- Measurement via subprocess ---------------------------------------------


def measure(image_path):
    """Invoke pil_silhouette.py on one image and return the parsed payload.

    The tool is invoked as a CLI subprocess by design (see module
    docstring). A non-zero return code is fatal to the gate run rather
    than being silently absorbed -- a control-set image the tool cannot
    read is exactly the kind of contamination that turns a "measured"
    noise floor into a fantasy.
    """
    proc = subprocess.run(
        [sys.executable, str(TOOL), str(image_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"pil_silhouette exit {proc.returncode} on {image_path}\n"
            f"STDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


def descriptor_pair(base_payload, other_payload):
    """Return (fill_ratio_delta, p2a_delta, orientation_l1) or Nones."""
    base = base_payload["image"]["descriptors"]
    other = other_payload["image"]["descriptors"]
    fr_a, fr_b = base["fill_ratio"], other["fill_ratio"]
    p2a_a, p2a_b = base["perimeter_squared_over_area"], other["perimeter_squared_over_area"]
    hist_a, hist_b = base["orientation_histogram"], other["orientation_histogram"]
    fr_delta = None if fr_a is None or fr_b is None else abs(fr_b - fr_a)
    p2a_delta = None if p2a_a is None or p2a_b is None else abs(p2a_b - p2a_a)
    hist_delta = None
    if hist_a is not None and hist_b is not None:
        hist_delta = float(sum(abs(a - b) for a, b in zip(hist_a, hist_b)))
    return {
        "fill_ratio_delta": None if fr_delta is None else round(fr_delta, 6),
        "perimeter_squared_over_area_delta": None if p2a_delta is None else round(p2a_delta, 6),
        "orientation_histogram_l1_delta": None if hist_delta is None else round(hist_delta, 6),
    }


# --- The sweeps -------------------------------------------------------------


def run_sweep(outdir):
    """Run the full no-op + reshape corpus and record every measurement.

    Everything is written to a fresh temporary directory per image so the
    tool sees a real file path (its whole contract is "give me a file"),
    and the corpus itself is never persisted -- the gate bundle keeps the
    numbers, not the images.
    """
    outdir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="silhouette-gate-") as tmpdir:
        tmp = Path(tmpdir)

        no_op_records = []
        reshape_records = []
        base_records = []

        # Base measurements per seed, so a delta is always vs the SAME-seed
        # base rather than a different-seeded draw of "the same" object.
        seed_base_payloads = {}
        for seed in CONTROL_SEEDS:
            base_path = tmp / f"base_seed{seed}.png"
            blob_object(seed=seed).save(base_path)
            payload = measure(base_path)
            seed_base_payloads[seed] = payload
            base_records.append({"seed": seed, "descriptors": payload["image"]["descriptors"], "flags": payload["image"]["flags"]})

        # NO-OP CONTROL SET: base image passed through pose/resample ops.
        for seed in CONTROL_SEEDS:
            base_img = blob_object(seed=seed)
            base_payload = seed_base_payloads[seed]
            for op, params in NO_OP_PLAN:
                perturbed = _apply(op, base_img, params)
                perturbed_path = tmp / f"noop_{op}_{seed}.png"
                # Some perturbations return RGB; PNG-save handles both.
                perturbed.save(perturbed_path)
                perturbed_payload = measure(perturbed_path)
                deltas = descriptor_pair(base_payload, perturbed_payload)
                no_op_records.append({
                    "seed": seed,
                    "op": op,
                    "params": params,
                    "deltas": deltas,
                    "perturbed_flags": perturbed_payload["image"]["flags"],
                })

        # RESHAPE FIXTURES: notched blob vs base.
        for seed in CONTROL_SEEDS:
            base_payload = seed_base_payloads[seed]
            for magnitude in NOTCH_MAGNITUDES:
                notched_path = tmp / f"reshape_{seed}_{int(magnitude*100):02d}.png"
                blob_with_notch(seed=seed, notch_area_fraction=magnitude).save(notched_path)
                notched_payload = measure(notched_path)
                deltas = descriptor_pair(base_payload, notched_payload)
                reshape_records.append({
                    "seed": seed,
                    "magnitude": magnitude,
                    "deltas": deltas,
                    "flags": notched_payload["image"]["flags"],
                })

    return {"base": base_records, "no_op": no_op_records, "reshape": reshape_records}


# --- Analysis ---------------------------------------------------------------


DESCRIPTOR_KEYS = (
    ("fill_ratio", "fill_ratio_delta"),
    ("perimeter_squared_over_area", "perimeter_squared_over_area_delta"),
    ("orientation_histogram_l1", "orientation_histogram_l1_delta"),
)


def _clean(values):
    return [float(v) for v in values if v is not None and np.isfinite(float(v))]


def _detection_limit(points, threshold):
    """Smallest magnitude whose median clears threshold AND stays clear.

    Mirrors derive.py's _detection_limit rule verbatim in spirit: a lucky
    crossing that dips at a larger magnitude does not count. Publishing
    a first-crossing would understate the descriptor's real usefulness
    exactly the way derive.py's comment warns against.
    """
    if threshold is None or not points:
        return {"magnitude": None, "reason": "no threshold available"}
    ordered = sorted(points, key=lambda p: p["magnitude"])
    for index, point in enumerate(ordered):
        if point["median"] is None:
            continue
        if all(
            (later["median"] is not None and later["median"] > threshold)
            for later in ordered[index:]
        ):
            return {
                "magnitude": point["magnitude"],
                "median_at_limit": point["median"],
                "threshold": threshold,
                "reason": "first magnitude whose median clears threshold and stays clear",
            }
    return {
        "magnitude": None,
        "threshold": threshold,
        "max_magnitude_tested": ordered[-1]["magnitude"],
        "median_at_max": ordered[-1]["median"],
        "reason": "not detected at any tested magnitude",
    }


def analyse(sweep_records):
    """Turn raw sweep records into (thresholds, response curves, verdicts)."""
    no_op = sweep_records["no_op"]
    reshape = sweep_records["reshape"]

    per_descriptor = {}
    for descriptor, delta_key in DESCRIPTOR_KEYS:
        # --- Threshold from no-op deltas ---------------------------------
        no_op_deltas = _clean([r["deltas"][delta_key] for r in no_op])
        n = len(no_op_deltas)
        alpha = alpha_for(n)
        threshold_block = bootstrap_quantile(
            no_op_deltas, alpha, f"silhouette|{descriptor}"
        )
        threshold = threshold_block["threshold"]

        # --- Response curve on reshape magnitudes ------------------------
        magnitudes = sorted({r["magnitude"] for r in reshape})
        points = []
        for magnitude in magnitudes:
            at = _clean([
                r["deltas"][delta_key]
                for r in reshape if r["magnitude"] == magnitude
            ])
            if not at:
                points.append({
                    "magnitude": magnitude, "n": 0, "median": None,
                    "min": None, "max": None,
                })
                continue
            arr = np.asarray(at, dtype=np.float64)
            points.append({
                "magnitude": magnitude,
                "n": int(arr.size),
                "median": round(float(np.median(arr)), 8),
                "min": round(float(arr.min()), 8),
                "max": round(float(arr.max()), 8),
            })

        detection = _detection_limit(points, threshold)

        # --- Ship / demote verdict --------------------------------------
        if detection["magnitude"] is None:
            verdict = "DEMOTE"
            reason = (
                "reshape response never clears the no-op noise floor at any "
                "tested magnitude"
            )
        elif detection["magnitude"] > REFERENCE_NOTCH_MAGNITUDE:
            verdict = "DEMOTE"
            reason = (
                f"detection limit {detection['magnitude']} exceeds the reference "
                f"shape-change magnitude {REFERENCE_NOTCH_MAGNITUDE} (a 5% area "
                "notch) -- descriptor cannot resolve modest shape changes above "
                "its own noise floor"
            )
        else:
            verdict = "SHIP"
            reason = (
                f"detection limit {detection['magnitude']} clears the reference "
                f"{REFERENCE_NOTCH_MAGNITUDE} shape-change magnitude"
            )

        per_descriptor[descriptor] = {
            "n_no_op": n,
            "alpha": alpha,
            "no_op_threshold_bootstrap": threshold_block,
            "no_op_distribution": _summarise(no_op_deltas),
            "reshape_response": points,
            "detection_limit": detection,
            "reference_notch_magnitude": REFERENCE_NOTCH_MAGNITUDE,
            "verdict": verdict,
            "verdict_reason": reason,
        }

    return per_descriptor


def _summarise(values):
    if not values:
        return {"n": 0}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "n": int(arr.size),
        "min": round(float(arr.min()), 8),
        "p25": round(float(np.percentile(arr, 25)), 8),
        "median": round(float(np.median(arr)), 8),
        "p75": round(float(np.percentile(arr, 75)), 8),
        "max": round(float(arr.max()), 8),
        "mean": round(float(arr.mean()), 8),
    }


# --- Entry point ------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Discrimination gate for pil_silhouette."
    )
    parser.add_argument(
        "--outdir",
        default=str(REPO_ROOT / "runs" / "2026-08-20-silhouette-discrimination"),
        help="output directory for the gate bundle",
    )
    args = parser.parse_args(argv)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    sweep = run_sweep(outdir)
    analysis = analyse(sweep)

    bundle = {
        "tool": "silhouette_gate",
        "version": "0.4.0",
        "measured_tool": "pil_silhouette",
        "control_seeds": list(CONTROL_SEEDS),
        "no_op_plan": [
            {"op": op, "params": params} for op, params in NO_OP_PLAN
        ],
        "reshape_magnitudes": list(NOTCH_MAGNITUDES),
        "reference_shape_change_magnitude": REFERENCE_NOTCH_MAGNITUDE,
        "descriptors": analysis,
        "notes": [
            "Rotation is included in the no-op class because pose is not a "
            "shape change and the tools' scale-invariance claim implies "
            "pose-invariance for a silhouette-shape descriptor. "
            "calibration/perturb.py has no rotate operation and is on the "
            "no-touch list; this gate implements rotation locally as "
            "Image.rotate(fillcolor=PREVIEW_BG) so exposed corners stay in "
            "the border-median background class.",
            "Reshape fixtures (blob_with_notch) are built here rather than "
            "added to calibration/scenes.py, which is on the no-touch list. "
            "The base geometry is reproduced from scenes.blob_object "
            "verbatim to keep the fixture visually consistent with the "
            "control corpus.",
            "The tool is invoked as a subprocess (never imported), per "
            "docs/phase3-build-plan.md #1 -- calibrating an imported "
            "function would calibrate something the caller never runs.",
        ],
    }

    (outdir / "sweep.json").write_text(
        json.dumps(sweep, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (outdir / "bundle.json").write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Terse stdout summary so a human running the script can see the
    # ship/demote verdicts without opening the JSON.
    print(f"[silhouette_gate] bundle -> {outdir}")
    for descriptor, block in analysis.items():
        print(
            f"  {descriptor:36s} verdict={block['verdict']:6s} "
            f"n={block['n_no_op']} alpha={block['alpha']} "
            f"threshold={block['no_op_threshold_bootstrap']['threshold']} "
            f"detection_limit={block['detection_limit']['magnitude']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
