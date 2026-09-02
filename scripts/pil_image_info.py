#!/usr/bin/env python
"""Report file-declared image facts that a resampled vision input cannot retain.

The tool deliberately reports container and decoder observations only.  It does
not measure palettes, similarity, structure, image quality, provenance, or
colour correctness.
"""

import argparse
import hashlib
import io
import json
import math
import sys
import warnings
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import ExifTags, Image, ImageCms


TOOL_VERSION = "0.7.0"

# This is an explicit decoder-mode table, rather than a claim inferred from a
# file's contents.  Modes Pillow does not document here intentionally report
# null: the tool must not guess a bit depth.
MODE_BIT_DEPTHS = {
    "1": 1,
    "L": 8,
    "LA": 8,
    "P": 8,
    "PA": 8,
    "RGB": 8,
    "RGBA": 8,
    "RGBX": 8,
    "CMYK": 8,
    "YCbCr": 8,
    "LAB": 8,
    "HSV": 8,
    "I;16": 16,
    "I;16B": 16,
    "I;16L": 16,
    "I;16N": 16,
    "I": 32,
    "F": 32,
}

INTERPRETATION_LIMITS = [
    "`uses_transparency` follows `pil_common.load_rgb_alpha`'s rule: an entirely opaque alpha channel carries no foreground information and reports false; `has_alpha_channel` reports an alpha channel or a PALETTE transparency key. Colour-key transparency in RGB/L images is reported only by `transparency_key`.",
    "An ICC profile is reported only as file metadata: this tool does not validate it, infer a colour space from it, convert pixels, or establish that the image is correctly colour-managed.",
    "EXIF values are producer-supplied file claims, not verified facts about the image, camera, subject, date, or provenance. Absent metadata is null; unreadable metadata is null plus a flag.",
    "`readable` means Pillow parsed the image header; pixel integrity is unverified unless alpha facts require pixels to be decoded. Dimensions, frame count, and DPI describe decoded file/header metadata. DPI is not a physical measurement, and none of these fields establishes image quality, provenance, or whether an earlier system resampled the image.",
    "`bit_depth_per_channel` describes Pillow's decoder mode, not necessarily the image file's stored bit depth.",
    "`sha256` identifies the file bytes, not the depicted image: visually identical files written by different encoders can have different hashes.",
]


