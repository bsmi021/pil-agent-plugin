#!/usr/bin/env python
"""W1 evidence-bundle measurement script.

Produces measurements.json: the before/after table over every ALPHA_CORPUS
scene, the sub-pixel excursion table at all four angles, the MEDIANCUT
centre-selection residual comparison (open question 11.3), the
ALPHA_FOREGROUND_MIN sweep on the WEIGHTED statistics (open question 11.2),
and the weighted-error-vs-partial_coverage_share data (open question 11.6).

NOT EXECUTED as part of writing this script -- see the bundle README for why
(this session's sandbox denied every attempt to invoke a Python process, `uv
run pytest` included). Run it yourself:

    uv run python runs/2026-08-20-w1-alpha-fix/measure.py

before trusting any number this script would have produced. Nothing in
measurements.json is populated by hand; if the file is absent or you are
reading this comment inside it, it was not run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
CALIBRATION = REPO_ROOT / "calibration"
for _path in (SCRIPTS, CALIBRATION):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import alpha_truth  # noqa: E402
import scenes  # noqa: E402
from pil_common import quantize_palette  # noqa: E402


def run_tool(script_name, *args):
    cmd = [sys.executable, str(SCRIPTS / script_name), *[str(a) for a in args]]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{script_name} exited {proc.returncode}: {proc.stderr}")
    return json.loads(proc.stdout)


def before_after_table(tmp_dir):
    """Bias in luminance/saturation/accent_fraction against alpha_truth, for
    every ALPHA_CORPUS scene, before (pre-0.4.0 unweighted) and after (the
    tool's live --foreground reading) the fix."""
    rows = []
    for label, scene, params in scenes.ALPHA_CORPUS:
        record = alpha_truth.scene_truth(label, scene, params)
        rgba_path = tmp_dir / f"{label}.png"
        scenes.build_alpha(scene, **params).save(rgba_path)
        result = run_tool("pil_palette_diff.py", rgba_path, "--foreground")
        image = result["images"]["a"]
        row = {
            "label": label,
            "source": image["foreground"]["source"],
            "partial_coverage_share": record["partial_share_of_foreground"],
            "pre_fix_bias": record["bias"],
            "post_fix": None,
        }
        if image["foreground"]["source"] == "alpha" and record["truth"]["luminance_mean"] is not None:
            truth = record["truth"]
            row["post_fix"] = {
                "luminance_mean_error": round(image["luminance"]["mean"] - truth["luminance_mean"], 4),
                "saturation_mean_error": round(image["saturation"]["mean"] - truth["saturation_mean"], 4),
                "accent_fraction_error": round(image["accent_pixel_fraction"] - truth["accent_fraction"], 6)
                if truth["accent_fraction"] is not None
                else None,
            }
        rows.append(row)
    return rows


def sub_pixel_excursion_table(tmp_dir):
    rows = []
    for angle in scenes.ALPHA_PHASE_ANGLES:
        readings, truths = [], []
        for offset in scenes.ALPHA_PHASE_OFFSETS:
            image = scenes.alpha_blade(angle_deg=angle, phase_px=offset, **scenes.ALPHA_PHASE_BLADE)
            path = tmp_dir / f"phase_a{angle}_p{offset}.png"
            image.save(path)
            result = run_tool("pil_palette_diff.py", path, "--foreground")
            readings.append(result["images"]["a"]["luminance"]["mean"])
            rgb, alpha = alpha_truth.truth_arrays(image)
            truths.append(alpha_truth.weighted_stats(rgb, alpha)["luminance_mean"])
        rows.append(
            {
                "angle_deg": angle,
                "commensurate": angle in scenes.ALPHA_COMMENSURATE_ANGLES,
                "readings": readings,
                "truths": truths,
                "reading_excursion": round(max(readings) - min(readings), 4),
                "truth_excursion": round(max(truths) - min(truths), 4),
                "excess_excursion": round(
                    (max(readings) - min(readings)) - (max(truths) - min(truths)), 4
                ),
            }
        )
    return rows


def mediancut_residual(n_colors=8, k_replicate=16):
    """Open question 11.3: unweighted-centre coverage-weighted palette (the
    shipped approach) vs a coverage-replicated quantisation (a measurement
    instrument, not a shipping candidate -- see pil_common.quantize_palette's
    docstring for why replication was rejected as the shipped fix)."""
    results = {}
    labels = ("glass_a64", "glass_a128", "glass_a192", "alpha_ladder")
    scene_by_label = {label: (scene, params) for label, scene, params in scenes.ALPHA_CORPUS}
    for label in labels:
        scene, params = scene_by_label[label]
        rgba = scenes.build_alpha(scene, **params)
        true_rgb, alpha = alpha_truth.truth_arrays(rgba)
        mask = alpha >= 8
        weights = alpha[mask].astype(np.float64) / 255.0
        strip = Image.fromarray(true_rgb[mask].reshape(1, -1, 3).astype(np.uint8), "RGB")

        shipped = quantize_palette(strip, n_colors, weights=weights)

        replicate_counts = np.maximum(1, np.round(weights * k_replicate)).astype(int)
        replicated_pixels = np.repeat(true_rgb[mask], replicate_counts, axis=0)
        replicated_strip = Image.fromarray(
            replicated_pixels.reshape(1, -1, 3).astype(np.uint8), "RGB"
        )
        replicated = quantize_palette(replicated_strip, n_colors)

        results[label] = {
            "shipped_unweighted_centres": shipped,
            "replicated_k16_reference": replicated,
        }
    return results


def alpha_floor_sweep_weighted(floors=(8, 16, 32, 64)):
    """Open question 11.2: does the weighted statistic stay insensitive to
    ALPHA_FOREGROUND_MIN across 8-64, per corpus scene?"""
    rows = []
    for label, scene, params in scenes.ALPHA_CORPUS:
        if label in ("degenerate_empty", "degenerate_opaque"):
            continue
        rgba = scenes.build_alpha(scene, **params)
        rgb, alpha = alpha_truth.truth_arrays(rgba)
        sweep = []
        for floor in floors:
            stats = alpha_truth.weighted_stats(rgb, alpha, alpha_min=floor)
            sweep.append({"floor": floor, "luminance_mean": stats["luminance_mean"]})
        means = [s["luminance_mean"] for s in sweep if s["luminance_mean"] is not None]
        rows.append(
            {
                "label": label,
                "sweep": sweep,
                "spread": round(max(means) - min(means), 4) if len(means) >= 2 else None,
            }
        )
    return rows


def weighted_error_vs_partial_coverage(before_after_rows):
    """Open question 11.6: post-fix weighted-statistic error against
    partial_coverage_share, over every scene that has a post_fix reading."""
    points = [
        {
            "label": row["label"],
            "partial_coverage_share": row["partial_coverage_share"],
            "luminance_mean_error": row["post_fix"]["luminance_mean_error"],
        }
        for row in before_after_rows
        if row["post_fix"] is not None
    ]
    return points


def main():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        before_after = before_after_table(tmp_dir)
        sub_pixel = sub_pixel_excursion_table(tmp_dir)

    payload = {
        "before_after_table": before_after,
        "sub_pixel_excursion_table": sub_pixel,
        "mediancut_residual_11_3": mediancut_residual(),
        "alpha_floor_sweep_weighted_11_2": alpha_floor_sweep_weighted(),
        "weighted_error_vs_partial_coverage_share_11_6": weighted_error_vs_partial_coverage(
            before_after
        ),
    }
    out = Path(__file__).resolve().parent / "measurements.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
