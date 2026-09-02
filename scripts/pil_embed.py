#!/usr/bin/env python
"""Semantic embedding fingerprints: does this look like the same THING?

The perceptual hashes in this repository identify the same *picture* -- a
rescale or re-encode of one photo. What they cannot do is connect two
different photographs of the same subject: a hash of an object from one
angle shares nothing with a hash from another. This tool computes a
descriptor in a pinned ONNX vision model's output space, so two fingerprints
compare by cosine similarity across photos, sessions, and days.

Everything about the claim is pinned and echoed so payloads are only ever
compared when they are actually comparable:

*   The model file is caller-supplied (``--model`` or the
    ``PIL_AGENT_EMBED_MODEL`` environment variable) and its sha256 is
    recorded in every payload. ``compare`` REFUSES two fingerprints whose
    model hashes differ -- vectors from different models share no geometry,
    and comparing them silently would be fabrication.
*   Preprocessing is a NAMED PROFILE (``--preprocessing`` or the
    ``PIL_AGENT_EMBED_PREPROCESSING`` environment variable) whose full
    numeric spec is echoed in parameters, so a fingerprint means the same
    thing on every run of this tool version. A model must be fed the
    preprocessing it was trained with -- ``imagenet`` for ImageNet
    classifiers such as mobilenetv2-12, ``clip`` for CLIP-family visual
    encoders. The wrong profile does NOT fail loudly: measured, it still
    produces well-formed vectors that still separate, just with a
    materially narrower margin (see the control in
    runs/2026-09-02-clip-embedding-discrimination/), which is precisely why
    the profile is named in every payload rather than inferred.
    ``compare`` REFUSES payloads whose preprocessing specs differ.
*   The stored vector is L2-normalised and rounded to 6 decimals; cosine is
    computed FROM THE STORED VALUES, so comparing two saved payloads
    reproduces exactly what a fresh two-image run reports.

Requires the optional embedding extra (``uv sync --extra embedding``) and a
model file; missing either exits 2 with a named reason and empty stdout --
a clean UNMEASURABLE upstream, the same pattern as the Blender and
Tesseract tools.

Usage:
    python pil_embed.py embed "a.png" ["b.png"] --model mobilenetv2-12.onnx
    python pil_embed.py embed "a.png" --model clip-vit-b32-visual.onnx \\
        --preprocessing clip
    python pil_embed.py embed "a.png" --region "0.2,0.1,0.8,0.9"
    python pil_embed.py compare --fingerprint-a a.json --fingerprint-b b.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_common import load_rgba_straight  # noqa: E402
from pil_region import (  # noqa: E402
    RegionError,
    parse_fractional_bbox,
    rect_to_fractional,
    resolve_pixel_rect,
)

TOOL_VERSION = "0.6.0"

MODEL_ENV_VAR = "PIL_AGENT_EMBED_MODEL"
PREPROCESSING_ENV_VAR = "PIL_AGENT_EMBED_PREPROCESSING"

# A vision model only means anything when fed the preprocessing it was
# trained with, and the numbers differ per model family: ImageNet
# classifiers take the torchvision mean/std after a 256-shortest-side
# resize, CLIP takes its own mean/std after a 224-shortest-side BICUBIC
# resize. Feeding one to the other degrades quietly rather than failing --
# the measured control in runs/2026-09-02-clip-embedding-discrimination/
# still separated its pair families, on a margin cut by ~36% -- so the
# profile is named in the payload and its spec is the comparability key.
PREPROCESSING_PROFILES = {
    "imagenet": {
        "resize_shortest_side": 256,
        "resample": "lanczos",
        "center_crop": 224,
        "scale": "1/255",
        "normalize_mean": [0.485, 0.456, 0.406],
        "normalize_std": [0.229, 0.224, 0.225],
        "layout": "NCHW",
        "dtype": "float32",
    },
    "clip": {
        "resize_shortest_side": 224,
        "resample": "bicubic",
        "center_crop": 224,
        "scale": "1/255",
        "normalize_mean": [0.48145466, 0.4578275, 0.40821073],
        "normalize_std": [0.26862954, 0.26130258, 0.27577711],
        "layout": "NCHW",
        "dtype": "float32",
    },
}

DEFAULT_PREPROCESSING_PROFILE = "imagenet"

# Kept as the historical name for the default profile's spec: payloads
# written before profiles existed carry exactly this dict.
PREPROCESSING = PREPROCESSING_PROFILES[DEFAULT_PREPROCESSING_PROFILE]

RESAMPLE_FILTERS = {"lanczos": "LANCZOS", "bicubic": "BICUBIC"}

VECTOR_DECIMALS = 6

MOBILENET_V2_12_SHA256 = (
    "c0c3f76d93fa3fd6580652a45618618a220fced18babf65774ed169de0432ad5"
)
CLIP_VIT_B32_VISUAL_SHA256 = (
    "06395063c0a5c28b1a8d4bd585261501a878c8f52d1216db6c4cbb651f7c13f1"
)

# What the tool is allowed to advertise is a property of the MODEL, not of
# the code: each entry below is the verdict of a discrimination gate run
# whose evidence lives under runs/. A model with no gate gets no claim.
GATE_VERDICTS = {
    MOBILENET_V2_12_SHA256: (
        "The discrimination gate (runs/2026-08-31-embedding-discrimination/) "
        "ADVERTISES exactly one capability for this model "
        "(mobilenetv2-12, imagenet preprocessing): robust same-image "
        "identification. Measured on real photographs: perturbed copies "
        "(rescale, JPEG re-encode, 75% crop, 5 degree rotation) scored "
        "cosine >= 0.9051 while every unrelated pair scored <= 0.4701 -- "
        "and the crop and rotation cases defeat dhash (Hamming 18 and 9) "
        "while the embedding holds. Same-venue or same-object matching "
        "ACROSS different photographs is DEMOTED: the same-venue band "
        "[0.4558, 0.6263] overlaps the unrelated maximum, so a mid-band "
        "cosine must never be read as a 'same place' or 'same thing' "
        "verdict. The raw number is still reported for ranking."
    ),
    CLIP_VIT_B32_VISUAL_SHA256: (
        "The discrimination gate "
        "(runs/2026-09-02-clip-embedding-discrimination/) ADVERTISES two "
        "capabilities for this model (CLIP ViT-B/32 visual encoder, clip "
        "preprocessing), measured on the same 13-photograph corpus as the "
        "mobilenetv2-12 gate. (1) Same-image identification: perturbed "
        "copies (rescale, JPEG re-encode, 75% crop, 5 degree rotation) "
        "scored cosine >= 0.8762 against a related-pair maximum of 0.7535. "
        "(2) Same-venue / related-scene RANKING, which mobilenetv2-12 "
        "could not do: photographs of one venue scored [0.6285, 0.7535] "
        "with every unrelated pair at <= 0.5118 -- full separation where "
        "the older model's bands overlapped. Both margins are narrow "
        "(0.1227 and 0.1167) on small families (n=4/5/8), so the ordering "
        "is what was demonstrated, NOT a threshold: rank candidates by "
        "cosine, do not read a verdict off a single value near a band "
        "edge. CLIP compresses the whole scale upward, so mobilenetv2-12 "
        "numbers do not transfer -- 0.51 is unrelated here and would have "
        "been a top same-venue score there."
    ),
}

GATE_UNGATED = (
    "This model file has NOT been discrimination-gated in this repository: "
    "no capability is advertised for it and no threshold from another "
    "model's gate transfers to it. Cosine values are reported as an "
    "ungated ranking signal only. To gate a model, follow the procedure "
    "recorded under runs/ -- perturbation, related and unrelated pair "
    "families on real input -- and read the separation before believing "
    "any verdict."
)

_LIMITS_HEAD = (
    "A fingerprint is a descriptor in the pinned ONNX model's OUTPUT space; "
    "what 'similar' means is inherited from that model's training, not from "
    "this repository. cosine_similarity is a relative retrieval signal for "
    "ranking candidates -- it is NOT calibrated here, carries no "
    "authoritative decision threshold, and a high value is never proof of "
    "identity, provenance, or shared origin."
)

_LIMITS_TAIL = [
    "Fingerprints are comparable ONLY under the same model file and "
    "preprocessing: compare refuses on model sha256 or preprocessing "
    "mismatch rather than producing a number that means nothing. The model "
    "hash in the engine block is the comparability key, and the named "
    "preprocessing profile must be the one the model was trained with. A "
    "mismatched profile is SILENT: measured on the gate corpus, running "
    "CLIP under imagenet preprocessing still separated the pair families, "
    "but the related-vs-unrelated margin fell from 0.1167 to 0.0741. "
    "Nothing in a payload reveals a wrong-profile run except the recorded "
    "profile name, so check it before trusting a comparison.",
    "Determinism is scoped like the Blender and Tesseract tools: same "
    "model file, same onnxruntime build, same machine reproduces the "
    "payload byte-for-byte (the session is pinned to the CPU provider with "
    "single-threaded execution). Cross-machine bit-identity is NOT "
    "claimed; cosine on stored rounded vectors IS reproducible anywhere "
    "from the payloads alone.",
    "The stored vector is L2-normalised and rounded to 6 decimals, and "
    "similarity is computed from those stored values, so two saved "
    "payloads compare identically to a fresh two-image run. The rounding "
    "bounds any cosine error at well below 1e-4 -- negligible against the "
    "uncalibrated nature of the signal itself.",
    "This tool reads the composited full frame (or --region crop). It does "
    "not segment the subject: background, lighting and framing all "
    "influence the descriptor. For an object on a backdrop, scope with "
    "--region (or crop via the foreground bbox that pil_image_analyze "
    "reports) before fingerprinting.",
]


def interpretation_limits(model_sha256):
    """Limits for this payload, with the gate verdict for THIS model."""
    return [
        _LIMITS_HEAD,
        GATE_VERDICTS.get(model_sha256, GATE_UNGATED),
        *_LIMITS_TAIL,
    ]


class EmbedError(Exception):
    """Tool-level failure with a caller-facing reason."""


def _load_runtime():
    try:
        import onnxruntime  # noqa: PLC0415
    except ImportError as exc:
        raise EmbedError(
            "onnxruntime is not installed; install the embedding extra "
            "(uv sync --extra embedding)"
        ) from exc
    return onnxruntime


def _resolve_model(explicit):
    path = explicit or os.environ.get(MODEL_ENV_VAR)
    if not path:
        raise EmbedError(
            f"no embedding model given; pass --model or set {MODEL_ENV_VAR} "
            "to an ONNX vision model file (e.g. mobilenetv2-12.onnx)"
        )
    model_path = Path(path)
    if not model_path.is_file():
        raise EmbedError(f"embedding model not found: {model_path}")
    return model_path


def _resolve_preprocessing(explicit):
    name = explicit or os.environ.get(PREPROCESSING_ENV_VAR) or (
        DEFAULT_PREPROCESSING_PROFILE
    )
    if name not in PREPROCESSING_PROFILES:
        raise EmbedError(
            f"unknown preprocessing profile {name!r}; choose one of "
            f"{', '.join(sorted(PREPROCESSING_PROFILES))} and match it to "
            "the preprocessing the model was trained with"
        )
    return name, PREPROCESSING_PROFILES[name]


def _check_model_input(session, spec):
    """Refuse a profile whose crop cannot be what this model declares.

    Catches the loud half of a profile/model mismatch -- a 224 profile on a
    299 or 384 input. The quiet half (right size, wrong mean/std) is
    undetectable here, which is why the profile name is recorded.
    """
    shape = session.get_inputs()[0].shape
    static = [d for d in shape if isinstance(d, int)]
    crop = spec["center_crop"]
    if len(static) >= 2 and static[-1] != crop:
        raise EmbedError(
            f"preprocessing produces {crop}x{crop} but the model declares "
            f"input shape {shape}; the profile does not match this model"
        )


def _preprocess(rgb, spec):
    width, height = rgb.size
    short = spec["resize_shortest_side"]
    if width <= height:
        new_size = (short, max(1, round(height * short / width)))
    else:
        new_size = (max(1, round(width * short / height)), short)
    from PIL import Image  # noqa: PLC0415

    resample = getattr(Image, RESAMPLE_FILTERS[spec["resample"]])
    resized = rgb.resize(new_size, resample)
    crop = spec["center_crop"]
    left = (resized.width - crop) // 2
    top = (resized.height - crop) // 2
    cropped = resized.crop((left, top, left + crop, top + crop))
    arr = np.asarray(cropped, dtype=np.float32) / 255.0
    mean = np.asarray(spec["normalize_mean"], dtype=np.float32)
    std = np.asarray(spec["normalize_std"], dtype=np.float32)
    arr = (arr - mean) / std
    return arr.transpose(2, 0, 1)[None, ...]


def _fingerprint(session, input_name, rgb, spec):
    tensor = _preprocess(rgb, spec)
    output = session.run(None, {input_name: tensor})[0]
    vector = np.asarray(output, dtype=np.float64).ravel()
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        raise EmbedError("model returned a zero vector; cannot fingerprint")
    unit = np.round(vector / norm, VECTOR_DECIMALS)
    return {
        "dim": int(vector.size),
        "l2_norm": round(norm, 6),
        "unit_values": [float(v) for v in unit],
    }


def _cosine(values_a, values_b):
    a = np.asarray(values_a, dtype=np.float64)
    b = np.asarray(values_b, dtype=np.float64)
    if a.shape != b.shape:
        raise EmbedError(f"fingerprint dimensions differ: {a.size} vs {b.size}")
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        raise EmbedError("zero-norm fingerprint; cosine undefined")
    return round(float(np.dot(a, b) / denominator), 6)


def _analyse_image(path, session, input_name, region_box, spec):
    composited_rgb, _straight, _alpha = load_rgba_straight(path)
    frame_size = composited_rgb.size
    region_block = None
    subject = composited_rgb
    if region_box is not None:
        rect = resolve_pixel_rect(region_box, frame_size)
        subject = composited_rgb.crop(rect)
        region_block = {
            "requested_fractional": region_box,
            "resolved_pixel_rect": list(rect),
            "resolved_fractional": rect_to_fractional(rect, frame_size),
            "space": "frame",
        }
    return {
        "path": str(path),
        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "size": list(frame_size),
        "region": region_block,
        "fingerprint": _fingerprint(session, input_name, subject, spec),
    }


def run_embed(args):
    onnxruntime = _load_runtime()
    model_path = _resolve_model(args.model)
    profile_name, spec = _resolve_preprocessing(args.preprocessing)
    region_box = (
        parse_fractional_bbox(args.region) if args.region is not None else None
    )

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = onnxruntime.InferenceSession(
        str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    input_name = session.get_inputs()[0].name
    _check_model_input(session, spec)

    images = {
        "a": _analyse_image(args.image_a, session, input_name, region_box, spec)
    }
    diff = None
    if args.image_b:
        images["b"] = _analyse_image(
            args.image_b, session, input_name, region_box, spec
        )
        diff = {
            "cosine_similarity": _cosine(
                images["a"]["fingerprint"]["unit_values"],
                images["b"]["fingerprint"]["unit_values"],
            )
        }

    model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
    return {
        "tool": "pil_embed",
        "version": TOOL_VERSION,
        "command": "embed",
        "engine": {
            "runtime": f"onnxruntime {onnxruntime.__version__}",
            "providers": ["CPUExecutionProvider"],
            "model_file": model_path.name,
            "model_sha256": model_sha256,
            "model_gated": model_sha256 in GATE_VERDICTS,
            "output_dim": images["a"]["fingerprint"]["dim"],
        },
        "parameters": {
            "region": region_box,
            "preprocessing": spec,
            "preprocessing_profile": profile_name,
            "vector_decimals": VECTOR_DECIMALS,
        },
        "images": images,
        "diff": diff,
        "flags": [] if model_sha256 in GATE_VERDICTS else ["model_not_gated"],
        "interpretation_limits": interpretation_limits(model_sha256),
    }


def _load_fingerprint_payload(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise EmbedError(f"cannot read fingerprint file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EmbedError(f"fingerprint file is not valid JSON: {exc}") from exc
    if payload.get("tool") != "pil_embed" or "images" not in payload:
        raise EmbedError(f"{path} is not a pil_embed payload")
    image = payload["images"].get("a")
    if not image or "fingerprint" not in image:
        raise EmbedError(f"{path} carries no fingerprint under images.a")
    return payload, image


def run_compare(args):
    payload_a, image_a = _load_fingerprint_payload(args.fingerprint_a)
    payload_b, image_b = _load_fingerprint_payload(args.fingerprint_b)

    model_a = payload_a["engine"]["model_sha256"]
    model_b = payload_b["engine"]["model_sha256"]
    if model_a != model_b:
        raise EmbedError(
            "fingerprints are not comparable: model sha256 differs "
            f"({model_a[:12]}... vs {model_b[:12]}...); vectors from "
            "different models share no geometry"
        )
    if payload_a["parameters"]["preprocessing"] != payload_b["parameters"]["preprocessing"]:
        raise EmbedError(
            "fingerprints are not comparable: preprocessing parameters differ "
            f"({payload_a['parameters'].get('preprocessing_profile', 'unnamed')}"
            f" vs {payload_b['parameters'].get('preprocessing_profile', 'unnamed')})"
        )

    return {
        "tool": "pil_embed",
        "version": TOOL_VERSION,
        "command": "compare",
        "engine": payload_a["engine"],
        "comparison": {
            "cosine_similarity": _cosine(
                image_a["fingerprint"]["unit_values"],
                image_b["fingerprint"]["unit_values"],
            ),
            "image_a_sha256": image_a["sha256"],
            "image_b_sha256": image_b["sha256"],
            "same_image_bytes": image_a["sha256"] == image_b["sha256"],
        },
        "flags": [] if model_a in GATE_VERDICTS else ["model_not_gated"],
        "interpretation_limits": interpretation_limits(model_a),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Semantic embedding fingerprints from a pinned ONNX "
        "vision model: cross-photo similarity by cosine, refused whenever "
        "two fingerprints are not actually comparable."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    embed_parser = sub.add_parser("embed", help="fingerprint one image, or two plus their cosine")
    embed_parser.add_argument("image_a", help="image to fingerprint")
    embed_parser.add_argument("image_b", nargs="?", help="optional second image")
    embed_parser.add_argument(
        "--model",
        default=None,
        help=f"ONNX vision model file (default: ${MODEL_ENV_VAR})",
    )
    embed_parser.add_argument(
        "--preprocessing",
        default=None,
        choices=sorted(PREPROCESSING_PROFILES),
        help="preprocessing profile matching the model's training "
        f"(default: ${PREPROCESSING_ENV_VAR}, else "
        f"{DEFAULT_PREPROCESSING_PROFILE}); 'imagenet' for ImageNet "
        "classifiers, 'clip' for CLIP-family visual encoders",
    )
    embed_parser.add_argument(
        "--region",
        default=None,
        help="fractional bbox 'L,T,R,B' (same parser as pil_crop.py), "
        "frame-relative, applied to BOTH images before fingerprinting",
    )

    compare_parser = sub.add_parser(
        "compare", help="cosine between two stored pil_embed payloads"
    )
    compare_parser.add_argument("--fingerprint-a", required=True)
    compare_parser.add_argument("--fingerprint-b", required=True)

    args = parser.parse_args(argv)

    try:
        if args.command == "embed":
            payload = run_embed(args)
        else:
            payload = run_compare(args)
    except (EmbedError, RegionError, OSError) as exc:
        print(f"pil_embed: {exc}", file=sys.stderr)
        return 2

    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
