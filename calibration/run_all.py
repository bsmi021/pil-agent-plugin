#!/usr/bin/env python
"""One entry point for the whole WP2 calibration.

    uv run python calibration/run_all.py

Regenerates ``runs/2026-08-20-foreground-recalibration/`` from nothing: builds the
synthetic corpus, runs both bundled tools over every pair, derives the
thresholds, detection limits, LCh hue boundaries and constant verdicts, and
writes the ledger.

By default it runs the whole pipeline **twice** and asserts the three JSON
payloads are byte-identical, because "deterministic" is a claim this bundle
makes about itself and an unverified claim of determinism is worth nothing. Pass
``--once`` to skip the second pass while iterating, ``--quick`` for a reduced
grid that smoke-tests the harness end to end in well under a minute.

Nothing here writes to ``scripts/``. Applying the derived constants is the
integrator's job, from this bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import PIL

CALIBRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = CALIBRATION_DIR.parent
sys.path.insert(0, str(CALIBRATION_DIR))

import derive  # noqa: E402
import measure  # noqa: E402
import perturb  # noqa: E402
import scenes  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "runs" / "2026-08-20-foreground-recalibration"
BUNDLE_DATE = "2026-08-20"

JSON_ARTEFACTS = ("derived-thresholds.json", "response-curves.json", "lch-hue-boundaries.json")


def _write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def null_change_check(jobs=None):
    """Re-run the pre-W2 opaque foreground corpus and compare six decimals."""
    historical_path = REPO_ROOT / "runs" / "2026-08-19-phase2-calibration" / "derived-thresholds.json"
    historical = json.loads(historical_path.read_text(encoding="utf-8"))
    units = []
    recipes = tuple(row[0] for row in measure.CONTROL_RECIPES)
    for seed in scenes.CONTROL_SEEDS:
        units.extend(
            measure._control_units(
                "thin_object",
                {"seed": seed},
                seed,
                recipes,
                ("foreground",),
                "control",
                "thin_object",
                ("palette", "structure"),
            )
        )
    with tempfile.TemporaryDirectory(prefix="pil-w2-a21-") as root:
        root = Path(root)
        tools_dir = root / "tools"
        tool_hashes = measure.snapshot_tools(tools_dir)
        records = measure.collect_units(tools_dir, root / "images", units, jobs=jobs)
    observed = derive.control_thresholds(records)["foreground_estimate"]["metrics"]
    expected = historical["control_sets"]["foreground"]["metrics"]
    comparisons = {}
    passed = True
    for metric in sorted(expected):
        old = expected[metric].get("threshold")
        new = observed.get(metric, {}).get("threshold")
        match = old is not None and new is not None and round(float(old), 6) == round(float(new), 6)
        passed = passed and match
        comparisons[metric] = {
            "existing": old,
            "rerun": new,
            "match_to_6dp": match,
        }
    return {
        "status": "PASS" if passed else "FAIL",
        "historical_bundle": "runs/2026-08-19-phase2-calibration",
        "corpus": "thin_object opaque foreground controls, 5 seeds x 20 recipes",
        "tool_sha256": tool_hashes,
        "comparisons": comparisons,
    }


def plan_summary(records):
    summary = {}
    for record in records:
        node = summary.setdefault(record["kind"], {})
        node[record["mode"]] = node.get(record["mode"], 0) + 1
    return {
        "units_by_kind_and_mode": summary,
        "total_units": len(records),
        "scenes": sorted({r["scene"] for r in records}),
        "perturbation_families": sorted({r["family"] for r in records if r["kind"] == "perturbation"}),
        "control_families": sorted({r["family"] for r in records if r["kind"] == "control"}),
        "grid_levels": perturb.grid_size(),
        "scene_size": list(scenes.SCENE_SIZE),
        "control_seeds": list(scenes.CONTROL_SEEDS),
        "grid_seeds": list(scenes.GRID_SEEDS),
        "foreground_scenes": list(scenes.FOREGROUND_SCENES),
        "alpha_corpus_scenes": len(scenes.ALPHA_CORPUS),
        "rgba_skipped_recipes": dict(measure.RGBA_SKIPPED_RECIPES),
    }


def run_once(out_dir, quick=False, jobs=None, keep_images=False, progress=None, acceptance=None):
    """Full pipeline into ``out_dir``. Returns the summary dict."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="pil-calib-"))
    try:
        tools_dir = workdir / "tools"
        tool_hashes = measure.snapshot_tools(tools_dir)
        records = measure.collect(
            tools_dir, workdir / "images", quick=quick, jobs=jobs, progress=progress
        )
        if progress:
            progress("  deriving thresholds, curves and boundaries")
        derived, response, boundaries = derive.derive(records, tools_dir, REPO_ROOT / "scripts")

        provenance = {
            "bundle_date": BUNDLE_DATE,
            "quick_mode": bool(quick),
            "python": sys.version.split()[0],
            "pillow": PIL.__version__,
            "numpy": np.__version__,
            "tool_sha256": tool_hashes,
            "calibration_sha256": {
                path.name: _sha256(path) for path in sorted(CALIBRATION_DIR.glob("*.py"))
            },
        }
        derived["provenance"] = provenance
        derived["corpus"] = plan_summary(records)
        derived["rgba_control_family"] = {
            "status": "calibrated",
            "source": "alpha",
            "recipes_declared": [row[0] for row in measure.RGBA_CONTROL_RECIPES],
            "recipes_measured": [
                row[0]
                for row in measure.RGBA_CONTROL_RECIPES
                if row[0] not in measure.RGBA_SKIPPED_RECIPES
            ],
            "skipped_recipes": dict(measure.RGBA_SKIPPED_RECIPES),
            "alpha_assertion": "RGB-only recipes assert byte-identical alpha; rescale uses premultiplied resampling.",
        }
        derived["acceptance"] = {"A2.1_null_change": acceptance}
        response["provenance"] = provenance
        boundaries["provenance"] = provenance

        _write_json(out_dir / "derived-thresholds.json", derived)
        _write_json(out_dir / "response-curves.json", response)
        _write_json(out_dir / "lch-hue-boundaries.json", boundaries)
        (out_dir / "README.md").write_text(
            render_readme(derived, response, boundaries), encoding="utf-8"
        )
        return {"records": len(records), "out_dir": str(out_dir), "provenance": provenance}
    finally:
        if keep_images:
            if progress:
                progress(f"  images kept at {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


# --- Ledger rendering ---------------------------------------------------------


def _num(value, digits=6):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, float):
        if value == 0:
            return "0"
        if abs(value) < 1e-4 or abs(value) >= 1e6:
            return f"{value:.3e}"
        return f"{round(value, digits):g}"
    return str(value)


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def render_readme(derived, response, boundaries):
    corpus = derived["corpus"]
    provenance = derived["provenance"]
    verdicts = derived["constant_verdicts"]
    full = derived["control_sets"].get("full_frame", {})
    fore = derived["control_sets"].get("foreground_estimate", {})
    fore_alpha = derived["control_sets"].get("foreground_alpha", {})
    historical_path = REPO_ROOT / "runs" / "2026-08-19-phase2-calibration" / "derived-thresholds.json"
    historical = json.loads(historical_path.read_text(encoding="utf-8")) if historical_path.exists() else {}
    historical_fore = historical.get("control_sets", {}).get("foreground", {}).get("metrics", {})

    parts = []
    parts.append(
        f"""# Phase 2 WP2 — threshold calibration

**Date:** {BUNDLE_DATE}
**Question:** are the tools' numeric constants defensible, and what is the
smallest real change each metric can actually resolve?
**Method:** Neyman–Pearson with a fixed false-alarm budget, over synthetic pairs
with exact ground truth. Every threshold is the **upper bound of a
{derived['method']['bootstrap_resamples']}-resample bootstrap CI on Q(1−α)** of the
no-change control distribution, never the point estimate, and every threshold is
recorded with its `n` and its α.
**Scope:** derivation only. No constant here has been applied to `scripts/` —
that is the integrator's step, from this bundle.
"""
    )

    parts.append(
        f"""## Why this method and not ROC

Fixed by [`docs/phase2-scope.md`](../../docs/phase2-scope.md) and restated here
because it is the part most likely to be "improved" back into a mistake:

- **Not Youden's J.** It needs a positive class that matches deployment. Ours is
  fabricated by our own perturbation generator, so maximising J optimises for the
  fabrication.
- **Not equal-error rate.** EER asserts symmetric costs. False negatives cost
  more here — an invariant wrongly reported `SATISFIED` is the failure WP3 exists
  to prevent.
- **Neyman–Pearson fits** because it only needs the *negative* class
  characterised densely, and that is the class synthetic controls can supply.

α is not chosen by taste. The bootstrap resamples with replacement and cannot
extrapolate past the observed maximum, so an extreme quantile from a small
control set is the maximum wearing a disguise. Working rule `n ≳ 3/α`:
α = 0.01 needs ~300 controls, α = 0.05 needs ~60. This run measured
**{full.get('control_pairs', 0)} full-frame controls** and
**{fore.get('control_pairs', 0)} foreground-mode controls**, so the two modes
carry different α — recorded per metric, per mode, in
`derived-thresholds.json`.
"""
    )

    parts.append("## Corpus\n\n" + _table(
        ["Block", "Units", "What it establishes"],
        [
            [
                "No-change controls",
                sum(corpus["units_by_kind_and_mode"].get("control", {}).values()),
                "the noise floor: identical re-save, PNG re-encode, rescale round trip, sub-threshold perturbation",
            ],
            [
                "Perturbation grid",
                sum(corpus["units_by_kind_and_mode"].get("perturbation", {}).values()),
                f"{len(corpus['perturbation_families'])} families × magnitude × extent, {corpus['grid_levels']} levels total",
            ],
            [
                "Foreground-fraction sweep",
                sum(corpus["units_by_kind_and_mode"].get("fg_sweep", {}).values())
                + sum(corpus["units_by_kind_and_mode"].get("fg_sweep_control", {}).values()),
                "BACKGROUND_DOMINANT_MAX and FOREGROUND_MIN_FRACTION",
            ],
            [
                "Accent-area sweep",
                sum(corpus["units_by_kind_and_mode"].get("accent_sweep", {}).values())
                + sum(corpus["units_by_kind_and_mode"].get("accent_sweep_control", {}).values()),
                "ACCENT_AREA_SMALL_FRACTION",
            ],
        ],
    ) + f"""

Four base scenes at {corpus['scene_size'][0]}×{corpus['scene_size'][1]} — dark base with vivid accents,
blocks-and-stripes layout, a thin object on a flat preview backdrop (~2% of
frame), and full-frame busy content — each built from
{len(corpus['control_seeds'])} seeds for controls and {len(corpus['grid_seeds'])} for the grid. Every
image is a pure function of its parameters; the only stochastic perturbation is
additive noise, drawn from a `PCG64` generator whose seed is derived up front.
""")

    parts.append(
        """## W2 foreground control scenes

The opaque foreground estimate uses exactly `thin_object`, `structured`,
`blob_object`, and `multipart_object`. `structured` qualifies because its
(32,32,36) margin is a genuine backdrop; `busy` is excluded because the
background is the subject, and `dark_accent` is excluded because its black base
would make the foreground mask a chroma mask rather than a backdrop estimate.

- `blob_object`: a filled rounded form at approximately 15% of the frame with
  low perimeter-to-area, testing whether rescale noise is specifically a thin-
  object effect.
- `multipart_object`: three separated rounded components at approximately 8%
  combined, testing a disjoint mask and cell-support gating.

The existing `ALPHA_CORPUS` supplies the RGBA source family. The A2.1
null-change preflight is recorded in `derived-thresholds.json`; it reports the
exact per-metric six-decimal comparisons rather than treating a mismatch as a
pass.
"""
    )

    a21 = (derived.get("acceptance") or {}).get("A2.1_null_change") or {}
    a21_rows = [
        [f"`{metric}`", _num(row.get("existing")), _num(row.get("rerun")), "yes" if row.get("match_to_6dp") else "NO"]
        for metric, row in sorted((a21.get("comparisons") or {}).items())
    ]
    parts.append(
        "## A2.1 null-change check\n\n"
        + f"Status: **{a21.get('status', 'unrecorded')}**. The unmodified opaque `thin_object` corpus was re-run against the post-W1 tool snapshot; equality is tested to six decimal places.\n\n"
        + _table(["Metric", "2026-08-19", "Re-run", "Match to 6 dp"], a21_rows)
        + "\n\n"
    )

    parts.append(
        "## Control thresholds — full frame\n\n"
        + _table(
            ["Metric", "n", "α", "Q(1−α)", "CI upper = **threshold**", "control max", "quantile == max?"],
            [
                [
                    f"`{metric}`",
                    _num(node["n"]),
                    _num(node["alpha"]),
                    _num(node["point_estimate"]),
                    f"**{_num(node['threshold'])}**",
                    _num(node["observed_max"]),
                    _num(node["degenerate"]),
                ]
                for metric, node in sorted(full.get("metrics", {}).items())
            ],
        )
        + "\n\nA `yes` in the last column means Q(1−α) has reached the observed maximum:"
        " the bootstrap cannot extrapolate past it, so that threshold is the control"
        " maximum and its CI is not a statement about the true quantile. Read those"
        " as floors, not estimates.\n"
    )

    def _ratio(node):
        medians = sorted(
            [value.get("median") for value in (node.get("by_control_family") or {}).values() if value.get("median") is not None],
            reverse=True,
        )
        if len(medians) < 2 or medians[1] == 0:
            return None
        return medians[0] / medians[1]

    foreground_rows = []
    for metric, node in sorted(fore.get("metrics", {}).items()):
        old = historical_fore.get(metric, {})
        no_place = node.get("threshold_no_placement")
        delta = None if no_place is None or node.get("threshold") is None else node["threshold"] - no_place
        factor = None if no_place in (None, 0) or node.get("threshold") is None else node["threshold"] / no_place
        foreground_rows.append([
            f"`{metric}`",
            _num(node["n"]),
            _num(node["alpha"]),
            f"**{_num(node['threshold'])}**",
            _num(no_place),
            _num(delta),
            _num(factor),
            _num(old.get("threshold")),
            _num(_ratio(node)),
            _num(_ratio(old)),
        ])
    parts.append(
        "## Control thresholds — foreground estimate (border-median)\n\n"
        + _table(
            ["Metric", "n", "α", "threshold", "no-placement threshold", "delta", "factor", "2026-08-19", "new dom./next", "old dom./next"],
            foreground_rows,
        )
        + "\n\nThe no-placement column uses the same Neyman–Pearson bootstrap construction after excluding only `rescale_roundtrip`; it is not a second method. A ratio above 10× remains a statement about the dominant family, not a general noise floor.\n"
    )

    increased_rows = []
    for metric, node in sorted(fore.get("metrics", {}).items()):
        old_value = historical_fore.get(metric, {}).get("threshold")
        new_value = node.get("threshold")
        if old_value is not None and new_value is not None and float(new_value) > float(old_value):
            increased_rows.append([
                f"`{metric}`",
                _num(old_value),
                _num(new_value),
                "The widened four-scene opaque control set measured a higher upper-tail floor; the table's dominant-family ratio and no-placement estimate show whether that increase is placement-led.",
            ])
    parts.append(
        "## Increased foreground thresholds — explanations\n\n"
        + (_table(["Metric", "Old", "New", "Explanation"], increased_rows) if increased_rows else "No foreground estimate increased relative to the 2026-08-19 bundle.")
        + "\n"
    )

    parts.append(
        "## Control thresholds — foreground alpha source\n\n"
        + _table(
            ["Metric", "n", "α", "threshold", "no-placement threshold", "dominant family"],
            [
                [
                    f"`{metric}`",
                    _num(node["n"]),
                    _num(node["alpha"]),
                    f"**{_num(node['threshold'])}**",
                    _num(node.get("threshold_no_placement")),
                    (max((node.get("by_control_family") or {}).items(), key=lambda item: item[1].get("median", 0.0))[0]
                     if node.get("by_control_family") else "—"),
                ]
                for metric, node in sorted(fore_alpha.get("metrics", {}).items())
            ],
        )
        + "\n\nThe RGBA source is measured over the existing `ALPHA_CORPUS` with premultiplied resampling for `rescale_roundtrip`.\n"
    )

    rgba = derived.get("rgba_control_family", {})
    parts.append(
        "## RGBA control-family accounting\n\n"
        + f"Declared recipes: {len(rgba.get('recipes_declared', []))}; measured recipes: {len(rgba.get('recipes_measured', []))}.\n\n"
        + _table(["Skipped recipe", "Reason"], [[f"`{name}`", reason] for name, reason in sorted(rgba.get("skipped_recipes", {}).items())])
        + "\n\n"
        + rgba.get("alpha_assertion", "")
        + "\n"
    )

    parts.append(
        "## Constants — current guess vs derived vs verdict\n\n"
        + _table(
            ["Constant", "Current", "Derived", "Verdict", "n", "α"],
            [
                [
                    f"`{row['constant']}`",
                    _num(row["current"]),
                    _num(row["derived"]),
                    f"**{row['verdict']}**",
                    _num(row["n"]),
                    _num(row["alpha"]),
                ]
                for row in verdicts
            ],
        )
        + "\n\n"
        + "\n\n".join(f"**`{row['constant']}`** — {row['evidence']}" for row in verdicts)
        + "\n"
    )

    parts.append(_detection_matrix(response))
    parts.append(_boundaries_section(boundaries))
    parts.append(_demotion_section(derived))

    parts.append(
        """## Limitations — documented, not solved

- **Synthetic perturbations are independent and uniform; real revisions are
  correlated and semantic.** A hue rotation over a band of rows is not what a
  re-render does. This calibration will systematically *underestimate* difficulty
  on real input, so every detection limit here is a best case.
- **No real-image validation set is included.** The scope asks for one; this repo
  distributes no images (the phase 1 reference is private and gated behind
  `PIL_AGENT_REFERENCE_IMAGE`), so the check that synthetic-derived thresholds do
  not fall apart on genuine input has *not* been performed. That gate is still
  open, and the thresholds here should be treated as provisional until it closes.
- **No academic precedent was found** for visual-regression or screenshot-diff
  threshold calibration specifically. This applies general Neyman–Pearson
  practice to the problem. It is not a validated domain methodology and must not
  be described as one.
- **The controls are not independent draws.** They are a small number of base
  scenes crossed with a fixed recipe list, so values within a recipe are
  correlated. The bootstrap treats them as exchangeable and therefore reports a
  tighter CI than the design earns. Thresholds are consequently slightly
  optimistic.
- **Sub-threshold perturbations are inside the control set by design**, per the
  scope. That deliberately raises the noise floor above pure re-encode noise, and
  it means every detection limit is quoted relative to a floor that already
  tolerates ±1 code value of exposure, σ≈1 of noise and a 1° hue rotation.
- **The control units are not independent draws.** The opaque foreground set is
  four scenes crossed with a fixed recipe list, and the alpha set is the existing
  `ALPHA_CORPUS` crossed with that list. Values within a recipe remain
  correlated, so the bootstrap CI is tighter than the design earns.
- **The alpha foreground path is calibrated here, but only for this synthetic
  corpus and one Pillow build.** RGBA `rescale_roundtrip` uses premultiplied
  resampling and one un-premultiply; RGB-only recipes assert unchanged alpha.
- **Sub-threshold perturbations are inside the control set by design**, so the
  detection limits describe a floor that already tolerates those perturbations.
- **The LCh accent gate is compared, not ranked.** Deciding between the HSV and
  LCh gates needs a ground-truth notion of "is this pixel an accent", which no
  synthetic corpus supplies. What is measured is what each gate admits and how
  much they disagree.
- **One machine, one Pillow.** JPEG and LANCZOS results are encoder-dependent;
  the provenance block records the exact versions.
"""
    )

    parts.append(
        f"""## Reproducing

```
uv run python calibration/run_all.py
```

Runs the whole pipeline twice and asserts the three JSON payloads are
byte-identical between passes. `--once` skips the second pass, `--quick` runs a
reduced grid, `--jobs N` sets the worker count.

The tools are **snapshotted** before measurement: `scripts/*.py` is copied to a
scratch directory and every measurement runs against that copy, with each file's
sha256 recorded in `provenance.tool_sha256`. Other work happens in this tree
concurrently, and without the snapshot one run could silently measure two
different versions of a tool.

Environment for this bundle: Python {provenance['python']}, Pillow
{provenance['pillow']}, numpy {provenance['numpy']}.

Files:

- `derived-thresholds.json` — control sets, thresholds with n/α/point
  estimate/CI, detection-limit table, the four constant-specific derivations,
  metric demotion analysis, and the constant verdicts.
- `response-curves.json` — every metric's response over every perturbation
  family's magnitude × extent grid, with monotonicity checks.
- `lch-hue-boundaries.json` — derived LCh hue-angle intervals and the accent-gate
  comparison.
- `README.md` — this ledger.

Sources: `calibration/scenes.py`, `perturb.py`, `measure.py`, `derive.py`,
`lch.py`, `run_all.py`.
"""
    )
    return "\n".join(parts)


