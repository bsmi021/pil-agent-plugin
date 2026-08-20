#!/usr/bin/env python
"""Turn calibration measurements into thresholds, response curves and verdicts.

Method, fixed by ``docs/phase2-scope.md`` and not re-litigated here:

*   **Neyman-Pearson with a fixed false-alarm budget.** Not Youden's J -- it
    needs a positive class matching deployment, and ours is fabricated by our
    own perturbation generator, so maximising J would optimise for the
    fabrication. Not equal-error rate -- EER asserts symmetric costs and we have
    stated false negatives cost more. NP only needs the *negative* class
    characterised densely, which is the class synthetic controls can supply.
*   **threshold = upper bound of a bootstrap CI on Q(1-alpha)**, at least 1000
    resamples, never the point estimate.
*   **alpha is constrained by control-set size, not taste.** ``n >~ 3/alpha``:
    alpha = 0.01 needs ~300 controls, alpha = 0.05 needs ~60. The bootstrap
    resamples with replacement and cannot extrapolate past the observed maximum,
    so an extreme quantile from a small control set is the maximum wearing a
    disguise. alpha is therefore derived per metric from that metric's own n and
    recorded next to the threshold.
*   **Every threshold is recorded with n, alpha, the point estimate and the CI
    upper bound.** A threshold without its n is not interpretable.

Everything a constant's verdict rests on is computed here rather than asserted,
including the two rules that are not simple thresholds -- the compound hue-shift
gate and the per-cell support floor -- which are re-implemented offline from the
recorded per-family and per-cell data and cross-checked against the tool's own
boolean before any sweep result is trusted.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
from PIL import Image

CALIBRATION_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CALIBRATION_DIR))

import lch  # noqa: E402

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_CI = 0.95
BOOTSTRAP_SEED = 20260819

# Control-set sizes needed for each alpha, from the scope's n >~ 3/alpha rule.
ALPHA_LADDER = ((300, 0.01), (60, 0.05), (30, 0.10))

# Metrics the ledger leads with. Everything measured is thresholded; these are
# the ones the constant verdicts and the README tables quote.
HEADLINE_METRICS = (
    "changed_area_fraction",
    "structural_dissimilarity",
    "dhash_distance",
    "ahash_distance",
    "hue_family_delta_max",
    "accent_fraction_delta_abs",
    "luminance_mean_delta_abs",
    "saturation_mean_delta_abs",
    "entropy_delta_abs",
)

# Control changed-area budget for the CHANGE_THRESHOLD derivation: at the chosen
# per-pixel gate, the (1-alpha) quantile of control changed-area must sit below
# one part in a thousand of the frame. Below that a caller reading
# changed_area_fraction cannot mistake re-encode noise for a real edit.
CHANGE_AREA_BUDGET = 0.001

CANDIDATE_HUE_ABS = (0.002, 0.005, 0.01, 0.02, 0.05, 0.10)
CANDIDATE_HUE_REL = (0.0, 0.10, 0.20, 0.30, 0.50)
CANDIDATE_PRESENCE_PIXELS = (1, 2, 5, 10, 20, 50, 100)
CANDIDATE_PRESENCE_FRACTION = (0.0, 0.0001, 0.0002, 0.0005, 0.001, 0.002)

SUPPORT_BUCKETS = ((1, 2), (2, 4), (4, 8), (8, 16), (16, 32), (32, 64), (64, 128), (128, None))

# sRGB grid resolution for the LCh hue-boundary derivation: 64^3 = 262144
# colours, full (not subsampled).
LCH_GRID_STEPS = 64

# Chroma / lightness gate the research reasoned from measured sRGB values. An
# input to this work, not an answer -- reported against the current HSV gate.
LCH_GATE_C_MIN = 20.0
LCH_GATE_L_MIN = 20.0


# --- Small statistics helpers -------------------------------------------------


def _clean(values):
    return [float(v) for v in values if v is not None and np.isfinite(float(v))]


def _stats(values):
    values = _clean(values)
    if not values:
        return {"n": 0}
    arr = np.asarray(values, dtype=np.float64)
    p25, median, p75 = np.percentile(arr, [25, 50, 75])
    return {
        "n": int(arr.size),
        "min": round(float(arr.min()), 8),
        "p25": round(float(p25), 8),
        "median": round(float(median), 8),
        "p75": round(float(p75), 8),
        "max": round(float(arr.max()), 8),
        "mean": round(float(arr.mean()), 8),
    }


def alpha_for(n):
    """The tightest alpha this control-set size can honestly support."""
    for needed, alpha in ALPHA_LADDER:
        if n >= needed:
            return alpha
    return None


def _seed_for(label):
    digest = hashlib.sha256(f"{BOOTSTRAP_SEED}|{label}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def bootstrap_quantile(values, alpha, label, resamples=BOOTSTRAP_RESAMPLES):
    """Q(1-alpha) with a percentile-bootstrap CI; the upper bound is the threshold.

    Returns every input the scope requires a threshold to be reported with, plus
    the observed maximum, because when ``point_estimate == max`` the quantile is
    the maximum and the CI is decoration -- the caller has to be able to see
    that.
    """
    values = _clean(values)
    n = len(values)
    result = {
        "n": n,
        "alpha": alpha,
        "quantile": None if alpha is None else round(1.0 - alpha, 6),
        "resamples": resamples,
        "ci_level": BOOTSTRAP_CI,
        "ci_method": "percentile bootstrap, two-sided",
        "quantile_method": "numpy linear interpolation",
    }
    if n == 0 or alpha is None:
        result.update(
            point_estimate=None,
            ci_lower=None,
            ci_upper=None,
            threshold=None,
            observed_max=None,
            degenerate=None,
            note="control set too small for any alpha on the n >= 3/alpha ladder"
            if n
            else "no control values for this metric",
        )
        return result

    arr = np.asarray(sorted(values), dtype=np.float64)
    q = 1.0 - alpha
    point = float(np.quantile(arr, q))
    rng = np.random.Generator(np.random.PCG64(_seed_for(label)))
    idx = rng.integers(0, n, size=(resamples, n))
    resampled = np.quantile(arr[idx], q, axis=1)
    tail = (1.0 - BOOTSTRAP_CI) / 2.0
    ci_lower, ci_upper = np.percentile(resampled, [100 * tail, 100 * (1 - tail)])

    result.update(
        point_estimate=round(point, 8),
        ci_lower=round(float(ci_lower), 8),
        ci_upper=round(float(ci_upper), 8),
        threshold=round(float(ci_upper), 8),
        observed_max=round(float(arr.max()), 8),
        degenerate=bool(point >= float(arr.max()) - 1e-12),
    )
    if result["degenerate"]:
        result["note"] = (
            "Q(1-alpha) has reached the observed maximum: the bootstrap cannot "
            "extrapolate past it, so this threshold is the control maximum and "
            "its CI is not a statement about the true quantile"
        )
    return result


def _rank(values):
    values = np.asarray(values, dtype=np.float64)
    order = np.argsort(values, kind="stable")
    ranks = np.empty(values.size, dtype=np.float64)
    ordered = values[order]
    i = 0
    while i < values.size:
        j = i
        while j + 1 < values.size and ordered[j + 1] == ordered[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0
        i = j + 1
    return ranks


def spearman(x, y):
    """Rank correlation without scipy. None when either side is constant."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if x.size < 3 or np.all(x == x[0]) or np.all(y == y[0]):
        return None
    return round(float(np.corrcoef(_rank(x), _rank(y))[0, 1]), 6)


