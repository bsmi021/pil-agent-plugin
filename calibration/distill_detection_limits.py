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

    OUT.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT.relative_to(REPO)} with {len(metrics)} metrics")


if __name__ == "__main__":
    main()