def _detection_matrix(response):
    curves = [c for c in response["curves"] if c["mode"] == "full_frame"]
    metrics = [m for m in derive.HEADLINE_METRICS if any(c["metric"] == m for c in curves)]
    groups = sorted({(c["perturbation"], c["extent"], c["magnitude_unit"]) for c in curves})
    index = {(c["perturbation"], c["extent"], c["metric"]): c for c in curves}

    rows = []
    for family, extent, unit in groups:
        cells = []
        for metric in metrics:
            curve = index.get((family, extent, metric))
            if curve is None:
                cells.append("—")
                continue
            limit = curve["detection_limit"]["magnitude"]
            mark = "" if curve["monotonic_nondecreasing"] else "†"
            cells.append(("—" if limit is None else _num(limit)) + mark)
        label = family if extent == 1.0 else f"{family} @ extent {extent:g}"
        rows.append([f"`{label}`", unit] + cells)

    body = _table(["Perturbation", "Magnitude unit"] + [f"`{m}`" for m in metrics], rows)
    return f"""## Detection limits — full frame

The smallest ground-truth magnitude whose **median** response clears that
metric's threshold *and stays clear at every larger magnitude tested*. A dash
means the metric never resolved that perturbation at any magnitude in the grid.
`†` marks a curve that was not monotonically non-decreasing in magnitude.

This table is the most valuable output of WP2: it is what tells a calling agent
how to read a *null* result. "Invariant satisfied" and "no change detected" are
not the same claim, and a `SATISFIED` verdict that does not carry the deciding
metric's detection limit is a guarantee the measurement cannot support.

{body}

Foreground-mode limits, per-scene breakdowns and the full response curves are in
`response-curves.json`.
"""