# --- Control thresholds -------------------------------------------------------


def metric_names(records):
    names = set()
    for record in records:
        names.update(record["metrics"])
    return sorted(names)


def control_thresholds(records):
    """Per (mode, metric) threshold from the no-change control set.

    Only ``kind == "control"`` feeds this. The two sweep control sets are
    deliberately degenerate at their small ends -- a 0.3%-of-frame object, a
    0.05%-of-frame accent -- and folding them in would raise every threshold to
    cover cases the flags already refuse to answer.
    """
    controls = [r for r in records if r["kind"] == "control"]
    out = {}
    for mode in sorted({r["mode"] for r in controls}):
        subset = [r for r in controls if r["mode"] == mode]
        per_metric = {}
        for metric in metric_names(subset):
            values = [r["metrics"].get(metric) for r in subset]
            n = len(_clean(values))
            derived = bootstrap_quantile(values, alpha_for(n), f"{mode}|{metric}")
            derived["distribution"] = _stats(values)
            derived["by_control_family"] = {
                family: _stats(
                    [r["metrics"].get(metric) for r in subset if r["family"] == family]
                )
                for family in sorted({r["family"] for r in subset})
            }
            per_metric[metric] = derived
        out[mode] = {
            "control_pairs": len(subset),
            "control_families": sorted({r["family"] for r in subset}),
            "scenes": sorted({r["scene"] for r in subset}),
            "metrics": per_metric,
        }
    return out


def threshold_value(thresholds, mode, metric):
    node = thresholds.get(mode, {}).get("metrics", {}).get(metric)
    return None if node is None else node.get("threshold")


# --- Response curves and detection limits -------------------------------------


def response_curves(records, thresholds):
    """One curve per (mode, perturbation family, extent, metric).

    Monotonicity is checked along the magnitude axis at fixed extent, because
    magnitude and extent are separate axes and interleaving them would make a
    perfectly well-behaved metric look non-monotone.
    """
    perturbations = [r for r in records if r["kind"] == "perturbation"]
    curves = []
    keys = sorted(
        {(r["mode"], r["family"], r["extent"]) for r in perturbations}
    )
    for mode, family, extent in keys:
        subset = [
            r for r in perturbations if r["mode"] == mode and r["family"] == family and r["extent"] == extent
        ]
        magnitudes = sorted({r["magnitude"] for r in subset})
        unit = subset[0]["magnitude_unit"]
        for metric in metric_names(subset):
            points = []
            for magnitude in magnitudes:
                at = [r for r in subset if r["magnitude"] == magnitude]
                stats = _stats([r["metrics"].get(metric) for r in at])
                stats["magnitude"] = magnitude
                stats["by_scene"] = {
                    scene: _stats([r["metrics"].get(metric) for r in at if r["scene"] == scene])
                    for scene in sorted({r["scene"] for r in at})
                }
                points.append(stats)
            usable = [p for p in points if p["n"]]
            medians = [p["median"] for p in usable]
            mags = [p["magnitude"] for p in usable]
            violations = [
                {"from": mags[i], "to": mags[i + 1], "median_from": medians[i], "median_to": medians[i + 1]}
                for i in range(len(medians) - 1)
                if medians[i + 1] < medians[i]
            ]
            threshold = threshold_value(thresholds, mode, metric)
            curves.append(
                {
                    "mode": mode,
                    "perturbation": family,
                    "extent": extent,
                    "magnitude_unit": unit,
                    "metric": metric,
                    "threshold": threshold,
                    "points": points,
                    "monotonic_nondecreasing": bool(usable) and not violations,
                    "monotonicity_violations": violations,
                    "spearman_magnitude_vs_median": spearman(mags, medians) if len(usable) > 2 else None,
                    "detection_limit": _detection_limit(usable, threshold),
                }
            )
    curves.sort(key=lambda c: (c["mode"], c["perturbation"], c["extent"], c["metric"]))
    return curves


def _detection_limit(points, threshold):
    """Smallest ground-truth magnitude whose median response clears the threshold.

    "Smallest that stays clear": the crossing must hold at that magnitude *and*
    at every larger one. A single lucky crossing followed by a dip is not a
    detection limit, it is noise, and publishing it would understate what the
    metric can actually resolve -- which is precisely the claim WP3 leans on
    when it reports a null result.
    """
    if threshold is None or not points:
        return {"magnitude": None, "reason": "no threshold available for this metric"}
    ordered = sorted(points, key=lambda p: p["magnitude"])
    for index, point in enumerate(ordered):
        if all(later["median"] > threshold for later in ordered[index:]):
            return {
                "magnitude": point["magnitude"],
                "median_at_limit": point["median"],
                "threshold": threshold,
                "reason": "first magnitude whose median clears the threshold and stays clear",
            }
    return {
        "magnitude": None,
        "threshold": threshold,
        "max_magnitude_tested": ordered[-1]["magnitude"],
        "median_at_max": ordered[-1]["median"],
        "reason": "not detected at any tested magnitude",
    }


def detection_limit_table(curves):
    table = {}
    for curve in curves:
        table.setdefault(curve["mode"], {}).setdefault(curve["metric"], {})[
            f"{curve['perturbation']}@extent={curve['extent']:g}"
        ] = {
            "magnitude": curve["detection_limit"]["magnitude"],
            "unit": curve["magnitude_unit"],
            "monotonic": curve["monotonic_nondecreasing"],
        }
    return table


# --- CHANGE_THRESHOLD ---------------------------------------------------------


