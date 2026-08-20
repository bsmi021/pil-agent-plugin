#!/usr/bin/env python
"""Discrimination gate for the alignment half of scripts/pil_alignment.py.

Alignment-half only -- the WCAG contrast half is verified against the standard
itself in tests/test_alignment.py and needs no threshold derivation, the same
way pil_color.py's CIEDE2000 is verified against the 34 Sharma reference
values rather than statistically calibrated.

Method, following docs/phase3-build-plan.md §1's rule that each A5 candidate
owns a self-contained gate script that:

*   Imports bootstrap_quantile and alpha_for from calibration.derive READ-ONLY
    (they are pure functions with no shared state).
*   Imports scene builders from calibration.scenes and perturbation operators
    from calibration.perturb READ-ONLY.
*   Invokes scripts/pil_alignment.py as a CLI SUBPROCESS on every measurement
    -- never by importing its functions, per calibration/measure.py's own
    docstring ("calibrating the importable functions would calibrate something
    the caller never runs").
*   Writes its own dated run bundle under runs/.
*   Does NOT write to scripts/detection_limits.json; the tool carries its own
    calibrated tolerance as a module constant (DEFAULT_ALIGNMENT_TOLERANCE_PX
    in scripts/pil_alignment.py) and cites this bundle in its
    interpretation_limits.

What the gate measures: the sub-pixel jitter noise floor of baseline/margin
detection under the no-op control set. "No-op" here means a perturbation that
leaves the object geometry unchanged -- rescale_roundtrip at mild factors,
jpeg_reencode at high quality, small gaussian_blur, small add_noise. All four
have ground-truth margin delta of ZERO between the original and perturbed
image; whatever the tool reports is jitter.

Note: perturb.py's `translate` moves geometry by a known number of pixels, so
its ground-truth margin delta is that translation and it is EXCLUDED from the
no-op set. It is included as an accuracy-check family: does the tool recover
the translation to within N pixels?

What ships: DEFAULT_ALIGNMENT_TOLERANCE_PX is the ceiling of the CI upper
bound on the observed control-set margin delta, at alpha_for(n). The tool's
default tolerance is that value (rounded up to a whole pixel so it never
claims more than the calibration measured). If the derived floor is not
usefully small ("useful" defined here as <= 8 px, on the argument that a UI
alignment tool has to catch mistakes smaller than one line of body text), the
alignment half is DEMOTED and the tool file records that fact -- the contrast
half continues to ship on its own.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

CALIBRATION_DIR = Path(__file__).resolve().parent
REPO_ROOT = CALIBRATION_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(CALIBRATION_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from derive import alpha_for, bootstrap_quantile  # noqa: E402
import perturb  # noqa: E402
import scenes  # noqa: E402

ALIGNMENT_TOOL = SCRIPTS_DIR / "pil_alignment.py"

# The scenes the gate exercises. All four synthetic scenes from
# calibration/scenes.py: two are near-empty (dark_accent has few edges,
# thin_object is a single narrow object), two have dense structure. If jitter
# depends heavily on scene content the bootstrap picks that up.
SCENE_BUILDERS = ("structured", "thin_object", "busy", "dark_accent")

# No-op perturbations: ground-truth margin delta is 0 between the original and
# the perturbed copy, so whatever the tool reports is jitter. Kept mild because
# the point is to measure the *noise floor*, not to prove the tool breaks under
# heavy processing.
NO_OP_PERTURBATIONS = (
    # rescale_roundtrip at mild factors (0.5 and 0.35 are the shallow end of
    # perturb.GRID's rescale_roundtrip family)
    ("rescale_roundtrip", {"factor": 0.5}),
    ("rescale_roundtrip", {"factor": 0.35}),
    # jpeg_reencode at high quality (95 is the shallow end of the JPEG family)
    ("jpeg_reencode", {"quality": 95}),
    ("jpeg_reencode", {"quality": 85}),
    # gaussian_blur at small radius
    ("gaussian_blur", {"radius": 0.5}),
    ("gaussian_blur", {"radius": 1.0}),
    # small add_noise, seed handled below
    ("add_noise", {"sigma": 2}),
    ("add_noise", {"sigma": 4}),
)

# Accuracy check: does the tool recover a known small translation?
TRANSLATE_ACCURACY = (
    # (fraction, expected pixel shift on a 384-wide scene)
    (1 / 384.0, 1),
    (2 / 384.0, 2),
    (4 / 384.0, 4),
    (8 / 384.0, 8),
)

CONTROL_SEEDS = (101, 211, 337, 449, 563)

# The tool's shipping profile threshold. Measuring at a different threshold
# would produce a noise floor that does not apply to what the tool actually
# ships, so this must match scripts/pil_alignment.DEFAULT_PROFILE_THRESHOLD.
PROFILE_THRESHOLD = 0.05

# When the derived CI upper bound exceeds this many pixels, the alignment half
# is demoted. "Useful" is defined against a UI-alignment use case: a tool
# meant to catch alignment mistakes must resolve differences smaller than one
# line of body text, ~= 8 pixels at typical screen densities.
DEMOTION_CEILING_PX = 8


def _run_tool(image_a, image_b, profile_threshold):
    """Invoke the tool as an agent would; parse and return its JSON payload."""
    proc = subprocess.run(
        [
            sys.executable,
            str(ALIGNMENT_TOOL),
            "alignment",
            str(image_a),
            str(image_b),
            "--profile-threshold",
            f"{profile_threshold:.6f}",
            "--tolerance-px",
            "0",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"pil_alignment failed (rc={proc.returncode})\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


def _build_scene(name, seed):
    fn = getattr(scenes, name)
    return fn(seed=seed)


def _apply(op_name, img, params, seed):
    """Wrap perturb.apply so `add_noise` gets a stable seed derived from the
    scene seed, not from the caller-supplied ``seed_offset``.

    add_noise is the only perturbation with a real random draw; the seed is
    derived up front so the whole gate is deterministic across runs.
    """
    resolved = dict(params)
    if op_name == "add_noise":
        resolved["seed"] = perturb.derive_seed(
            "alignment_gate", seed, op_name, params.get("sigma"),
        )
    return perturb.apply(op_name, img, resolved)


def _max_axis_delta(payload):
    """Peak absolute margin delta reported for the pair, in pixels.

    The bootstrap operates on scalars; taking the peak across both axes is the
    right summary for a "tolerance in pixels" answer (the tolerance has to
    cover whichever axis moves most). Empty-profile results are None.
    """
    if payload["comparison"]["profile_empty"]:
        return None
    return payload["comparison"]["max_pixel_delta"]


def measure_noise_floor(tmpdir):
    """Run every (scene, seed, no-op perturbation) pair through the tool.

    Returns a list of measurement dicts and a list of the scalar
    max_pixel_delta values fed to bootstrap_quantile.
    """
    tmpdir = Path(tmpdir)
    tmpdir.mkdir(parents=True, exist_ok=True)

    records = []
    values = []
    for scene_name in SCENE_BUILDERS:
        for seed in CONTROL_SEEDS:
            base_img = _build_scene(scene_name, seed)
            base_path = tmpdir / f"{scene_name}_{seed}_base.png"
            base_img.save(base_path)
            for op_name, params in NO_OP_PERTURBATIONS:
                perturbed = _apply(op_name, base_img, params, seed)
                perturbed_path = (
                    tmpdir / f"{scene_name}_{seed}_{op_name}_"
                    f"{'_'.join(f'{k}={v}' for k,v in params.items())}.png"
                )
                perturbed.save(perturbed_path)
                payload = _run_tool(base_path, perturbed_path, PROFILE_THRESHOLD)
                delta = _max_axis_delta(payload)
                record = {
                    "kind": "control",
                    "scene": scene_name,
                    "seed": seed,
                    "perturbation": op_name,
                    "params": params,
                    "max_pixel_delta": delta,
                    "profile_empty": payload["comparison"]["profile_empty"],
                    "expected_delta": 0,
                }
                records.append(record)
                if delta is not None:
                    values.append(float(delta))
    return records, values


def measure_translation_accuracy(tmpdir):
    """Extra evidence: does the tool recover a known small translation?

    Not fed into the noise-floor bootstrap (the ground-truth delta is nonzero
    here, so pooling it with the no-op values would inflate the "noise" number
    by the signal it is meant to be distinguishing). Instead it is published
    alongside as a signal check.
    """
    tmpdir = Path(tmpdir)
    tmpdir.mkdir(parents=True, exist_ok=True)

    records = []
    for scene_name in SCENE_BUILDERS:
        for seed in CONTROL_SEEDS[:2]:
            base_img = _build_scene(scene_name, seed)
            base_path = (
                tmpdir / f"trans_{scene_name}_{seed}_base.png"
            )
            base_img.save(base_path)
            for fraction, expected_px in TRANSLATE_ACCURACY:
                perturbed = perturb.translate(base_img, fraction)
                perturbed_path = (
                    tmpdir / f"trans_{scene_name}_{seed}_{expected_px}px.png"
                )
                perturbed.save(perturbed_path)
                payload = _run_tool(base_path, perturbed_path, PROFILE_THRESHOLD)
                observed = _max_axis_delta(payload)
                records.append({
                    "kind": "translation",
                    "scene": scene_name,
                    "seed": seed,
                    "fraction": fraction,
                    "expected_delta_px": expected_px,
                    "observed_max_delta_px": observed,
                    "accuracy_error_px": (
                        None if observed is None
                        else int(abs(observed - expected_px))
                    ),
                    "profile_empty": payload["comparison"]["profile_empty"],
                })
    return records


def derive_thresholds(values):
    """Bootstrap Q(1-alpha) with CI, following calibration/derive.py's rule."""
    clean = [float(v) for v in values if v is not None]
    n = len(clean)
    alpha = alpha_for(n)
    result = bootstrap_quantile(clean, alpha, "alignment_gate|max_pixel_delta")
    return result, n, alpha, clean


