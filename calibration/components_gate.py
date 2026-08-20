#!/usr/bin/env python
"""Discrimination gate for scripts/pil_components.py -- A5-CC.

Answers two questions with numbers, per docs/phase3-build-plan.md section
3.1's acceptance-criteria list:

*   **How large a spurious component can the no-op control set produce?**
    Take ``blob_object`` (1 true component) and ``multipart_object``
    (3 true components) from ``calibration/scenes.py`` and run each through
    every no-op-class perturbation in ``calibration/perturb.py`` --
    ``rescale_roundtrip`` at high factors, high-quality ``jpeg_reencode``,
    small ``gaussian_blur``, small ``add_noise`` -- then invoke the tool as
    a CLI subprocess (the same way ``calibration/measure.py`` invokes the
    shipped tools) with ``--min-blob-area-fraction 0``, and read the
    largest **spurious** blob's frame-fractional area off the payload. That
    distribution is the noise floor; its bootstrap CI upper bound on
    ``Q(1-alpha)`` at ``alpha_for(n)`` is the floor's derived value.

*   **Would that floor swallow useful signal?** Draw an independent
    real-change corpus directly with ``PIL.ImageDraw`` -- rounded-rectangle
    blobs at a range of pixel areas laid out non-overlapping on the same
    ``PREVIEW_BG`` colour ``scenes.blob_object`` / ``multipart_object``
    already use, at counts of 1 .. 6 blobs -- and check, at every fixture,
    whether the tool at the derived floor reports the fixture's true
    component count. The smallest blob area at which the tool still returns
    the correct count is the useful-signal detection limit; if it sits
    below (or on the same order as) blob sizes anyone plausibly cares about,
    the tool ships. If it doesn't, this script prints numbers and refuses
    to ship, per the plan's demotion path and the codebase-wide
    ``never claim more than you measured`` rule.

Design constraints held throughout:

*   Imports ``bootstrap_quantile`` and ``alpha_for`` from
    ``calibration/derive.py`` **read-only**; imports ``blob_object`` and
    ``multipart_object`` from ``calibration/scenes.py`` **read-only**;
    imports the no-op perturbation ops from ``calibration/perturb.py``
    **read-only**. Does NOT touch derive.py / scenes.py / perturb.py /
    measure.py / distill_detection_limits.py or ``scripts/detection_limits.json``.
*   Invokes ``scripts/pil_components.py`` as a CLI subprocess with the
    active Python interpreter, never by importing the tool's own functions.
    Calibrating the importable functions would calibrate something the
    caller never runs -- ``calibration/measure.py``'s docstring records
    that principle for the whole calibration surface.
*   Writes to the caller-supplied ``--out`` directory only; makes no other
    filesystem changes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

CALIBRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = CALIBRATION_DIR.parent
SCRIPTS = REPO_ROOT / "scripts"

sys.path.insert(0, str(CALIBRATION_DIR))
sys.path.insert(0, str(SCRIPTS))

# Read-only imports, per the plan's section 2.2 file-ownership register.
from derive import alpha_for, bootstrap_quantile  # noqa: E402
from scenes import PREVIEW_BG, blob_object, multipart_object  # noqa: E402
import perturb  # noqa: E402


TOOL = "pil_components.py"

# Control seeds -- fixed here rather than reused from scenes.CONTROL_SEEDS
# because the sweep needs to be extended per-perturbation-flavour and pinning
# the seed set locally keeps the run-to-run bytes identical without adding a
# dependency on that constant's future changes.
CONTROL_SEEDS = (101, 211, 337, 449, 563, 677, 787, 883, 991, 1069)

# No-op perturbations. Each entry: (family, magnitude, op, kwargs). Magnitudes
# were chosen for the perturb.py control class -- the same rescale factors and
# JPEG qualities calibration/measure.py already treats as "control" and every
# scene under them is a re-encode of itself.
NO_OP_PERTURBATIONS = (
    ("identity", None, None, {}),
    ("rescale_roundtrip@0.9", 0.9, "rescale_roundtrip", {"factor": 0.9}),
    ("rescale_roundtrip@0.75", 0.75, "rescale_roundtrip", {"factor": 0.75}),
    ("jpeg_reencode@95", 95, "jpeg_reencode", {"quality": 95}),
    ("jpeg_reencode@85", 85, "jpeg_reencode", {"quality": 85}),
    ("gaussian_blur@0.5", 0.5, "gaussian_blur", {"radius": 0.5}),
    ("gaussian_blur@1.0", 1.0, "gaussian_blur", {"radius": 1.0}),
    # noise: perturb.add_noise takes an explicit seed so run-to-run bytes are
    # reproducible. The seed is derived from the scene seed by perturb.derive_seed
    # so two invocations produce identical noise.
    ("add_noise@sigma=2", 2, "add_noise", {"sigma": 2, "seed_offset": 0}),
    ("add_noise@sigma=4", 4, "add_noise", {"sigma": 4, "seed_offset": 1}),
)

CONTROL_SCENES = (
    ("blob_object", blob_object, 1),
    ("multipart_object", multipart_object, 3),
)

# Real-change fixture: rounded-rectangle blobs laid out in a horizontal row
# on the same PREVIEW_BG the scene builders use. count is the true component
# count; side is the pixel side length of each rounded rectangle.
REAL_CHANGE_FIXTURES = tuple(
    (count, side)
    for count in (1, 2, 3, 4, 5, 6)
    for side in (6, 8, 12, 16, 24, 32, 48)
)

REAL_CHANGE_SIZE = (384, 288)


def _apply_noop(img, family, op, kwargs, seed):
    """Apply one no-op perturbation, resolving perturb.add_noise's seed.

    ``add_noise`` needs an explicit integer seed rather than a magic; every
    other op is a pure function of its args. We derive a deterministic seed
    from the scene seed and the family label so a re-run produces the same
    noise.
    """
    if op is None:
        return img
    fn = perturb.OPERATIONS[op]
    if op == "add_noise":
        seed_offset = kwargs.get("seed_offset", 0)
        real_seed = perturb.derive_seed(family, int(seed), int(seed_offset))
        real_kwargs = {k: v for k, v in kwargs.items() if k != "seed_offset"}
        return fn(img, seed=real_seed, **real_kwargs)
    return fn(img, **kwargs)


def _draw_real_change_fixture(count, side, size=REAL_CHANGE_SIZE):
    """Draw ``count`` non-overlapping rounded rectangles of edge ``side``.

    All blobs share a colour and are laid on the same ``PREVIEW_BG`` colour
    scene.blob_object / multipart_object use, so the fixture is visually
    consistent with the frozen calibration corpus without editing it (§2.3).
    The row layout guarantees the count is a hand-countable literal integer.
    """
    w, h = size
    img = Image.new("RGB", size, PREVIEW_BG)
    draw = ImageDraw.Draw(img)
    if count < 1:
        return img
    # Centre the row of `count` blobs; enforce a gap larger than one pixel so
    # 8-connectivity cannot silently fuse two adjacent components.
    gap = max(4, side // 2)
    total_width = count * side + (count - 1) * gap
    if total_width > w - 8:
        # Wrap onto two rows if necessary so all blobs fit inside the frame.
        per_row = max(1, (w - 8) // (side + gap))
        rows = (count + per_row - 1) // per_row
        row_stride = side + gap
        for i in range(count):
            row = i // per_row
            col = i % per_row
            row_count = per_row if row < rows - 1 else count - per_row * (rows - 1)
            row_total = row_count * side + (row_count - 1) * gap
            x0 = (w - row_total) // 2 + col * (side + gap)
            y0 = (h - rows * row_stride) // 2 + row * row_stride
            radius = max(1, side // 3)
            draw.rounded_rectangle(
                [x0, y0, x0 + side - 1, y0 + side - 1],
                radius=radius,
                fill=(150, 178, 210),
            )
        return img
    x0 = (w - total_width) // 2
    y0 = (h - side) // 2
    radius = max(1, side // 3)
    for i in range(count):
        left = x0 + i * (side + gap)
        draw.rounded_rectangle(
            [left, y0, left + side - 1, y0 + side - 1],
            radius=radius,
            fill=(150, 178, 210),
        )
    return img


def _run_tool(python_exe, image_path, min_frac):
    """Invoke pil_components.py as a subprocess, exactly as an agent would."""
    cmd = [
        python_exe,
        str(SCRIPTS / TOOL),
        str(image_path),
        "--min-blob-area-fraction",
        str(min_frac),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SystemExit(
            f"{TOOL} exited {proc.returncode}\n"
            f"cmd: {' '.join(cmd)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


def measure_noop_controls(python_exe, workdir):
    """Collect max-spurious-blob fractions across the no-op control corpus.

    For each (scene, seed, perturbation) triple: build the perturbed image,
    save it as PNG, invoke the tool with min-blob-area-fraction=0 (so every
    blob is reported), and take the frame fraction of the LARGEST spurious
    blob. Spurious = every blob past the scene's true component count when
    the blobs are ordered by descending area, so the corpus's real blobs
    are excluded from the noise sample.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    records = []
    for scene_name, builder, true_count in CONTROL_SCENES:
        for seed in CONTROL_SEEDS:
            base = builder(seed=seed)
            for family, magnitude, op, kwargs in NO_OP_PERTURBATIONS:
                perturbed = _apply_noop(base, family, op, kwargs, seed)
                # Convert every fixture to RGB before saving so the mask
                # source is border-median (matching scene builders that
                # return no alpha channel).
                if perturbed.mode != "RGB":
                    perturbed = perturbed.convert("RGB")
                image_path = workdir / f"{scene_name}_{seed}_{family}.png"
                perturbed.save(image_path)
                payload = _run_tool(python_exe, image_path, 0.0)
                comps = sorted(
                    payload["components"], key=lambda c: -c["area_pixels"]
                )
                spurious = comps[true_count:]
                max_spurious_fraction = (
                    max(c["area_fraction_of_frame"] for c in spurious)
                    if spurious
                    else 0.0
                )
                spurious_pixel_max = (
                    max(c["area_pixels"] for c in spurious) if spurious else 0
                )
                records.append(
                    {
                        "scene": scene_name,
                        "true_count": true_count,
                        "seed": seed,
                        "perturbation": family,
                        "magnitude": magnitude,
                        "reported_count": payload["component_count"],
                        "spurious_count": len(spurious),
                        "max_spurious_fraction": max_spurious_fraction,
                        "max_spurious_pixels": spurious_pixel_max,
                    }
                )
    return records