def change_threshold_analysis(records, current=10):
    """Sweep the per-pixel luminance gate instead of assuming it.

    Two criteria, both reported:

    *   **area** -- the (1-alpha) quantile of control changed-area must sit under
        CHANGE_AREA_BUDGET. This is the primary criterion; ``changed_area_fraction``
        is a number callers compare against, so its noise floor is what matters.
    *   **bbox** -- the fraction of control pairs producing *any* changed pixel,
        which is what decides whether ``changed_region_bbox_fractional`` comes
        back non-null. Stricter, and reported because a non-null bbox reads to a
        caller as "something changed here".
    """
    controls = [
        r
        for r in records
        if r["kind"] == "control"
        and r["mode"] == "full_frame"
        and "changed_area_by_threshold" in r["diagnostics"]
    ]
    perturbations = [
        r
        for r in records
        if r["kind"] == "perturbation"
        and r["mode"] == "full_frame"
        and "changed_area_by_threshold" in r["diagnostics"]
    ]
    if not controls:
        return {"status": "no data"}

    candidates = sorted(
        {int(t) for r in controls for t in r["diagnostics"]["changed_area_by_threshold"]}
    )
    n = len(controls)
    alpha = alpha_for(n)
    rows = []
    for t in candidates:
        values = [r["diagnostics"]["changed_area_by_threshold"][str(t)] for r in controls]
        derived = bootstrap_quantile(values, alpha, f"change_threshold|{t}")
        nonzero = float(np.mean([1.0 if v > 0 else 0.0 for v in values]))
        rows.append(
            {
                "threshold": t,
                "control_q": derived["point_estimate"],
                "control_ci_upper": derived["ci_upper"],
                "control_max": derived["observed_max"],
                "control_any_changed_pixel_rate": round(nonzero, 6),
                "meets_area_budget": bool(
                    derived["ci_upper"] is not None and derived["ci_upper"] <= CHANGE_AREA_BUDGET
                ),
                "meets_bbox_budget": bool(alpha is not None and nonzero <= alpha),
            }
        )

    area_pick = next((r["threshold"] for r in rows if r["meets_area_budget"]), None)
    bbox_pick = next((r["threshold"] for r in rows if r["meets_bbox_budget"]), None)

    # What the choice costs in sensitivity: median response of the weakest
    # tested perturbation level of each family, at each candidate gate.
    sensitivity = {}
    for family in sorted({r["family"] for r in perturbations}):
        family_records = [r for r in perturbations if r["family"] == family]
        smallest = min(r["magnitude"] for r in family_records)
        at = [r for r in family_records if r["magnitude"] == smallest]
        sensitivity[family] = {
            "smallest_magnitude": smallest,
            "unit": at[0]["magnitude_unit"],
            "median_changed_area": {
                str(t): round(
                    float(
                        np.median(
                            [r["diagnostics"]["changed_area_by_threshold"][str(t)] for r in at]
                        )
                    ),
                    8,
                )
                for t in candidates
            },
        }

    return {
        "current": current,
        "n": n,
        "alpha": alpha,
        "area_budget": CHANGE_AREA_BUDGET,
        "sweep": rows,
        "derived_area_criterion": area_pick,
        "derived_bbox_criterion": bbox_pick,
        "derived": area_pick,
        "sensitivity_at_smallest_perturbation": sensitivity,
    }


# --- The compound hue-shift rule ---------------------------------------------


def _supported(families, min_pixels, min_fraction):
    return {
        name
        for name, (pixels, _frac_accents, frac_frame) in families.items()
        if pixels >= min_pixels and frac_frame >= min_fraction
    }


def hue_rule_fires(families_a, families_b, abs_gate, rel_gate, min_pixels, min_fraction):
    """Offline re-implementation of pil_palette_diff's accent_hue_shift_detected.

    Reconstructed rather than re-measured because sweeping the gates through the
    CLI would mean re-running the whole corpus once per candidate pair. The
    reconstruction is cross-checked against the tool's own boolean at the
    current constants before any sweep result is used.
    """
    present_a = _supported(families_a, min_pixels, min_fraction)
    present_b = _supported(families_b, min_pixels, min_fraction)
    moved = False
    for name, (_pixels, before, _frame) in families_a.items():
        if name not in present_a and name not in present_b:
            continue
        after = families_b[name][1]
        delta = round(after - before, 6)
        if abs(delta) < abs_gate:
            continue
        reference = max(before, after)
        if reference <= 0:
            continue
        if abs(delta) / reference < rel_gate:
            continue
        moved = True
    return bool(moved or (present_a - present_b) or (present_b - present_a))


def _families(record, side):
    return record["diagnostics"].get(f"hue_families_{side}")


def _hue_usable(record):
    return _families(record, "a") is not None and _families(record, "b") is not None


def hue_rule_analysis(records, current_abs=0.02, current_rel=0.30, current_pixels=10, current_fraction=0.0002):
    """Neyman-Pearson over the compound gate: fix false alarms, maximise power."""
    controls = [
        r for r in records if r["kind"] == "control" and r["mode"] == "full_frame" and _hue_usable(r)
    ]
    rotations = [
        r
        for r in records
        if r["kind"] == "perturbation" and r["family"] == "hue_rotation" and r["mode"] == "full_frame" and _hue_usable(r)
    ]
    if not controls or not rotations:
        return {"status": "no data"}

    agree = sum(
        1
        for r in controls + rotations
        if hue_rule_fires(
            _families(r, "a"), _families(r, "b"), current_abs, current_rel, current_pixels, current_fraction
        )
        == bool(r["diagnostics"]["accent_hue_shift_detected"])
    )
    total = len(controls) + len(rotations)
    reconstruction = {
        "checked": total,
        "agreements": agree,
        "agreement_rate": round(agree / total, 6),
        "note": "offline rule vs the tool's own accent_hue_shift_detected at the current constants",
    }

    n = len(controls)
    alpha = alpha_for(n)
    extents = sorted({r["extent"] for r in rotations})

    # Pre-extract the census pairs once. The sweep evaluates the rule ~1000
    # times over the whole corpus, and re-walking the record dicts each time
    # would dominate the runtime.
    control_pairs = [(_families(r, "a"), _families(r, "b")) for r in controls]
    rotation_groups = {}
    for extent in extents:
        at_extent = [r for r in rotations if r["extent"] == extent]
        rotation_groups[extent] = [
            (
                magnitude,
                [
                    (_families(r, "a"), _families(r, "b"))
                    for r in at_extent
                    if r["magnitude"] == magnitude
                ],
            )
            for magnitude in sorted({r["magnitude"] for r in at_extent})
        ]

    def evaluate(abs_gate, rel_gate, min_pixels, min_fraction, detail=False):
        """False-alarm rate on controls, detection limit on hue rotations.

        All four gates move together. The presence floors can fire the rule on
        their own -- a family crossing the support floor registers as lost or
        gained regardless of the shift gates -- so sweeping the shift gates with
        the floors pinned could find *no* combination inside the false-alarm
        budget while a perfectly good joint setting existed.
        """
        false_alarms = sum(
            1
            for fam_a, fam_b in control_pairs
            if hue_rule_fires(fam_a, fam_b, abs_gate, rel_gate, min_pixels, min_fraction)
        )
        limits = {}
        for extent, groups in rotation_groups.items():
            rates = []
            for magnitude, pairs in groups:
                fired = sum(
                    1
                    for fam_a, fam_b in pairs
                    if hue_rule_fires(fam_a, fam_b, abs_gate, rel_gate, min_pixels, min_fraction)
                )
                rates.append((magnitude, fired / len(pairs)))
            limit = None
            for index, (magnitude, _rate) in enumerate(rates):
                if all(rate >= 0.5 for _m, rate in rates[index:]):
                    limit = magnitude
                    break
            node = {"detection_limit_degrees": limit}
            if detail:
                node["detection_rate_by_magnitude"] = [
                    {"magnitude": m, "rate": round(rate, 6)} for m, rate in rates
                ]
            limits[f"extent={extent:g}"] = node
        return {
            "hue_shift_min_absolute": abs_gate,
            "hue_shift_min_relative": rel_gate,
            "hue_presence_min_pixels": min_pixels,
            "hue_presence_min_fraction": min_fraction,
            "control_false_alarms": false_alarms,
            "control_false_alarm_rate": round(false_alarms / n, 6),
            "within_budget": bool(alpha is not None and false_alarms / n <= alpha),
            "detection": limits,
        }

    widest = f"extent={max(extents):g}"
    narrowest = f"extent={min(extents):g}"

    def sort_key(row):
        """Fix the false-alarm rate, then maximise power: lowest detection limit
        first, ties broken toward the more conservative gate and the smaller
        presence floor (which costs least sensitivity elsewhere)."""
        wide = row["detection"][widest]["detection_limit_degrees"]
        narrow = row["detection"][narrowest]["detection_limit_degrees"]
        return (
            float("inf") if wide is None else wide,
            float("inf") if narrow is None else narrow,
            row["control_false_alarm_rate"],
            -row["hue_shift_min_absolute"],
            -row["hue_shift_min_relative"],
            row["hue_presence_min_pixels"],
            row["hue_presence_min_fraction"],
        )

    gate_sweep = [
        evaluate(a, r, p, f)
        for a in CANDIDATE_HUE_ABS
        for r in CANDIDATE_HUE_REL
        for p in CANDIDATE_PRESENCE_PIXELS
        for f in CANDIDATE_PRESENCE_FRACTION
    ]
    eligible = [row for row in gate_sweep if row["within_budget"]]
    best = min(eligible, key=sort_key) if eligible else None
    if best is not None:
        best = evaluate(
            best["hue_shift_min_absolute"],
            best["hue_shift_min_relative"],
            best["hue_presence_min_pixels"],
            best["hue_presence_min_fraction"],
            detail=True,
        )

    # The empirical distribution the absolute gate is really thresholding, for
    # readers who want the raw quantile rather than the rule-level sweep.
    per_family_deltas = []
    per_family_relative = []
    for record in controls:
        fam_a, fam_b = _families(record, "a"), _families(record, "b")
        for name, (_pixels, before, _frame) in fam_a.items():
            after = fam_b[name][1]
            delta = round(after - before, 6)
            per_family_deltas.append(abs(delta))
            reference = max(before, after)
            if reference > 0 and abs(delta) >= min(CANDIDATE_HUE_ABS):
                per_family_relative.append(abs(delta) / reference)

    return {
        "reconstruction_check": reconstruction,
        "n": n,
        "alpha": alpha,
        "current": {
            "hue_shift_min_absolute": current_abs,
            "hue_shift_min_relative": current_rel,
            "hue_presence_min_pixels": current_pixels,
            "hue_presence_min_fraction": current_fraction,
        },
        "current_performance": evaluate(
            current_abs, current_rel, current_pixels, current_fraction, detail=True
        ),
        "gate_sweep": gate_sweep,
        "gate_sweep_size": len(gate_sweep),
        "gate_sweep_eligible": len(eligible),
        "gate_choice": best,
        "control_absolute_delta_quantile": bootstrap_quantile(
            per_family_deltas, alpha_for(len(per_family_deltas)), "hue|absolute"
        ),
        "control_relative_delta_quantile": bootstrap_quantile(
            per_family_relative, alpha_for(len(per_family_relative)), "hue|relative"
        ),
        "control_absolute_delta_distribution": _stats(per_family_deltas),
        "control_relative_delta_distribution": _stats(per_family_relative),
    }


