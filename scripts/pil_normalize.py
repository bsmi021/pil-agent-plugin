"""Explicit stored-pixel or display-normalized image preparation."""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from PIL import Image, ImageCms, ImageOps
from pil_io import digest, emit, save_png

TOOL_VERSION = "0.9.1"
PIPELINE_VERSION = "display-srgb-v1"


def normalize_image(path, mode="stored"):
    if mode not in ("stored", "display"):
        raise ValueError("input mode must be stored or display")
    with Image.open(path) as source:
        source.load()
        w, h = source.size
        orientation = int(source.getexif().get(274, 1))
        profile = source.info.get("icc_profile")
        image = source.copy()
    matrices = {
        1: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        2: [[-1, 0, w], [0, 1, 0], [0, 0, 1]],
        3: [[-1, 0, w], [0, -1, h], [0, 0, 1]],
        4: [[1, 0, 0], [0, -1, h], [0, 0, 1]],
        5: [[0, 1, 0], [1, 0, 0], [0, 0, 1]],
        6: [[0, -1, h], [1, 0, 0], [0, 0, 1]],
        7: [[0, -1, h], [-1, 0, w], [0, 0, 1]],
        8: [[0, 1, 0], [-1, 0, w], [0, 0, 1]],
    }
    if mode == "display" and orientation not in matrices:
        raise ValueError("invalid EXIF orientation")
    if mode == "display":
        image = ImageOps.exif_transpose(image)
    alpha = (
        image.convert("RGBA").getchannel("A")
        if image.mode != "LAB"
        else Image.new("L", image.size, 255)
    )
    transformed = False
    if mode == "display" and profile:
        try:
            colour = (
                image
                if image.mode in ("RGB", "CMYK", "LAB", "L")
                else image.convert("RGB")
            )
            image = ImageCms.profileToProfile(
                colour,
                ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                ImageCms.createProfile("sRGB"),
                renderingIntent=1,
                outputMode="RGB",
            )
            transformed = True
        except Exception as exc:
            raise ValueError(f"ICC conversion failed: {exc}") from exc
    elif mode == "display" and image.mode not in ("RGB", "RGBA", "P", "L", "LA", "1"):
        raise ValueError(
            "display normalization requires an ICC profile for this colour mode"
        )
    image = image.convert("RGBA")
    image.putalpha(alpha)
    image.info.clear()
    metadata = {
        "source": str(path),
        "source_sha256": digest(path),
        "source_size": [w, h],
        "size": list(image.size),
        "mode": mode,
        "pipeline": PIPELINE_VERSION if mode == "display" else "stored-v1",
        "source_icc_sha256": hashlib.sha256(profile).hexdigest() if profile else None,
        "source_orientation": orientation,
        "orientation_applied": orientation if mode == "display" else 1,
        "source_to_output": matrices.get(orientation, matrices[1])
        if mode == "display"
        else matrices[1],
        "coordinate_convention": "pixel edges; pixel centers are x+0.5,y+0.5",
        "colour_transform_applied": transformed,
        "output_colour_space": "sRGB" if mode == "display" else "stored channels",
        "assumed_profile": "sRGB" if mode == "display" and not profile else None,
        "rendering_intent": "relative_colorimetric" if transformed else None,
        "alpha": "preserved; not colour transformed",
    }
    return image, metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--mode", choices=["stored", "display"], default="display")
    parser.add_argument(
        "--output", required=True, help="new PNG path; never overwrites"
    )
    args = parser.parse_args(argv)
    try:
        image, metadata = normalize_image(args.image, args.mode)
        save_png(args.output, image)
        emit(
            {
                "tool": "pil_normalize",
                "version": TOOL_VERSION,
                "input": metadata,
                "output": args.output,
                "output_sha256": digest(args.output),
            }
        )
        return 0
    except (ValueError, OSError) as exc:
        print(f"pil_normalize: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
