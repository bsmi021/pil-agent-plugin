#!/usr/bin/env python
"""Depth for concept images: model-inferred RELATIVE depth, and how it compares to a render.

A concept image carries no depth data. ``estimate`` runs Depth Anything V2
Small (an ONNX export, caller-supplied and never bundled) and writes what the
model *infers*: relative inverse depth, larger = nearer, with an arbitrary
scale and shift per image. It is not metric, not in scene units, and for
stylised art it is a learned guess. ``compare`` then puts that guess next to
the exact depth of a Blender render (the ``.npy`` from
``blender_inspect_render --modes depth``): it aligns scale and shift by least
squares in inverse-depth space, reports how well the two agree in ordering
(Spearman) and in value after alignment (AbsRel), and draws where they
disagree most.

The model is pinned the same way ``pil_embed``'s is: caller-supplied
(``--model`` or ``PIL_AGENT_DEPTH_MODEL``), sha256 recorded in every payload,
preprocessing a named profile whose numeric spec is echoed. Missing
onnxruntime, model or input exits 2 with a one-line reason and empty stdout.

Usage:
    python pil_depth.py diagnose --model depth_anything_v2_vits.onnx
    python pil_depth.py estimate concept.png --model depth_anything_v2_vits.onnx \\
        --output-dir out/ [--mask selection-mask.json]
    python pil_depth.py compare out/concept_inverse_depth.npy render_depth.npy \\
        --output-dir out/ [--mask mask.png]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_common import load_rgba_straight  # noqa: E402
from pil_io import digest, save_png  # noqa: E402
from pil_region import RegionError, rect_to_fractional  # noqa: E402

TOOL_VERSION = "0.10.0"

MODEL_ENV_VAR = "PIL_AGENT_DEPTH_MODEL"

# Depth Anything V2 (github.com/DepthAnything/Depth-Anything-V2, image2tensor):
# resize with the aspect ratio kept so BOTH sides are at least 518 (the
# shorter side lands on 518), each side rounded to a multiple of 14 (the ViT
# patch size), bicubic, /255, ImageNet mean/std, NCHW. ``max_long_side`` is
# this tool's own guard against extreme panoramas, not part of the reference
# pipeline; it only bites when the long side would exceed it. A model exported
# with a STATIC input size is fed that size instead (the image is resized to
# it directly, distorting aspect), and the payload says which happened.
PREPROCESSING_PROFILES = {
    "depth-anything-v2": {
        "input_size": 518,
        "multiple_of": 14,
        "resize": "keep_aspect_lower_bound",
        "resample": "bicubic",
        "max_long_side": 1036,
        "scale": "1/255",
        "normalize_mean": [0.485, 0.456, 0.406],
        "normalize_std": [0.229, 0.224, 0.225],
        "layout": "NCHW",
        "dtype": "float32",
    },
}
DEFAULT_PREPROCESSING_PROFILE = "depth-anything-v2"

# sha256 of model files this repository has actually run. Anything else is
# reported as unverified. Filled in from real smoke runs (never guessed).
KNOWN_MODELS = {
    "afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c": (
        "Depth Anything V2 Small, fp32 ONNX (onnx-community/depth-anything-v2-small "
        "onnx/model.onnx, Apache-2.0); ran the AC-MDE-02 smoke on a real concept image"
    ),
}

DEPTH_KIND = "relative_inverse_depth"

# compare: render grid is the target; a concept map is resampled onto it only
# when the aspect ratios agree this closely (relative difference of w/h).
ASPECT_TOLERANCE = 0.01
MIN_VALID_PIXELS = 100
DEFAULT_GRID = 8
DEFAULT_TOP_REGIONS = 3
# Cells need this fraction of their pixels valid to be ranked as regions.
MIN_CELL_COVERAGE = 0.25
# Disagreement colour scale saturates here, in units of the render's robust
# inverse-depth range (p95 - p5), so heatmaps are comparable between runs.
ERROR_CLIP = 0.5

_COLOR_STOPS = [
    (0.00, (0, 0, 4)),
    (0.25, (87, 16, 110)),
    (0.50, (188, 55, 84)),
    (0.75, (249, 142, 9)),
    (1.00, (252, 255, 164)),
]
_INVALID_COLOR = (48, 48, 48)
_OUTSIDE_MASK_SHADE = 0.35


class DepthError(Exception):
    """Tool-level failure with a caller-facing reason."""


# ---------------------------------------------------------------- estimate


def _load_runtime():
    try:
        import onnxruntime  # noqa: PLC0415
    except (ImportError, OSError) as exc:
        raise DepthError(
            "onnxruntime is unavailable; install the embedding extra "
            "(uv sync --extra embedding) and run with .venv/Scripts/python.exe "
            "on Windows. For DLL load failures, install the Microsoft Visual "
            f"C++ runtime matching Python's architecture. Cause: {exc}"
        ) from exc
    return onnxruntime


def _resolve_model(explicit):
    path = explicit or os.environ.get(MODEL_ENV_VAR)
    if not path:
        raise DepthError(
            f"no depth model given; pass --model or set {MODEL_ENV_VAR} to a "
            "Depth Anything V2 Small ONNX file"
        )
    model_path = Path(path)
    if not model_path.is_file():
        raise DepthError(f"depth model not found: {model_path}")
    return model_path


def _static_input_size(session):
    """(width, height) if the model declares both spatial dims, else None."""
    shape = session.get_inputs()[0].shape
    if len(shape) != 4:
        raise DepthError(f"depth model input must be NCHW (4-D); it declares {shape}")
    height, width = shape[2], shape[3]
    if isinstance(height, int) and isinstance(width, int) and height > 0 and width > 0:
        return width, height
    return None


def _round_to_multiple(value, multiple, minimum):
    rounded = int(round(value / multiple)) * multiple
    if rounded < minimum:
        rounded = int(math.ceil(value / multiple)) * multiple
    return max(rounded, multiple)


def _target_size(width, height, spec, static_size):
    """The (width, height) the network is fed, and how it was chosen."""
    if static_size is not None:
        return static_size, "model_static_input"
    base = spec["input_size"]
    multiple = spec["multiple_of"]
    scale = max(base / width, base / height)
    new_w = _round_to_multiple(width * scale, multiple, base)
    new_h = _round_to_multiple(height * scale, multiple, base)
    cap = spec["max_long_side"]
    if max(new_w, new_h) > cap:
        factor = cap / max(new_w, new_h)
        new_w = max(multiple, int(new_w * factor) // multiple * multiple)
        new_h = max(multiple, int(new_h * factor) // multiple * multiple)
    return (new_w, new_h), "keep_aspect_lower_bound"


def _preprocess(rgb, spec, static_size):
    from PIL import Image  # noqa: PLC0415

    (new_w, new_h), mode = _target_size(rgb.width, rgb.height, spec, static_size)
    resized = rgb.resize((new_w, new_h), getattr(Image, spec["resample"].upper()))
    arr = np.asarray(resized, dtype=np.float32) / 255.0
    mean = np.asarray(spec["normalize_mean"], dtype=np.float32)
    std = np.asarray(spec["normalize_std"], dtype=np.float32)
    arr = (arr - mean) / std
    return arr.transpose(2, 0, 1)[None, ...].astype(np.float32), (new_w, new_h), mode


def _resize_float(arr, size, resample_name="BILINEAR"):
    from PIL import Image  # noqa: PLC0415

    image = Image.fromarray(np.ascontiguousarray(arr, dtype=np.float32), mode="F")
    out = image.resize(size, getattr(Image, resample_name))
    return np.asarray(out, dtype=np.float32)


def _setup(model_arg):
    onnxruntime = _load_runtime()
    model_path = _resolve_model(model_arg)
    try:
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        session = onnxruntime.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
    except Exception as exc:
        # ORT exposes several native exception types, not all RuntimeError.
        raise DepthError(f"cannot load ONNX model {model_path}: {exc}") from exc
    static_size = _static_input_size(session)
    return onnxruntime, model_path, session, static_size


def _model_block(model_path, digest_hex):
    known = digest_hex in KNOWN_MODELS
    return {
        "model_file": model_path.name,
        "model_sha256": digest_hex,
        "model_known": known,
        "model_note": KNOWN_MODELS.get(digest_hex),
    }


def _mask_from_manifest(path, image_path, image_size):
    from pil_mask import read_mask  # noqa: PLC0415

    try:
        mask, info = read_mask(path, image_path)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise DepthError(f"cannot use mask {path}: {exc}") from exc
    if mask.shape != (image_size[1], image_size[0]):
        raise DepthError(
            f"mask is {mask.shape[1]}x{mask.shape[0]} but the image is "
            f"{image_size[0]}x{image_size[1]}; resizing a mask is refused"
        )
    return mask, info


def colorize(values, valid, lo, hi):
    """Map values in [lo, hi] onto the ramp; invalid pixels are dark grey."""
    span = hi - lo
    t = np.clip((np.where(valid, values, lo) - lo) / span, 0.0, 1.0)
    positions = [p for p, _ in _COLOR_STOPS]
    rgb = np.stack(
        [np.interp(t, positions, [c[i] for _, c in _COLOR_STOPS]) for i in range(3)],
        axis=-1,
    )
    rgb[~valid] = _INVALID_COLOR
    return np.round(rgb).astype(np.uint8)


def _write_npy(path, array):
    with Path(path).open("xb") as stream:
        np.save(stream, array)


def _output_paths(output_dir, *names):
    directory = Path(output_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DepthError(f"cannot create output directory {directory}: {exc}") from exc
    paths = [directory / name for name in names]
    existing = [p for p in paths if p.exists()]
    if existing:
        raise DepthError(
            f"refusing to overwrite {existing[0]}; use a new --output-dir or a "
            "different input name"
        )
    return paths


def _estimate_limits():
    return [
        "MODEL-INFERRED, RELATIVE, NOT METRIC. The values are relative inverse "
        "depth (larger = nearer) with an arbitrary scale and shift for this one "
        "image. They are not distances, carry no scene units, and cannot be "
        "compared between images or against a render without alignment "
        "(pil_depth compare).",
        "Single-image depth is a learned guess, not a measurement. Concept art "
        "and stylised illustration sit outside the photographs the model was "
        "trained on: it can invent depth for flat drawings and misjudge "
        "occluded or ambiguous structure. Read the heatmap against the image.",
        "The model was fed the named preprocessing profile at the size recorded "
        "under parameters; the sha256 and profile identify what produced the "
        "numbers. The map is bilinearly resampled to the image size, so fine "
        "detail is model resolution, not pixel resolution. Alpha is "
        "composited onto black before inference.",
        "The heatmap is min-max normalised per image (inside the mask when "
        "one is given), so its colours are not comparable between images. The "
        ".npy is the model output unscaled.",
        "Determinism is scoped like pil_embed: same model file, same "
        "onnxruntime build, same machine (CPU provider, single-threaded) "
        "reproduces the payload; cross-machine bit-identity is not claimed.",
    ]


def run_diagnose(args):
    runtime, model, session, static_size = _setup(args.model)
    digest_hex = hashlib.sha256(model.read_bytes()).hexdigest()
    return {
        "tool": "pil_depth",
        "command": "diagnose",
        "version": TOOL_VERSION,
        "python": sys.executable,
        "runtime": f"onnxruntime {runtime.__version__}",
        "model_path": str(model),
        **_model_block(model, digest_hex),
        "preprocessing_profile": DEFAULT_PREPROCESSING_PROFILE,
        "input_shape": session.get_inputs()[0].shape,
        "output_shape": session.get_outputs()[0].shape,
        "static_input_size": list(static_size) if static_size else None,
        "note": "Model loaded on CPU; no inference performed.",
    }


def run_estimate(args):
    onnxruntime, model_path, session, static_size = _setup(args.model)
    spec = PREPROCESSING_PROFILES[DEFAULT_PREPROCESSING_PROFILE]
    image_path = Path(args.image)
    try:
        rgb, _straight, _alpha = load_rgba_straight(image_path)
    except Exception as exc:
        raise DepthError(f"cannot read image {image_path}: {exc}") from exc
    width, height = rgb.size

    mask = mask_info = None
    if args.mask:
        mask, mask_info = _mask_from_manifest(args.mask, image_path, rgb.size)

    stem = image_path.stem
    npy_path, png_path = _output_paths(
        args.output_dir, f"{stem}_inverse_depth.npy", f"{stem}_inverse_depth.png"
    )

    tensor, fed_size, size_mode = _preprocess(rgb, spec, static_size)
    input_name = session.get_inputs()[0].name
    raw = np.asarray(session.run(None, {input_name: tensor})[0], dtype=np.float32)
    depth = np.squeeze(raw)
    if depth.ndim != 2:
        raise DepthError(
            f"model output has shape {tuple(raw.shape)}; expected a single 2-D "
            "depth map (Depth Anything V2's predicted_depth)"
        )
    if not np.isfinite(depth).all():
        raise DepthError("model returned non-finite depth values")
    model_output_shape = [int(depth.shape[0]), int(depth.shape[1])]
    if (depth.shape[1], depth.shape[0]) != (width, height):
        depth = _resize_float(depth, (width, height), "BILINEAR")

    inside = mask if mask is not None else np.ones(depth.shape, dtype=bool)
    values = depth[inside]
    lo, hi = float(values.min()), float(values.max())
    if not hi > lo:
        raise DepthError("model returned a constant depth map; nothing to visualise")

    heat = colorize(depth, np.ones(depth.shape, dtype=bool), lo, hi)
    if mask is not None:
        heat[~mask] = np.round(heat[~mask] * _OUTSIDE_MASK_SHADE).astype(np.uint8)
    from PIL import Image  # noqa: PLC0415

    _write_npy(npy_path, depth.astype(np.float32))
    save_png(png_path, Image.fromarray(heat, mode="RGB"))

    model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
    known = model_sha256 in KNOWN_MODELS
    return {
        "tool": "pil_depth",
        "version": TOOL_VERSION,
        "command": "estimate",
        "depth_kind": DEPTH_KIND,
        "metric": False,
        "convention": "larger value = nearer to the camera",
        "statement": (
            "Model-inferred RELATIVE inverse depth, not metric: the scale and "
            "shift are arbitrary for this image."
        ),
        "engine": {
            "runtime": f"onnxruntime {onnxruntime.__version__}",
            "providers": ["CPUExecutionProvider"],
            **_model_block(model_path, model_sha256),
        },
        "parameters": {
            "preprocessing_profile": DEFAULT_PREPROCESSING_PROFILE,
            "preprocessing": spec,
            "input_size_mode": size_mode,
            "fed_size": list(fed_size),
            "model_output_shape": model_output_shape,
            "output_resampling": (
                "none" if model_output_shape == [height, width]
                else "bilinear_to_image_size"
            ),
            "mask": mask_info,
        },
        "image": {
            "path": str(image_path),
            "sha256": digest(image_path),
            "size": [width, height],
        },
        "depth": {
            "shape": [height, width],
            "dtype": "float32",
            "min": round(lo, 6),
            "max": round(hi, 6),
            "mean": round(float(values.mean()), 6),
            "stats_scope": "mask" if mask is not None else "full_frame",
        },
        "files": {
            "npy": str(npy_path),
            "npy_sha256": digest(npy_path),
            "heatmap_png": str(png_path),
            "heatmap": {
                "normalisation": "min-max over "
                + ("mask pixels" if mask is not None else "the full frame"),
                "lo": round(lo, 6),
                "hi": round(hi, 6),
                "brighter": "nearer",
                "outside_mask": "darkened" if mask is not None else None,
            },
        },
        "flags": [] if known else ["model_not_verified"],
        "interpretation_limits": _estimate_limits(),
    }


# ----------------------------------------------------------------- compare


def rank_average(values):
    """Ranks 1..n with tied values sharing their average rank."""
    values = np.asarray(values)
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts).astype(np.float64)
    starts = ends - counts + 1.0
    return ((starts + ends) / 2.0)[inverse.ravel()]


def spearman(a, b):
    """Spearman rank correlation (Pearson on average ranks)."""
    ra, rb = rank_average(a), rank_average(b)
    ra -= ra.mean()
    rb -= rb.mean()
    denominator = math.sqrt(float((ra * ra).sum()) * float((rb * rb).sum()))
    if denominator == 0.0:
        raise DepthError("rank correlation is undefined: one map has a single rank")
    return float((ra * rb).sum() / denominator)


def fit_scale_shift(x, y):
    """Least-squares (s, t) minimising sum (s*x + t - y)^2."""
    design = np.stack([x, np.ones_like(x)], axis=1)
    solution, *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(solution[0]), float(solution[1])


def _load_npy(path, label):
    try:
        array = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise DepthError(f"cannot read {label} depth {path}: {exc}") from exc
    if array.ndim != 2 or array.dtype.kind not in "fiu":
        raise DepthError(
            f"{label} depth {path} must be a 2-D numeric array; got "
            f"shape {array.shape} dtype {array.dtype}"
        )
    array = array.astype(np.float64)
    if np.isinf(array).any():
        raise DepthError(f"{label} depth {path} contains infinite values")
    return array


def _resample_concept(concept, shape):
    """Bilinear-resample onto the render grid; NaN stays out of the result."""
    valid = np.isfinite(concept)
    if not valid.any():
        raise DepthError("concept depth has no finite pixels")
    fill = float(concept[valid].mean())
    filled = np.where(valid, concept, fill).astype(np.float32)
    size = (shape[1], shape[0])
    values = _resize_float(filled, size, "BILINEAR").astype(np.float64)
    coverage = _resize_float(valid.astype(np.float32), size, "BILINEAR")
    return values, coverage >= 0.999


def _load_compare_mask(path, shape):
    from PIL import Image  # noqa: PLC0415

    try:
        with Image.open(path) as source:
            size = source.size
            values = np.asarray(source.convert("L"))
    except (OSError, ValueError) as exc:
        raise DepthError(f"cannot read mask {path}: {exc}") from exc
    if values.shape != tuple(shape):
        raise DepthError(
            f"mask is {size[0]}x{size[1]} but the render depth is "
            f"{shape[1]}x{shape[0]}; the mask must be a raster at the render's "
            "size (resizing a mask is refused)"
        )
    mask = values > 0
    if not mask.any():
        raise DepthError("mask selects no pixels")
    return mask


def _cell_edges(length, count):
    return [round(i * length / count) for i in range(count + 1)]


def worst_regions(error, signed, valid, grid, top):
    """Rank grid cells by mean error; fractional [L,T,R,B] boxes."""
    height, width = error.shape
    xs, ys = _cell_edges(width, grid), _cell_edges(height, grid)
    cells = []
    for row in range(grid):
        for col in range(grid):
            top_px, bottom_px = ys[row], ys[row + 1]
            left_px, right_px = xs[col], xs[col + 1]
            if right_px <= left_px or bottom_px <= top_px:
                continue
            cell_valid = valid[top_px:bottom_px, left_px:right_px]
            count = int(cell_valid.sum())
            if count < MIN_CELL_COVERAGE * cell_valid.size:
                continue
            cell_error = error[top_px:bottom_px, left_px:right_px][cell_valid]
            cell_signed = signed[top_px:bottom_px, left_px:right_px][cell_valid]
            cells.append(
                (float(cell_error.mean()), row * grid + col, (left_px, top_px, right_px, bottom_px),
                 float(cell_signed.mean()), count)
            )
    cells.sort(key=lambda c: (-c[0], c[1]))
    regions = []
    for rank, (mean_error, _index, rect, mean_signed, count) in enumerate(cells[:top], 1):
        regions.append(
            {
                "rank": rank,
                "bbox_fractional": [round(v, 6) for v in rect_to_fractional(rect, (width, height))],
                "space": "frame",
                "mean_error": round(mean_error, 6),
                "mean_signed_error": round(mean_signed, 6),
                "reading": "concept nearer than render" if mean_signed > 0
                else "concept farther than render",
                "valid_pixels": count,
            }
        )
    return regions


def _draw_regions(rgb, regions):
    from PIL import Image, ImageDraw  # noqa: PLC0415

    image = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for region in regions:
        left, top, right, bottom = region["bbox_fractional"]
        box = (round(left * width), round(top * height),
               max(round(right * width) - 1, round(left * width)),
               max(round(bottom * height) - 1, round(top * height)))
        draw.rectangle(box, outline=(0, 255, 255))
        draw.text((box[0] + 2, box[1] + 1), str(region["rank"]), fill=(0, 255, 255))
    return image


def _compare_limits():
    return [
        "Scale and shift are fitted away, so this tests whether the concept's "
        "depth ORDER and relative shape agree with the render, not whether "
        "distances or proportions are right. Two free parameters can hide a "
        "uniform error.",
        "The concept side is model-inferred relative depth (see pil_depth "
        "estimate). Disagreement is either a model error or a real difference "
        "between the model and the concept; the numbers cannot say which. "
        "Look at the disagreement heatmap and the two images.",
        "The two maps must show the same framing. Only the aspect ratio is "
        "checked (relative tolerance "
        f"{ASPECT_TOLERANCE}); a shifted or differently cropped view produces "
        "large disagreement that is framing, not depth.",
        "Spearman is a rank agreement over the valid pixels and AbsRel is "
        "computed after the fit; neither has a calibrated pass/fail threshold "
        "in this repository. Report them with the pixel count and read the "
        "regions rather than applying a cut-off.",
        "The render's depth is exact for the Blender scene; only the concept "
        "is inferred. Pixels are compared only where both maps are finite "
        "(background NaN and anything outside the mask are excluded).",
    ]


def run_compare(args):
    concept = _load_npy(args.concept_depth, "concept")
    render = _load_npy(args.render_depth, "render")
    if not (1 <= args.grid <= 64):
        raise DepthError("--grid must be between 1 and 64")
    if args.top < 1:
        raise DepthError("--top must be at least 1")

    concept_shape, render_shape = concept.shape, render.shape
    resampling = {
        "rule": "The render grid is the target. If the shapes differ but the "
        f"aspect ratios (w/h) agree within {ASPECT_TOLERANCE} relative, the "
        "concept map is bilinearly resampled to the render's shape (a "
        "resampled pixel is valid only where >= 99.9% of its bilinear support "
        "was finite). Otherwise the compare is refused.",
        "aspect_tolerance": ASPECT_TOLERANCE,
        "concept_shape": list(concept_shape),
        "render_shape": list(render_shape),
        "applied": False,
    }
    if concept_shape != render_shape:
        aspect_concept = concept_shape[1] / concept_shape[0]
        aspect_render = render_shape[1] / render_shape[0]
        if abs(aspect_concept - aspect_render) / aspect_render > ASPECT_TOLERANCE:
            raise DepthError(
                f"shape mismatch: concept depth is {concept_shape[1]}x{concept_shape[0]} "
                f"(aspect {aspect_concept:.4f}), render depth is "
                f"{render_shape[1]}x{render_shape[0]} (aspect {aspect_render:.4f}); "
                f"aspect ratios differ by more than {ASPECT_TOLERANCE} so the "
                "maps cannot show the same framing. Re-render at the concept's "
                "aspect ratio or crop the concept."
            )
        concept, concept_valid = _resample_concept(concept, render_shape)
        resampling["applied"] = True
        resampling["method"] = "bilinear"
    else:
        concept_valid = np.isfinite(concept)

    mask = None
    if args.mask:
        mask = _load_compare_mask(args.mask, render_shape)

    with np.errstate(invalid="ignore"):
        render_valid = np.isfinite(render) & (render > 0)
    valid = concept_valid & render_valid
    if mask is not None:
        valid &= mask
    n_valid = int(valid.sum())
    if n_valid < MIN_VALID_PIXELS:
        raise DepthError(
            f"only {n_valid} pixels are valid in both maps (need at least "
            f"{MIN_VALID_PIXELS}); check the mask, the render's background and "
            "that both show the same view"
        )

    x = concept[valid]
    d = render[valid]
    y = 1.0 / d
    if float(x.max() - x.min()) == 0.0:
        raise DepthError("concept depth is constant over the valid pixels; cannot align")
    if float(y.max() - y.min()) == 0.0:
        raise DepthError("render depth is constant over the valid pixels; cannot align")

    scale, shift = fit_scale_shift(x, y)
    aligned = scale * x + shift
    residual = aligned - y
    y_lo, y_hi = np.percentile(y, [5, 95])
    y_range = float(y_hi - y_lo)
    if not y_range > 0.0:
        raise DepthError("render inverse depth has no spread (p95 == p5); cannot scale errors")

    positive = aligned > 0
    dropped = int((~positive).sum())
    absrel_inverse = float(np.mean(np.abs(residual) / y))
    if positive.any():
        absrel = float(np.mean(np.abs(1.0 / aligned[positive] - d[positive]) / d[positive]))
    else:
        absrel = None
    ss_res = float((residual ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r_squared = 1.0 - ss_res / ss_tot
    rho = spearman(x, y)

    error_map = np.zeros(render_shape, dtype=np.float64)
    signed_map = np.zeros(render_shape, dtype=np.float64)
    error_map[valid] = np.abs(residual) / y_range
    # aligned inverse depth above the render's = the concept says nearer.
    signed_map[valid] = residual / y_range
    regions = worst_regions(error_map, signed_map, valid, args.grid, args.top)

    heat = colorize(error_map, valid, 0.0, ERROR_CLIP)
    stem = f"{Path(args.concept_depth).stem}_vs_{Path(args.render_depth).stem}"
    (png_path,) = _output_paths(args.output_dir, f"{stem}_disagreement.png")
    save_png(png_path, _draw_regions(heat, regions))

    flags = []
    if scale <= 0:
        flags.append("inverted_relation")
    if dropped:
        flags.append("aligned_nonpositive_pixels_dropped_from_absrel")
    finite_errors = error_map[valid]
    return {
        "tool": "pil_depth",
        "version": TOOL_VERSION,
        "command": "compare",
        "statement": (
            "Concept depth is model-inferred RELATIVE inverse depth; the render "
            "depth is exact. Scale and shift are fitted away before AbsRel."
        ),
        "inputs": {
            "concept": {
                "path": str(args.concept_depth),
                "sha256": digest(args.concept_depth),
                "shape": list(concept_shape),
            },
            "render": {
                "path": str(args.render_depth),
                "sha256": digest(args.render_depth),
                "shape": list(render_shape),
                "finite_fraction": round(float(render_valid.mean()), 6),
            },
        },
        "parameters": {
            "resampling": resampling,
            "mask": {"path": str(args.mask), "selected_pixels": int(mask.sum())}
            if mask is not None else None,
            "grid": args.grid,
            "top_regions": args.top,
            "min_cell_coverage": MIN_CELL_COVERAGE,
            "error_definition": (
                "|aligned concept - 1/render_depth| / (p95 - p5 of 1/render_depth); "
                "aligned concept = scale * concept + shift"
            ),
            "error_clip": ERROR_CLIP,
        },
        "alignment": {
            "space": "inverse_depth",
            "model": "1/render_depth ~= scale * concept + shift",
            "fit": "least squares over valid pixels",
            "scale": round(scale, 9),
            "shift": round(shift, 9),
            "valid_pixels": n_valid,
            "valid_fraction_of_frame": round(n_valid / valid.size, 6),
            "pixels_dropped_from_absrel": dropped,
        },
        "metrics": {
            "spearman": round(rho, 6),
            "spearman_between": "concept values and 1/render_depth",
            "absrel": None if absrel is None else round(absrel, 6),
            "absrel_space": "depth: mean(|1/aligned - d| / d); aligned <= 0 pixels dropped",
            "absrel_inverse_depth": round(absrel_inverse, 6),
            "r_squared_inverse_depth": round(r_squared, 6),
            "error_mean": round(float(finite_errors.mean()), 6),
            "error_p90": round(float(np.percentile(finite_errors, 90)), 6),
        },
        "disagreement": {
            "png": str(png_path),
            "png_sha256": digest(png_path),
            "colour": "dark = agree; bright = disagree; saturates at error_clip; "
            "grey = not compared; cyan boxes = ranked regions",
            "regions": regions,
        },
        "flags": flags,
        "interpretation_limits": _compare_limits(),
    }


# -------------------------------------------------------------------- main


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Concept-image depth: model-inferred relative inverse depth "
        "(Depth Anything V2 Small, ONNX) and its comparison with a render's depth."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    diagnose = sub.add_parser("diagnose", help="check runtime and model without an image")
    diagnose.add_argument("--model", default=None, help=f"ONNX depth model (else ${MODEL_ENV_VAR})")

    estimate = sub.add_parser("estimate", help="infer relative inverse depth for one image")
    estimate.add_argument("image", help="concept image")
    estimate.add_argument("--model", default=None, help=f"ONNX depth model (default: ${MODEL_ENV_VAR})")
    estimate.add_argument(
        "--mask", default=None,
        help="selection-mask-v1 manifest from pil_mask.py bound to this image; "
        "scopes the statistics and heatmap normalisation to the selection",
    )
    estimate.add_argument("--output-dir", required=True,
                          help="directory for <stem>_inverse_depth.npy/.png (never overwritten)")

    compare = sub.add_parser("compare", help="compare concept depth with a render's depth")
    compare.add_argument("concept_depth", help=".npy from `estimate` (relative inverse depth)")
    compare.add_argument("render_depth", help=".npy depth render (metric depth, NaN background)")
    compare.add_argument(
        "--mask", default=None,
        help="binary mask PNG (non-zero = compare) at the render depth's size",
    )
    compare.add_argument("--grid", type=int, default=DEFAULT_GRID,
                         help="regions are ranked over an N x N grid (default 8)")
    compare.add_argument("--top", type=int, default=DEFAULT_TOP_REGIONS,
                         help="number of worst regions to report (default 3)")
    compare.add_argument("--output-dir", required=True,
                         help="directory for the disagreement PNG (never overwritten)")

    args = parser.parse_args(argv)
    try:
        if args.command == "diagnose":
            payload = run_diagnose(args)
        elif args.command == "estimate":
            payload = run_estimate(args)
        else:
            payload = run_compare(args)
        text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    except (DepthError, RegionError, OSError, ValueError) as exc:
        print(f"pil_depth: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