# --- CELL_MIN_SUPPORT_PIXELS --------------------------------------------------


def cell_support_analysis(records, current=16):
    """Per-cell statistical divergence as a function of foreground support.

    On pairs that did not change, a cell's divergence is pure noise, and the
    noise grows as the cell's foreground shrinks. The floor should sit where
    that growth stops mattering -- defined here as the smallest bucket whose
    99th-percentile divergence, and every larger bucket's, is within twice the
    best-supported bucket's.
    """
    samples = []
    for record in records:
        if record["mode"] != "foreground":
            continue
        for support, divergence in record["diagnostics"].get("cell_support_samples", []):
            samples.append((int(support), float(divergence)))
    if not samples:
        return {"status": "no data"}

    supports = np.array([s for s, _ in samples])
    divergences = np.array([d for _, d in samples])

    buckets = []
    for low, high in SUPPORT_BUCKETS:
        mask = supports >= low if high is None else (supports >= low) & (supports < high)
        selected = divergences[mask]
        buckets.append(
            {
                "support_min": low,
                "support_max": high,
                "n": int(selected.size),
                "median": round(float(np.median(selected)), 6) if selected.size else None,
                "p90": round(float(np.percentile(selected, 90)), 6) if selected.size else None,
                "p99": round(float(np.percentile(selected, 99)), 6) if selected.size else None,
                "max": round(float(selected.max()), 6) if selected.size else None,
            }
        )

    populated = [b for b in buckets if b["n"] >= 20]
    reference = populated[-1]["p99"] if populated else None
    derived = None
    if reference is not None:
        for index, bucket in enumerate(populated):
            if all(later["p99"] <= 2.0 * reference for later in populated[index:]):
                derived = bucket["support_min"]
                break

    return {
        "current": current,
        "samples": len(samples),
        "buckets": buckets,
        "reference_p99": reference,
        "rule": "smallest support bucket whose p99 divergence, and every larger bucket's, is <= 2x the best-supported bucket's p99",
        "derived": derived,
    }


# --- Foreground and accent sweeps ---------------------------------------------


def _measured_foreground(record):
    """The mask fraction the tool itself reported for side A."""
    return record["diagnostics"].get("foreground_fraction_a")


