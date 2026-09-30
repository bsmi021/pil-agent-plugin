#!/usr/bin/env python
"""Numeric helpers for `blender_depth_order.py`, imported by its Blender probe.

The probe runs inside Blender's bundled Python. Everything that turns depth
arrays and projected vertices into ordering facts lives here, so the same code
is unit-tested host-side on any Python that has numpy (the summary formatter
needs nothing but the standard library):

- the camera model and vertex projection (the fallback when an object leaves no
  pixels of its own, and the source of every object's depth range),
- per-pixel pair maths over two objects' isolated depth maps,
- the bounding-box pair estimate used by the fallback,
- the plain-language `summary` line for an overlapping pair.

Depth here is always planar: the distance along the camera's forward axis, in
scene units, the same quantity as the EEVEE Depth pass.

This is a shared module, not a tool: it declares no `TOOL_VERSION`. numpy is
imported lazily, so importing this module never needs it.
"""

from __future__ import annotations

import math
from decimal import Decimal

import blender_render_kernels as K

DEPTH_KIND = "planar distance along the camera forward axis, in scene units"


def _numpy():
    import numpy

    return numpy


def r6(value):
    return round(float(value), 6)


# --- camera model and projection -------------------------------------------------


def camera_model(location, toward, right, up, projection, width, height, lens_mm=None, ortho_scale=None):
    """A camera as plain numbers: pose, projection and image size.

    ``toward`` points from the subject to the camera (the camera looks along its
    negation). A perspective camera needs ``lens_mm`` (sensor fit AUTO, 36 mm);
    an orthographic one needs ``ortho_scale``, which Blender applies to the
    longer image side.
    """
    if projection == "orthographic":
        if not ortho_scale or ortho_scale <= 0:
            raise ValueError("an orthographic camera needs a positive ortho_scale")
        if width >= height:
            half_x, half_y = ortho_scale / 2.0, ortho_scale / 2.0 * height / width
        else:
            half_x, half_y = ortho_scale / 2.0 * width / height, ortho_scale / 2.0
        return {
            "location": tuple(location), "toward": tuple(toward), "right": tuple(right), "up": tuple(up),
            "projection": projection, "half_x": half_x, "half_y": half_y,
        }
    if projection != "perspective":
        raise ValueError(f"unknown projection {projection!r}")
    if not lens_mm or lens_mm <= 0:
        raise ValueError("a perspective camera needs a positive lens_mm")
    tan_x, tan_y = K.half_fov_tangents(lens_mm, width, height)
    return {
        "location": tuple(location), "toward": tuple(toward), "right": tuple(right), "up": tuple(up),
        "projection": projection, "half_x": tan_x, "half_y": tan_y,
    }


def project_points(points, camera):
    """``(depth, u, v)`` arrays for world-space ``points``.

    ``depth`` is planar. ``u`` runs left to right and ``v`` top to bottom, both
    as fractions of the image (0..1 inside the frame). A perspective point at or
    behind the camera plane has no projection: its ``u`` and ``v`` are NaN.
    """
    np = _numpy()
    rel = np.asarray(points, dtype=np.float64) - np.asarray(camera["location"], dtype=np.float64)
    depth = rel @ (-np.asarray(camera["toward"], dtype=np.float64))
    x = rel @ np.asarray(camera["right"], dtype=np.float64)
    y = rel @ np.asarray(camera["up"], dtype=np.float64)
    if camera["projection"] == "orthographic":
        ndc_x, ndc_y = x / camera["half_x"], y / camera["half_y"]
    else:
        ahead = depth > 1e-12
        safe = np.where(ahead, depth, 1.0)
        ndc_x = np.where(ahead, x / (safe * camera["half_x"]), np.nan)
        ndc_y = np.where(ahead, y / (safe * camera["half_y"]), np.nan)
    return depth, (ndc_x + 1.0) / 2.0, (1.0 - ndc_y) / 2.0


