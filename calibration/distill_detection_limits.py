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
        "provenance": {                   # loader-ignored, for readers
          "full_frame":  {"n", "alpha", "scenes", "dominant_control_family"},
          "foreground":  {"n", "alpha", "scenes", "dominant_control_family"}
        },
        "detection_limits": {
          "<perturbation>": "16 sigma code values",
          "<perturbation>": "not resolved at any tested magnitude",
          ...
        }
      }
    }

Provenance is recorded PER MODE, and that is a correction rather than a
flourish. An earlier version of this script emitted a single flat ``n`` and
``alpha`` taken from the full-frame record, sitting next to BOTH thresholds --
so the file advertised n=400 / alpha=0.01 over a ``threshold_foreground`` that
was actually derived at n=100 / alpha=0.05 from one scene. Every foreground
threshold in the bundle carries the weaker provenance, and the flat fields
misrepresented all of them at once.

``dominant_control_family`` is emitted for the same reason. A threshold whose
control distribution is set by a single family is a statement about that family
rather than about noise in general: foreground ``luminance_mean_delta_abs`` is
34.129 because ``rescale_roundtrip`` sits at median 27.460, while every other
foreground family for that metric is at or under 1.564. Surfacing the family
next to the number keeps a reader from mistaking one for the other.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "runs" / "2026-08-19-phase2-calibration" / "derived-thresholds.json"
OUT = REPO / "scripts" / "detection_limits.json"


def _provenance(record, scenes):
    """Per-mode provenance for one metric's threshold, or None if not derived.

    ``dominant_control_family`` is the family with the highest median in this
    metric's control distribution, together with that median and the next
    family's, so a reader can see at a glance whether the threshold rests on one
    family or on the whole control set.
    """
    if not record:
        return None
    families = record.get("by_control_family") or {}
    ranked = sorted(
        families.items(), key=lambda kv: kv[1].get("median", 0.0), reverse=True
    )
    dominant = None
    if ranked:
        top_name, top_stats = ranked[0]
        runner_up = ranked[1][1].get("median") if len(ranked) > 1 else None
        dominant = {
            "family": top_name,
            "median": top_stats.get("median"),
            "n": top_stats.get("n"),
            "next_family_median": runner_up,
        }
    return {
        "n": record.get("n"),
        "alpha": record.get("alpha"),
        "scenes": list(scenes) if scenes else None,
        "dominant_control_family": dominant,
    }


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

    full_scenes = bundle["control_sets"]["full_frame"].get("scenes")
    fg_scenes = bundle["control_sets"]["foreground"].get("scenes")

    metrics = {}
    for name, record in sorted(full.items()):
        fg_record = fg.get(name)
        metrics[name] = {
            "threshold": record["threshold"],
            "threshold_foreground": fg_record["threshold"] if fg_record else None,
            "provenance": {
                "full_frame": _provenance(record, full_scenes),
                "foreground": _provenance(fg_record, fg_scenes),
            },
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
            "provenance": dissim["provenance"],
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
            # The hue rule is a compound boolean gate, not a scalar threshold,
            # so it carries the joint sweep's provenance rather than a control
            # distribution's. Full-frame only -- the sweep was never run in
            # foreground mode.
            "provenance": {
                "full_frame": {
                    "n": hue_rule.get("n"),
                    "alpha": hue_rule.get("alpha"),
                    "scenes": list(full_scenes) if full_scenes else None,
                    "dominant_control_family": None,
                },
                "foreground": None,
            },
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