def foreground_sweep_analysis(records, thresholds, background_dominant_max=0.10, foreground_min_fraction=0.02):
    """How small a subject can get before the numbers stop describing it.

    Two questions, one sweep:

    *   BACKGROUND_DOMINANT_MAX -- below what frame share does a *maximal* object
        change stop clearing the full-frame control threshold? Below that point
        a full-frame verdict is about the backdrop, and the flag has to fire.
    *   FOREGROUND_MIN_FRACTION -- below what frame share does foreground mode's
        own control noise stop clearing its own threshold? Below that the mask
        is unstable and its statistics are noise.
    """
    sweep = [r for r in records if r["kind"] in ("fg_sweep", "fg_sweep_control")]
    if not sweep:
        return {"status": "no data"}

    levels = {}
    measured = {}
    for record in sweep:
        level = record["scene_label"]
        node = levels.setdefault(
            level,
            {
                "level": level,
                "nominal_fraction": None,
                "measured_foreground_fraction": None,
                "full_frame": {},
                "foreground": {},
                "control_noise": {"full_frame": {}, "foreground": {}},
            },
        )
        if record["mode"] == "foreground":
            fraction = _measured_foreground(record)
            if fraction is not None:
                measured.setdefault(level, []).append(float(fraction))
        if node["nominal_fraction"] is None and record["kind"] == "fg_sweep":
            node["nominal_fraction"] = record["magnitude"]

    # Median over every foreground-mode measurement at that level: the tool's
    # own mask fraction, never the nominal value the scene was asked for.
    for level, values in measured.items():
        levels[level]["measured_foreground_fraction"] = round(float(np.median(values)), 6)

    for level, node in levels.items():
        for mode in ("full_frame", "foreground"):
            for variant in ("object_recolour", "object_offset"):
                group = [
                    r
                    for r in sweep
                    if r["scene_label"] == level and r["mode"] == mode and r["family"] == variant
                ]
                node[mode][variant] = {
                    metric: _stats([r["metrics"].get(metric) for r in group])
                    for metric in HEADLINE_METRICS
                }
                node[mode][variant]["detected"] = {
                    metric: _detected(node[mode][variant][metric], threshold_value(thresholds, mode, metric))
                    for metric in HEADLINE_METRICS
                }
            controls = [
                r for r in sweep if r["scene_label"] == level and r["mode"] == mode and r["kind"] == "fg_sweep_control"
            ]
            node["control_noise"][mode] = {
                metric: {
                    **_stats([r["metrics"].get(metric) for r in controls]),
                    "exceeds_threshold": _detected(
                        _stats([r["metrics"].get(metric) for r in controls]),
                        threshold_value(thresholds, mode, metric),
                        key="max",
                    ),
                }
                for metric in HEADLINE_METRICS
            }

    ordered = sorted(levels.values(), key=lambda node: node["measured_foreground_fraction"] or 0.0)

    # Crossing points, read off the ordered sweep.
    detect_metrics = ("changed_area_fraction", "structural_dissimilarity", "hue_family_delta_max")
    background_crossing = None
    for node in ordered:
        recolour = node["full_frame"].get("object_recolour", {}).get("detected", {})
        if all(recolour.get(metric) for metric in detect_metrics):
            background_crossing = node["measured_foreground_fraction"]
            break

    foreground_crossing = None
    for node in ordered:
        noise = node["control_noise"]["foreground"]
        if not any(noise.get(metric, {}).get("exceeds_threshold") for metric in detect_metrics):
            foreground_crossing = node["measured_foreground_fraction"]
            break

    return {
        "current": {
            "BACKGROUND_DOMINANT_MAX": background_dominant_max,
            "FOREGROUND_MIN_FRACTION": foreground_min_fraction,
        },
        "levels": ordered,
        "detection_metrics": list(detect_metrics),
        "smallest_foreground_fraction_where_full_frame_sees_a_total_object_recolour": background_crossing,
        "smallest_foreground_fraction_where_foreground_mode_control_noise_stays_below_threshold": foreground_crossing,
    }


def _detected(stats, threshold, key="median"):
    if threshold is None or not stats.get("n"):
        return None
    return bool(stats[key] > threshold)


def accent_sweep_analysis(records, hue_abs_gate, current=0.005):
    """Where hue statistics stop being trustworthy as the accent area shrinks."""
    sweep = [r for r in records if r["kind"] in ("accent_sweep", "accent_sweep_control")]
    if not sweep:
        return {"status": "no data"}

    levels = []
    for label in sorted({r["scene_label"] for r in sweep}):
        controls = [r for r in sweep if r["scene_label"] == label and r["kind"] == "accent_sweep_control"]
        signals = [r for r in sweep if r["scene_label"] == label and r["kind"] == "accent_sweep"]
        nominal = signals[0]["magnitude"] if signals else None
        measured = [
            r["diagnostics"].get("accent_pixel_fraction_a")
            for r in controls
            if r["diagnostics"].get("accent_pixel_fraction_a") is not None
        ]
        control_delta = _stats([r["metrics"].get("hue_family_delta_max") for r in controls])
        levels.append(
            {
                "level": label,
                "nominal_accent_fraction": nominal,
                "measured_accent_pixel_fraction": round(float(np.median(measured)), 6) if measured else None,
                "control_hue_family_delta_max": control_delta,
                "control_noise_exceeds_absolute_gate": bool(
                    control_delta.get("n") and hue_abs_gate is not None and control_delta["max"] > hue_abs_gate
                ),
                "control_false_alarm_rate": round(
                    float(np.mean([1.0 if r["diagnostics"]["accent_hue_shift_detected"] else 0.0 for r in controls])),
                    6,
                )
                if controls
                else None,
                "hue_rotation_30_detected_rate": round(
                    float(np.mean([1.0 if r["diagnostics"]["accent_hue_shift_detected"] else 0.0 for r in signals])),
                    6,
                )
                if signals
                else None,
                "accent_area_very_small_flag_rate": round(
                    float(
                        np.mean(
                            [
                                1.0 if "accent_area_very_small" in r["diagnostics"]["palette_flags_a"] else 0.0
                                for r in controls
                            ]
                        )
                    ),
                    6,
                )
                if controls
                else None,
            }
        )

    levels.sort(key=lambda node: node["measured_accent_pixel_fraction"] or 0.0)
    trustworthy = None
    for node in levels:
        if not node["control_noise_exceeds_absolute_gate"] and not node["control_false_alarm_rate"]:
            trustworthy = node["measured_accent_pixel_fraction"]
            break

    return {
        "current": current,
        "absolute_gate_used": hue_abs_gate,
        "levels": levels,
        "smallest_accent_fraction_with_no_control_false_alarms": trustworthy,
    }


# --- LCh hue-family boundaries ------------------------------------------------


