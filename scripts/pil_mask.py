"""Create image-bound binary selections from raster masks and polygons."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from pil_io import digest, emit, read_json, save_png, write_json
from pil_normalize import normalize_image

TOOL_VERSION = "0.9.1"


def _polygon(size, points):
    if len(points) < 3:
        raise ValueError("polygon requires at least three points")
    if any(
        len(p) != 2
        or any(not math.isfinite(v) for v in p)
        or not (0 <= p[0] < size[0] and 0 <= p[1] < size[1])
        for p in points
    ):
        raise ValueError(
            "polygon coordinates must be finite pixel centers inside the input frame"
        )
    image = Image.new("L", size)
    ImageDraw.Draw(image).polygon([tuple(p) for p in points], fill=255)
    return np.asarray(image) > 0


def create_mask(image_path, spec, output, input_mode="stored"):
    if not isinstance(spec, dict):
        raise ValueError("selection spec must be a JSON object")
    image, preparation = normalize_image(image_path, input_mode)
    output = Path(output)
    raster_output = output.with_suffix(".png")
    if (
        output.resolve() == raster_output.resolve()
        or output.exists()
        or raster_output.exists()
    ):
        raise ValueError("mask output must be a new JSON path with a new PNG sibling")
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        raise ValueError("selection requires a name")
    mask = np.zeros((image.height, image.width), dtype=bool)
    for raster in spec.get("rasters", []):
        with Image.open(raster) as candidate:
            if candidate.size != image.size:
                raise ValueError(
                    "raster mask dimensions differ; implicit resizing is refused"
                )
            mask |= np.asarray(candidate.convert("L")) > 0
    for polygon in spec.get("polygons", []):
        mask |= _polygon(image.size, polygon)
    for polygon in spec.get("subtract_polygons", []):
        mask &= ~_polygon(image.size, polygon)
    for raster in spec.get("subtract_rasters", []):
        with Image.open(raster) as candidate:
            if candidate.size != image.size:
                raise ValueError("raster mask dimensions differ")
            mask &= ~(np.asarray(candidate.convert("L")) > 0)
    if not mask.any():
        raise ValueError("selection is empty")
    result = {
        "tool": "pil_mask",
        "version": TOOL_VERSION,
        "schema": "selection-mask-v1",
        "name": spec["name"],
        "image_sha256": digest(image_path),
        "input_mode": input_mode,
        "size": list(image.size),
        "selection_semantics": "binary_selection_not_alpha_coverage",
        "selected_pixels": int(mask.sum()),
        "coordinate_space": "prepared_frame_pixel_centers",
        "raster": raster_output.name,
        "source_to_output": preparation["source_to_output"],
    }
    save_png(raster_output, Image.fromarray(mask.astype("uint8") * 255))
    try:
        result["raster_sha256"] = digest(raster_output)
        write_json(output, result)
    except Exception:
        raster_output.unlink()
        raise
    return result


def read_mask(path, image_path, input_mode="stored"):
    data = read_json(path)
    if not isinstance(data, dict) or not {
        "schema",
        "image_sha256",
        "input_mode",
        "size",
        "raster",
        "raster_sha256",
        "name",
        "selection_semantics",
    }.issubset(data):
        raise ValueError("mask manifest must be a complete selection-mask-v1 object")
    if (
        data.get("schema") != "selection-mask-v1"
        or data.get("image_sha256") != digest(image_path)
        or data.get("input_mode") != input_mode
    ):
        raise ValueError("mask binding does not match image bytes and input mode")
    raster = Path(path).parent / data["raster"]
    if digest(raster) != data["raster_sha256"]:
        raise ValueError("mask raster binding changed")
    image, _ = normalize_image(image_path, input_mode)
    with Image.open(raster) as source:
        if list(source.size) != data["size"] or source.size != image.size:
            raise ValueError("mask dimensions do not match image")
        values = np.asarray(source.convert("L"))
    if not np.isin(values, [0, 255]).all() or not values.any():
        raise ValueError("mask must contain a nonempty binary selection")
    return values > 0, {
        "source": "explicit_selection",
        "manifest_sha256": digest(path),
        "name": data["name"],
        "selected_pixels": int((values > 0).sum()),
        "semantics": data["selection_semantics"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument(
        "--spec",
        required=True,
        help="JSON name, polygons, rasters and subtraction selections",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--input-mode", choices=["stored", "display"], default="stored")
    args = parser.parse_args(argv)
    try:
        spec = read_json(args.spec)
        if not isinstance(spec, dict):
            raise ValueError("selection spec must be a JSON object")
        for key in ("rasters", "subtract_rasters"):
            spec[key] = [str(Path(args.spec).parent / p) for p in spec.get(key, [])]
        emit(create_mask(args.image, spec, args.output, args.input_mode))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"pil_mask: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
