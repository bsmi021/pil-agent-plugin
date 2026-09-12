"""Native-resolution perceptual differences, alpha changes and separate regions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import numpy as np
from PIL import Image
from pil_color import rgb_ciede2000
from pil_io import emit, positive, save_png
from pil_normalize import normalize_image
from pil_mask import read_mask

TOOL_VERSION = "0.9.0"
METRIC_VERSION = "native-de00-alpha-v1"


def rgba_array(image):
    return np.asarray(image.convert("RGBA"), dtype=np.float64)


def appearance(array):
    return array[:, :, :3] * array[:, :, 3:4] / 255.0


def difference_arrays(a, b, support=None, delta_e_threshold=2.0, alpha_threshold=1.0):
    positive(delta_e_threshold, "delta-e threshold", True)
    positive(alpha_threshold, "alpha threshold", True)
    if a.shape != b.shape:
        raise ValueError("image dimensions must match; use explicit registration first")
    if support is None:
        support = np.ones(a.shape[:2], dtype=bool)
    if not support.any():
        raise ValueError("comparison has no common support")
    delta = np.zeros(a.shape[:2], dtype=float)
    # Bound intermediate Lab/CIEDE2000 arrays on large frames.
    for start in range(0, a.shape[0], 128):
        delta[start : start + 128] = rgb_ciede2000(
            appearance(a[start : start + 128]), appearance(b[start : start + 128])
        )
    alpha = np.abs(a[:, :, 3] - b[:, :, 3])
    changed = ((delta > delta_e_threshold) | (alpha > alpha_threshold)) & support
    values = delta[support]
    return (
        {
            "delta_e_mean": round(float(values.mean()), 8),
            "delta_e_max": round(float(values.max()), 8),
            "delta_e_p95": round(float(np.percentile(values, 95)), 8),
            "alpha_mean_absolute_delta": round(float(alpha[support].mean()), 8),
            "changed_area_fraction": round(float(changed.sum() / support.sum()), 8),
            "changed_pixels": int(changed.sum()),
            "compared_pixels": int(support.sum()),
        },
        delta,
        changed,
    )


def regions(mask, delta, min_pixels=1):
    # Scanline union-find: unions overlapping runs in neighboring rows (4-connected).
    parents, records, previous = [], [], []

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for y, row in enumerate(mask):
        edges = np.diff(np.r_[False, row, False].astype(np.int8))
        runs = list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))
        current = []
        j = 0
        for left, right in runs:
            index = len(parents)
            parents.append(index)
            records.append(
                (
                    int(left),
                    y,
                    int(right),
                    y + 1,
                    int(right - left),
                    float(delta[y, left:right].max()),
                )
            )
            while j < len(previous) and previous[j][1] <= left:
                j += 1
            k = j
            while k < len(previous) and previous[k][0] < right:
                parents[root(previous[k][2])] = root(index)
                k += 1
            current.append((left, right, index))
        previous = current
    groups = {}
    for i, (left, top, right, bottom, count, peak) in enumerate(records):
        key = root(i)
        if key not in groups:
            groups[key] = [left, top, right, bottom, count, peak]
        else:
            g = groups[key]
            g[:] = [
                min(g[0], left),
                min(g[1], top),
                max(g[2], right),
                max(g[3], bottom),
                g[4] + count,
                max(g[5], peak),
            ]
    result = [
        {"bbox_pixels": g[:4], "changed_pixels": g[4], "peak_delta_e": round(g[5], 6)}
        for g in groups.values()
        if g[4] >= min_pixels
    ]
    return sorted(result, key=lambda r: (-r["changed_pixels"], r["bbox_pixels"]))


def compare_images(
    image_a,
    image_b,
    *,
    input_mode="stored",
    mask_a=None,
    mask_b=None,
    delta_e_threshold=2.0,
    alpha_threshold=1.0,
    min_pixels=1,
    ssim=False,
):
    if min_pixels < 1:
        raise ValueError("min-pixels must be positive")
    a, ma = normalize_image(image_a, input_mode)
    b, mb = normalize_image(image_b, input_mode)
    if a.size != b.size:
        raise ValueError("image dimensions must match")
    support = np.ones((a.height, a.width), dtype=bool)
    for path, source in [(mask_a, image_a), (mask_b, image_b)]:
        if path:
            selection, _ = read_mask(path, source, input_mode)
            support &= selection
    metrics, delta, changed = difference_arrays(
        rgba_array(a), rgba_array(b), support, delta_e_threshold, alpha_threshold
    )
    result = {
        "tool": "pil_diff_regions",
        "version": TOOL_VERSION,
        "metric_version": METRIC_VERSION,
        "inputs": {"a": ma, "b": mb},
        "parameters": {
            "delta_e_threshold": delta_e_threshold,
            "alpha_threshold": alpha_threshold,
            "min_pixels": min_pixels,
        },
        **metrics,
        "regions": regions(changed, delta, min_pixels),
        "flags": ["uncalibrated_thresholds"],
        "interpretation_limits": [
            "Delta E describes sRGB appearance composited on black; alpha differences are measured separately.",
            "Stored mode assumes sRGB channels; use display mode for tagged inputs.",
            "Selection support is the intersection; excluded pixels are not assessed.",
        ],
    }
    if ssim:
        if mask_a or mask_b:
            raise ValueError(
                "standard SSIM requires a full rectangular frame; masked SSIM is not implemented"
            )
        try:
            from skimage.metrics import structural_similarity
        except ImportError as exc:
            raise ValueError("SSIM requires the comparison extra") from exc
        if min(a.size) < 7:
            raise ValueError("SSIM requires dimensions of at least 7 pixels")
        result["ssim"] = float(
            structural_similarity(
                appearance(rgba_array(a)),
                appearance(rgba_array(b)),
                data_range=255,
                channel_axis=2,
            )
        )
    return result, delta, changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_a")
    parser.add_argument("image_b")
    parser.add_argument("--input-mode", choices=["stored", "display"], default="stored")
    parser.add_argument("--mask-a")
    parser.add_argument("--mask-b")
    parser.add_argument("--delta-e-threshold", type=float, default=2.0)
    parser.add_argument("--alpha-threshold", type=float, default=1.0)
    parser.add_argument("--min-pixels", type=int, default=1)
    parser.add_argument("--ssim", action="store_true")
    parser.add_argument(
        "--output-dir",
        help="new directory for native delta array, heatmap, mask and paired crops",
    )
    parser.add_argument("--max-crops", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.max_crops < 0:
            raise ValueError("max-crops must be nonnegative")
        result, delta, changed = compare_images(
            args.image_a,
            args.image_b,
            input_mode=args.input_mode,
            mask_a=args.mask_a,
            mask_b=args.mask_b,
            delta_e_threshold=args.delta_e_threshold,
            alpha_threshold=args.alpha_threshold,
            min_pixels=args.min_pixels,
            ssim=args.ssim,
        )
        if args.output_dir:
            out = Path(args.output_dir)
            out.mkdir()
            np.save(out / "delta-e.npy", delta, allow_pickle=False)
            heat = np.zeros((*delta.shape, 3), dtype="uint8")
            heat[:, :, 0] = np.minimum(delta / 20, 1) * 255
            save_png(out / "heatmap.png", Image.fromarray(heat))
            save_png(
                out / "changed.png", Image.fromarray(changed.astype("uint8") * 255)
            )
            a, _ = normalize_image(args.image_a, args.input_mode)
            b, _ = normalize_image(args.image_b, args.input_mode)
            crops = []
            for i, region in enumerate(result["regions"][: args.max_crops], 1):
                box = region["bbox_pixels"]
                for label, image in [("a", a), ("b", b)]:
                    crop = image.crop(box)
                    target = out / f"{i}-{label}.png"
                    save_png(
                        target,
                        crop.resize(
                            (crop.width * 4, crop.height * 4), Image.Resampling.NEAREST
                        ),
                    )
                    crops.append(str(target))
            result["artifacts"] = {
                "delta_e": str(out / "delta-e.npy"),
                "heatmap": str(out / "heatmap.png"),
                "changed_mask": str(out / "changed.png"),
                "crops": crops,
                "heatmap_saturation_delta_e": 20,
                "crop_upscale": 4,
            }
        emit(result)
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"pil_diff_regions: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