def lch_hue_boundaries(tools_dir, steps=LCH_GRID_STEPS):
    """Derive LCh hue-angle intervals by measuring the current HSV behaviour.

    There is no authoritative CIELAB hue-angle boundary set for basic colour
    names -- the phase 2 research looked and found two mutually inconsistent
    application-specific sets. Porting the current 0-255 HSV bounds into degrees
    would be worse than doing nothing. So the boundaries here are *derived by
    measurement against the current HSV behaviour*: classify a dense sRGB grid
    with the shipping rule, convert each point to LCh, and fit the interval set
    that best reproduces that classification. They describe what the tool does
    today in a perceptually uniform space. They are not a colour-naming
    standard, and nothing here should be cited as one.

    Classification runs through PIL's own ``convert("HSV")`` on a real image, not
    a reimplementation, so the grid is classified by exactly the code path the
    palette tool uses.
    """
    sys.path.insert(0, str(Path(tools_dir).resolve()))
    import pil_common  # noqa: PLC0415

    values = np.linspace(0, 255, steps).round().astype(np.uint8)
    r, g, b = np.meshgrid(values, values, values, indexing="ij")
    cube = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    side = int(round(cube.shape[0] ** 0.5))
    if side * side != cube.shape[0]:
        raise SystemExit(f"grid of {cube.shape[0]} points is not square; pick another step count")

    grid_image = Image.fromarray(cube.reshape(side, side, 3), "RGB")
    hsv = np.asarray(grid_image.convert("HSV")).reshape(-1, 3).astype(np.int16)

    sat_min = pil_common.DEFAULT_ACCENT_SAT_MIN
    val_min = pil_common.DEFAULT_ACCENT_VAL_MIN
    hsv_gate = (hsv[:, 1] > sat_min) & (hsv[:, 2] > val_min)

    lch_values = lch.rgb_to_lch(cube)
    lightness, chroma, hue_angle = lch_values[:, 0], lch_values[:, 1], lch_values[:, 2]
    lch_gate = (chroma >= LCH_GATE_C_MIN) & (lightness >= LCH_GATE_L_MIN)

    families = [name for name, _ranges in pil_common.HUE_FAMILIES]
    assignment = np.full(cube.shape[0], -1, dtype=np.int16)
    for index, (_name, ranges) in enumerate(pil_common.HUE_FAMILIES):
        member = np.zeros(cube.shape[0], dtype=bool)
        for low, high in ranges:
            member |= (hsv[:, 0] >= low) & (hsv[:, 0] <= high)
        assignment[member] = index

    gated = hsv_gate & (assignment >= 0)
    per_family = {}
    means = {}
    for index, name in enumerate(families):
        mask = gated & (assignment == index)
        angles = hue_angle[mask]
        low, high, width = lch.circular_spread(angles)
        mean = lch.circular_mean(angles)
        means[name] = mean
        per_family[name] = {
            "hsv_hue_ranges_0_255": [list(pair) for pair in dict(pil_common.HUE_FAMILIES)[name]],
            "gated_points": int(mask.sum()),
            "lch_hue_mean_deg": None if mean is None else round(mean, 3),
            "lch_hue_p1_deg": None if low is None else round(low, 3),
            "lch_hue_p99_deg": None if high is None else round(high, 3),
            "lch_hue_spread_deg": None if width is None else round(width, 3),
            "lch_chroma_median": round(float(np.median(chroma[mask])), 3) if mask.any() else None,
            "lch_lightness_median": round(float(np.median(lightness[mask])), 3) if mask.any() else None,
        }

    ordered = [name for name in families if means[name] is not None]
    ordered.sort(key=lambda name: means[name])

    boundaries = []
    for position, name in enumerate(ordered):
        nxt = ordered[(position + 1) % len(ordered)]
        angles_here = hue_angle[gated & (assignment == families.index(name))]
        angles_next = hue_angle[gated & (assignment == families.index(nxt))]
        split, cost = _best_split(means[name], means[nxt], angles_here, angles_next)
        boundaries.append(
            {
                "from_family": name,
                "to_family": nxt,
                "boundary_deg": round(split, 3),
                "misclassified_points_at_boundary": int(cost),
            }
        )

    # Contiguous derived intervals: each family runs from the boundary that ends
    # its predecessor to the boundary that starts its successor.
    intervals = {}
    for position, name in enumerate(ordered):
        start = boundaries[(position - 1) % len(boundaries)]["boundary_deg"]
        end = boundaries[position]["boundary_deg"]
        intervals[name] = [start, end]

    derived_assignment = np.full(cube.shape[0], -1, dtype=np.int16)
    for name, (start, end) in intervals.items():
        span = (end - start) % 360.0
        offset = (hue_angle - start) % 360.0
        derived_assignment[gated & (offset < span)] = families.index(name)

    agree = int(((derived_assignment == assignment) & gated).sum())
    total = int(gated.sum())

    return {
        "method": lch_hue_boundaries.__doc__.strip().splitlines()[0],
        "provenance_note": (
            "Derived by measurement against the current HSV behaviour; no "
            "authoritative CIELAB hue-angle boundary set for basic colour names "
            "exists to port. These intervals reproduce the shipping classifier in "
            "a perceptually uniform space -- they are not a colour-naming standard."
        ),
        "grid": {
            "steps_per_channel": steps,
            "points": int(cube.shape[0]),
            "subsampled": False,
            "hsv_source": "PIL Image.convert('HSV') on the grid itself",
        },
        "accent_gate": {
            "hsv": {"saturation_min": int(sat_min), "value_min": int(val_min)},
            "lch_candidate": {"chroma_min": LCH_GATE_C_MIN, "lightness_min": LCH_GATE_L_MIN},
            "hsv_pass_fraction": round(float(hsv_gate.mean()), 6),
            "lch_pass_fraction": round(float(lch_gate.mean()), 6),
            "both_pass_fraction": round(float((hsv_gate & lch_gate).mean()), 6),
            "hsv_only_fraction": round(float((hsv_gate & ~lch_gate).mean()), 6),
            "lch_only_fraction": round(float((~hsv_gate & lch_gate).mean()), 6),
            "agreement_rate": round(float((hsv_gate == lch_gate).mean()), 6),
            "hsv_pass_with_lightness_below_20": round(
                float((hsv_gate & (lightness < LCH_GATE_L_MIN)).sum() / max(1, hsv_gate.sum())), 6
            ),
            "hsv_pass_with_chroma_below_20": round(
                float((hsv_gate & (chroma < LCH_GATE_C_MIN)).sum() / max(1, hsv_gate.sum())), 6
            ),
            "hsv_only_median_lightness": round(float(np.median(lightness[hsv_gate & ~lch_gate])), 3)
            if (hsv_gate & ~lch_gate).any()
            else None,
            "hsv_only_median_chroma": round(float(np.median(chroma[hsv_gate & ~lch_gate])), 3)
            if (hsv_gate & ~lch_gate).any()
            else None,
            "lch_only_median_lightness": round(float(np.median(lightness[~hsv_gate & lch_gate])), 3)
            if (~hsv_gate & lch_gate).any()
            else None,
            "lch_only_median_chroma": round(float(np.median(chroma[~hsv_gate & lch_gate])), 3)
            if (~hsv_gate & lch_gate).any()
            else None,
        },
        "families": per_family,
        "family_order_by_lch_hue": ordered,
        "boundaries": boundaries,
        "derived_intervals_deg": {name: [round(a, 3), round(b, 3)] for name, (a, b) in intervals.items()},
        "reproduction": {
            "gated_points": total,
            "agreements": agree,
            "agreement_rate": round(agree / total, 6) if total else None,
            "disagreement_rate": round(1.0 - agree / total, 6) if total else None,
        },
    }


def _best_split(mean_a, mean_b, angles_a, angles_b, step=0.25):
    """Angle between two family means that misclassifies fewest gated points."""
    span = (mean_b - mean_a) % 360.0
    if span == 0:
        return mean_a % 360.0, 0
    offsets = np.arange(0.0, span + step, step)
    da = lch.arc_delta(mean_a, angles_a)
    db = lch.arc_delta(mean_a, angles_b)
    # Points of A past the split (but before B's mean) and points of B before it.
    costs = [
        int(((da > offset) & (da <= span)).sum() + ((db < offset)).sum()) for offset in offsets
    ]
    best = int(np.argmin(costs))
    return (mean_a + float(offsets[best])) % 360.0, costs[best]


# --- Perceptual colour (WP1 dependency) ---------------------------------------


def de2000_section(records, thresholds, scripts_dir):
    """Thresholds for whatever perceptual-colour fields the palette tool emits.

    WP1 owns ``scripts/pil_color.py`` and the field names. When it has not landed
    -- or has landed but the palette tool does not yet emit a distance -- this
    section says so rather than inventing a threshold for a metric that does not
    exist.
    """
    present = (Path(scripts_dir) / "pil_color.py").exists()
    discovered = sorted(
        {
            metric
            for record in records
            for metric in record["metrics"]
            if any(hint in metric.lower() for hint in ("de2000", "delta_e", "deltae", "de00"))
        }
    )
    if not discovered:
        return {
            "status": "pending WP1",
            "pil_color_py_present": present,
            "note": (
                "scripts/pil_color.py "
                + ("exists but " if present else "does not exist and ")
                + "pil_palette_diff emitted no perceptual-colour distance field, so no "
                "DeltaE2000 palette threshold could be derived. Re-run this bundle once "
                "WP1 lands; the harness discovers the field names automatically."
            ),
        }
    return {
        "status": "derived",
        "pil_color_py_present": present,
        "metrics": discovered,
        "thresholds": {
            mode: {metric: thresholds[mode]["metrics"].get(metric) for metric in discovered}
            for mode in thresholds
        },
    }


