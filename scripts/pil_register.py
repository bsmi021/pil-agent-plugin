"""Bounded image registration with raw/aligned measurements and valid overlap."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import numpy as np
from PIL import Image
from pil_io import emit, positive, save_png
from pil_normalize import normalize_image
from pil_diff_regions import appearance, rgba_array, difference_arrays

TOOL_VERSION = "0.9.6"


def register_images(
    image_a,
    image_b,
    *,
    motion="translation",
    max_shift=20.0,
    max_rotation=5.0,
    max_scale_change=0.05,
    min_overlap=0.8,
    input_mode="stored",
    output_dir=None,
):
    try:
        import cv2
    except ImportError as exc:
        raise ValueError(
            "registration requires the reconstruction extra (OpenCV)"
        ) from exc
    positive(max_shift, "max-shift")
    positive(max_rotation, "max-rotation", True)
    positive(max_scale_change, "max-scale-change", True)
    if not 0 < min_overlap <= 1:
        raise ValueError("min-overlap must be in (0,1]")
    if motion not in ("translation", "rigid", "affine"):
        raise ValueError("unknown motion model")
    a, ma = normalize_image(image_a, input_mode)
    b, mb = normalize_image(image_b, input_mode)
    aa, bb = rgba_array(a), rgba_array(b)
    raw, _, _ = difference_arrays(aa, bb)
    result = {
        "tool": "pil_register",
        "version": TOOL_VERSION,
        "status": "UNMEASURABLE",
        "motion": motion,
        "inputs": {"a": ma, "b": mb},
        "raw": raw,
        "aligned": None,
        "candidate_to_reference": None,
        "valid_overlap_fraction": None,
        "interpretation_limits": [
            "Alignment removes the reported transform; the raw comparison retains displacement evidence.",
            "Aligned metrics are diagnostic and do not establish object identity or permitted deformation.",
        ],
    }
    gray_a = cv2.cvtColor(appearance(aa).astype("float32"), cv2.COLOR_RGB2GRAY)
    gray_b = cv2.cvtColor(appearance(bb).astype("float32"), cv2.COLOR_RGB2GRAY)
    if min(float(gray_a.std()), float(gray_b.std())) < 0.1:
        return {**result, "reason": "insufficient_texture"}
    shift, response = cv2.phaseCorrelate(gray_a, gray_b)
    if not np.isfinite([*shift, response]).all() or response < 0.15:
        return {
            **result,
            "reason": "ambiguous_registration",
            "response": float(response) if np.isfinite(response) else None,
        }
    forward = np.array([[1, 0, shift[0]], [0, 1, shift[1]]], dtype=np.float32)
    kind = {
        "translation": cv2.MOTION_TRANSLATION,
        "rigid": cv2.MOTION_EUCLIDEAN,
        "affine": cv2.MOTION_AFFINE,
    }[motion]
    try:
        response, forward = cv2.findTransformECC(
            gray_a,
            gray_b,
            forward,
            kind,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-7),
            None,
            1,
        )
    except cv2.error:
        return {**result, "reason": "registration_did_not_converge"}
    transform = cv2.invertAffineTransform(forward).astype(float)
    scales = np.linalg.svd(transform[:, :2], compute_uv=False)
    rotation = np.degrees(np.arctan2(transform[1, 0], transform[0, 0]))
    if (
        not np.isfinite(transform).all()
        or np.linalg.det(transform[:, :2]) <= 0
        or np.max(np.abs(transform[:, 2])) > max_shift
        or abs(rotation) > max_rotation
        or np.max(np.abs(scales - 1)) > max_scale_change
    ):
        return {
            **result,
            "reason": "transform_exceeds_bounds",
            "candidate_to_reference": transform.tolist(),
        }
    # Warp premultiplied channels and alpha together, then recover straight RGB.
    premult = bb.copy()
    premult[:, :, :3] = appearance(bb)
    warped = cv2.warpAffine(
        premult,
        transform,
        a.size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
    )
    valid = (
        cv2.warpAffine(
            np.ones(bb.shape[:2], dtype=np.float32),
            transform,
            a.size,
            flags=cv2.INTER_LINEAR,
        )
        >= 0.999
    )
    overlap = float(valid.mean())
    if overlap < min_overlap:
        return {
            **result,
            "reason": "insufficient_overlap",
            "valid_overlap_fraction": overlap,
        }
    aligned_array = warped.copy()
    aligned_array[:, :, :3] = np.divide(
        warped[:, :, :3] * 255,
        warped[:, :, 3:4],
        out=np.zeros_like(warped[:, :, :3]),
        where=warped[:, :, 3:4] > 0,
    )
    metrics, _, _ = difference_arrays(aa, aligned_array, valid)
    result.update(
        status="REGISTERED",
        candidate_to_reference=transform.tolist(),
        response=float(response),
        valid_overlap_fraction=overlap,
        aligned=metrics,
        bounds={
            "max_shift": max_shift,
            "max_rotation": max_rotation,
            "max_scale_change": max_scale_change,
            "min_overlap": min_overlap,
        },
    )
    if output_dir:
        out = Path(output_dir)
        out.mkdir()
        save_png(
            out / "aligned.png",
            Image.fromarray(np.clip(np.rint(aligned_array), 0, 255).astype("uint8")),
        )
        save_png(out / "overlap.png", Image.fromarray(valid.astype("uint8") * 255))
        result["artifacts"] = {
            "aligned": str(out / "aligned.png"),
            "overlap": str(out / "overlap.png"),
        }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_a")
    parser.add_argument("image_b")
    parser.add_argument(
        "--motion", choices=["translation", "rigid", "affine"], default="translation"
    )
    parser.add_argument("--max-shift", type=float, default=20.0)
    parser.add_argument("--max-rotation", type=float, default=5.0)
    parser.add_argument("--max-scale-change", type=float, default=0.05)
    parser.add_argument("--min-overlap", type=float, default=0.8)
    parser.add_argument("--input-mode", choices=["stored", "display"], default="stored")
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)
    try:
        result = register_images(**vars(args))
        emit(result)
        return 0 if result["status"] == "REGISTERED" else 1
    except (ValueError, OSError) as exc:
        print(f"pil_register: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
