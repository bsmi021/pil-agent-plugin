#!/usr/bin/env python
"""Word-level OCR via the Tesseract engine, with frame-mapped coordinates.

Text is the one semantic signal that is genuinely checkable: a transcription
either matches the pixels or it does not, and a reader can re-crop the exact
region this tool cites (`pil_crop`, same parser and rounding) and look. This
tool shells out to a system-installed ``tesseract`` binary -- the same
pattern as pil_blender_mesh.py's Blender probe: no Python OCR dependency,
and when the binary cannot be found the tool exits 2 with empty stdout and a
named reason, a clean UNMEASURABLE upstream, never a traceback.

What it reports, and what it refuses to overclaim:

*   Per-word text, engine confidence, and bounding boxes in BOTH full-frame
    pixel and fractional coordinates -- even under ``--region``, boxes are
    mapped back to the source frame so they remain sealable and croppable
    against the original file.
*   Per-line assembly (Tesseract's own block/paragraph/line grouping) and a
    ``full_text`` join, so a caller can read the page top-to-bottom.
*   ``--claims-out`` writes a claims file compatible with
    ``pil_semantic_record.py seal``: each accepted line becomes one
    ``text_transcription`` claim whose evidence names the engine and its
    line confidence. OCR output is an engine ESTIMATE -- sealing it as a
    ``vision_claim`` is exactly right, and the confidence band mapping is a
    reporting convention echoed in parameters, not a calibrated threshold.

Determinism is scoped narrowly, like the Blender render tools: the same
image through the same Tesseract build on the same machine reproduces the
payload byte-for-byte; cross-machine or cross-version determinism is NOT
claimed, and the engine version is recorded in every payload so a reader
can tell when two payloads are comparable.

Usage:
    python pil_ocr.py "screenshot.png"
    python pil_ocr.py "photo.jpg" --region "0.24,0.17,0.48,0.23"
    python pil_ocr.py "photo.jpg" --claims-out claims.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_common import load_rgba_straight  # noqa: E402
from pil_region import (  # noqa: E402
    RegionError,
    parse_fractional_bbox,
    rect_to_fractional,
    resolve_pixel_rect,
)

TOOL_VERSION = "0.8.0"

DEFAULT_LANG = "eng"
DEFAULT_PSM = 3  # Tesseract's default: fully automatic page segmentation.

# Reporting conventions for mapping Tesseract's 0-100 word/line confidence
# to the semantic record's high/medium/low bands, echoed in parameters.
# These are conventions, not calibrated thresholds: no discrimination gate
# has validated them, and the raw engine confidences are always reported
# alongside so a caller who disagrees has the uninterpreted numbers.
CONFIDENCE_HIGH_MIN = 85.0
CONFIDENCE_MEDIUM_MIN = 60.0

# Lines below this mean engine confidence are excluded from --claims-out
# (never from the OCR payload itself). Same convention status as above.
DEFAULT_CLAIMS_MIN_CONFIDENCE = 60.0

INTERPRETATION_LIMITS = [
    "Every transcription here is a TESSERACT ENGINE ESTIMATE, not ground "
    "truth and not a pixel measurement: the engine can hallucinate words in "
    "texture, split or merge real ones, and misread stylised, neon, curved, "
    "handwritten or low-contrast text severely. Verify any transcription "
    "that matters by re-cropping its bbox_fractional with pil_crop.py (same "
    "parser and rounding) and looking.",
    "confidence is Tesseract's own 0-100 word/line figure, reported raw. It "
    "is NOT calibrated by this repository: no detection limit or error rate "
    "has been measured for it, and the high/medium/low bands used by "
    "--claims-out are reporting conventions echoed in parameters, nothing "
    "more.",
    "Word and line boxes are mapped to FULL-FRAME coordinates even when "
    "--region was used, so they remain croppable and sealable against the "
    "original file. bbox_fractional is frame-relative.",
    "Determinism is scoped narrowly, exactly like the Blender render tools: "
    "same image, same Tesseract build, same machine reproduces this payload "
    "byte-for-byte. Cross-machine and cross-version determinism is NOT "
    "claimed; the engine block records the version so a reader can tell "
    "when two payloads are comparable.",
    "--claims-out writes text_transcription claims for "
    "pil_semantic_record.py seal, excluding lines below "
    "claims_min_confidence. A sealed OCR claim is still a claim: sealing "
    "makes it attributable and checkable, not true, and its evidence field "
    "names the engine and confidence so a reader can weigh it.",
    "OCR reads the composited image (alpha over black). Text rendered in "
    "transparency alone, or legible only against a different backdrop, may "
    "not be found.",
]


def _find_tesseract(explicit):
    override = explicit or os.environ.get("PIL_AGENT_TESSERACT")
    if override:
        return str(Path(override)) if Path(override).is_file() else None
    found = shutil.which("tesseract")
    if found:
        return found
    if os.name == "nt":
        for variable, suffix in (
            ("ProgramFiles", "Tesseract-OCR"),
            ("ProgramFiles(x86)", "Tesseract-OCR"),
            ("LOCALAPPDATA", "Programs/Tesseract-OCR"),
            ("LOCALAPPDATA", "Tesseract-OCR"),
        ):
            root = os.environ.get(variable)
            if root:
                candidate = Path(root) / suffix / "tesseract.exe"
                if candidate.is_file():
                    return str(candidate)
    return None


def _engine_version(executable):
    result = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace", timeout=30,
    )
    # First line, e.g. "tesseract 5.3.4"; stderr on some builds.
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0] if text else "tesseract (version unreported)"


def _diagnose(executable, lang):
    engine = _engine_version(executable)
    result = subprocess.run(
        [executable, "--list-langs"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True, timeout=30,
    )
    languages = sorted({line.strip() for line in
                        (result.stdout + "\n" + result.stderr).splitlines()
                        if line.strip() and not line.startswith("List of")})
    missing = sorted(set(lang.split("+")) - set(languages))
    if missing:
        raise RuntimeError(
            f"Tesseract language data missing: {', '.join(missing)}; install "
            "the requested traineddata or set TESSDATA_PREFIX to its directory"
        )
    return {"tool": "pil_ocr", "command": "diagnose", "version": TOOL_VERSION,
            "python": sys.executable, "executable": executable, "engine": engine,
            "languages": languages, "requested_language": lang,
            "tessdata_prefix": os.environ.get("TESSDATA_PREFIX")}


def _confidence_band(conf):
    if conf >= CONFIDENCE_HIGH_MIN:
        return "high"
    if conf >= CONFIDENCE_MEDIUM_MIN:
        return "medium"
    return "low"


def _run_tesseract(executable, image, lang, psm):
    """Run tesseract TSV output over a PIL image, via a temporary PNG."""
    with tempfile.TemporaryDirectory(prefix="pil_ocr_") as tmp:
        input_path = Path(tmp) / "input.png"
        image.save(input_path)
        result = subprocess.run(
            [executable, str(input_path), "stdout", "-l", lang, "--psm", str(psm), "tsv"],
            capture_output=True,
            text=True,
            encoding="utf-8", errors="replace",
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"tesseract exited {result.returncode}: {result.stderr.strip()[:500]}"
        )
    return result.stdout


def _parse_tsv(tsv_text, origin, frame_size):
    """Parse Tesseract TSV into frame-mapped words.

    origin is the (left, top) of the OCR crop within the source frame, so
    every reported box is in full-frame coordinates regardless of --region.
    """
    origin_left, origin_top = origin
    frame_w, frame_h = frame_size
    words = []
    lines = iter(tsv_text.splitlines())
    header = next(lines, None)
    if header is None:
        return words
    columns = header.split("\t")
    index = {name: i for i, name in enumerate(columns)}
    required = {"level", "block_num", "par_num", "line_num", "word_num",
                "left", "top", "width", "height", "conf", "text"}
    if not required <= set(index):
        raise RuntimeError("tesseract TSV output is missing expected columns")
    for row in lines:
        fields = row.split("\t")
        if len(fields) != len(columns):
            continue
        if fields[index["level"]] != "5":
            continue
        text = fields[index["text"]]
        if not text.strip():
            continue
        conf = float(fields[index["conf"]])
        if conf < 0:
            continue
        left = int(fields[index["left"]]) + origin_left
        top = int(fields[index["top"]]) + origin_top
        right = left + int(fields[index["width"]])
        bottom = top + int(fields[index["height"]])
        words.append(
            {
                "text": text,
                "confidence": round(conf, 2),
                "block": int(fields[index["block_num"]]),
                "paragraph": int(fields[index["par_num"]]),
                "line": int(fields[index["line_num"]]),
                "word": int(fields[index["word_num"]]),
                "bbox_pixels": [left, top, right, bottom],
                "bbox_fractional": [
                    round(left / frame_w, 6),
                    round(top / frame_h, 6),
                    round(right / frame_w, 6),
                    round(bottom / frame_h, 6),
                ],
            }
        )
    return words


def _assemble_lines(words):
    """Group words into Tesseract's own reading-order lines."""
    grouped = {}
    for word in words:
        key = (word["block"], word["paragraph"], word["line"])
        grouped.setdefault(key, []).append(word)
    lines = []
    for key in sorted(grouped):
        members = sorted(grouped[key], key=lambda w: w["word"])
        confs = [w["confidence"] for w in members]
        left = min(w["bbox_pixels"][0] for w in members)
        top = min(w["bbox_pixels"][1] for w in members)
        right = max(w["bbox_pixels"][2] for w in members)
        bottom = max(w["bbox_pixels"][3] for w in members)
        frac = [
            min(w["bbox_fractional"][0] for w in members),
            min(w["bbox_fractional"][1] for w in members),
            max(w["bbox_fractional"][2] for w in members),
            max(w["bbox_fractional"][3] for w in members),
        ]
        lines.append(
            {
                "text": " ".join(w["text"] for w in members),
                "confidence_mean": round(sum(confs) / len(confs), 2),
                "confidence_min": round(min(confs), 2),
                "word_count": len(members),
                "bbox_pixels": [left, top, right, bottom],
                "bbox_fractional": frac,
            }
        )
    return lines