# --- Metric demotion ----------------------------------------------------------


def demotion_candidates(curves):
    """Metrics that fail to resolve most perturbations at any tested magnitude.

    Phase 1 demoted RGB palette distance on exactly this basis. Demotion is an
    acceptable outcome: a metric whose detection limit is worse than the smallest
    change anyone cares about is worse than useless, because a null from it reads
    as reassurance.
    """
    summary = {}
    for curve in curves:
        node = summary.setdefault(
            curve["metric"], {"metric": curve["metric"], "curves": 0, "undetected": 0, "non_monotonic": 0, "modes": set()}
        )
        node["curves"] += 1
        node["modes"].add(curve["mode"])
        if curve["detection_limit"]["magnitude"] is None:
            node["undetected"] += 1
        if not curve["monotonic_nondecreasing"]:
            node["non_monotonic"] += 1

    out = []
    for metric in sorted(summary):
        node = summary[metric]
        undetected_rate = node["undetected"] / node["curves"]
        out.append(
            {
                "metric": metric,
                "curves": node["curves"],
                "undetected_curves": node["undetected"],
                "undetected_rate": round(undetected_rate, 6),
                "non_monotonic_curves": node["non_monotonic"],
                "modes": sorted(node["modes"]),
                "recommend_demote": bool(undetected_rate >= 0.7),
            }
        )
    return out


# --- Verdicts -----------------------------------------------------------------


def constant_verdicts(analysis):
    """The constants table: current guess vs derived value vs verdict."""
    change = analysis["change_threshold"]
    hue = analysis["hue_rule"]
    cells = analysis["cell_support"]
    fg = analysis["foreground_sweep"]
    accent = analysis["accent_sweep"]
    gate = analysis["lch_hue_boundaries"]["accent_gate"]

    rows = []

    derived_change = change.get("derived")
    rows.append(
        {
            "constant": "CHANGE_THRESHOLD",
            "location": "scripts/pil_common.py",
            "current": change.get("current"),
            "derived": derived_change,
            "verdict": "KEEP"
            if derived_change is None or derived_change == change.get("current")
            else f"REPLACE-WITH-{derived_change}",
            "n": change.get("n"),
            "alpha": change.get("alpha"),
            "evidence": (
                f"Swept the per-pixel luminance gate over {change.get('n')} full-frame controls. "
                f"Smallest gate whose bootstrap CI upper bound on Q(1-alpha) of changed-area stays "
                f"below {CHANGE_AREA_BUDGET}: {derived_change}. Gate that also keeps the "
                f"'any changed pixel' rate within alpha: {change.get('derived_bbox_criterion')}. "
                "See change_threshold.sweep for the full table and "
                "change_threshold.sensitivity_at_smallest_perturbation for what the choice costs."
            ),
        }
    )

    choice = hue.get("gate_choice") or {}
    current_hue = hue.get("current", {})
    current_perf = hue.get("current_performance", {})
    for constant, key in (
        ("HUE_SHIFT_MIN_ABSOLUTE", "hue_shift_min_absolute"),
        ("HUE_SHIFT_MIN_RELATIVE", "hue_shift_min_relative"),
    ):
        derived = choice.get(key)
        current = current_hue.get(key)
        if derived is None:
            verdict = "KEEP"
        elif derived == current:
            verdict = "KEEP"
        else:
            verdict = f"REPLACE-WITH-{derived}"
        if constant == "HUE_SHIFT_MIN_RELATIVE" and derived == 0.0:
            verdict = "DEMOTE"
        rows.append(
            {
                "constant": constant,
                "location": "scripts/pil_palette_diff.py",
                "current": current,
                "derived": derived,
                "verdict": verdict,
                "n": hue.get("n"),
                "alpha": hue.get("alpha"),
                "evidence": (
                    f"Neyman-Pearson sweep of the compound gate over {hue.get('n')} controls and the "
                    "hue-rotation grid. Current constants: false-alarm rate "
                    f"{current_perf.get('control_false_alarm_rate')} (budget {hue.get('alpha')}), "
                    f"detection limit {_limit_text(current_perf)}. Chosen pair: false-alarm rate "
                    f"{choice.get('control_false_alarm_rate')}, detection limit {_limit_text(choice)}. "
                    f"Offline rule reproduced the tool's own verdict on "
                    f"{hue.get('reconstruction_check', {}).get('agreement_rate')} of checked pairs."
                ),
            }
        )

    presence = choice
    for constant, key, current_key in (
        ("HUE_PRESENCE_MIN_FRACTION", "hue_presence_min_fraction", "hue_presence_min_fraction"),
        ("HUE_PRESENCE_MIN_PIXELS", "hue_presence_min_pixels", "hue_presence_min_pixels"),
    ):
        derived = presence.get(key)
        current = current_hue.get(current_key)
        rows.append(
            {
                "constant": constant,
                "location": "scripts/pil_common.py",
                "current": current,
                "derived": derived,
                "verdict": "KEEP" if derived is None or derived == current else f"REPLACE-WITH-{derived}",
                "n": hue.get("n"),
                "alpha": hue.get("alpha"),
                "evidence": (
                    "The presence floors were swept jointly with the shift gates -- they can fire the "
                    "rule on their own, so pinning them would have searched the wrong space. Floors "
                    f"chosen by the joint search: pixels={presence.get('hue_presence_min_pixels')}, "
                    f"fraction={presence.get('hue_presence_min_fraction')}, at false-alarm rate "
                    f"{presence.get('control_false_alarm_rate')} and detection limit {_limit_text(presence)}."
                ),
            }
        )

    derived_cells = cells.get("derived")
    rows.append(
        {
            "constant": "CELL_MIN_SUPPORT_PIXELS",
            "location": "scripts/pil_common.py",
            "current": cells.get("current"),
            "derived": derived_cells,
            "verdict": "KEEP" if derived_cells is None or derived_cells == cells.get("current") else f"REPLACE-WITH-{derived_cells}",
            "n": cells.get("samples"),
            "alpha": None,
            "evidence": (
                f"{cells.get('samples')} cell pairs from foreground-mode control comparisons, bucketed by "
                f"foreground support. Rule: {cells.get('rule')}. Reference p99 divergence at best support: "
                f"{cells.get('reference_p99')}."
            ),
        }
    )

    background_crossing = fg.get("smallest_foreground_fraction_where_full_frame_sees_a_total_object_recolour")
    rows.append(
        {
            "constant": "BACKGROUND_DOMINANT_MAX",
            "location": "scripts/pil_common.py",
            "current": fg.get("current", {}).get("BACKGROUND_DOMINANT_MAX"),
            "derived": background_crossing,
            "verdict": _crossing_verdict(fg.get("current", {}).get("BACKGROUND_DOMINANT_MAX"), background_crossing, at_least=True),
            "n": len(fg.get("levels", [])),
            "alpha": None,
            "evidence": (
                "Foreground-fraction sweep: the smallest measured foreground share at which a *total* object "
                f"recolour still clears the full-frame control thresholds on all of {fg.get('detection_metrics')} "
                f"is {background_crossing}. The flag must fire at or below that share, so the constant is sound "
                "only if it sits at or above it."
            ),
        }
    )

    foreground_crossing = fg.get("smallest_foreground_fraction_where_foreground_mode_control_noise_stays_below_threshold")
    rows.append(
        {
            "constant": "FOREGROUND_MIN_FRACTION",
            "location": "scripts/pil_common.py",
            "current": fg.get("current", {}).get("FOREGROUND_MIN_FRACTION"),
            "derived": foreground_crossing,
            "verdict": _crossing_verdict(fg.get("current", {}).get("FOREGROUND_MIN_FRACTION"), foreground_crossing, at_least=True),
            "n": len(fg.get("levels", [])),
            "alpha": None,
            "evidence": (
                "Same sweep, foreground mode: the smallest measured foreground share at which foreground-mode "
                f"control noise stays under its own thresholds is {foreground_crossing}. Below that the mask is "
                "unstable and foreground statistics are noise, so foreground_too_small must fire."
            ),
        }
    )

    accent_crossing = accent.get("smallest_accent_fraction_with_no_control_false_alarms")
    rows.append(
        {
            "constant": "ACCENT_AREA_SMALL_FRACTION",
            "location": "scripts/pil_common.py",
            "current": accent.get("current"),
            "derived": accent_crossing,
            "verdict": _crossing_verdict(accent.get("current"), accent_crossing, at_least=True),
            "n": len(accent.get("levels", [])),
            "alpha": None,
            "evidence": (
                "Accent-area sweep: the smallest measured accent pixel fraction at which no-change controls "
                f"produce no hue-shift false alarms and stay under the absolute gate is {accent_crossing}. "
                "accent_area_very_small must fire at or below that fraction."
            ),
        }
    )

    rows.append(
        {
            "constant": "DEFAULT_ACCENT_SAT_MIN / DEFAULT_ACCENT_VAL_MIN (HSV accent gate)",
            "location": "scripts/pil_common.py",
            "current": f"HSV S>{gate['hsv']['saturation_min']}, V>{gate['hsv']['value_min']}",
            "derived": f"LCh C>={LCH_GATE_C_MIN}, L>={LCH_GATE_L_MIN}",
            "verdict": _gate_verdict(gate),
            "n": None,
            "alpha": None,
            "evidence": (
                f"Over a {LCH_GRID_STEPS}^3 sRGB grid the HSV gate admits {gate['hsv_pass_fraction']} of colours "
                f"and the LCh gate {gate['lch_pass_fraction']}; they agree on {gate['agreement_rate']}. "
                f"{gate['hsv_pass_with_lightness_below_20']} of HSV-admitted colours have CIELAB L < "
                f"{LCH_GATE_L_MIN} (near-black yet counted as a vivid accent) and "
                f"{gate['hsv_pass_with_chroma_below_20']} have C < {LCH_GATE_C_MIN} (near-neutral yet counted "
                "as vivid). The LCh gate is a candidate, not a calibrated answer: no perturbation experiment "
                "here can rank two gates without a ground-truth notion of 'is an accent', which this corpus "
                "does not supply."
            ),
        }
    )
    return rows


