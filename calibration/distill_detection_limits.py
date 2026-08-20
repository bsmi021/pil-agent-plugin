"""Distill the WP2 calibration bundle into scripts/detection_limits.json.

pil_contract_verdict.py consumes a compact thresholds file so that verdicts can
carry calibrated decision thresholds and detection limits without parsing the
full 1 MB research bundle. This script is the only writer of that file; run it
after any calibration re-run:

    uv run python calibration/distill_detection_limits.py

Schema written -- pil_contract_verdict.load_thresholds()'s flat map, where
EVERY top-level key is a metric (the loader rejects non-metric keys, so file
provenance lives in the calibration bundle and in this docstring, not in the
file):

    {
      "<metric>": {
        "threshold": number,              # full-frame control threshold
        "threshold_foreground": number | null,   # loader-ignored, for readers
        "n": int, "alpha": number,        # loader-ignored, for readers
        "detection_limits": {
          "<perturbation>": "16 sigma code values",
          "<perturbation>": "not resolved at any tested magnitude",
          ...
        }
      }
    }
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "runs" / "2026-08-19-phase2-calibration" / "derived-thresholds.json"
OUT = REPO / "scripts" / "detection_limits.json"


def _limit_string(entry):
    if entry["magnitude"] is None:
        return "not resolved at any tested magnitude"
    text = f"{entry['magnitude']:g} {entry['unit']}"
    if not entry.get("monotonic", True):
        text += " (non-monotonic response)"
    return text


def main():
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))

    full = bundle["control_sets"]["full_frame"]["metrics"]
    fg = bundle["control_sets"]["foreground"]["metrics"]
    limits_full = bundle["detection_limits"]["full_frame"]

    metrics = {}
    for name, record in sorted(full.items()):
        fg_record = fg.get(name)
        metrics[name] = {
            "threshold": record["threshold"],
            "threshold_foreground": fg_record["threshold"] if fg_record else None,
            "n": record["n"],
            "alpha": record["alpha"],
            "detection_limits": {
                pert: _limit_string(entry)
                for pert, entry in sorted(limits_full.get(name, {}).items())
            },
        }

    # Derived aliases for the names pil_contract_verdict actually queries.
    #
    # structural_similarity: the calibration measured structural_DISsimilarity
    # (their sum is 1 by construction), so the similarity floor is one minus
    # the dissimilarity ceiling and the detection limits carry over unchanged.
    if "structural_dissimilarity" in metrics:
        dissim = metrics["structural_dissimilarity"]
        metrics["structural_similarity"] = {
            "threshold": round(1.0 - dissim["threshold"], 6),
            "threshold_foreground": (
                round(1.0 - dissim["threshold_foreground"], 6)
                if dissim["threshold_foreground"] is not None
                else None
            ),
            "n": dissim["n"],
            "alpha": dissim["alpha"],
            "detection_limits": dict(dissim["detection_limits"]),
        }

    # entropy_delta: the bundle's name carries the _abs suffix.
    if "entropy_delta_abs" in metrics:
        metrics["entropy_delta"] = dict(metrics["entropy_delta_abs"])

    # accent_hue_shift_detected: a compound boolean rule, so it has no scalar
    # threshold -- but the calibration measured its detection limits directly
    # (hue_rule.gate_choice), and those are what a null verdict must cite.
    hue_rule = bundle.get("hue_rule") or {}
    detection = (hue_rule.get("gate_choice") or {}).get("detection") or {}
    if detection:
        metrics["accent_hue_shift_detected"] = {
            "threshold": None,
            "threshold_foreground": None,
            "n": hue_rule.get("n"),
            "alpha": hue_rule.get("alpha"),
            "detection_limits": {
                f"hue_rotation@{extent}": (
                    f"{entry['detection_limit_degrees']:g} degrees"
                    if entry.get("detection_limit_degrees") is not None
                    else "not resolved at any tested magnitude"
                )
                for extent, entry in sorted(detection.items())
            },
        }

    OUT.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT.relative_to(REPO)} with {len(metrics)} metrics")


if __name__ == "__main__":
    main()
