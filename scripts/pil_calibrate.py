"""Build and apply domain profiles using source-disjoint held-out image pairs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
from pil_diff_regions import compare_images, METRIC_VERSION
from pil_io import digest, emit, read_json, write_json
from pil_pipeline import measure

TOOL_VERSION = "0.9.0"
DOMAINS = ("screenshot", "photograph", "transparent-render", "concept-versus-render")
METRICS = (
    "changed_area_fraction",
    "delta_e_mean",
    "delta_e_p95",
    "alpha_mean_absolute_delta",
)


def identity(
    domain, input_mode, mask_source, metric, delta_e_threshold, alpha_threshold
):
    if (
        domain not in DOMAINS
        or input_mode not in ("stored", "display")
        or mask_source not in ("full_frame", "explicit_selection")
        or metric not in METRICS
    ):
        raise ValueError("invalid domain, input mode, mask source or metric")
    return {
        "domain": domain,
        "input_mode": input_mode,
        "mask_source": mask_source,
        "metric": metric,
        "metric_version": METRIC_VERSION,
        "delta_e_threshold": delta_e_threshold,
        "alpha_threshold": alpha_threshold,
        "pipeline": "native-normalized-intersection-v1",
        "registration": "none",
    }


def _rates(rows, threshold):
    negative = [r for r in rows if not r["changed"]]
    positive = [r for r in rows if r["changed"]]
    if not negative or not positive:
        raise ValueError("each split requires changed and unchanged examples")
    return {
        "false_alarm_rate": sum(r["value"] > threshold for r in negative)
        / len(negative),
        "miss_rate": sum(r["value"] <= threshold for r in positive) / len(positive),
        "negative_pairs": len(negative),
        "positive_pairs": len(positive),
    }


def _cluster_interval(rows, threshold, changed):
    groups = {}
    for row in rows:
        if row["changed"] == changed:
            groups.setdefault(row["source"], []).append(
                float(
                    (row["value"] <= threshold)
                    if changed
                    else (row["value"] > threshold)
                )
            )
    if len(groups) < 2:
        raise ValueError("each class needs at least two source groups per split")
    group_hits = np.array([sum(groups[k]) for k in sorted(groups)])
    group_counts = np.array([len(groups[k]) for k in sorted(groups)])
    rng = np.random.default_rng(20260905)
    selected = rng.integers(0, len(groups), (1000, len(groups)))
    samples = group_hits[selected].sum(axis=1) / group_counts[selected].sum(axis=1)
    return {
        "method": "source_cluster_percentile_bootstrap",
        "replicates": 1000,
        "source_count": len(groups),
        "estimand": "pair error rate with whole source groups resampled",
        "confidence_level": 0.95,
        "interval": [float(v) for v in np.quantile(samples, [0.025, 0.975])],
        "limitation": "Empirical interval conditional on observed source groups; zero observed errors does not establish zero population risk.",
    }


def calibrate(manifest):
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "calibration-corpus-v1"
    ):
        raise ValueError("expected calibration-corpus-v1")
    config = identity(
        manifest.get("domain"),
        manifest.get("input_mode", "stored"),
        manifest.get("mask_source", "full_frame"),
        manifest.get("metric", "changed_area_fraction"),
        manifest.get("delta_e_threshold", 2.0),
        manifest.get("alpha_threshold", 1.0),
    )
    target = manifest.get("max_false_alarm_rate", 0.05)
    max_miss = manifest.get("max_miss_rate", 0.2)
    if not (0 <= target < 1 and 0 <= max_miss < 1):
        raise ValueError("error budgets must be in [0,1)")
    rows = []
    source_splits = {}
    hash_splits = {}
    if not isinstance(manifest.get("pairs"), list) or not all(
        isinstance(pair, dict) for pair in manifest["pairs"]
    ):
        raise ValueError("corpus pairs must be an array of objects")
    for pair in manifest.get("pairs", []):
        source = pair.get("source")
        split = pair.get("split")
        if (
            not isinstance(source, str)
            or not source
            or split not in ("train", "validation")
            or not isinstance(pair.get("changed"), bool)
        ):
            raise ValueError(
                "pairs require source, train/validation split and boolean changed"
            )
        if source in source_splits and source_splits[source] != split:
            raise ValueError("source group leakage between train and validation")
        source_splits[source] = split
        hashes = {key: digest(pair[key]) for key in ("a", "b")}
        for sha in hashes.values():
            if sha in hash_splits and hash_splits[sha] != split:
                raise ValueError("image bytes leak between train and validation")
            hash_splits[sha] = split
        selected = bool(pair.get("mask_a") or pair.get("mask_b"))
        if selected != (config["mask_source"] == "explicit_selection"):
            raise ValueError("mask source does not match profile")
        metrics, _, _ = compare_images(
            pair["a"],
            pair["b"],
            input_mode=config["input_mode"],
            mask_a=pair.get("mask_a"),
            mask_b=pair.get("mask_b"),
            delta_e_threshold=config["delta_e_threshold"],
            alpha_threshold=config["alpha_threshold"],
        )
        magnitude = pair.get("magnitude")
        if magnitude is not None and (
            not isinstance(magnitude, (int, float))
            or not np.isfinite(magnitude)
            or magnitude < 0
        ):
            raise ValueError("magnitude must be a finite nonnegative number")
        rows.append(
            {
                "source": source,
                "split": split,
                "changed": pair["changed"],
                "value": metrics[config["metric"]],
                "hashes": hashes,
                "perturbation": pair.get("perturbation", "unspecified"),
                "magnitude": magnitude,
            }
        )
    train = [r for r in rows if r["split"] == "train"]
    validation = [r for r in rows if r["split"] == "validation"]
    negatives = [r["value"] for r in train if not r["changed"]]
    if not negatives:
        raise ValueError("training requires unchanged pairs")
    threshold = float(np.quantile(negatives, 1 - target, method="higher"))
    training = _rates(train, threshold)
    heldout = _rates(validation, threshold)
    for split_rows in (train, validation):
        for changed in (False, True):
            _cluster_interval(split_rows, threshold, changed)
    heldout["false_alarm_interval"] = _cluster_interval(validation, threshold, False)
    heldout["miss_interval"] = _cluster_interval(validation, threshold, True)
    heldout["accepted"] = (
        heldout["false_alarm_rate"] <= target and heldout["miss_rate"] <= max_miss
    )
    buckets = {}
    for row in validation:
        if row["changed"] and row["magnitude"] is not None:
            buckets.setdefault((row["perturbation"], row["magnitude"]), []).append(
                row["value"] > threshold
            )
    detection = [
        {
            "perturbation": name,
            "magnitude": magnitude,
            "detected": sum(values),
            "pairs": len(values),
            "detection_rate": sum(values) / len(values),
        }
        for (name, magnitude), values in sorted(buckets.items())
    ]
    result = {
        "tool": "pil_calibrate",
        "version": TOOL_VERSION,
        "schema": "domain-profile-v1",
        "identity": config,
        "threshold": threshold,
        "comparison": ">",
        "training": training,
        "validation": heldout,
        "error_budgets": {"false_alarm_rate": target, "miss_rate": max_miss},
        "observed_detection_points": detection,
        "records": rows,
        "scope": manifest.get("scope", "caller-supplied corpus only"),
        "interpretation_limits": [
            "Acceptance is observed performance on held-out source groups, not a universal accuracy claim.",
            "Detection points are measured magnitudes, not extrapolated detection limits.",
            "No-change means below this metric threshold; geometry and semantic intent are not assessed.",
        ],
    }
    result["profile_id"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    return result


def evaluate_profile(
    profile, image_a, image_b, *, domain, input_mode="stored", mask_a=None, mask_b=None
):
    if not isinstance(profile, dict) or profile.get("schema") != "domain-profile-v1":
        raise ValueError("expected domain-profile-v1")
    sealed = dict(profile)
    expected = sealed.pop("profile_id", None)
    if (
        hashlib.sha256(
            json.dumps(sealed, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        != expected
    ):
        raise ValueError("profile content hash mismatch")
    config = profile["identity"]
    current = identity(
        domain,
        input_mode,
        "explicit_selection" if mask_a or mask_b else "full_frame",
        config["metric"],
        config["delta_e_threshold"],
        config["alpha_threshold"],
    )
    if current != config:
        raise ValueError("domain or pipeline identity does not match profile")
    if not profile["validation"]["accepted"]:
        raise ValueError("profile did not meet its held-out error budgets")
    result, _, _ = compare_images(
        image_a,
        image_b,
        input_mode=input_mode,
        mask_a=mask_a,
        mask_b=mask_b,
        delta_e_threshold=config["delta_e_threshold"],
        alpha_threshold=config["alpha_threshold"],
    )
    legacy = measure(
        "pil_structure_diff",
        image_a,
        image_b,
        input_mode=input_mode,
        mask_a=mask_a,
        mask_b=mask_b,
    )
    value = result[config["metric"]]
    return {
        "tool": "pil_calibrate",
        "version": TOOL_VERSION,
        "command": "evaluate",
        "profile_id": profile["profile_id"],
        "identity": config,
        "value": value,
        "threshold": profile["threshold"],
        "verdict": "CHANGE_DETECTED"
        if value > profile["threshold"]
        else "NO_CHANGE_DETECTED",
        "measurement": result,
        "legacy_structure": legacy["result"]["diff"],
        "observed_detection_points": profile["observed_detection_points"],
        "interpretation_limits": profile["interpretation_limits"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("manifest")
    build.add_argument("--output", required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("profile")
    evaluate.add_argument("image_a")
    evaluate.add_argument("image_b")
    evaluate.add_argument("--domain", required=True, choices=DOMAINS)
    evaluate.add_argument(
        "--input-mode", choices=["stored", "display"], default="stored"
    )
    evaluate.add_argument("--mask-a")
    evaluate.add_argument("--mask-b")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            manifest = read_json(args.manifest)
            if (
                not isinstance(manifest, dict)
                or not isinstance(manifest.get("pairs"), list)
                or not all(isinstance(pair, dict) for pair in manifest["pairs"])
            ):
                raise ValueError(
                    "corpus must be an object with an array of pair objects"
                )
            for pair in manifest.get("pairs", []):
                for key in ("a", "b", "mask_a", "mask_b"):
                    if pair.get(key):
                        pair[key] = str(Path(args.manifest).parent / pair[key])
            result = calibrate(manifest)
            write_json(args.output, result)
        else:
            result = evaluate_profile(
                read_json(args.profile),
                args.image_a,
                args.image_b,
                domain=args.domain,
                input_mode=args.input_mode,
                mask_a=args.mask_a,
                mask_b=args.mask_b,
            )
        emit(result)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"pil_calibrate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