def build_bundle(bundle_dir):
    """Run every measurement, publish the bundle, return the summary payload."""
    bundle_dir = Path(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    tmpdir = bundle_dir / "scratch"

    noise_records, noise_values = measure_noise_floor(tmpdir)
    translation_records = measure_translation_accuracy(tmpdir)

    derived, n, alpha, values = derive_thresholds(noise_values)
    ci_upper = derived.get("ci_upper")
    observed_max = derived.get("observed_max")

    tolerance_default = None
    demoted = False
    demotion_reason = None
    if ci_upper is None:
        demoted = True
        demotion_reason = (
            "bootstrap could not derive a Q(1-alpha) upper bound "
            f"(n={n}, alpha={alpha}): no honest tolerance available"
        )
    else:
        tolerance_default = int(math.ceil(ci_upper))
        if tolerance_default > DEMOTION_CEILING_PX:
            demoted = True
            demotion_reason = (
                f"derived noise-floor tolerance ({tolerance_default}px) "
                f"exceeds the useful ceiling ({DEMOTION_CEILING_PX}px). "
                "A UI-alignment tool must resolve differences smaller than "
                "one line of body text; a wider floor swallows the signal "
                "the tool exists to report."
            )

    thresholds_payload = {
        "gate": "alignment",
        "method": {
            "rule": (
                "Neyman-Pearson noise-floor derivation: run tool on each "
                "(scene, seed, no-op perturbation) pair; record the peak "
                "absolute margin delta per pair; take the bootstrap-CI "
                "upper bound on Q(1-alpha) of that distribution as the "
                "sub-pixel jitter tolerance."
            ),
            "no_op_perturbations": [
                {"op": name, "params": params}
                for name, params in NO_OP_PERTURBATIONS
            ],
            "excluded_from_control_set": [
                "translate (ground-truth delta is nonzero; pooling with "
                "no-op values would inflate the noise floor by the signal)"
            ],
            "profile_threshold": PROFILE_THRESHOLD,
            "scenes": list(SCENE_BUILDERS),
            "seeds": list(CONTROL_SEEDS),
            "demotion_ceiling_px": DEMOTION_CEILING_PX,
        },
        "distribution_summary": {
            "n": n,
            "min": min(values) if values else None,
            "median": (
                sorted(values)[n // 2] if values else None
            ),
            "max": max(values) if values else None,
            "mean": (sum(values) / n) if values else None,
        },
        "bootstrap": derived,
        "shipped_default_tolerance_px": tolerance_default,
        "demoted": demoted,
        "demotion_reason": demotion_reason,
        "translation_accuracy": translation_records,
    }

    (bundle_dir / "derived-thresholds.json").write_text(
        json.dumps(thresholds_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "measurements.jsonl").write_text(
        "\n".join(
            json.dumps(record, sort_keys=True) for record in noise_records
        ) + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "translation-accuracy.jsonl").write_text(
        "\n".join(
            json.dumps(record, sort_keys=True) for record in translation_records
        ) + "\n",
        encoding="utf-8",
    )
    return thresholds_payload


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-dir",
        default=str(
            REPO_ROOT / "runs" / "2026-08-20-alignment-discrimination"
        ),
        help="where to write derived-thresholds.json and the raw records",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    payload = build_bundle(args.bundle_dir)
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