def depth_extent(depth):
    """``{near, far, unit, kind}`` over a vertex depth array, or None if empty."""
    np = _numpy()
    depth = np.asarray(depth)
    if depth.size == 0:
        return None
    return {"near": r6(depth.min()), "far": r6(depth.max()), "unit": "scene units", "kind": DEPTH_KIND}


# --- screen-space bounding boxes ---------------------------------------------------


def bbox_from_uv(u, v):
    """Fractional bbox of projected points, or None when any point has no projection."""
    np = _numpy()
    u, v = np.asarray(u), np.asarray(v)
    if u.size == 0 or not (np.isfinite(u).all() and np.isfinite(v).all()):
        return None
    return {"x_min": r6(u.min()), "y_min": r6(v.min()), "x_max": r6(u.max()), "y_max": r6(v.max())}


def bbox_from_mask(mask):
    """Fractional bbox of the True pixels of an ``(H, W)`` mask, or None if empty.

    The box spans whole pixels, so a one-pixel object is ``1 / W`` wide.
    """
    np = _numpy()
    height, width = mask.shape
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0:
        return None
    return {
        "x_min": r6(cols[0] / width), "y_min": r6(rows[0] / height),
        "x_max": r6((cols[-1] + 1) / width), "y_max": r6((rows[-1] + 1) / height),
    }


def bbox_in_frame(bbox):
    """True when a fractional bbox lies wholly inside the image."""
    return bool(bbox) and bbox["x_min"] >= 0.0 and bbox["y_min"] >= 0.0 and bbox["x_max"] <= 1.0 and bbox["y_max"] <= 1.0


def bbox_area(bbox):
    return max(bbox["x_max"] - bbox["x_min"], 0.0) * max(bbox["y_max"] - bbox["y_min"], 0.0)


def bbox_overlap_area(a, b):
    width = min(a["x_max"], b["x_max"]) - max(a["x_min"], b["x_min"])
    height = min(a["y_max"], b["y_max"]) - max(a["y_min"], b["y_min"])
    return max(width, 0.0) * max(height, 0.0)


# --- pair maths ------------------------------------------------------------------------


def range_gap(front_range, back_range):
    """Signed geometric gap: the back object's near depth minus the front's far.

    Positive means the two depth ranges are separated along the view axis;
    negative means they interleave.
    """
    return back_range["near"] - front_range["far"]


def pixel_pair(depth_a, depth_b, tolerance):
    """Per-pixel ordering of two objects from their isolated ``(H, W)`` depth maps.

    Each map holds the object's own nearest-surface depth where it covers a
    pixel and NaN elsewhere, so occlusion by other objects does not matter. Over
    the pixels both cover, the front object is the one whose surface is nearer at
    the median pixel; ``depth_gap`` is that median separation. Returns None when
    the two share no pixel, else a dict with ``a_front`` (bool: on a tie, or when
    the median gap is zero, A counts as the front), ``overlap_pixels``,
    ``depth_gap`` (>= 0), ``depth_gap_min`` and ``depth_gap_max`` (over the
    overlap, positive when the front object is nearer, negative where the two
    interleave), ``front_fraction`` (share of overlap pixels where the front
    object is nearer by more than ``tolerance``) and ``tie``.
    """
    np = _numpy()
    both = np.isfinite(depth_a) & np.isfinite(depth_b)
    count = int(both.sum())
    if count == 0:
        return None
    diff = depth_b[both].astype(np.float64) - depth_a[both].astype(np.float64)  # > 0 where A is nearer
    median = float(np.median(diff))
    a_front = median >= 0.0
    signed = diff if a_front else -diff
    return {
        "a_front": a_front,
        "overlap_pixels": count,
        "depth_gap": abs(median),
        "depth_gap_min": float(signed.min()),
        "depth_gap_max": float(signed.max()),
        "front_fraction": float((signed > tolerance).sum()) / count,
        "tie": abs(median) <= tolerance,
    }


