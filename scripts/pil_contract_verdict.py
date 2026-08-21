#!/usr/bin/env python
"""Contract-driven verdicts over one or many image pairs.

Emits JSON on stdout. The caller declares *intent* -- what it asked to change,
and what it required to stay put -- and this tool returns one verdict per
declared item instead of a pile of raw metrics.

    python pil_contract_verdict.py --contract contract.json ref.png render.png
    python pil_contract_verdict.py --expect palette.warmer a.png b.png
    python pil_contract_verdict.py --contract c.json --pairs views.json --foreground

Three verdict states, and the third carries the design's weight:

*   ``SATISFIED`` / ``VIOLATED`` -- measured, with the deciding field cited in
    ``evidence``.
*   ``UNMEASURABLE`` -- the predicate cannot be answered from two images. This
    must never silently degrade into an approximation. Edge density looks like a
    polygon-count proxy and is not one; a tool that quietly substitutes it
    produces confident wrong answers. The registry below therefore carries an
    explicit refuse list, and predicates on it are refused in every run, with no
    numeric evidence attached at all.

**Every negative finding carries its detection limit.** "Invariant satisfied"
and "no change detected" are not the same claim: a metric that cannot resolve
changes below some magnitude reports an invariant as SATISFIED when a
smaller-but-real change occurred. So a SATISFIED invariant -- and a VIOLATED
expectation, which rests on the same "nothing was detected" evidence -- reports
the detection limit of the metric that decided it. Those limits come from the
calibration bundle via --thresholds (or scripts/detection_limits.json when the
integrator has generated it). When no such file is available the field reads
"uncalibrated: detection limit not available"; this tool never invents a number.

Multi-pair mode aggregates worst-case per contract item: any VIOLATED pair makes
the item VIOLATED, and the per-pair detail travels alongside. A single diverging
view is a real failure even when the mean looks healthy, so nothing here
averages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from functools import cached_property
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pil_palette_diff  # noqa: E402
import pil_structure_diff  # noqa: E402
from pil_common import (  # noqa: E402
    ACCENT_SPACES,
    DEFAULT_ACCENT_CHROMA_MIN,
    DEFAULT_ACCENT_LIGHTNESS_MIN,
    DEFAULT_ACCENT_SAT_MIN,
    DEFAULT_ACCENT_SPACE,
    DEFAULT_ACCENT_VAL_MIN,
    DEFAULT_BACKGROUND_DELTA,
    HUE_FAMILIES,
    HUE_PRESENCE_MIN_FRACTION,
    HUE_PRESENCE_MIN_PIXELS,
    WORKING_LONG_EDGE,
    edge_magnitude,
    foreground_mask,
    load_rgb_alpha,
    mask_bbox,
    parse_grid,
    resize_mask,
)

TOOL_VERSION = "0.5.0"

SATISFIED = "SATISFIED"
VIOLATED = "VIOLATED"
UNMEASURABLE = "UNMEASURABLE"

# Worst-case ordering for WP4 aggregation. A single diverging pair must decide
# the item, so this is a max over pairs and never a mean.
VERDICT_SEVERITY = {SATISFIED: 0, UNMEASURABLE: 1, VIOLATED: 2}

ROLE_EXPECTED = "expected"
ROLE_INVARIANT = "invariant"

# The two refusals, spelled once. These strings are the contract: a caller
# matching on them must keep matching after any refactor here.
GEOMETRY_REFUSAL = "needs scene mesh statistics; a render cannot answer this"
STYLE_REFUSAL = "not reducible to pixel statistics"

# What a negative finding says when no calibration bundle is loaded. Never a
# number -- an invented detection limit is worse than an absent one, because it
# reads as measurement.
UNCALIBRATED_DETECTION_LIMIT = "uncalibrated: detection limit not available"

# The mandatory caveat on every detail.* item, attached regardless of verdict.
# Phase 1's central lesson: entropy and edge density are 2D image-complexity
# proxies that move with shading, lighting and camera angle, and reading them as
# geometry is exactly the failure this plugin exists to prevent.
DETAIL_CAVEAT = (
    "detail.* is a 2D image-complexity proxy (luminance entropy and edge "
    "magnitude), NOT geometry: it does not measure polygon count, mesh density "
    "or topology, and shading, lighting and camera angle move it independently "
    "of the underlying model. Never read this item as a geometry verdict."
)

# Directional hue mass for palette.warmer / palette.cooler. green and magenta
# are deliberately in neither set: they sit between the two axes, so counting
# them would let a green-ward shift read as a warming or a cooling depending on
# which arbitrary side they were assigned to.
WARM_FAMILIES = ("red", "orange", "yellow")
COOL_FAMILIES = ("cyan", "blue", "purple")

HUE_FAMILY_NAMES = tuple(name for name, _ in HUE_FAMILIES)

# Shipped defaults, echoed into parameters on every run. These are *shipped
# defaults, not calibrated thresholds* -- WP2's derived numbers arrive through
# --thresholds, and until they do, every negative finding says so via its
# detection limit.
SHIPPED_THRESHOLDS = {
    # exact.unchanged: changed-area fraction under which two frames count as
    # pixel-identical, paired with dhash_distance == 0.
    "changed_area_fraction": 0.001,
    # layout.composition_preserved: structural_similarity floor.
    "structural_similarity": 0.85,
    # palette.warmer / .cooler: minimum directional mass shift in
    # hue_family_fraction_deltas. Mirrors pil_palette_diff's
    # HUE_SHIFT_MIN_ABSOLUTE, which is the magnitude at which a single family's
    # move stops being resampling noise.
    "hue_family_mass_shift": 0.02,
    # identity.silhouette_preserved: intersection-over-union floor on the two
    # bbox-normalised foreground masks.
    "silhouette_iou": 0.85,
    # detail.*: both signals must move together and both must clear their
    # margin, so a lighting change that moves one alone cannot produce a
    # verdict.
    "entropy_delta": 0.05,
    "edge_mean_delta": 0.5,
}

INTERPRETATION_LIMITS = [
    "A verdict is only as good as the threshold behind it. Until a calibration "
    "bundle is supplied via --thresholds, every threshold here is a shipped "
    "default -- reasoned, echoed in parameters, but not derived from measured "
    "false-alarm rates. Read detection_limit before trusting any negative.",
    "SATISFIED on an invariant is NOT a guarantee that nothing changed. It "
    "means no change was detected by the deciding metric, which cannot resolve "
    "changes below its detection limit. The detection_limit field bounds what "
    "the verdict actually rules out; when it reads 'uncalibrated', the verdict "
    "bounds nothing and should be read as 'no evidence of change'.",
    "VIOLATED on an expect_change item means the requested change was not "
    "observed -- not that something broke. The same detection-limit caveat "
    "applies: a change smaller than the metric can resolve is indistinguishable "
    "from no change at all.",
    "UNMEASURABLE is a refusal, not a failure. geometry.* needs scene mesh "
    "statistics that no render carries; style.* and identity.same_character are "
    "not reducible to pixel statistics at all. These are never approximated, "
    "and their items carry no numeric evidence, because a plausible number is "
    "how an eager tool does damage.",
    DETAIL_CAVEAT,
    "identity.silhouette_preserved is restricted to cleanly maskable frames. "
    "Each foreground mask is cropped to its own bounding box and normalised to "
    "a common canvas before IoU, so the comparison is translation and scale "
    "invariant but NOT pose invariant. An empty mask on either side returns "
    "UNMEASURABLE rather than a fabricated score.",
    "Predicate truth does not depend on role. role decides only how the finding "
    "is phrased and whether a detection limit is attached, so the same "
    "predicate declared as both an expectation and an invariant will always "
    "return opposite-reading verdicts of the same measurement.",
    "Aggregation is worst-case per contract item, never a mean: one diverging "
    "pair makes the item VIOLATED. Read aggregate for the decision and the "
    "per-pair items for where it came from.",
    "geometry.* is UNMEASURABLE by default -- a render carries no polygon or "
    "vertex count. It becomes SATISFIED/VIOLATED when both sides of a pair "
    "supply scene-stats JSON via --scene-stats-a / --scene-stats-b (produced by "
    "scripts/pil_blender_mesh.py, which reads Blender scene data directly). "
    "Counts read from a scene reflect the base mesh; an unapplied Subdivision "
    "or Solidify modifier makes render-visible density differ from these "
    "counts. When only one side supplies scene stats the predicate is "
    "UNMEASURABLE with that reason cited, not approximated from the side that "
    "was supplied.",
]

# --- geometry predicates (unlocked only when scene stats are supplied) --------
#
# The refuse list in REFUSED_FAMILIES below still applies whenever no scene
# stats are attached to the pair -- see evaluate(). These handlers only run when
# evidence.scene_stats_a and evidence.scene_stats_b are both present, so a
# stock run without --scene-stats-{a,b} preserves the existing UNMEASURABLE
# behaviour byte-for-byte.

GEOMETRY_METRIC = ("poly_count",)  # negative geometry findings read the
# uncalibrated sentinel: exact scene reads have no probabilistic detection
# limit to publish. A calibration bundle may still ship a limit for this
# metric name via the standard --thresholds mechanism.


def _mesh_object(stats, name):
    return (stats.get("mesh_objects") or {}).get(name)


def _totals(stats, field):
    return int((stats.get("totals") or {}).get(field, 0))


def _cmp_finding(a_value, b_value, direction, description):
    """Return truth for {decrease, increase, unchanged} on two integer counts.

    ``direction`` is one of those three strings and reads exactly as the
    predicate suffix; ``description`` is the evidence field/label spelled by
    the caller (e.g. ``"polys"`` for a per-object comparison).
    """
    if direction == "decrease":
        truth = b_value < a_value
        wanted = f"b({b_value}) < a({a_value})"
    elif direction == "increase":
        truth = b_value > a_value
        wanted = f"b({b_value}) > a({a_value})"
    elif direction == "unchanged":
        truth = b_value == a_value
        wanted = f"b({b_value}) == a({a_value})"
    else:
        return None, None
    detail = f"{description}: {wanted}"
    return truth, detail


def _geometry_count_finding(head, argument, stats_a, stats_b, field):
    """Resolve geometry.{poly,vertex}_count.{decrease,increase,unchanged}[(NAME)].

    Whole-scene form reads ``stats.totals[field]``; parameterised form looks up
    a single mesh object by name and refuses (UNMEASURABLE) rather than
    inventing a count when the named object is missing from either side.
    """
    parts = head.split(".")
    if len(parts) != 3:
        return None
    direction = parts[2]
    if direction not in ("decrease", "increase", "unchanged"):
        return None

    if argument is None:
        a_value = _totals(stats_a, field)
        b_value = _totals(stats_b, field)
        truth, detail = _cmp_finding(a_value, b_value, direction, f"scene {field}")
        if truth is None:
            return None
        return _measured(
            truth,
            {
                f"a_{field}": a_value,
                f"b_{field}": b_value,
                "scope": "scene_totals",
            },
            GEOMETRY_METRIC,
            detail,
        )

    name = argument.strip().strip("\"'")
    a_obj = _mesh_object(stats_a, name)
    b_obj = _mesh_object(stats_b, name)
    if a_obj is None or b_obj is None:
        missing = []
        if a_obj is None:
            missing.append("a")
        if b_obj is None:
            missing.append("b")
        return _unmeasurable(
            f"mesh object {name!r} is not present in scene stats for side "
            f"{' and '.join(missing)}; supply a scene whose objects include it, "
            "or check the exact object name emitted by pil_blender_mesh"
        )
    a_value = int(a_obj[field])
    b_value = int(b_obj[field])
    truth, detail = _cmp_finding(
        a_value, b_value, direction, f"{name}.{field}"
    )
    if truth is None:
        return None
    return _measured(
        truth,
        {
            f"a_{field}": a_value,
            f"b_{field}": b_value,
            "object": name,
            "scope": "mesh_object",
        },
        GEOMETRY_METRIC,
        detail,
    )


def _geometry_topology_finding(argument, stats_a, stats_b):
    """Resolve geometry.topology_preserved[(NAME)] from scene stats.

    Whole-scene form: same object set AND matching polys/verts on every shared
    object. Object-name form: matching polys/verts on that one object.
    A missing object is UNMEASURABLE, not silently VIOLATED, because
    "the caller named a mesh that this scene does not contain" is a caller
    error rather than a topology answer.
    """
    if argument is None:
        objs_a = stats_a.get("mesh_objects") or {}
        objs_b = stats_b.get("mesh_objects") or {}
        added = sorted(set(objs_b) - set(objs_a))
        removed = sorted(set(objs_a) - set(objs_b))
        changed = []
        for name in sorted(set(objs_a) & set(objs_b)):
            a = objs_a[name]
            b = objs_b[name]
            if int(a["polys"]) != int(b["polys"]) or int(a["verts"]) != int(b["verts"]):
                changed.append(
                    {
                        "object": name,
                        "a_polys": int(a["polys"]),
                        "b_polys": int(b["polys"]),
                        "a_verts": int(a["verts"]),
                        "b_verts": int(b["verts"]),
                    }
                )
        truth = not (added or removed or changed)
        return _measured(
            truth,
            {
                "objects_added_in_b": added,
                "objects_removed_from_b": removed,
                "objects_with_changed_counts": changed,
                "scope": "scene_totals",
            },
            GEOMETRY_METRIC,
            f"added {added or '[]'}, removed {removed or '[]'}, "
            f"changed {len(changed)} object(s)",
        )

    name = argument.strip().strip("\"'")
    a_obj = _mesh_object(stats_a, name)
    b_obj = _mesh_object(stats_b, name)
    if a_obj is None or b_obj is None:
        missing = []
        if a_obj is None:
            missing.append("a")
        if b_obj is None:
            missing.append("b")
        return _unmeasurable(
            f"mesh object {name!r} is not present in scene stats for side "
            f"{' and '.join(missing)}; supply a scene whose objects include it, "
            "or check the exact object name emitted by pil_blender_mesh"
        )
    polys_match = int(a_obj["polys"]) == int(b_obj["polys"])
    verts_match = int(a_obj["verts"]) == int(b_obj["verts"])
    truth = polys_match and verts_match
    return _measured(
        truth,
        {
            "object": name,
            "a_polys": int(a_obj["polys"]),
            "b_polys": int(b_obj["polys"]),
            "a_verts": int(a_obj["verts"]),
            "b_verts": int(b_obj["verts"]),
            "scope": "mesh_object",
        },
        GEOMETRY_METRIC,
        f"{name}: polys {a_obj['polys']}->{b_obj['polys']}, "
        f"verts {a_obj['verts']}->{b_obj['verts']}",
    )


def _resolve_geometry(head, argument, stats_a, stats_b):
    """Dispatch a geometry.* predicate to its resolver, or return None.

    Returns None to signal "this exact spelling is not something we resolve
    from scene stats", which lets evaluate() fall back to the same
    GEOMETRY_REFUSAL used when no stats are supplied at all -- an unknown
    geometry predicate is still refused, not answered from an unrelated field.
    """
    if head in ("geometry.poly_count.decrease",
                "geometry.poly_count.increase",
                "geometry.poly_count.unchanged"):
        return _geometry_count_finding(head, argument, stats_a, stats_b, "polys")
    if head in ("geometry.vertex_count.decrease",
                "geometry.vertex_count.increase",
                "geometry.vertex_count.unchanged"):
        return _geometry_count_finding(head, argument, stats_a, stats_b, "verts")
    if head == "geometry.topology_preserved":
        return _geometry_topology_finding(argument, stats_a, stats_b)
    return None


# --- thresholds and detection limits -----------------------------------------


def default_thresholds_path():
    """The integrator-generated bundle, if it has been generated.

    Deliberately resolved at call time rather than import time so a test (or a
    caller) can rely on --thresholds regardless of what sits next to this file.
    """
    return Path(__file__).resolve().parent / "detection_limits.json"


def load_thresholds(path):
    """Build the threshold table; returns (table, source).

    The file maps metric name -> {"threshold": number, "detection_limits":
    {perturbation: magnitude_string}}. Both keys are optional per metric: a
    bundle may publish a detection limit for a metric whose threshold is still
    the shipped default, or vice versa. Metrics absent from the file keep their
    shipped default and an empty limit set, which is what makes the emitted
    detection_limit read "uncalibrated" rather than silently reading zero.
    """
    table = {
        metric: {"threshold": value, "detection_limits": {}}
        for metric, value in SHIPPED_THRESHOLDS.items()
    }
    if path is None:
        return table, "shipped defaults"

    path = Path(path)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read --thresholds {path}: {exc}")
    except ValueError as exc:
        raise SystemExit(f"invalid JSON in --thresholds {path}: {exc}")
    if not isinstance(loaded, dict):
        raise SystemExit(f"--thresholds {path} must contain a JSON object")

    for metric, entry in loaded.items():
        if not isinstance(entry, dict):
            raise SystemExit(
                f"--thresholds {path}: entry for {metric!r} must be an object "
                'of the form {"threshold": ..., "detection_limits": {...}}'
            )
        slot = table.setdefault(metric, {"threshold": None, "detection_limits": {}})
        if "threshold" in entry:
            slot["threshold"] = entry["threshold"]
        limits = entry.get("detection_limits") or {}
        if not isinstance(limits, dict):
            raise SystemExit(
                f"--thresholds {path}: detection_limits for {metric!r} must be "
                "an object mapping perturbation -> magnitude string"
            )
        slot["detection_limits"] = {str(k): str(v) for k, v in limits.items()}

    return table, str(path)


def threshold_for(table, metric):
    entry = table.get(metric) or {}
    value = entry.get("threshold")
    if value is None:
        value = SHIPPED_THRESHOLDS[metric]
    return value


def detection_limit_for(table, metrics):
    """The published detection limits of the metrics that decided a finding.

    Sorted and joined so the string is byte-stable across runs. With nothing
    published for any of them, returns the uncalibrated sentinel -- never a
    number, and never an empty string that a caller might read as "none".
    """
    parts = []
    for metric in metrics:
        limits = (table.get(metric) or {}).get("detection_limits") or {}
        for perturbation in sorted(limits):
            parts.append(f"{metric} / {perturbation}: {limits[perturbation]}")
    if not parts:
        return UNCALIBRATED_DETECTION_LIMIT
    return "; ".join(parts)


# --- evidence ----------------------------------------------------------------


class PairEvidence:
    """Everything the predicates may read about one image pair.

    Each underlying analysis runs at most once and is shared by every predicate
    that needs it, so a contract with ten items still costs one palette pass and
    one structure pass per image.
    """

    def __init__(self, path_a, path_b, options, scene_stats_a=None, scene_stats_b=None):
        self.path_a = path_a
        self.path_b = path_b
        self.options = options
        # Scene stats travel with the pair so the geometry predicates can read
        # them without a second I/O pass. Both default to None, which is the
        # case that keeps geometry.* on the byte-identical UNMEASURABLE path.
        self.scene_stats_a = scene_stats_a
        self.scene_stats_b = scene_stats_b

    def _palette(self, path):
        return pil_palette_diff.analyse(
            path,
            self.options["colors"],
            self.options["accent_sat"],
            self.options["accent_val"],
            self.options["foreground"],
            self.options["background_delta"],
            self.options["accent_space"],
        )

    def _structure(self, path):
        return pil_structure_diff.analyse(
            path,
            self.options["cols"],
            self.options["rows"],
            self.options["foreground"],
            self.options["background_delta"],
        )

    @cached_property
    def palette_a(self):
        return self._palette(self.path_a)

    @cached_property
    def palette_b(self):
        return self._palette(self.path_b)

    @cached_property
    def palette_diff(self):
        return pil_palette_diff.build_diff(self.palette_a, self.palette_b)

    @cached_property
    def _structure_a(self):
        return self._structure(self.path_a)

    @cached_property
    def _structure_b(self):
        return self._structure(self.path_b)

    @property
    def structure_a(self):
        return self._structure_a[0]

    @property
    def structure_b(self):
        return self._structure_b[0]

    @property
    def working_a(self):
        return self._structure_a[1]

    @property
    def working_b(self):
        return self._structure_b[1]

    @cached_property
    def structure_diff(self):
        return pil_structure_diff.build_diff(
            self.structure_a, self.structure_b, self.working_a, self.working_b
        )

    @cached_property
    def edge_mean_delta(self):
        """Change in mean edge magnitude on the working copies.

        The structure tool reports entropy_delta but no aggregate edge delta, so
        it is derived here from the same working images it measured, which keeps
        it scale-invariant and (in --foreground mode) background-free.
        """
        before = float(edge_magnitude(self.working_a).mean())
        after = float(edge_magnitude(self.working_b).mean())
        return round(after - before, 4)

    @cached_property
    def silhouettes(self):
        """(mask_a, mask_b) normalised to a common canvas, or None per side.

        Masks are derived independently of --foreground: the silhouette question
        is about the object outline whether or not the rest of the run masks the
        background out.
        """
        delta = self.options["background_delta"]
        out = []
        for path in (self.path_a, self.path_b):
            rgb, alpha = load_rgb_alpha(path)
            mask, _source, _hex = foreground_mask(rgb, alpha, delta)
            out.append(_normalised_silhouette(mask))
        return tuple(out)

    @cached_property
    def flags(self):
        """Union of every flag raised by the underlying analyses.

        Deduped and sorted rather than kept per-image: these are disqualifiers a
        reader must see, and which side raised one does not change that.
        """
        raised = set()
        for block in (
            self.palette_a,
            self.palette_b,
            self.palette_diff,
            self.structure_a,
            self.structure_b,
            self.structure_diff,
        ):
            raised.update(block.get("flags") or [])
        return sorted(raised)


def _normalised_silhouette(mask):
    """Crop a boolean mask to its bounding box and centre it on a fixed canvas.

    Returns None for an empty mask -- the caller turns that into UNMEASURABLE
    rather than into a score. Cropping first makes the comparison translation
    invariant; scaling the long edge to WORKING_LONG_EDGE makes it scale
    invariant while *preserving* aspect ratio, so a squashed or stretched
    silhouette still registers as different. Both sides are normalised the same
    way, independently, so IoU stays symmetric.
    """
    bbox = mask_bbox(mask)
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    cropped = mask[top:bottom, left:right]
    height, width = cropped.shape
    scale = WORKING_LONG_EDGE / float(max(width, height))
    target_w = max(1, min(WORKING_LONG_EDGE, int(round(width * scale))))
    target_h = max(1, min(WORKING_LONG_EDGE, int(round(height * scale))))
    resized = resize_mask(cropped, (target_w, target_h))
    canvas = np.zeros((WORKING_LONG_EDGE, WORKING_LONG_EDGE), dtype=bool)
    y0 = (WORKING_LONG_EDGE - target_h) // 2
    x0 = (WORKING_LONG_EDGE - target_w) // 2
    canvas[y0 : y0 + target_h, x0 : x0 + target_w] = resized
    return canvas


# --- findings ----------------------------------------------------------------


def _measured(truth, evidence, metrics, detail, caveats=()):
    return {
        "truth": bool(truth),
        "evidence": evidence,
        "metrics": tuple(metrics),
        "detail": detail,
        "caveats": list(caveats),
    }


def _unmeasurable(detail, caveats=()):
    """An unmeasurable finding carries NO numeric evidence.

    This is load-bearing, not tidiness: a refused predicate that ships numbers
    invites a caller to read them as the answer, which is precisely the
    approximation the refuse list exists to prevent.
    """
    return {
        "truth": None,
        "evidence": {},
        "metrics": (),
        "detail": detail,
        "caveats": list(caveats),
    }


_PHRASE = {
    (ROLE_EXPECTED, True): "expected change observed",
    (ROLE_EXPECTED, False): "expected change not observed",
    (ROLE_INVARIANT, True): "invariant holds",
    (ROLE_INVARIANT, False): "invariant broken",
}


def _item(name, role, finding, thresholds):
    truth = finding["truth"]
    if truth is None:
        return {
            "caveats": finding["caveats"],
            "detection_limit": None,
            "evidence": finding["evidence"],
            "predicate": name,
            "reason": finding["detail"],
            "role": role,
            "verdict": UNMEASURABLE,
        }

    verdict = SATISFIED if truth else VIOLATED
    # A negative finding is one that rests on "nothing was detected": an
    # invariant that held, or an expectation that did not materialise. Both are
    # bounded by what the deciding metric can resolve, so both carry the limit.
    negative = (role == ROLE_INVARIANT and truth) or (
        role == ROLE_EXPECTED and not truth
    )
    return {
        "caveats": finding["caveats"],
        "detection_limit": (
            detection_limit_for(thresholds, finding["metrics"]) if negative else None
        ),
        "evidence": finding["evidence"],
        "predicate": name,
        "reason": f"{_PHRASE[(role, truth)]} -- {finding['detail']}",
        "role": role,
        "verdict": verdict,
    }


# --- predicate implementations -----------------------------------------------


def _p_exact_unchanged(ev, th, _arg):
    diff = ev.structure_diff
    limit = threshold_for(th, "changed_area_fraction")
    distance = diff["dhash_distance"]
    changed = diff["changed_area_fraction"]
    truth = distance == 0 and changed < limit
    return _measured(
        truth,
        {
            "changed_area_fraction": changed,
            "changed_area_fraction_threshold": limit,
            "dhash_distance": distance,
        },
        ("changed_area_fraction",),
        f"dhash_distance {distance} (needs 0), changed_area_fraction {changed} "
        f"(needs < {limit})",
    )


def _p_palette_scheme_preserved(ev, _th, _arg):
    diff = ev.palette_diff
    shifted = diff["accent_hue_shift_detected"]
    lost = diff["hue_families_lost"]
    truth = (not shifted) and not lost
    return _measured(
        truth,
        {
            "accent_hue_shift_detected": shifted,
            "accent_palette_distance_de2000": diff["accent_palette_distance_de2000"],
            "hue_families_gained": diff["hue_families_gained"],
            "hue_families_lost": lost,
        },
        ("hue_family_mass_shift",),
        f"accent_hue_shift_detected {shifted}, hue_families_lost {lost or '[]'}",
    )


def _directional_mass(deltas):
    warm = round(sum(deltas[name] for name in WARM_FAMILIES), 6)
    cool = round(sum(deltas[name] for name in COOL_FAMILIES), 6)
    return warm, cool


def _p_palette_warmer(ev, th, _arg):
    return _directional_finding(ev, th, warming=True)


def _p_palette_cooler(ev, th, _arg):
    return _directional_finding(ev, th, warming=False)


def _directional_finding(ev, th, warming):
    """Directional hue-mass shift, measured on fractions of accent pixels.

    "Warmer" is not "more red": it is warm mass gained *and* cool mass not
    gained, both beyond a margin, so a render that saturates every hue at once
    does not read as a warming.
    """
    diff = ev.palette_diff
    deltas = diff["hue_family_fraction_deltas"]
    warm, cool = _directional_mass(deltas)
    margin = threshold_for(th, "hue_family_mass_shift")
    gained, opposed = (warm, cool) if warming else (cool, warm)
    truth = gained >= margin and opposed <= 0.0
    direction = "warm" if warming else "cool"
    other = "cool" if warming else "warm"
    return _measured(
        truth,
        {
            "cool_family_delta": cool,
            "hue_family_fraction_deltas": deltas,
            "hue_family_mass_shift_margin": margin,
            "warm_family_delta": warm,
        },
        ("hue_family_mass_shift",),
        f"{direction}_family_delta {gained} (needs >= {margin}), "
        f"{other}_family_delta {opposed} (needs <= 0)",
    )


def _p_palette_hue_present(ev, _th, arg):
    name = (arg or "").strip().strip("\"'")
    if name not in HUE_FAMILY_NAMES:
        return _unmeasurable(
            f"unknown hue family {name!r}; this tool's census covers "
            f"{', '.join(HUE_FAMILY_NAMES)}"
        )
    family = ev.palette_b["hue_families"][name]
    truth = (
        family["pixels"] >= HUE_PRESENCE_MIN_PIXELS
        and family["fraction_of_frame"] >= HUE_PRESENCE_MIN_FRACTION
    )
    return _measured(
        truth,
        {
            "hue_family": name,
            "min_fraction": HUE_PRESENCE_MIN_FRACTION,
            "min_pixels": HUE_PRESENCE_MIN_PIXELS,
            "pixels": family["pixels"],
            "fraction_of_frame": family["fraction_of_frame"],
        },
        # Its own metric name: the floor here is a *support* gate from
        # pil_common, not the mass-shift margin, so a calibration bundle must be
        # able to publish a detection limit for it separately.
        ("hue_presence",),
        f"{name} in image b: {family['pixels']} px "
        f"(needs >= {HUE_PRESENCE_MIN_PIXELS}), fraction_of_frame "
        f"{family['fraction_of_frame']} (needs >= {HUE_PRESENCE_MIN_FRACTION})",
    )


def _p_layout_composition_preserved(ev, th, _arg):
    similarity = ev.structure_diff["structural_similarity"]
    floor = threshold_for(th, "structural_similarity")
    if similarity is None:
        return _unmeasurable(
            "structural_similarity is null: no grid cell pair carried enough "
            "foreground support to be scored, so there is nothing to compare"
        )
    return _measured(
        similarity >= floor,
        {
            "structural_similarity": similarity,
            "structural_similarity_threshold": floor,
        },
        ("structural_similarity",),
        f"structural_similarity {similarity} (needs >= {floor})",
    )


def _p_layout_region_changed(ev, _th, arg):
    try:
        target = _parse_region(arg)
    except ValueError as exc:
        return _unmeasurable(f"malformed region argument {arg!r}: {exc}")
    bbox = ev.structure_diff["changed_region_bbox_fractional"]
    if bbox is None:
        return _measured(
            False,
            {
                "changed_region_bbox_fractional": None,
                "region": target,
            },
            ("changed_area_fraction",),
            "no changed region was detected anywhere in the frame",
        )
    return _measured(
        _boxes_intersect(bbox, target),
        {
            "changed_region_bbox_fractional": bbox,
            "region": target,
        },
        ("changed_area_fraction",),
        f"changed_region_bbox_fractional {bbox} vs requested region {target}",
    )


def _p_detail_increased(ev, th, _arg):
    return _detail_finding(ev, th, increasing=True)


def _p_detail_decreased(ev, th, _arg):
    return _detail_finding(ev, th, increasing=False)


def _detail_finding(ev, th, increasing):
    entropy_delta = ev.structure_diff["entropy_delta"]
    edge_delta = ev.edge_mean_delta
    entropy_margin = threshold_for(th, "entropy_delta")
    edge_margin = threshold_for(th, "edge_mean_delta")
    if increasing:
        truth = entropy_delta >= entropy_margin and edge_delta >= edge_margin
        wanted = f">= +{entropy_margin} / >= +{edge_margin}"
    else:
        truth = entropy_delta <= -entropy_margin and edge_delta <= -edge_margin
        wanted = f"<= -{entropy_margin} / <= -{edge_margin}"
    return _measured(
        truth,
        {
            "edge_mean_delta": edge_delta,
            "edge_mean_delta_margin": edge_margin,
            "entropy_delta": entropy_delta,
            "entropy_delta_margin": entropy_margin,
        },
        ("entropy_delta", "edge_mean_delta"),
        f"entropy_delta {entropy_delta} / edge_mean_delta {edge_delta} "
        f"(both need {wanted})",
        caveats=(DETAIL_CAVEAT,),
    )


def _p_identity_silhouette_preserved(ev, th, _arg):
    if "foreground_mask_mismatch" in ev.flags:
        return _unmeasurable(
            "one side's foreground mask fell back to full-frame, so the two "
            "silhouettes are not commensurable"
        )
    mask_a, mask_b = ev.silhouettes
    if mask_a is None or mask_b is None:
        missing = "a" if mask_a is None else "b"
        if mask_a is None and mask_b is None:
            missing = "a and b"
        return _unmeasurable(
            f"foreground mask is empty on image {missing}: no silhouette to "
            "compare. Supply a render with real alpha, or a frame whose subject "
            "separates from the background."
        )
    intersection = int(np.logical_and(mask_a, mask_b).sum())
    union = int(np.logical_or(mask_a, mask_b).sum())
    if union == 0:
        return _unmeasurable(
            "both normalised silhouettes are empty: no silhouette to compare"
        )
    iou = round(intersection / union, 6)
    floor = threshold_for(th, "silhouette_iou")
    return _measured(
        iou >= floor,
        {
            "silhouette_iou": iou,
            "silhouette_iou_threshold": floor,
            "silhouette_working_edge": WORKING_LONG_EDGE,
        },
        ("silhouette_iou",),
        f"silhouette_iou {iou} (needs >= {floor}), measured on bbox-cropped "
        f"foreground masks normalised to a {WORKING_LONG_EDGE}px canvas",
    )


def _parse_region(arg):
    if arg is None:
        raise ValueError("expected [left, top, right, bottom] as fractions")
    try:
        box = json.loads(arg)
    except ValueError:
        raise ValueError("expected a JSON list [left, top, right, bottom]")
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError("expected exactly four fractional coordinates")
    try:
        box = [float(v) for v in box]
    except (TypeError, ValueError):
        raise ValueError("coordinates must be numbers")
    if not (box[0] < box[2] and box[1] < box[3]):
        raise ValueError("left must be < right and top must be < bottom")
    return box


def _boxes_intersect(one, other):
    """Half-open overlap test: boxes that merely touch do not intersect."""
    return not (
        one[2] <= other[0]
        or other[2] <= one[0]
        or one[3] <= other[1]
        or other[3] <= one[1]
    )


# --- registry ----------------------------------------------------------------

# name -> (handler, takes_argument). This table IS the vocabulary: anything
# absent from it, and not caught by the refuse rules below, is UNMEASURABLE.
MEASURABLE = {
    "detail.decreased": (_p_detail_decreased, False),
    "detail.increased": (_p_detail_increased, False),
    "exact.unchanged": (_p_exact_unchanged, False),
    "identity.silhouette_preserved": (_p_identity_silhouette_preserved, False),
    "layout.composition_preserved": (_p_layout_composition_preserved, False),
    "layout.region_changed": (_p_layout_region_changed, True),
    "palette.cooler": (_p_palette_cooler, False),
    "palette.hue_present": (_p_palette_hue_present, True),
    "palette.scheme_preserved": (_p_palette_scheme_preserved, False),
    "palette.warmer": (_p_palette_warmer, False),
}

# The refuse list. Predicates that *sound* measurable but are not are exactly
# where an eager tool does damage, so these are refused by family, before any
# unknown-predicate handling, and can never be reached by a partial match.
REFUSED_EXACT = {
    "identity.same_character": STYLE_REFUSAL,
}
REFUSED_FAMILIES = (
    ("geometry", GEOMETRY_REFUSAL),
    ("style", STYLE_REFUSAL),
)

_CALL_RE = re.compile(r"^(?P<head>[A-Za-z0-9_.]+)\((?P<arg>.*)\)$", re.S)


def _split_call(name):
    match = _CALL_RE.match(name.strip())
    if match is None:
        return name.strip(), None
    return match.group("head"), match.group("arg")


def _refusal_for(head):
    if head in REFUSED_EXACT:
        return REFUSED_EXACT[head]
    for family, reason in REFUSED_FAMILIES:
        if head == family or head.startswith(family + "."):
            return reason
    return None


def evaluate(name, role, evidence, thresholds):
    head, argument = _split_call(name)

    # Geometry predicates: unlockable to SATISFIED/VIOLATED when the caller
    # supplied scene stats for both sides. Attempted BEFORE the refuse-list
    # check so scene stats can override the default refusal, but only for
    # geometry.* -- the style/identity refusals are unchanged. When exactly one
    # side supplies stats, the predicate is UNMEASURABLE with that reason
    # cited; when neither side does, fall through to the refuse-list handling
    # below unchanged, preserving the load-bearing default refusal exactly.
    if head == "geometry" or head.startswith("geometry."):
        stats_a = getattr(evidence, "scene_stats_a", None)
        stats_b = getattr(evidence, "scene_stats_b", None)
        if stats_a is not None or stats_b is not None:
            if stats_a is None or stats_b is None:
                missing = "a" if stats_a is None else "b"
                return _item(
                    name,
                    role,
                    _unmeasurable(
                        f"geometry predicates need scene stats for both sides; "
                        f"only supplied for {'b' if missing == 'a' else 'a'} "
                        f"(missing side {missing}). Rerun with "
                        f"--scene-stats-{missing} pointing at pil_blender_mesh "
                        "output for that scene."
                    ),
                    thresholds,
                )
            finding = _resolve_geometry(head, argument, stats_a, stats_b)
            if finding is not None:
                return _item(name, role, finding, thresholds)
            # An unknown geometry.* spelling with stats supplied still refuses,
            # rather than answering from the wrong field.
            return _item(name, role, _unmeasurable(GEOMETRY_REFUSAL), thresholds)

    # The refuse list is consulted first so no future registry entry can shadow
    # it, and so a parameterised spelling of a refused predicate is refused too.
    reason = _refusal_for(head)
    if reason is not None:
        return _item(name, role, _unmeasurable(reason), thresholds)

    entry = MEASURABLE.get(head)
    if entry is None:
        return _item(
            name,
            role,
            _unmeasurable(
                f"unknown predicate: {name!r} is not in this tool's registry "
                f"({', '.join(sorted(MEASURABLE))})"
            ),
            thresholds,
        )

    handler, takes_argument = entry
    if takes_argument and argument is None:
        return _item(
            name,
            role,
            _unmeasurable(f"predicate {head!r} requires an argument, e.g. {head}(...)"),
            thresholds,
        )
    if not takes_argument and argument is not None:
        return _item(
            name,
            role,
            _unmeasurable(f"predicate {head!r} takes no argument"),
            thresholds,
        )
    return _item(name, role, handler(evidence, thresholds, argument), thresholds)


# --- contract and pair inputs -------------------------------------------------


def _dedup(names):
    """Preserve declaration order, drop repeats -- a contract listing the same
    predicate twice must not produce two identical items."""
    seen = set()
    out = []
    for name in names:
        if not isinstance(name, str):
            raise SystemExit(f"contract entries must be strings, got {name!r}")
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def load_contract(path, extra_expect, extra_invariant):
    """Merge the contract file with the --expect / --invariant conveniences.

    File entries lead, CLI entries extend. Both orders are preserved so the
    emitted items line up with what the caller wrote.
    """
    expect, invariant = [], []
    if path is not None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            raise SystemExit(f"cannot read --contract {path}: {exc}")
        except ValueError as exc:
            raise SystemExit(f"invalid JSON in --contract {path}: {exc}")
        if not isinstance(data, dict):
            raise SystemExit(f"--contract {path} must contain a JSON object")
        expect = list(data.get("expect_change") or [])
        invariant = list(data.get("invariant") or [])

    expect.extend(extra_expect or [])
    invariant.extend(extra_invariant or [])
    return _dedup(expect), _dedup(invariant)


def load_scene_stats(path):
    """Read a pil_blender_mesh payload and return its scene dict.

    A scene-stats file must be JSON with a ``scene`` object carrying
    ``mesh_objects`` and ``totals``. Anything else is a caller error and turned
    into a SystemExit with a message, exactly like the --contract loader --
    stdin remains byte-empty and no partial state is written.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read scene stats {path}: {exc}")
    except ValueError as exc:
        raise SystemExit(f"invalid JSON in scene stats {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"scene stats {path} must contain a JSON object")
    scene = data.get("scene")
    if not isinstance(scene, dict):
        raise SystemExit(
            f"scene stats {path} is missing a 'scene' object; expected the "
            "payload emitted by scripts/pil_blender_mesh.py"
        )
    if not isinstance(scene.get("mesh_objects"), dict) or not isinstance(
        scene.get("totals"), dict
    ):
        raise SystemExit(
            f"scene stats {path} 'scene' object must carry 'mesh_objects' and "
            "'totals'; upgrade pil_blender_mesh or regenerate the file"
        )
    return scene


def load_pairs(path):
    """Read a pair manifest: [{"a": ..., "b": ...}, ...] or {"pairs": [...]}.

    Relative paths resolve against the manifest's own directory, so a manifest
    can travel with the images it names.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read --pairs {path}: {exc}")
    except ValueError as exc:
        raise SystemExit(f"invalid JSON in --pairs {path}: {exc}")

    entries = data.get("pairs") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        raise SystemExit(
            f"--pairs {path} must contain a non-empty list of "
            '{"a": ..., "b": ...} objects'
        )

    base = path.parent
    pairs = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or "a" not in entry or "b" not in entry:
            raise SystemExit(
                f"--pairs {path}: entry {index} must be an object with 'a' and 'b'"
            )
        pairs.append((_resolve(entry["a"], base), _resolve(entry["b"], base)))
    return pairs


def _resolve(value, base):
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    return base / candidate


# --- aggregation --------------------------------------------------------------


def aggregate(pair_blocks):
    """Worst-case per contract item, with the per-pair detail retained.

    Emphatically not a mean: a single diverging pair is a real failure, and
    averaging it against healthy pairs is the exact dilution WP4 exists to
    prevent. Ordering follows the contract, not the verdicts, so two runs over
    the same contract emit the same rows in the same order.
    """
    order = []
    seen = {}
    for block in pair_blocks:
        for item in block["items"]:
            key = (item["predicate"], item["role"])
            if key not in seen:
                seen[key] = []
                order.append(key)
            seen[key].append(
                {
                    "a": block["a"],
                    "b": block["b"],
                    "index": block["index"],
                    "verdict": item["verdict"],
                }
            )

    rows = []
    for predicate, role in order:
        per_pair = seen[(predicate, role)]
        worst = max(per_pair, key=lambda p: VERDICT_SEVERITY[p["verdict"]])["verdict"]
        rows.append(
            {
                "pair_verdicts": per_pair,
                "pairs_violated": sum(1 for p in per_pair if p["verdict"] == VIOLATED),
                "pairs_unmeasurable": sum(
                    1 for p in per_pair if p["verdict"] == UNMEASURABLE
                ),
                "predicate": predicate,
                "role": role,
                "verdict": worst,
            }
        )
    return rows


# --- CLI ----------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evaluate a change contract against one or many image pairs."
    )
    parser.add_argument("image_a", nargs="?", help="reference image path")
    parser.add_argument("image_b", nargs="?", help="comparison image path")
    parser.add_argument(
        "--contract",
        help='JSON contract: {"expect_change": [...], "invariant": [...]}',
    )
    parser.add_argument(
        "--expect",
        action="append",
        metavar="PREDICATE",
        help="declare an expected change; repeatable, extends --contract",
    )
    parser.add_argument(
        "--invariant",
        action="append",
        metavar="PREDICATE",
        help="declare an invariant; repeatable, extends --contract",
    )
    parser.add_argument(
        "--pairs",
        help='JSON manifest of image pairs: [{"a": ..., "b": ...}, ...]. '
        "Evaluates the contract per pair and aggregates worst-case.",
    )
    parser.add_argument(
        "--scene-stats-a",
        help="path to pil_blender_mesh JSON for image_a's source scene. When "
        "both --scene-stats-a and --scene-stats-b are supplied, geometry.* "
        "predicates resolve to SATISFIED/VIOLATED against the scene counts "
        "instead of the default UNMEASURABLE refusal. Single-pair mode only.",
    )
    parser.add_argument(
        "--scene-stats-b",
        help="path to pil_blender_mesh JSON for image_b's source scene. See "
        "--scene-stats-a.",
    )
    parser.add_argument(
        "--thresholds",
        help="JSON bundle of calibrated thresholds and detection limits, "
        "mapping metric -> {threshold, detection_limits}. Defaults to "
        "scripts/detection_limits.json when that file exists; without it, "
        "shipped defaults apply and every negative finding says its detection "
        "limit is uncalibrated.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="passed through to the palette and structure tools: mask the "
        "background out and measure the foreground only -- required for "
        "meaningful numbers on object renders over a shared preview background",
    )
    parser.add_argument(
        "--background-delta",
        type=float,
        default=DEFAULT_BACKGROUND_DELTA,
        help="OKLab distance from the border-median colour within which an "
        f"opaque pixel counts as background (default {DEFAULT_BACKGROUND_DELTA})",
    )
    parser.add_argument(
        "--grid", default="4x3", help="structural analysis grid as COLSxROWS"
    )
    parser.add_argument(
        "--colors", type=int, default=8, help="palette size per image (default 8)"
    )
    parser.add_argument(
        "--accent-space",
        choices=ACCENT_SPACES,
        default=DEFAULT_ACCENT_SPACE,
        help="space in which 'vivid accent' is defined (default "
        f"{DEFAULT_ACCENT_SPACE})",
    )
    args = parser.parse_args(argv)

    if args.colors < 2:
        parser.error("--colors must be >= 2")
    cols, rows = parse_grid(args.grid)

    if args.pairs:
        if args.image_a or args.image_b:
            parser.error(
                "--pairs replaces the positional image pair; pass one or the other"
            )
        if args.scene_stats_a or args.scene_stats_b:
            parser.error(
                "--scene-stats-{a,b} is single-pair only; multi-pair scene "
                "stats are not supported by this tool"
            )
        pairs = load_pairs(args.pairs)
    else:
        if not args.image_a or not args.image_b:
            parser.error(
                "a contract needs two images to compare: pass image_a and "
                "image_b, or a --pairs manifest"
            )
        pairs = [(Path(args.image_a), Path(args.image_b))]

    expect, invariant = load_contract(args.contract, args.expect, args.invariant)
    if not expect and not invariant:
        parser.error(
            "empty contract: declare at least one predicate via --contract, "
            "--expect or --invariant"
        )

    thresholds_path = args.thresholds
    if thresholds_path is None:
        default_path = default_thresholds_path()
        if default_path.is_file():
            thresholds_path = default_path
    thresholds, thresholds_source = load_thresholds(thresholds_path)

    options = {
        "accent_sat": DEFAULT_ACCENT_SAT_MIN,
        "accent_space": args.accent_space,
        "accent_val": DEFAULT_ACCENT_VAL_MIN,
        "background_delta": args.background_delta,
        "colors": args.colors,
        "cols": cols,
        "foreground": args.foreground,
        "rows": rows,
    }

    scene_stats_a = load_scene_stats(args.scene_stats_a) if args.scene_stats_a else None
    scene_stats_b = load_scene_stats(args.scene_stats_b) if args.scene_stats_b else None

    pair_blocks = []
    for index, (path_a, path_b) in enumerate(pairs):
        evidence = PairEvidence(
            path_a,
            path_b,
            options,
            scene_stats_a=scene_stats_a,
            scene_stats_b=scene_stats_b,
        )
        items = [evaluate(n, ROLE_EXPECTED, evidence, thresholds) for n in expect]
        items += [evaluate(n, ROLE_INVARIANT, evidence, thresholds) for n in invariant]
        pair_blocks.append(
            {
                "a": str(path_a),
                "b": str(path_b),
                "flags": evidence.flags,
                "index": index,
                "items": items,
            }
        )

    payload = {
        "tool": "pil_contract_verdict",
        "version": TOOL_VERSION,
        "parameters": {
            "accent_space": args.accent_space,
            "accent_thresholds": {
                "chroma_min": DEFAULT_ACCENT_CHROMA_MIN,
                "lightness_min": DEFAULT_ACCENT_LIGHTNESS_MIN,
                "saturation_min": DEFAULT_ACCENT_SAT_MIN,
                "value_min": DEFAULT_ACCENT_VAL_MIN,
            },
            "background_delta": args.background_delta,
            "colors": args.colors,
            "foreground": args.foreground,
            "grid": {"cols": cols, "rows": rows},
            "hue_families": {"cool": list(COOL_FAMILIES), "warm": list(WARM_FAMILIES)},
            "hue_presence_min": {
                "fraction": HUE_PRESENCE_MIN_FRACTION,
                "pixels": HUE_PRESENCE_MIN_PIXELS,
            },
            # The whole registry, echoed so a calling agent can discover the
            # vocabulary -- including what will always be refused -- without
            # reading this file.
            "predicates": {
                "measurable": sorted(MEASURABLE),
                "parameterised": [
                    "layout.region_changed([left,top,right,bottom])",
                    "palette.hue_present(NAME)",
                ],
                "refused": {
                    "geometry.*": GEOMETRY_REFUSAL,
                    "identity.same_character": STYLE_REFUSAL,
                    "style.*": STYLE_REFUSAL,
                },
            },
            # Scene-stats sources, echoed so a payload self-describes why
            # geometry.* resolved to a real verdict (or did not). Both null
            # keeps the default UNMEASURABLE geometry path visible in the
            # parameters block, not just as a per-item reason.
            "scene_stats": {
                "a": args.scene_stats_a,
                "b": args.scene_stats_b,
            },
            # Both the active numbers and where they came from: a verdict whose
            # threshold cannot be traced is not interpretable.
            "thresholds": thresholds,
            "thresholds_source": thresholds_source,
        },
        "contract": {"expect_change": expect, "invariant": invariant},
        "pairs": pair_blocks,
        "aggregate": aggregate(pair_blocks) if args.pairs else None,
        "interpretation_limits": INTERPRETATION_LIMITS,
    }

    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