def _limit_text(node):
    detection = (node or {}).get("detection") or {}
    parts = [f"{key}: {value.get('detection_limit_degrees')}deg" for key, value in sorted(detection.items())]
    return ", ".join(parts) if parts else "n/a"


def _crossing_verdict(current, crossing, at_least=True):
    if crossing is None or current is None:
        return "KEEP"
    if at_least:
        return "KEEP" if current >= crossing else f"REPLACE-WITH-{crossing}"
    return "KEEP" if current <= crossing else f"REPLACE-WITH-{crossing}"


def _gate_verdict(gate):
    """The HSV gate earns REPLACE only when it demonstrably admits non-accents."""
    dark = gate["hsv_pass_with_lightness_below_20"]
    neutral = gate["hsv_pass_with_chroma_below_20"]
    if dark >= 0.05 or neutral >= 0.05:
        return f"REPLACE-WITH-LCh C>={LCH_GATE_C_MIN:g}, L>={LCH_GATE_L_MIN:g}"
    return "KEEP"


# --- Entry point --------------------------------------------------------------


def derive(records, tools_dir, scripts_dir):
    """Everything downstream of measurement. Returns the three JSON payloads."""
    thresholds = control_thresholds(records)
    curves = response_curves(records, thresholds)
    change = change_threshold_analysis(records)
    hue = hue_rule_analysis(records)
    cells = cell_support_analysis(records)
    fg = foreground_sweep_analysis(records, thresholds)
    hue_abs = (hue.get("gate_choice") or {}).get("hue_shift_min_absolute") or 0.02
    accent = accent_sweep_analysis(records, hue_abs)
    boundaries = lch_hue_boundaries(tools_dir)

    analysis = {
        "thresholds": thresholds,
        "change_threshold": change,
        "hue_rule": hue,
        "cell_support": cells,
        "foreground_sweep": fg,
        "accent_sweep": accent,
        "lch_hue_boundaries": boundaries,
    }

    derived_thresholds = {
        "method": {
            "rule": "Neyman-Pearson with a fixed false-alarm budget",
            "threshold": "upper bound of a percentile bootstrap CI on Q(1-alpha) of the no-change control distribution",
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "bootstrap_ci_level": BOOTSTRAP_CI,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "alpha_rule": "alpha = 0.01 when n >= 300, 0.05 when n >= 60, 0.10 when n >= 30, else undefined (n >= 3/alpha)",
            "not_used": [
                "Youden's J -- requires a well-sampled positive class matching deployment; ours is fabricated by our own generator",
                "equal-error rate -- asserts symmetric costs, and false negatives cost more here",
            ],
            "metric_orientation": "every metric is recorded as a distance: 0 = no difference, larger = more different; structural_similarity enters as 1 - similarity",
        },
        "control_sets": thresholds,
        "detection_limits": detection_limit_table(curves),
        "change_threshold": change,
        "hue_rule": hue,
        "cell_support": cells,
        "foreground_sweep": fg,
        "accent_sweep": accent,
        "perceptual_colour_de2000": de2000_section(records, thresholds, scripts_dir),
        "metric_demotion": demotion_candidates(curves),
        "constant_verdicts": constant_verdicts(analysis),
    }

    response = {
        "method": {
            "grid": "magnitude x extent, 5-6 magnitudes per perturbation family",
            "aggregate": "median across scenes and scene seeds at each magnitude; per-scene medians retained",
            "monotonicity": "checked along magnitude at fixed extent; Spearman rank correlation reported alongside explicit violations",
            "detection_limit": "smallest magnitude whose median response clears the metric's threshold and stays clear at every larger magnitude",
        },
        "curves": curves,
    }

    return derived_thresholds, response, boundaries
