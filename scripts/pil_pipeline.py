"""Compose explicit normalization/selections with existing measurement CLIs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import numpy as np
from PIL import Image
from pil_io import emit
from pil_mask import read_mask
from pil_normalize import normalize_image

TOOL_VERSION = "0.9.3"
TOOLS = (
    "pil_image_analyze",
    "pil_palette_diff",
    "pil_structure_diff",
    "pil_ocr",
    "pil_embed",
)


def _logical(value, replacements):
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_logical(v, replacements) for v in value]
    if isinstance(value, dict):
        return {k: _logical(v, replacements) for k, v in value.items()}
    return value


def measure(
    tool,
    image_a,
    image_b=None,
    *,
    input_mode="stored",
    mask_a=None,
    mask_b=None,
    arguments=None,
):
    if tool not in TOOLS:
        raise ValueError(
            "pipeline supports only the documented image measurement tools"
        )
    if tool == "pil_ocr" and image_b is not None:
        raise ValueError("OCR accepts one image per call")
    if mask_b and image_b is None:
        raise ValueError("mask-b requires image-b")
    arguments = list(arguments or [])
    if "--help" in arguments:
        raise ValueError(
            "use the original tool --help or pil_capabilities for arguments"
        )
    inputs, paths, replacements = {}, [], {}
    with tempfile.TemporaryDirectory(prefix="pil-prepared-") as directory:
        for label, path, selection in [("a", image_a, mask_a), ("b", image_b, mask_b)]:
            if path is None:
                continue
            image, metadata = normalize_image(path, input_mode)
            if selection:
                mask, metadata["mask"] = read_mask(selection, path, input_mode)
                array = np.array(image)
                array[:, :, 3] = np.where(mask, array[:, :, 3], 0)
                if not array[:, :, 3].any():
                    raise ValueError("selection contains no visible image support")
                image = Image.fromarray(array)
                if tool == "pil_ocr":
                    background = Image.new("RGBA", image.size, "white")
                    background.alpha_composite(image)
                    image = background.convert("RGB")
            target = Path(directory) / f"{label}.png"
            image.save(target)
            replacements[str(target)] = f"prepared://{label}"
            metadata["prepared_reference"] = f"prepared://{label}"
            inputs[label] = metadata
            paths.append(str(target))
        argv = [sys.executable, str(Path(__file__).with_name(f"{tool}.py"))]
        if tool == "pil_embed":
            argv.append("embed")
        argv += paths + arguments
        if (mask_a or mask_b) and tool in TOOLS[:3] and "--foreground" not in arguments:
            argv.append("--foreground")
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", timeout=180
        )
        if proc.returncode not in (0, 1) or not proc.stdout.strip():
            raise ValueError(f"{tool} refused: {proc.stderr.strip()}")
        result = _logical(json.loads(proc.stdout), replacements)
    return {
        "tool": "pil_pipeline",
        "version": TOOL_VERSION,
        "measurement_tool": tool,
        "inputs": inputs,
        "calibration_status": "requires_pipeline_specific_profile",
        "exit_code": proc.returncode,
        "result": result,
        "interpretation_limits": [
            "Result paths prepared:// identify transient prepared pixels, not the original files.",
            "Masks select pixels; retained source alpha still supplies coverage weights.",
            "Original threshold interpretations are not validated for this preparation pipeline.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", required=True, choices=TOOLS)
    parser.add_argument("--image-a", required=True)
    parser.add_argument("--image-b")
    parser.add_argument("--mask-a")
    parser.add_argument("--mask-b")
    parser.add_argument("--input-mode", choices=["stored", "display"], default="stored")
    parser.add_argument(
        "arguments", nargs=argparse.REMAINDER, help="original tool options after --"
    )
    args = parser.parse_args(argv)
    try:
        options = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
        result = measure(
            args.tool,
            args.image_a,
            args.image_b,
            input_mode=args.input_mode,
            mask_a=args.mask_a,
            mask_b=args.mask_b,
            arguments=options,
        )
        emit(result)
        return result["exit_code"]
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"pil_pipeline: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
