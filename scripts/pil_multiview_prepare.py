#!/usr/bin/env python
"""Extract deterministic, ordered foreground contours from a multi-view image set.

OpenCV supplies contour tracing and polygon simplification.  Foreground identity
still comes from ``pil_common`` so this tool agrees with every existing PIL Agent
measurement about alpha and border-estimated background pixels.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_common import (
    DEFAULT_BACKGROUND_DELTA,
    foreground_mask,
    load_rgb_alpha,
    mask_bbox,
)

TOOL_VERSION = "0.6.0"


class PrepareError(ValueError):
    pass


def _opencv():
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise PrepareError(
            "OpenCV is required; install the plugin's reconstruction extra"
        ) from exc
    return cv2


def _resolve(path: str, manifest_path: Path | None) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() and manifest_path is not None:
        candidate = manifest_path.resolve().parent / candidate
    return candidate.resolve()


def _canonical_contour(points: np.ndarray) -> np.ndarray:
    """Return one stable winding and start vertex for an Nx2 contour."""
    if len(points) < 3:
        return points
    x = points[:, 0]
    y = points[:, 1]
    signed_twice_area = float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    if signed_twice_area < 0:
        points = points[::-1]
    start = min(range(len(points)), key=lambda index: (int(points[index, 1]), int(points[index, 0])))
    return np.roll(points, -start, axis=0)


def _prepare_view(view: dict, manifest_path: Path | None, background_delta: float, epsilon: float) -> dict:
    if not isinstance(view, dict):
        raise PrepareError("each view must be an object")
    name = view.get("name")
    image_value = view.get("image")
    if not isinstance(name, str) or not name.strip():
        raise PrepareError("each view requires a non-empty name")
    if not isinstance(image_value, str) or not image_value:
        raise PrepareError(f"view {name!r} requires an image path")
    image_path = _resolve(image_value, manifest_path)
    if not image_path.is_file():
        raise PrepareError(f"view {name!r} image not found: {image_path}")

    try:
        rgb, alpha = load_rgb_alpha(image_path)
    except (OSError, ValueError) as exc:
        raise PrepareError(f"cannot read view {name!r} image {image_path}: {exc}") from exc
    mask, source, background_hex = foreground_mask(rgb, alpha, background_delta)
    bbox = mask_bbox(mask)
    pixel_count = int(mask.sum())
    if bbox is None or pixel_count == 0:
        return {
            "name": name,
            "image": str(image_path),
            "status": "UNMEASURABLE",
            "reason": "foreground_mask_empty",
            "size": list(rgb.size),
            "foreground": {"source": source, "background_hex": background_hex, "pixel_count": 0},
            "bbox_normalized": None,
            "contour_normalized": None,
            "landmarks": view.get("landmarks", {}),
        }

    cv2 = _opencv()
    binary = mask.astype(np.uint8) * 255
    contours, _hierarchy = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise PrepareError(f"view {name!r} has foreground pixels but OpenCV found no contour")
    contour = max(contours, key=cv2.contourArea)
    perimeter = float(cv2.arcLength(contour, True))
    tolerance = max(0.0, float(epsilon)) * perimeter
    simplified = cv2.approxPolyDP(contour, tolerance, True).reshape(-1, 2)
    if len(simplified) < 3:
        simplified = contour.reshape(-1, 2)
    simplified = _canonical_contour(simplified.astype(np.int64))

    width, height = rgb.size
    denom_x = max(1, width - 1)
    denom_y = max(1, height - 1)
    normalized = [
        [round(float(x) / denom_x, 8), round(float(y) / denom_y, 8)]
        for x, y in simplified
    ]
    left, top, right, bottom = bbox
    bbox_normalized = [
        round(left / width, 8),
        round(top / height, 8),
        round(right / width, 8),
        round(bottom / height, 8),
    ]
    return {
        "name": name,
        "image": str(image_path),
        "status": "MEASURED",
        "size": [width, height],
        "foreground": {
            "source": source,
            "background_hex": background_hex,
            "pixel_count": pixel_count,
            "fraction_of_frame": round(pixel_count / float(width * height), 8),
        },
        "bbox_normalized": bbox_normalized,
        "contour_normalized": normalized,
        "contour_area_pixels": round(float(cv2.contourArea(contour)), 4),
        "landmarks": view.get("landmarks", {}),
    }


def prepare_manifest(manifest: dict, manifest_path: Path | None = None) -> dict:
    if not isinstance(manifest, dict) or manifest.get("schema") != "multiview-spec-v1":
        raise PrepareError("manifest schema must be 'multiview-spec-v1'")
    views = manifest.get("views")
    if not isinstance(views, list) or not views:
        raise PrepareError("manifest requires a non-empty views array")
    names = [view.get("name") for view in views if isinstance(view, dict)]
    if len(names) != len(set(names)):
        raise PrepareError("view names must be unique")
    settings = manifest.get("foreground", {})
    background_delta = float(settings.get("background_delta", DEFAULT_BACKGROUND_DELTA))
    epsilon = float(manifest.get("contour_epsilon_fraction", 0.0025))
    prepared = [
        _prepare_view(view, manifest_path, background_delta, epsilon)
        for view in views
    ]
    refused = sum(view["status"] != "MEASURED" for view in prepared)
    return {
        "tool": "pil_multiview_prepare",
        "version": TOOL_VERSION,
        "schema": "prepared-multiview-v1",
        "status": "PREPARED" if refused == 0 else "PREPARED_WITH_REFUSALS",
        "parameters": {
            "background_delta": background_delta,
            "contour_epsilon_fraction": epsilon,
            "view_count": len(prepared),
            "refused_view_count": refused,
        },
        "views": prepared,
        "interpretation_limits": [
            "Contours are 2D rendered-appearance evidence, not recovered mesh topology.",
            "Generated concept views are not assumed to carry calibrated cameras; camera projections and scale anchors must be supplied separately to solve metric 3D geometry.",
            "Every requested view remains in the payload. An empty foreground is marked UNMEASURABLE rather than dropped.",
        ],
    }


def _reject(reason: str) -> int:
    print(f"pil_multiview_prepare: {reason}", file=sys.stderr)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare ordered contours from a multi-view image manifest.")
    parser.add_argument("manifest", help="multiview-spec-v1 JSON file")
    args = parser.parse_args(argv)
    path = Path(args.manifest)
    if not path.is_file():
        return _reject(f"manifest not found: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        payload = prepare_manifest(manifest, path)
    except (OSError, ValueError, PrepareError) as exc:
        return _reject(str(exc))
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