def _bytes_descriptor(value):
    """Represent opaque bytes without pretending they are readable metadata.

    Bytes can be invalid text or proprietary payloads.  A length and digest let
    callers identify the exact bytes while this tool refuses to invent a text
    interpretation for them.
    """
    raw = bytes(value)
    return {
        "type": "bytes",
        "length": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _serialise_exif_value(value):
    """Render supported EXIF values deterministically without lossy decoding.

    EXIF is producer metadata and can contain arbitrary bytes.  This function
    keeps numbers and rationals machine-readable, describes undecodable bytes,
    and refuses unknown objects so the caller receives an unreadable-metadata
    flag instead of a plausible but fabricated value.
    """
    if value is None or isinstance(value, (bool, int)):
        return value
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        return [int(value.numerator), int(value.denominator)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("EXIF float is not finite")
        return value
    if isinstance(value, str):
        value.encode("utf-8", "strict")
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return bytes(value).decode("utf-8", "strict")
        except UnicodeDecodeError:
            return _bytes_descriptor(value)
    if isinstance(value, (tuple, list)):
        return [_serialise_exif_value(item) for item in value]
    raise TypeError(f"unsupported EXIF value type: {type(value).__name__}")


def _serialise_transparency_key(value):
    """Keep transparency data numeric so PNG alpha tables never become text.

    This field describes decoder/container transparency rather than EXIF.  In
    particular, a P-mode tRNS table is a sequence of alpha bytes even when its
    byte values happen to form valid UTF-8, so decoding it as metadata text
    would make the JSON schema depend on the table's content.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return [int(byte) for byte in bytes(value)]
    if isinstance(value, (tuple, list)):
        return [int(item) for item in value]
    raise TypeError(f"unsupported transparency value type: {type(value).__name__}")


def _serialise_dpi(value):
    """Keep DPI as two rational pairs across all decoder/container value types.

    Pillow exposes DPI as integers, floats, or TIFF rational objects depending
    on the container.  Representing every axis as ``[numerator, denominator]``
    gives this metadata field one content-independent JSON schema without
    rounding a producer-supplied resolution into a physical measurement.
    """
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise TypeError("DPI must contain exactly two axes")

    def rational_pair(axis):
        if isinstance(axis, bool):
            raise TypeError("DPI axis must be numeric")
        if hasattr(axis, "numerator") and hasattr(axis, "denominator"):
            fraction = Fraction(int(axis.numerator), int(axis.denominator))
        elif isinstance(axis, int):
            fraction = Fraction(axis, 1)
        elif isinstance(axis, float) and math.isfinite(axis):
            fraction = Fraction(str(axis))
        else:
            raise TypeError(f"unsupported DPI axis type: {type(axis).__name__}")
        return [fraction.numerator, fraction.denominator]

    return [rational_pair(axis) for axis in value]


def _exif_entries(exif):
    """Report IFD0 plus parsed camera sub-IFDs without exposing file offsets.

    ExifOffset and GPSInfo are TIFF pointers, not producer claims.  Their
    target directories contain the actual camera claims, so nested keys carry
    an IFD prefix to retain their origin and to avoid collisions with IFD0.
    """
    pointer_tags = {ExifTags.IFD.Exif, ExifTags.IFD.GPSInfo}
    entries = {
        f"{ExifTags.TAGS.get(tag, 'Unknown')} ({tag})": _serialise_exif_value(value)
        for tag, value in exif.items()
        if tag not in pointer_tags
    }
    for ifd_tag, prefix, tag_names in (
        (ExifTags.IFD.Exif, "EXIF", ExifTags.TAGS),
        (ExifTags.IFD.GPSInfo, "GPS", ExifTags.GPSTAGS),
    ):
        for tag, value in exif.get_ifd(ifd_tag).items():
            entries[f"{prefix}.{tag_names.get(tag, 'Unknown')} ({tag})"] = _serialise_exif_value(
                value
            )
    return {key: entries[key] for key in sorted(entries)}


def _read_exif(img):
    """Read EXIF claims while preserving absent-versus-unreadable distinction.

    Pillow can retain a corrupt EXIF APP1 block even when it cannot parse it.
    The returned flag makes that failure visible; this helper never treats an
    empty or failed parse as proof that the producer supplied no EXIF.
    """
    raw = img.info.get("exif")
    if raw is None:
        # A whole-file read can fail after Image.open has already parsed enough
        # header data to return a record.  That is not evidence of an EXIF
        # failure when no EXIF block was present to parse.
        try:
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                exif = img.getexif()
                entries = _exif_entries(exif)
        except (AttributeError, OSError, SyntaxError, TypeError, ValueError):
            return False, None, None
        return bool(entries), {key: entries[key] for key in sorted(entries)} or None, None
    try:
        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            exif = img.getexif()
            entries = _exif_entries(exif)
        if not _looks_like_exif_header(raw) or not _has_sane_first_ifd(raw):
            raise ValueError("EXIF block has no TIFF header")
        if any("corrupt exif" in str(warning.message).lower() for warning in caught_warnings):
            raise ValueError("Pillow reported corrupt EXIF data")
        return True, {key: entries[key] for key in sorted(entries)}, None
    except (AttributeError, OSError, SyntaxError, TypeError, ValueError):
        return True, None, "exif_unreadable"


def _looks_like_exif_header(raw):
    """Check only whether container EXIF begins with an APP1 or bare TIFF header.

    JPEG normally retains an ``Exif\\0\\0`` APP1 payload, while WebP retains its
    valid EXIF payload as bare TIFF.  This is not a TIFF validator: Pillow's
    parser warnings decide whether a syntactically headed block was unreadable.
    """
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return False
    raw = bytes(raw)
    if raw.startswith(b"Exif\x00\x00"):
        raw = raw[6:]
    return len(raw) >= 8 and raw[:4] in (b"II*\x00", b"MM\x00*")


def _has_sane_first_ifd(raw):
    """Reject IFD offsets/counts that cannot fit in the declared EXIF bytes.

    Pillow sometimes represents malformed EXIF as an empty mapping without a
    warning.  Checking the first directory's offset and fixed-width entry span
    catches that silent parse failure while intentionally leaving value-level
    TIFF validation to Pillow.
    """
    raw = bytes(raw)
    if raw.startswith(b"Exif\x00\x00"):
        raw = raw[6:]
    if not _looks_like_exif_header(raw):
        return False
    byteorder = "little" if raw[:2] == b"II" else "big"
    first_ifd_offset = int.from_bytes(raw[4:8], byteorder)
    if first_ifd_offset + 2 > len(raw):
        return False
    entry_count = int.from_bytes(raw[first_ifd_offset : first_ifd_offset + 2], byteorder)
    first_ifd_end = first_ifd_offset + 2 + entry_count * 12 + 4
    return first_ifd_end <= len(raw)


def _read_icc_description(raw):
    """Return a profile's declared description without treating it as pixel truth.

    ICC bytes may be malformed or misleading.  This helper exposes the profile's
    own label when Pillow can read it and otherwise refuses to infer a colour
    space or a colour-management guarantee.
    """
    try:
        profile = ImageCms.getOpenProfile(io.BytesIO(raw))
        description = ImageCms.getProfileDescription(profile).strip()
        if not description:
            raise ValueError("ICC profile has no readable description")
        return description, None
    except (ImageCms.PyCMSError, OSError, TypeError, ValueError):
        return None, "icc_unreadable"


def _alpha_facts(img):
    """Separate alpha-channel presence from observed non-opaque pixels.

    This mirrors `load_rgb_alpha`'s public distinction so foreground callers
    agree about real transparency.  It does not infer transparency from colour,
    palette appearance, or semantic image content.
    """
    has_alpha = "A" in img.getbands() or (
        img.mode == "P" and "transparency" in img.info
    )
    if not has_alpha:
        return False, False, None, None
    alpha = np.asarray(img.convert("RGBA").getchannel("A"), dtype=np.uint8)
    alpha_min = int(alpha.min())
    alpha_max = int(alpha.max())
    return True, bool((alpha < 255).any()), alpha_min, alpha_max


def _palette_size(img):
    """Report palette table slots without analysing the image's colours.

    A palette table is a file/decoder property, whereas a colour census belongs
    to the palette-diff tool.  This helper refuses to count used colours or make
    a similarity claim.
    """
    if img.mode != "P":
        return None
    palette = img.getpalette()
    return len(palette) // 3 if palette is not None else 0


def inspect_image(path):
    """Inspect one file and return a complete, JSON-safe fact record.

    The function exists so a batch can retain useful reports when one input is
    bad.  It refuses to raise an image-decoding error to the whole batch and
    does not turn unreadable files into partial successful records.
    """
    path_text = str(path)
    try:
        file_path = Path(path)
        file_bytes = file_path.stat().st_size
        sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
        with Image.open(file_path) as img:
            has_alpha, uses_transparency, alpha_min, alpha_max = _alpha_facts(img)
            raw_icc = img.info.get("icc_profile")
            icc_present = raw_icc is not None
            icc_description = None
            flags = []
            if icc_present:
                icc_description, icc_flag = _read_icc_description(raw_icc)
                if icc_flag:
                    flags.append(icc_flag)
            exif_present, exif, exif_flag = _read_exif(img)
            if exif_flag:
                flags.append(exif_flag)
            try:
                n_frames = int(getattr(img, "n_frames", 1))
                is_animated = bool(getattr(img, "is_animated", False))
            except (AttributeError, OSError, ValueError):
                n_frames = None
                is_animated = None
                flags.append("frames_unreadable")
            dpi = img.info.get("dpi")
            return {
                "path": path_text,
                "readable": True,
                "reason": None,
                "size": [img.width, img.height],
                "width": img.width,
                "height": img.height,
                "mode": img.mode,
                "format": img.format,
                "bands": list(img.getbands()),
                "bit_depth_per_channel": MODE_BIT_DEPTHS.get(img.mode),
                "has_alpha_channel": has_alpha,
                "uses_transparency": uses_transparency,
                "alpha_min": alpha_min,
                "alpha_max": alpha_max,
                "transparency_key": _serialise_transparency_key(img.info["transparency"])
                if "transparency" in img.info
                else None,
                "palette_size": _palette_size(img),
                "icc_profile_present": icc_present,
                "icc_profile_bytes": len(raw_icc) if icc_present else None,
                "icc_profile_description": icc_description,
                "exif_present": exif_present,
                "exif": exif,
                "dpi": _serialise_dpi(dpi) if dpi is not None else None,
                "n_frames": n_frames,
                "is_animated": is_animated,
                "file_bytes": file_bytes,
                "sha256": sha256,
                "flags": sorted(flags),
            }
    except Exception as exc:
        return {"path": path_text, "readable": False, "reason": str(exc)}


def build_payload(paths):
    """Build the deterministic batch payload without changing any input file.

    The top-level wrapper makes positional input order explicit and gives callers
    a stable place for the tool contract.  It refuses to aggregate image facts
    into judgments such as similarity, palette, or structure metrics.
    """
    return {
        "tool": "pil_image_info",
        "version": TOOL_VERSION,
        "parameters": {"image_count": len(paths)},
        "images": [inspect_image(path) for path in paths],
        "interpretation_limits": INTERPRETATION_LIMITS,
    }


def main(argv=None):
    """Run the metadata-only CLI and keep rejection stdout empty.

    Argument parsing is deliberately left to argparse, whose invalid-invocation
    path exits 2 before any JSON is written.  Read failures are reportable input
    results, not invocation rejections, so a batch still receives its other
    files and returns exit 1.
    """
    parser = argparse.ArgumentParser(
        description="Report image file facts that resampled vision inputs do not retain."
    )
    parser.add_argument("images", metavar="IMAGE", nargs="+", help="image file to inspect")
    args = parser.parse_args(argv)
    payload = build_payload(args.images)
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0 if all(image["readable"] for image in payload["images"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