def _boundaries_section(boundaries):
    families = boundaries["families"]
    intervals = boundaries["derived_intervals_deg"]
    gate = boundaries["accent_gate"]
    grid = boundaries["grid"]
    reproduction = boundaries["reproduction"]

    rows = []
    for name in boundaries["family_order_by_lch_hue"]:
        node = families[name]
        low, high = intervals.get(name, (None, None))
        rows.append(
            [
                f"`{name}`",
                ", ".join(f"{a}–{b}" for a, b in node["hsv_hue_ranges_0_255"]),
                _num(node["gated_points"]),
                _num(node["lch_hue_mean_deg"]),
                f"{_num(low)}° – {_num(high)}°",
                f"{_num(node['lch_hue_p1_deg'])}° – {_num(node['lch_hue_p99_deg'])}°",
            ]
        )

    gate_rows = [
        ["HSV gate pass fraction (whole sRGB grid)", _num(gate["hsv_pass_fraction"])],
        ["LCh gate pass fraction", _num(gate["lch_pass_fraction"])],
        ["Gates agree", _num(gate["agreement_rate"])],
        ["HSV-admitted with CIELAB L < 20 (near-black, called vivid)", _num(gate["hsv_pass_with_lightness_below_20"])],
        ["HSV-admitted with C < 20 (near-neutral, called vivid)", _num(gate["hsv_pass_with_chroma_below_20"])],
        ["Median L of HSV-only colours", _num(gate["hsv_only_median_lightness"])],
        ["Median C of HSV-only colours", _num(gate["hsv_only_median_chroma"])],
        ["Median L of LCh-only colours", _num(gate["lch_only_median_lightness"])],
        ["Median C of LCh-only colours", _num(gate["lch_only_median_chroma"])],
    ]

    return f"""## LCh hue-family boundaries

**Derived by measurement against the current HSV behaviour; no authoritative set
exists to port.** The phase 2 research looked for a CIELAB hue-angle boundary set
for basic colour names and found two mutually inconsistent application-specific
sets — porting the current 0–255 HSV bounds into degrees would have been worse
than doing nothing.

So: a {grid['steps_per_channel']}³ sRGB grid ({grid['points']} colours, full, not
subsampled) is classified by the shipping rule — through PIL's own
`convert("HSV")`, not a reimplementation, so the classifier under test is exactly
the one the palette tool runs — every gated point is converted to LCh (D65,
hand-rolled in numpy, *not* Pillow's D50 `convert("LAB")`), and the interval set
that best reproduces that classification is fitted by minimising misclassification
at each boundary.

These intervals describe **what the tool does today**, in a perceptually uniform
space. They are not a colour-naming standard and must not be cited as one.

{_table(["Family", "HSV H (0–255)", "Gated points", "LCh mean", "Derived interval", "1st–99th pct"], rows)}

Reproduction of the HSV classification by the derived intervals:
**{_num(reproduction['agreement_rate'])}** agreement over {_num(reproduction['gated_points'])}
gated points — disagreement rate **{_num(reproduction['disagreement_rate'])}**. The
residual is where HSV hue and LCh hue genuinely disorder each other; no contiguous
interval set can remove it.

### Accent gate — HSV vs LCh

{_table(["Measurement", "Value"], gate_rows)}

The research's suggested `C_MIN = 20`, `L_MIN = 20` are *reasoned from measured
sRGB values, not calibrated*, and enter this work as an input. What is measured
above is what each gate admits and how far they disagree — not which is right.
Ranking them needs a ground-truth notion of "is this pixel an accent", which a
synthetic corpus cannot supply.
"""