def measure_real_change(python_exe, workdir, min_blob_area_fraction):
    """Detection curve: does the derived floor still recover known counts?

    Draw the (count, side) grid, invoke the tool with the derived floor,
    and record whether the reported count matches the ground truth. This is
    the honest test that the noise floor did not silently erase the signal
    it was meant to separate from noise.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    records = []
    for count, side in REAL_CHANGE_FIXTURES:
        img = _draw_real_change_fixture(count, side)
        image_path = workdir / f"real_change_c{count}_s{side}.png"
        img.save(image_path)
        payload = _run_tool(python_exe, image_path, min_blob_area_fraction)
        blob_area_fraction = (side * side) / float(REAL_CHANGE_SIZE[0] * REAL_CHANGE_SIZE[1])
        records.append(
            {
                "true_count": count,
                "side_pixels": side,
                "per_blob_area_pixels": side * side,
                "per_blob_area_fraction": blob_area_fraction,
                "reported_count": payload["component_count"],
                "correct": payload["component_count"] == count,
                "min_blob_area_pixels_applied": payload["min_blob_area_pixels_applied"],
            }
        )
    return records


def summarise_noop(records):
    """Return the raw stats and the bootstrap-CI-derived floor."""
    fractions = [r["max_spurious_fraction"] for r in records]
    n = len(fractions)
    alpha = alpha_for(n)
    stats = bootstrap_quantile(fractions, alpha, "components|noop_max_spurious_fraction")
    zero_count = sum(1 for r in records if r["spurious_count"] == 0)
    return {
        "n": n,
        "alpha": alpha,
        "control_records": records,
        "fractions_summary": {
            "min": min(fractions),
            "max": max(fractions),
            "mean": sum(fractions) / n if n else None,
            "zero_spurious_count_share": zero_count / n if n else None,
        },
        "derived_floor_fraction": stats["threshold"],
        "derived_floor_bootstrap": stats,
    }


def detection_limit(real_change_records):
    """Smallest per-blob area fraction that always yields the correct count.

    "Always" is over the true counts present at that per-blob size: at a
    given side, if any (count, side) fixture at that size mis-reports its
    count, that size cannot be trusted. The detection limit is the smallest
    side whose entire row is correct AND every larger side is also correct.
    """
    sides = sorted({r["side_pixels"] for r in real_change_records})
    per_side_correct = {
        side: all(r["correct"] for r in real_change_records if r["side_pixels"] == side)
        for side in sides
    }
    for i, side in enumerate(sides):
        if all(per_side_correct[s] for s in sides[i:]):
            example = next(
                r for r in real_change_records if r["side_pixels"] == side
            )
            return {
                "smallest_side_pixels": side,
                "per_blob_area_pixels": side * side,
                "per_blob_area_fraction": example["per_blob_area_fraction"],
                "reason": "smallest per-blob size that returns the correct count and stays correct at every larger size",
            }
    return {
        "smallest_side_pixels": None,
        "reason": "no tested size returned the correct count consistently",
        "per_side_correct": per_side_correct,
    }


def decide_verdict(noop_summary, detection):
    """Ship-or-demote based on the numbers, not on hope.

    Ship condition: the detection limit's per-blob area fraction is at
    least an order of magnitude larger than the derived noise-floor
    fraction, so there is a real gap between "noise" and "signal". Demote
    otherwise, with the numbers in the payload so the reason is auditable.
    """
    floor = noop_summary["derived_floor_fraction"] or 0.0
    signal = detection.get("per_blob_area_fraction")
    if signal is None:
        return {
            "ship": False,
            "reason": "no per-blob size in the tested corpus reliably returns the correct count; useful signal is unresolved",
            "noise_floor_fraction": floor,
            "detection_limit_fraction": None,
        }
    gap_ratio = (signal / floor) if floor > 0 else float("inf")
    ship = gap_ratio >= 10.0
    return {
        "ship": bool(ship),
        "noise_floor_fraction": floor,
        "detection_limit_fraction": signal,
        "signal_to_floor_ratio": gap_ratio,
        "ship_criterion": "signal_to_floor_ratio >= 10 (one decade separation between noise floor and useful signal)",
        "reason": (
            "noise floor and signal are separated by at least a decade"
            if ship
            else "noise floor is within one decade of the smallest reliably-detected blob; demote per docs/phase3-handoff.md section 5"
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Discrimination gate for pil_components.py (A5-CC)."
    )
    parser.add_argument(
        "--out",
        required=True,
        help="output directory for the run bundle (created if absent)",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="python interpreter to invoke the tool with (default: the running interpreter)",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    scratch = out_dir / "scratch"
    scratch.mkdir(exist_ok=True)

    control_records = measure_noop_controls(args.python, scratch / "controls")
    noop_summary = summarise_noop(control_records)

    derived_floor = noop_summary["derived_floor_fraction"] or 0.0
    real_change_records = measure_real_change(
        args.python, scratch / "real_change", derived_floor
    )
    detection = detection_limit(real_change_records)
    verdict = decide_verdict(noop_summary, detection)

    payload = {
        "tool_under_test": "scripts/pil_components.py",
        "method": {
            "noise_floor": (
                "no-op controls: blob_object (n=1 true component) and "
                "multipart_object (n=3 true components) under identity, "
                "rescale_roundtrip@{0.9,0.75}, jpeg_reencode@{95,85}, "
                "gaussian_blur@{0.5,1.0}, add_noise@{sigma=2,4}. "
                "Metric: largest spurious blob frame-fraction, threshold = "
                "bootstrap CI upper bound of Q(1-alpha)."
            ),
            "real_change": (
                "independent PIL.ImageDraw corpus: rounded rectangles at "
                "counts {1..6} and sides {6,8,12,16,24,32,48} pixels on "
                "PREVIEW_BG, laid non-overlapping so the count is a "
                "hand-countable literal integer. Detection limit = smallest "
                "per-blob side that yields the correct count at every "
                "larger size."
            ),
            "alpha_rule": "alpha = 0.01 when n >= 300, 0.05 when n >= 60, 0.10 when n >= 30 (per calibration/derive.ALPHA_LADDER)",
        },
        "noop_controls": noop_summary,
        "real_change_curve": real_change_records,
        "detection": detection,
        "verdict": verdict,
    }

    (out_dir / "derived-thresholds.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Human-friendly hop for a reader eyeballing the bundle.
    print(
        json.dumps(
            {
                "n_noop_controls": noop_summary["n"],
                "alpha": noop_summary["alpha"],
                "derived_floor_fraction": noop_summary["derived_floor_fraction"],
                "detection_limit_fraction": detection.get("per_blob_area_fraction"),
                "ship": verdict["ship"],
                "reason": verdict["reason"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
