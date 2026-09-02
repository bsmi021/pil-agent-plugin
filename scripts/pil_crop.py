#!/usr/bin/env python
"""Cut a fractional bounding box out of an image, at native resolution.

Emits JSON on stdout and writes the crop to --out. Closes the "zoom loop": a
vision encoder resamples an image before an agent ever sees it, so detail
below that resolution is gone and the model does not even know the true pixel
dimensions of what it was shown. The measurement tools already emit *where to
look* -- changed_region_bbox_fractional, most_divergent_cells,
foreground.bbox_fractional -- this tool hands vision back a native-resolution
view of that region, at the resolution the encoder never received.

Usage:
    python pil_crop.py render.png --out crop.png --region "[0.1,0.4,0.3,0.9]"
    python pil_crop.py render.png --out crop.png --region 0.1,0.4,0.3,0.9 --scale 4
    python pil_crop.py render.png --out crop.png --region 0,0,1,1 --region-space foreground

--region accepts both spellings the plugin's own tools already emit and that a
human commonly types. --region-space selects what the fractions are relative
to: "frame" (the default, and what every fractional coordinate the other
tools emit is expressed in) or "foreground" (the object's own bounding box,
useful for cutting the same part of two differently-framed renders).

Fractional-to-pixel resolution and its half-up-per-edge rounding rule are
owned by pil_region.py, not reimplemented here: this tool's whole reason to
exist is that --region on the diff tools and a pre-cropped file from this
tool must resolve to the *same* pixels, and importing one parser is what
makes that a guarantee rather than a coincidence.

Exit codes: 0 on success. 2 on any rejection -- a malformed or degenerate
region, an unreadable source image, a refused overwrite, or an empty
foreground mask under --region-space foreground -- with nothing at all
written to stdout, so a caller can never mistake a rejected run for a partial
answer.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_common import (  # noqa: E402
    DEFAULT_BACKGROUND_DELTA,
    foreground_mask,
    load_rgb_alpha,
    mask_bbox,
)
from pil_region import (  # noqa: E402
    DEFAULT_REGION_SPACE,
    REGION_SPACES,
    RegionError,
    parse_fractional_bbox,
    rect_to_fractional,
    resolve_pixel_rect,
)

TOOL_VERSION = "0.7.0"

INTERPRETATION_LIMITS = [
    "A crop is a view, not a measurement. Nothing in this payload asserts "
    "that the region contains what you believe it contains.",
    "--scale is nearest-neighbour: it invents no colour, and it recovers no "
    "detail the source never had. A 4x upscale of a 3-pixel feature is still "
    "a 3-pixel feature, drawn larger.",
    "Fractional->pixel resolution rounds half up on each edge independently. "
    "resolved_pixel_rect and resolved_fractional record exactly what was "
    "cut, and they will differ from what you asked for. "
    "resolved_fractional is always frame-relative, regardless of "
    "region_space; read it and resolved_pixel_rect, not parameters.region.",
    "region_space=foreground resolves against a foreground bounding box "
    "derived by pil_common's rule -- exact on a file with real alpha, an "
    "estimate from the border-median colour otherwise. The crop inherits "
    "that estimate's error; reference_rect records the box used.",
    "No metadata is carried across: the output has no EXIF, no ICC profile "
    "and no text chunks, whatever the source had.",
]


class CropRejected(Exception):
    """Mark a failure that must exit 2 with empty stdout and no file written.

    Every rejection this tool can produce -- a malformed region, an
    unreadable source, a refused overwrite, an empty foreground mask -- is
    raised as this one type so main() has a single place to enforce "nothing
    was printed and nothing was written" rather than re-deriving that
    guarantee at each call site.
    """


def _sha256(data):
    """Hash raw bytes so a caller can verify byte-determinism from the
    payload alone, without re-reading either file."""
    return hashlib.sha256(data).hexdigest()


def _load_source_pixels(source_bytes, display_path):
    """Decode the source at full resolution without compositing its alpha.

    A crop is a view of the file's own stored pixels: a partial-coverage
    pixel's stored colour is what the caller asked to see, and forcing it
    onto any background -- even implicitly, through a mode conversion that
    drops alpha -- would hand back a darkened colour the file never claimed.
    RGBA is used whenever the source carries an alpha channel or palette
    transparency; the conversion is claimed pixel-exact only for the directly
    representable 8-bit modes (1, L, P, LA, RGB and RGBA). Integer and
    floating-point high-bit-depth modes are rejected instead of being clipped
    or rescaled, because this tool has no display-transfer policy with which
    to make that conversion faithful. info is cleared immediately so no
    inherited ICC profile, EXIF block, DPI or text chunk can leak into the
    saved crop.

    SCOPE OF THAT REJECTION, measured rather than assumed. It reaches only the
    modes Pillow still reports as high-bit-depth by the time this function sees
    them -- I, F and I;*. A 16-bit COLOUR PNG (RGB/RGBA/LA) never gets here as
    such: Pillow decodes it to 8-bit at open(), so ``img.mode`` is already
    "RGB". That path is a high-byte scale, not the clipping this gate exists to
    prevent -- measured on a 16-bit source, 1186 distinct values become 256 and
    ``loaded == (v >> 8)`` holds exactly while ``loaded == clip(v, 0, 255)``
    does not -- so the crop is a faithful 8-bit rendition of the source rather
    than a corrupted one. The distinction matters because the rejection message
    says the tool "will not clip or rescale", and for 16-bit colour the rescale
    has already happened in the decoder, out of this tool's reach.
    """
    try:
        img = Image.open(io.BytesIO(source_bytes))
        img.load()
    except OSError as exc:
        raise CropRejected(f"cannot read image {display_path!r}: {exc}") from exc
    if img.mode == "I" or img.mode == "F" or img.mode.startswith("I;"):
        raise CropRejected(
            f"unsupported high-bit-depth image mode {img.mode!r}; "
            "pil_crop accepts 8-bit pixels only and will not itself clip or "
            "rescale them. (A 16-bit COLOUR source does not reach this path: "
            "Pillow scales it to 8 bits in the decoder, which is faithful "
            "high-byte scaling rather than clipping.)"
        )
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )
    pixels = img.convert("RGBA") if has_alpha else img.convert("RGB")
    pixels.info = {}
    return pixels, has_alpha


def _foreground_reference_rect(path, background_delta):
    """The frame-relative foreground bbox that --region-space foreground cuts against.

    Reuses pil_common's own mask rule -- alpha when the file carries real
    transparency, the border-median colour otherwise -- so a region resolved
    here and a region resolved by --region-space foreground on the diff
    tools name the same rectangle; two independently written masks would
    make that agreement a coincidence rather than a guarantee. Raises
    CropRejected on an empty mask instead of silently falling back to the
    frame, because the caller explicitly asked for a space that, on this
    file, does not exist.
    """
    rgb, alpha = load_rgb_alpha(path)
    mask, _source, _background_hex = foreground_mask(rgb, alpha, background_delta)
    bbox = mask_bbox(mask)
    if bbox is None:
        raise CropRejected(
            "region-space foreground: the foreground mask is empty, so "
            "there is no foreground bounding box to resolve --region against"
        )
    return bbox


def build_crop(args):
    """Validate the request, cut the crop, and assemble its payload.

    Every check that can reject the request runs before any bytes are
    written to --out, so a rejected run leaves the output path untouched and
    a caller reading stdout can never parse a partial answer. Returns
    (payload, out_path, png_bytes); main() is the only place that writes.
    """
    if args.scale < 1:
        raise CropRejected(f"--scale must be >= 1, got {args.scale}")

    out_path = Path(args.out)
    if out_path.suffix.lower() != ".png":
        raise CropRejected(
            f"--out must end in .png (PNG-only in {TOOL_VERSION}), got {args.out!r}"
        )
    if out_path.exists() and not args.overwrite:
        raise CropRejected(
            f"{args.out} already exists; pass --overwrite to replace it"
        )

    source_path = Path(args.image)
    try:
        source_bytes = source_path.read_bytes()
    except OSError as exc:
        raise CropRejected(f"cannot read image {args.image!r}: {exc}") from exc

    pixels, has_alpha = _load_source_pixels(source_bytes, args.image)
    width, height = pixels.size

    try:
        box = parse_fractional_bbox(args.region)
    except RegionError as exc:
        raise CropRejected(str(exc)) from exc

    reference_rect = None
    if args.region_space == "foreground":
        reference_rect = _foreground_reference_rect(source_path, args.background_delta)
        ref_left, ref_top, ref_right, ref_bottom = reference_rect
        size = (ref_right - ref_left, ref_bottom - ref_top)
        origin = (ref_left, ref_top)
    else:
        size = (width, height)
        origin = (0, 0)

    try:
        rect = resolve_pixel_rect(box, size, origin=origin)
        resolved_fractional = rect_to_fractional(rect, (width, height))
    except RegionError as exc:
        raise CropRejected(str(exc)) from exc

    crop = pixels.crop(rect)
    if args.scale > 1:
        crop = crop.resize(
            (crop.width * args.scale, crop.height * args.scale), Image.NEAREST
        )
    crop.info = {}

    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    output_bytes = buffer.getvalue()

    payload = {
        "tool": "pil_crop",
        "version": TOOL_VERSION,
        "parameters": {
            "region": box,
            "region_space": args.region_space,
            "scale": args.scale,
            "resample": "nearest",
            "overwrite": args.overwrite,
            "background_delta": args.background_delta,
        },
        "source": {
            "path": str(args.image),
            "size": [width, height],
            "mode": pixels.mode,
            "has_alpha": has_alpha,
            "sha256": _sha256(source_bytes),
        },
        "region": {
            "requested_fractional": box,
            "resolved_pixel_rect": list(rect),
            "resolved_fractional": resolved_fractional,
            "space": args.region_space,
            "reference_rect": list(reference_rect) if reference_rect else None,
        },
        "output": {
            "path": str(args.out),
            "size": list(crop.size),
            "sha256": _sha256(output_bytes),
            "bytes": len(output_bytes),
        },
        "flags": [],
        "interpretation_limits": INTERPRETATION_LIMITS,
    }
    return payload, out_path, output_bytes


def build_parser():
    """Isolate CLI wiring so build_crop stays testable without argparse."""
    parser = argparse.ArgumentParser(
        description="Cut a fractional region out of an image at native resolution."
    )
    parser.add_argument("image", help="source image path")
    parser.add_argument(
        "--out",
        required=True,
        help="output PNG path -- required, and never guessed: a tool that "
        "writes to a location it invented is a tool that overwrites "
        "something",
    )
    parser.add_argument(
        "--region",
        required=True,
        help="fractional bbox as '[L,T,R,B]' or 'L,T,R,B', same spelling "
        "accepted by --region on the diff tools",
    )
    parser.add_argument(
        "--region-space",
        choices=REGION_SPACES,
        default=DEFAULT_REGION_SPACE,
        help="resolve --region against the whole frame or the foreground's "
        f"own bounding box (default {DEFAULT_REGION_SPACE})",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=1,
        help="integer nearest-neighbour upscale factor (default 1); never "
        "resampled smoothly, so magnification invents no colour",
    )
    parser.add_argument(
        "--background-delta",
        type=float,
        default=DEFAULT_BACKGROUND_DELTA,
        help="OKLab distance from the border-median colour within which an "
        "opaque pixel counts as background, used only when --region-space "
        f"foreground falls back to the border-median mask (default "
        f"{DEFAULT_BACKGROUND_DELTA})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow replacing an existing --out file (refused by default)",
    )
    return parser


def main(argv=None):
    """Own the only filesystem write and JSON emission.

    Keeping the write before printing ensures that a rejected write cannot
    emit a partial answer: build_crop and the output write must both succeed
    before anything reaches stdout.
    """
    args = build_parser().parse_args(argv)

    try:
        payload, out_path, output_bytes = build_crop(args)
        out_path.write_bytes(output_bytes)
    except CropRejected as exc:
        print(f"pil_crop: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"pil_crop: cannot write output {args.out!r}: {exc}", file=sys.stderr)
        return 2

    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
