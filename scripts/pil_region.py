"""Shared fractional-bbox rules for region-aware image tools.

This deliberately has no image or array dependencies: crop and measurement
tools must agree on their pixel boundaries before either one opens an image.
"""

import json
import math


REGION_SPACES = ("frame", "foreground")
DEFAULT_REGION_SPACE = "frame"


class RegionError(ValueError):
    """Reject a region that cannot identify the pixels the caller requested.

    A malformed or empty region must stop the operation rather than be widened,
    guessed at, or silently treated as a full-frame measurement.
    """


def _error(value, reason):
    return RegionError(f"invalid fractional bbox {value!r}: {reason}")


def _fractional_box(box):
    """Keep the validation shared by parsing and direct API callers.

    The public resolver also accepts a previously parsed box, so it must refuse
    invalid direct values instead of relying on a caller to have parsed first.
    """
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise _error(box, "expected exactly four numeric values")

    values = []
    for value in box:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _error(box, f"non-numeric coordinate {value!r}")
        value = float(value)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise _error(box, f"coordinate {value!r} is outside [0.0, 1.0]")
        values.append(value)

    left, top, right, bottom = values
    if left >= right:
        raise _error(box, f"left {left!r} must be less than right {right!r}")
    if top >= bottom:
        raise _error(box, f"top {top!r} must be less than bottom {bottom!r}")
    return values


def _size_and_origin(size, origin):
    """Reject non-pixel geometry before it can produce an ambiguous rectangle."""
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        raise RegionError(f"invalid size {size!r}: expected (width, height)")
    if not isinstance(origin, (list, tuple)) or len(origin) != 2:
        raise RegionError(f"invalid origin {origin!r}: expected (left, top)")
    width, height = size
    origin_x, origin_y = origin
    values = (width, height, origin_x, origin_y)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise RegionError(
            f"invalid size/origin {size!r}, {origin!r}: pixel geometry must be integers"
        )
    if width < 1 or height < 1:
        raise RegionError(f"invalid size {size!r}: extents must be at least one pixel")
    return width, height, origin_x, origin_y


def parse_fractional_bbox(text):
    """Provide one accepted spelling for emitted and human-entered regions.

    Crop and diff payloads emit JSON while command-line users commonly type CSV;
    accepting both prevents private parsers from drifting. It refuses malformed,
    non-finite, out-of-frame, inverted, and degenerate requests rather than
    interpreting an uncertain region as a measurement target.
    """
    if not isinstance(text, str):
        raise _error(text, "expected a JSON list or comma-separated string")
    source = text
    text = text.strip()
    if text.startswith("["):
        try:
            values = json.loads(text)
        except json.JSONDecodeError as error:
            raise _error(source, f"invalid JSON list ({error.msg})") from None
        if not isinstance(values, list):
            raise _error(source, "expected a JSON list")
    else:
        values = text.split(",")

    if len(values) != 4:
        raise _error(source, f"expected four values, got {len(values)}")

    parsed = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise _error(source, f"non-numeric coordinate {value!r}")
        try:
            parsed.append(float(value))
        except (TypeError, ValueError):
            raise _error(source, f"non-numeric coordinate {value!r}") from None
    return _fractional_box(parsed)


def resolve_pixel_rect(box, size, origin=(0, 0)):
    """Resolve shared crop boundaries so A1 and A4 measure the same pixels.

    Each edge uses independent half-up rounding, rather than banker's rounding,
    so the crop and region tools have one inspectable rule. It refuses a rect
    that rounds to no pixels instead of widening or otherwise clamping it,
    because that would silently measure pixels the caller did not ask for.
    """
    left, top, right, bottom = _fractional_box(box)
    width, height, origin_x, origin_y = _size_and_origin(size, origin)

    pixel_left = int(math.floor(left * width + 0.5))
    pixel_top = int(math.floor(top * height + 0.5))
    pixel_right = int(math.floor(right * width + 0.5))
    pixel_bottom = int(math.floor(bottom * height + 0.5))

    pixel_left = max(0, min(pixel_left, width))
    pixel_top = max(0, min(pixel_top, height))
    pixel_right = max(0, min(pixel_right, width))
    pixel_bottom = max(0, min(pixel_bottom, height))
    if pixel_right <= pixel_left or pixel_bottom <= pixel_top:
        raise _error(box, "resolved rectangle has zero width or height")
    return (
        pixel_left + origin_x,
        pixel_top + origin_y,
        pixel_right + origin_x,
        pixel_bottom + origin_y,
    )


def rect_to_fractional(rect, size, origin=(0, 0)):
    """Report the region actually cut, rather than re-emitting its request.

    Resolution is lossy at pixel boundaries, so payloads need the six-decimal
    fractions of the resolved rect for reproducible follow-up work. It refuses
    malformed or out-of-bounds rects rather than reporting a fraction that could
    not have come from the stated reference area.
    """
    width, height, origin_x, origin_y = _size_and_origin(size, origin)
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        raise RegionError(f"invalid rect {rect!r}: expected four integer edges")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in rect):
        raise RegionError(f"invalid rect {rect!r}: edges must be integers")

    left, top, right, bottom = rect
    left -= origin_x
    top -= origin_y
    right -= origin_x
    bottom -= origin_y
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise RegionError(
            f"invalid rect {rect!r}: must be a non-empty rect inside size {size!r}"
        )
    return [
        round(left / width, 6),
        round(top / height, 6),
        round(right / width, 6),
        round(bottom / height, 6),
    ]