def _claims_from_lines(lines, min_confidence, engine):
    claims = []
    for line in lines:
        if line["confidence_mean"] < min_confidence:
            continue
        left, top, right, bottom = line["bbox_fractional"]
        # pil_semantic_record requires 0 <= L < R <= 1; clamp rounding spill
        # and skip degenerate boxes rather than emitting an invalid claim.
        left, top = max(0.0, left), max(0.0, top)
        right, bottom = min(1.0, right), min(1.0, bottom)
        if not (left < right and top < bottom):
            continue
        claims.append(
            {
                "kind": "text_transcription",
                "value": line["text"],
                "region_fractional": [left, top, right, bottom],
                "confidence": _confidence_band(line["confidence_mean"]),
                "evidence": (
                    f"{engine} line, mean word confidence "
                    f"{line['confidence_mean']} over {line['word_count']} word(s)"
                ),
            }
        )
    return claims


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Word-level OCR (Tesseract) with frame-mapped boxes and "
        "optional claims-file output for pil_semantic_record.py."
    )
    parser.add_argument("image", nargs="?", help="image file to read text from")
    parser.add_argument("--diagnose", action="store_true",
                        help="check executable and language data without an image")
    parser.add_argument(
        "--region",
        default=None,
        help="fractional bbox 'L,T,R,B' (same parser and rounding as "
        "pil_crop.py), frame-relative; OCR runs on the crop but every "
        "reported box is mapped back to full-frame coordinates",
    )
    parser.add_argument("--lang", default=DEFAULT_LANG, help=f"Tesseract language (default {DEFAULT_LANG})")
    parser.add_argument(
        "--psm",
        type=int,
        default=DEFAULT_PSM,
        help=f"Tesseract page segmentation mode (default {DEFAULT_PSM})",
    )
    parser.add_argument(
        "--tesseract-executable",
        default=None,
        help="path to the tesseract binary (else PIL_AGENT_TESSERACT, PATH, "
        "then standard Windows install directories)",
    )
    parser.add_argument(
        "--claims-out",
        default=None,
        help="also write a pil_semantic_record claims file with one "
        "text_transcription claim per accepted line",
    )
    parser.add_argument(
        "--claims-min-confidence",
        type=float,
        default=DEFAULT_CLAIMS_MIN_CONFIDENCE,
        help="exclude lines below this mean confidence from --claims-out "
        f"(default {DEFAULT_CLAIMS_MIN_CONFIDENCE}; the OCR payload itself "
        "always reports every word)",
    )
    args = parser.parse_args(argv)
    if not args.diagnose and args.image is None:
        parser.error("image is required unless --diagnose is used")

    executable = _find_tesseract(args.tesseract_executable)
    if executable is None:
        print(
            "pil_ocr: tesseract executable not found (explicit flag / "
            "PIL_AGENT_TESSERACT override, else PATH and Windows install "
            "directories). Use --tesseract-executable with a file path. "
            "Windows: winget install --id UB-Mannheim.TesseractOCR --exact; "
            "Debian/Ubuntu: apt-get install tesseract-ocr.",
            file=sys.stderr,
        )
        return 2

    if args.diagnose:
        try:
            payload = _diagnose(executable, args.lang)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            print(f"pil_ocr: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    try:
        region_box = parse_fractional_bbox(args.region) if args.region is not None else None
    except RegionError as exc:
        print(f"pil_ocr: {exc}", file=sys.stderr)
        return 2

    try:
        composited_rgb, _straight, _alpha = load_rgba_straight(args.image)
    except OSError as exc:
        print(f"pil_ocr: cannot read image: {exc}", file=sys.stderr)
        return 2

    frame_size = composited_rgb.size
    region_block = None
    origin = (0, 0)
    subject = composited_rgb
    if region_box is not None:
        try:
            rect = resolve_pixel_rect(region_box, frame_size)
        except RegionError as exc:
            print(f"pil_ocr: {exc}", file=sys.stderr)
            return 2
        subject = composited_rgb.crop(rect)
        origin = (rect[0], rect[1])
        region_block = {
            "requested_fractional": region_box,
            "resolved_pixel_rect": list(rect),
            "resolved_fractional": rect_to_fractional(rect, frame_size),
            "space": "frame",
        }

    try:
        engine = _engine_version(executable)
        tsv = _run_tesseract(executable, subject, args.lang, args.psm)
        words = _parse_tsv(tsv, origin, frame_size)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"pil_ocr: {exc}", file=sys.stderr)
        return 2

    lines = _assemble_lines(words)
    flags = []
    if not words:
        flags.append("no_text_found")

    claims = _claims_from_lines(lines, args.claims_min_confidence, engine)
    if args.claims_out is not None:
        if claims:
            Path(args.claims_out).write_text(
                json.dumps({"claims": claims}, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            flags.append("claims_out_skipped_no_accepted_lines")

    payload = {
        "tool": "pil_ocr",
        "version": TOOL_VERSION,
        "engine": engine,
        "parameters": {
            "lang": args.lang,
            "psm": args.psm,
            "region": region_box,
            "claims_min_confidence": args.claims_min_confidence,
            "confidence_bands": {
                "high_min": CONFIDENCE_HIGH_MIN,
                "medium_min": CONFIDENCE_MEDIUM_MIN,
            },
        },
        "image": {
            "path": str(args.image),
            "size": list(frame_size),
        },
        "region": region_block,
        "words": words,
        "lines": lines,
        "full_text": "\n".join(line["text"] for line in lines),
        "claims_emitted": len(claims) if args.claims_out is not None else None,
        "flags": flags,
        "interpretation_limits": INTERPRETATION_LIMITS,
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