def _demotion_section(derived):
    rows = [
        [
            f"`{row['metric']}`",
            _num(row["curves"]),
            _num(row["undetected_curves"]),
            _num(row["undetected_rate"]),
            _num(row["non_monotonic_curves"]),
            "**DEMOTE**" if row["recommend_demote"] else "keep",
        ]
        for row in derived["metric_demotion"]
    ]
    de2000 = derived["perceptual_colour_de2000"]
    note = (
        f"\n\n**Perceptual colour (ΔE2000): {de2000['status']}.** {de2000.get('note', '')}"
        if de2000["status"] != "derived"
        else f"\n\n**Perceptual colour (ΔE2000): derived** for {', '.join('`' + m + '`' for m in de2000['metrics'])}."
    )
    return f"""## Metric demotion

Demotion is an acceptable outcome, and phase 1 already did it once informally to
RGB palette distance. A metric whose detection limit is worse than the smallest
change anyone cares about is worse than useless: a null from it reads as
reassurance. A metric is flagged here when it fails to resolve ≥70% of the
perturbation curves at *any* tested magnitude.

{_table(["Metric", "Curves", "Never detected", "Rate", "Non-monotonic", "Recommendation"], rows)}{note}
"""


# --- Entry point --------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description="Regenerate the WP2 calibration bundle.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output bundle directory")
    parser.add_argument("--quick", action="store_true", help="reduced grid; smoke-tests the harness")
    parser.add_argument("--jobs", type=int, default=None, help="parallel workers (default: min(16, cpus))")
    parser.add_argument("--once", action="store_true", help="skip the determinism pass")
    parser.add_argument("--keep-images", action="store_true", help="leave the scratch corpus on disk")
    args = parser.parse_args(argv)

    def progress(message):
        print(message, flush=True)

    started = time.monotonic()
    acceptance = null_change_check(args.jobs)
    print(f"A2.1 null-change check: {acceptance['status']}")
    print(f"pass 1: full pipeline -> {args.out}")
    summary = run_once(args.out, args.quick, args.jobs, args.keep_images, progress, acceptance)
    print(f"pass 1 complete: {summary['records']} units in {time.monotonic() - started:.1f}s")
    _print_verdicts(args.out)

    if args.once:
        print("determinism pass skipped (--once)")
        return 0

    verify_dir = Path(tempfile.mkdtemp(prefix="pil-calib-verify-"))
    try:
        started = time.monotonic()
        print(f"pass 2: determinism check -> {verify_dir}")
        run_once(verify_dir, args.quick, args.jobs, False, progress, acceptance)
        print(f"pass 2 complete in {time.monotonic() - started:.1f}s")

        mismatched = []
        for name in JSON_ARTEFACTS:
            first = (Path(args.out) / name).read_bytes()
            second = (verify_dir / name).read_bytes()
            status = "identical" if first == second else "DIFFERS"
            print(f"  {name}: {status} ({len(first)} bytes)")
            if first != second:
                mismatched.append(name)
        readme_same = (Path(args.out) / "README.md").read_bytes() == (verify_dir / "README.md").read_bytes()
        print(f"  README.md: {'identical' if readme_same else 'DIFFERS'}")

        if mismatched:
            raise SystemExit(
                "determinism check FAILED: "
                + ", ".join(mismatched)
                + " differ between two runs of the same pipeline"
            )
        print("determinism check PASSED: all JSON payloads byte-identical across two runs")
    finally:
        shutil.rmtree(verify_dir, ignore_errors=True)
    return 0


def _print_verdicts(out_dir):
    payload = json.loads((Path(out_dir) / "derived-thresholds.json").read_text(encoding="utf-8"))
    print("constant verdicts:")
    for row in payload["constant_verdicts"]:
        print(f"  {row['constant']}: current={row['current']} derived={row['derived']} -> {row['verdict']}")
    demote = [r["metric"] for r in payload["metric_demotion"] if r["recommend_demote"]]
    print(f"metrics recommended for demotion: {', '.join(demote) if demote else 'none'}")


if __name__ == "__main__":
    raise SystemExit(main())