def pair_record(name_a, obj_a, depth_a, name_b, obj_b, depth_b, tolerance):
    """The payload record for one pair of objects, or None if they do not overlap.

    ``obj_*`` are object records with ``depth_range``, ``screen_bbox`` and
    ``screen_pixels``. When both objects have a depth map the pair is measured
    per pixel (``method: "passes"``); otherwise it is estimated from the two
    projected-vertex bounding boxes and near depths (``method: "vertices"``).
    """
    if depth_a is not None and depth_b is not None:
        found = pixel_pair(depth_a, depth_b, tolerance)
        if found is None:
            return None
        a_front = found["a_front"]
        overlap = found["overlap_pixels"]
        front_pixels = (obj_a if a_front else obj_b)["screen_pixels"]
        back_pixels = (obj_b if a_front else obj_a)["screen_pixels"]
        measured = {
            "method": "passes",
            "depth_gap": r6(found["depth_gap"]),
            "depth_gap_min": r6(found["depth_gap_min"]),
            "depth_gap_max": r6(found["depth_gap_max"]),
            "front_fraction": r6(found["front_fraction"]),
            "tie": found["tie"],
            "overlap_pixels": overlap,
            "overlap_fraction_of_front": r6(overlap / front_pixels),
            "overlap_fraction_of_back": r6(overlap / back_pixels),
        }
    else:
        box_a, box_b = obj_a["screen_bbox"], obj_b["screen_bbox"]
        if not box_a or not box_b:
            return None
        area = bbox_overlap_area(box_a, box_b)
        if area <= 0.0 or bbox_area(box_a) <= 0.0 or bbox_area(box_b) <= 0.0:
            return None
        near_a, near_b = obj_a["depth_range"]["near"], obj_b["depth_range"]["near"]
        a_front = near_a <= near_b
        gap = abs(near_b - near_a)
        front_box, back_box = (box_a, box_b) if a_front else (box_b, box_a)
        measured = {
            "method": "vertices",
            "depth_gap": r6(gap),
            "depth_gap_min": None,
            "depth_gap_max": None,
            "front_fraction": None,
            "tie": gap <= tolerance,
            "overlap_pixels": None,
            "overlap_fraction_of_front": r6(area / bbox_area(front_box)),
            "overlap_fraction_of_back": r6(area / bbox_area(back_box)),
        }
    front_name, back_name = (name_a, name_b) if a_front else (name_b, name_a)
    front_obj, back_obj = (obj_a, obj_b) if a_front else (obj_b, obj_a)
    return {
        "front": front_name,
        "back": back_name,
        **measured,
        "range_gap": r6(range_gap(front_obj["depth_range"], back_obj["depth_range"])),
    }


# --- summary text ----------------------------------------------------------------------


def _plain_number(value, figures=3):
    """``value`` to ``figures`` significant figures, never in exponent form."""
    return format(Decimal(f"{value:.{figures}g}"), "f")


def _percent(fraction):
    if fraction <= 0.0:
        return "0%"
    return "under 1%" if fraction < 0.005 else f"{round(fraction * 100)}%"


def summary_line(view, pair):
    """One plain-language sentence for an overlapping pair in ``view``.

    "Belt is 0.021 in front of Tunic in view front (overlap 34% of Belt)". A tie
    reads "is at the same depth as"; a pair that interleaves says how much of the
    overlap the front object actually wins; a fallback pair says it is estimated.
    """
    front, back = pair["front"], pair["back"]
    share = _percent(pair["overlap_fraction_of_front"])
    if pair["tie"]:
        text = f"{front} is at the same depth as {back} in view {view} (overlap {share} of {front})"
    else:
        text = f"{front} is {_plain_number(pair['depth_gap'])} in front of {back} in view {view} (overlap {share} of {front})"
        fraction = pair.get("front_fraction")
        if fraction is not None and fraction < 0.95:
            text += f"; {front} is nearer on only {_percent(fraction)} of the overlap"
    if pair["method"] == "vertices":
        text += ", estimated from projected vertices"
    return text
