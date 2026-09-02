#!/usr/bin/env python
"""Draw deterministic, readable geometric annotations on a copy of an image.

The command exists to close the tool-to-vision grounding loop: a reader can
refer to a visible box number instead of an imprecise description.  It refuses
uncertain geometry, unreadable inputs, unsafe overwrites, and malformed
structure-diff payloads rather than emitting a partial JSON result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pil_region import RegionError, parse_fractional_bbox, resolve_pixel_rect  # noqa: E402


TOOL_VERSION = "0.7.0"
DEFAULT_LABEL_SCALE = 3
DEFAULT_THICKNESS = 2
MAX_LABEL_SCALE = 20
MAX_THICKNESS = 20
MAX_GRID_DIMENSION = 64
GLYPH_GAP = 1

# Ranked worst-first, and the ranking is what the read-back bundle measured.
# A numeral over another numeral cannot be read at all. One hard against the
# frame edge reads as truncated and gets transcribed as a different digit --
# blind readers offered "3" for a complete 2 drawn at x == width - 15. One over
# a box outline keeps its own ink, since numerals are painted last, and one
# crossed by a 1px grid line is usually still legible. Sitting inside a
# neighbouring box's interior is last because it damages nothing about the
# glyph itself, only the reader's sense of which box it is commenting on --
# see interior occlusion in the read-back bundle, image 5 (edge_overlap.png).
HAZARD_KINDS = ("glyph", "frame_edge", "box_outline", "grid_line", "box_interior")
HAZARD_FLAGS = {
    "glyph": "glyph_overlaps_glyph",
    "frame_edge": "glyph_touches_frame_edge",
    "box_outline": "glyph_overlaps_box_outline",
    "grid_line": "glyph_overlaps_grid_line",
    "box_interior": "glyph_overlaps_box_interior",
}
CLAMPED_FLAG = "glyph_clamped_into_frame"

# The table exists to make numerals byte-identical across Pillow versions and
# machines. Two alternatives were considered and rejected. Pillow's bundled
# default font is host-font independent but its glyphs changed shape between
# Pillow releases, so output would drift across versions on one machine, and
# byte-identical output is this repository's core contract. Plain geometric
# markers carrying the numbers only in the JSON legend cannot be read back from
# the image at all, which is the entire purpose of the overlay.
_DIGITS = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
}

INTERPRETATION_LIMITS = [
    "The overlay is drawn on a copy. The source file is not modified; its sha256 is recorded here so you can verify that.",
    "Never measure an annotated image. The boxes, grid and numerals are pixels: feeding this output to pil_palette_diff or pil_structure_diff measures the annotation as well as the content.",
    "Box numbers are geometric glyphs from a table defined in this file, not rendered text, so the drawn pixels are pixel-identical across every machine and every Pillow version. The PNG bytes are only byte-identical across repeated runs in one environment: Pillow's PNG encoder output has changed between versions, so output.sha256 can differ across environments even when every pixel matches -- compare pixels, not the digest, when comparing across machines or Pillow versions. Only digits exist; your labels appear in legend, never in the image.",
    "Numbering is by position (top, then left), not by the order you passed the boxes. requested_index maps each drawn number back to your input. It is unique only within a source: --box numbers its own arguments from 0, and each --from-json region list numbers its own entries from 0, so read (source, requested_index) as the pair that identifies an input.",
    "The boxes are the caller's. This tool asserts nothing about what is inside them.",
]


def sha256_file(path):
    """Return a file digest because callers need source/output provenance.

    It refuses a missing or unreadable file by propagating the filesystem
    exception so the CLI can reject the request without writing JSON.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_grid(text):
    """Validate grid dimensions because gridlines must have one clear scale.

    It refuses non-integer ``COLSxROWS`` spellings, zero dimensions, and
    impractically large grids rather than silently drawing a different grid.
    """
    if not isinstance(text, str) or text.count("x") != 1:
        raise ValueError("grid must be COLSxROWS")
    cols_text, rows_text = text.split("x")
    try:
        cols, rows = int(cols_text), int(rows_text)
    except ValueError:
        raise ValueError("grid must be COLSxROWS") from None
    if not (1 <= cols <= MAX_GRID_DIMENSION and 1 <= rows <= MAX_GRID_DIMENSION):
        raise ValueError("grid dimensions are out of range")
    return cols, rows


def _rgb_array(image):
    """Create RGB pixels because local glyph contrast needs visible luminance."""
    return np.asarray(image.convert("RGB"), dtype=np.float32)


def _luminance(pixels):
    """Calculate local luminance because global contrast can hide a label.

    An empty crop scores 0.0 so a footprint clipped by the frame still yields a
    black-or-white decision instead of raising.
    """
    if pixels.size == 0:
        return 0.0
    return float(np.mean(pixels[..., 0] * 0.2126 + pixels[..., 1] * 0.7152 + pixels[..., 2] * 0.0722))


def _colour_for_mode(image, colour):
    """Adapt a glyph colour to the copied image mode without changing geometry.

    It refuses any mode it cannot express the colour in, rather than handing
    Pillow a tuple that mode would silently reinterpret. ``annotate`` always
    converts its working copy to RGB or RGBA before this is ever called, so
    only those two modes are handled; there is no third branch to reach.
    """
    if image.mode == "RGBA":
        return (*colour, 255)
    if image.mode == "RGB":
        return colour
    raise ValueError(f"unsupported annotation mode {image.mode!r}")


def _glyph_size(number, scale):
    """Return a geometric label's footprint because placement must avoid boxes.

    It refuses unknown digits so a caller cannot obtain a partially rendered
    number.
    """
    text = str(number)
    if any(digit not in _DIGITS for digit in text):
        raise ValueError(f"unsupported label {text!r}")
    return (len(text) * 5 + (len(text) - 1)) * scale, 7 * scale


def _rects_overlap(first, second):
    """Test two rects for shared pixels because placement claims must be provable.

    Every rect here is half-open ``[left, top, right, bottom)``, the same
    convention as ``legend[*].pixel_rect`` and ``legend[*].glyph_rect``, so one
    predicate settles occlusion, collision and grid questions alike.
    """
    return (
        first[0] < second[2]
        and second[0] < first[2]
        and first[1] < second[3]
        and second[1] < first[3]
    )


def _rect_contains(outer, inner):
    """Test containment because an "inside" placement record must be checkable."""
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def _row_runs(row):
    """Find contiguous True runs in one boolean row because ink comes in stripes.

    Returns half-open ``(start, end)`` column pairs, vectorised so a tall box
    does not cost a per-pixel Python loop.
    """
    padded = np.concatenate(([False], row, [False]))
    edges = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return tuple(zip(starts.tolist(), ends.tolist()))


def _mask_to_rects(mask):
    """Cover a boolean pixel mask with an exact set of rectangles.

    Consecutive rows that share the identical set of column runs are merged
    into one band per run, so the returned rects' union is exactly the True
    cells -- no more, no less -- whatever shape the ink turns out to be.
    """
    rects = []
    open_bands = {}
    height = mask.shape[0]
    for y in range(height):
        runs = set(_row_runs(mask[y]))
        for run in list(open_bands):
            if run not in runs:
                rects.append((run[0], open_bands.pop(run), run[1], y))
        for run in runs:
            if run not in open_bands:
                open_bands[run] = y
    for run, start_y in open_bands.items():
        rects.append((run[0], start_y, run[1], height))
    return rects


def _outline_bands(rect, thickness):
    """List the pixels a box outline actually inks because glyphs must dodge them.

    A closed-form model of Pillow's thick-rectangle stroke was tried and was
    wrong: Pillow overshoots outside the requested rect whenever a side is
    shorter than about twice the thickness, and the overshoot is asymmetric
    between edges in a way that resists a clean formula (verified by direct
    inspection of the rendered pixels). Rather than re-guess the formula, this
    renders the identical ``draw.rectangle(..., outline=..., width=thickness)``
    call Pillow itself uses onto a small scratch canvas sized to this rect's
    own width and height, reads back exactly which pixels got inked, and
    offsets that mask onto the rect's coordinates. The result is Pillow's own
    ink, not a model of it, so it cannot drift out of sync with a future
    Pillow release the way the formula did.
    """
    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    pad = thickness + 2  # more than the largest overshoot observed at MAX_THICKNESS
    scratch = Image.new("L", (width + 2 * pad, height + 2 * pad), 0)
    ImageDraw.Draw(scratch).rectangle(
        [pad, pad, pad + width - 1, pad + height - 1], fill=None, outline=255, width=thickness
    )
    mask = np.asarray(scratch) > 0
    return [
        (left + x0 - pad, top + y0 - pad, left + x1 - pad, top + y1 - pad)
        for x0, y0, x1, y1 in _mask_to_rects(mask)
    ]


def _frame_bands(image_size, margin):
    """Mark the frame border because a numeral flush to it reads as cut off.

    The blind read-back found this and nothing else would have: a complete
    numeral drawn hard against the right edge was reported as clipped, with a
    different digit offered as an alternative reading. Treating the border as a
    hazard keeps numerals off it wherever any alternative exists.
    """
    width, height = image_size
    if margin <= 0:
        return []
    return [
        (0, 0, margin, height),
        (width - margin, 0, width, height),
        (0, 0, width, margin),
        (0, height - margin, width, height),
    ]


def _placement_candidates(rect, image_size, glyph_size, thickness, frame_margin):
    """Offer only placements whose recorded name is true of their own geometry.

    Every outside placement is offered before any inside one, because section
    6.1's occlusion rule is that a numeral must not cover what it labels; the
    corner-aligned spellings come first, then a horizontally shifted one that
    stays outside the box while clearing the frame border, which is the only
    placement available to a box pinned against a frame edge. A candidate is
    dropped unless it lies wholly in the frame and satisfies its own claim --
    an ``outside_`` rect is disjoint from the box, an ``inside_`` rect is
    contained by it -- so ``glyph_placement`` can never describe a glyph that is
    somewhere else.
    """
    left, top, right, bottom = rect
    width, height = image_size
    glyph_width, glyph_height = glyph_size
    above = top - glyph_height - GLYPH_GAP
    below = bottom + GLYPH_GAP
    shifted = min(max(left, frame_margin), max(0, width - glyph_width - frame_margin))
    proposed = (
        (left, above, "outside_top_left"),
        (right - glyph_width, above, "outside_top_right"),
        (shifted, above, "outside_top_shifted"),
        (left, below, "outside_bottom_left"),
        (right - glyph_width, below, "outside_bottom_right"),
        (shifted, below, "outside_bottom_shifted"),
        (left + thickness, top + thickness, "inside_top_left"),
        (right - glyph_width - thickness, top + thickness, "inside_top_right"),
    )
    seen = set()
    candidates = []
    for x, y, name in proposed:
        if (x, y) in seen:
            continue
        glyph = (x, y, x + glyph_width, y + glyph_height)
        if x < 0 or y < 0 or glyph[2] > width or glyph[3] > height:
            continue
        if name.startswith("outside_") and _rects_overlap(glyph, rect):
            continue
        if name.startswith("inside_") and not _rect_contains(rect, glyph):
            continue
        seen.add((x, y))
        candidates.append((x, y, name))
    return candidates


def _clamped_origin(rect, image_size, glyph_size):
    """Force a last-resort origin because a numeral outside the frame is lost.

    Reached only when no truthful placement exists, typically a box smaller than
    the numeral pinned against a frame edge. The result is named ``clamped``, not
    ``inside_*``, so the legend never claims containment the pixels lack.
    """
    width, height = image_size
    glyph_width, glyph_height = glyph_size
    x = min(max(0, rect[0]), max(0, width - glyph_width))
    y = min(max(0, rect[1]), max(0, height - glyph_height))
    return x, y, "clamped"


def _hazard_counts(glyph, hazards):
    """Count what a candidate would be painted across because legibility ranks them.

    Returns one count per ``HAZARD_KINDS`` entry, which is what makes the
    least-bad fallback a comparison rather than a guess.
    """
    counts = dict.fromkeys(HAZARD_KINDS, 0)
    for kind, rect in hazards:
        if _rects_overlap(glyph, rect):
            counts[kind] += 1
    return counts


def _place_glyph(rect, image_size, glyph_size, thickness, frame_margin, hazards):
    """Choose where a numeral goes because a collided numeral cannot be read.

    Takes the first candidate that hits nothing; failing that the least-hit one,
    ties broken by preference order so the choice stays deterministic. It
    returns the hazard kinds it could not avoid instead of hiding them, so the
    caller can raise a flag rather than emit a silently unreadable label.
    """
    candidates = _placement_candidates(rect, image_size, glyph_size, thickness, frame_margin)
    if not candidates:
        candidates = [_clamped_origin(rect, image_size, glyph_size)]
    best = None
    for index, (x, y, name) in enumerate(candidates):
        glyph = (x, y, x + glyph_size[0], y + glyph_size[1])
        counts = _hazard_counts(glyph, hazards)
        ranking = tuple(counts[kind] for kind in HAZARD_KINDS) + (index,)
        if best is None or ranking < best[0]:
            best = (ranking, x, y, name, counts)
        if not any(counts.values()):
            break
    _, x, y, name, counts = best
    return x, y, name, [kind for kind in HAZARD_KINDS if counts[kind]]


def _glyph_luminance(content, origin, glyph_size):
    """Measure the numeral's own footprint because local contrast is what is read.

    It samples the caller's original pixels, deliberately not the annotated
    copy: the colour of a numeral is then a function of the source image and the
    footprint alone, so it cannot change because some other box happened to be
    drawn nearby first.
    """
    x, y = origin
    glyph_width, glyph_height = glyph_size
    return _luminance(content[y : y + glyph_height, x : x + glyph_width])


def _draw_glyph(draw, origin, number, scale, colour):
    """Paint a bitmap number because geometric digits are deterministic.

    It refuses a number containing any character the digit table does not
    define, rather than painting a partial numeral, and never fills or tints the
    annotated box.
    """
    _glyph_size(number, scale)
    x0, y0 = origin
    cursor = x0
    for digit in str(number):
        for row, bits in enumerate(_DIGITS[digit]):
            for column, bit in enumerate(bits):
                if bit == "1":
                    left = cursor + column * scale
                    top = y0 + row * scale
                    draw.rectangle(
                        [left, top, left + scale - 1, top + scale - 1], fill=colour
                    )
        cursor += 6 * scale


def _draw_grid(draw, size, cols, rows, colour):
    """Draw the requested fractional grid first because boxes must win overlaps.

    It draws the ``cols - 1`` vertical and ``rows - 1`` horizontal internal lines
    only; the frame edge is not a line and is not counted. It returns that count together
    with the pixel rect of every line drawn, so numeral placement can avoid
    painting a digit across one.
    """
    width, height = size
    lines = []
    for col in range(1, cols):
        x = int(np.floor(col * width / cols + 0.5))
        draw.line([(x, 0), (x, height - 1)], fill=colour, width=1)
        lines.append((x, 0, x + 1, height))
    for row in range(1, rows):
        y = int(np.floor(row * height / rows + 0.5))
        draw.line([(0, y), (width - 1, y)], fill=colour, width=1)
        lines.append((0, y, width, y + 1))
    return len(lines), lines


def _load_structure_boxes(path):
    """Extract only the two specified diff regions because other metrics are not boxes.

    It refuses missing, malformed, or non-structure-diff JSON rather than
    guessing at fields from an unrelated payload.
    """
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("tool") != "pil_structure_diff":
        raise ValueError("--from-json must contain a pil_structure_diff payload")
    diff = payload.get("diff")
    if not isinstance(diff, dict):
        raise ValueError("structure-diff payload has no diff object")
    cells = diff.get("most_divergent_cells", [])
    if not isinstance(cells, list):
        raise ValueError("most_divergent_cells must be a list")
    boxes = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict) or "bounds_fractional" not in cell:
            raise ValueError("every divergent cell must have bounds_fractional")
        boxes.append(
            {
                "box": parse_fractional_bbox(json.dumps(cell["bounds_fractional"])),
                "source": "--from-json:most_divergent_cells",
                "requested_index": index,
                "label": None,
            }
        )
    changed = diff.get("changed_region_bbox_fractional")
    if changed is not None:
        boxes.append(
            {
                "box": parse_fractional_bbox(json.dumps(changed)),
                "source": "--from-json:changed_region_bbox_fractional",
                "requested_index": 0,
                "label": None,
            }
        )
    return boxes


def _hex(colour):
    """Serialize a glyph colour because the legend must be machine-readable.

    It always emits lowercase six-digit hex, so two legends compare as strings.
    """
    return "#" + "".join(f"{int(channel):02x}" for channel in colour)


def annotate(image, boxes, grid, label_scale, thickness):
    """Render annotations and a legend because callers need visual and JSON grounding.

    It refuses invalid boxes, dimensions, or drawing values before returning a
    rendered copy, its fully resolved legend, the grid line count, and the flags
    raised where a numeral could not be placed clear of everything else.
    """
    if not (1 <= label_scale <= MAX_LABEL_SCALE):
        raise ValueError("label scale is out of range")
    if not (1 <= thickness <= MAX_THICKNESS):
        raise ValueError("thickness is out of range")

    source = image.copy()
    if source.mode not in ("RGB", "RGBA"):
        source = source.convert("RGB")
    width, height = source.size
    cols, rows = grid
    prepared = []
    for item in boxes:
        box = item["box"]
        rect = resolve_pixel_rect(box, (width, height))
        prepared.append({**item, "box": box, "rect": rect})
    prepared.sort(key=lambda item: (item["box"][1], item["box"][0], item["box"][3], item["box"][2]))

    draw = ImageDraw.Draw(source)
    grid_colour = _colour_for_mode(source, (128, 128, 128))
    lines_drawn, grid_lines = _draw_grid(draw, source.size, cols, rows, grid_colour)
    box_colour = _colour_for_mode(source, (255, 170, 0))

    # Every outline is painted before any numeral, so a box drawn later can
    # never cut through a number drawn earlier: numerals are the topmost ink.
    for item in prepared:
        left, top, right, bottom = item["rect"]
        draw.rectangle(
            [left, top, right - 1, bottom - 1], fill=None, outline=box_colour, width=thickness
        )

    # One glyph cell of clearance: enough that a reader sees background on the
    # far side of the numeral and does not report it as clipped.
    frame_margin = label_scale
    hazards = [("frame_edge", band) for band in _frame_bands(source.size, frame_margin)]
    hazards.extend(("grid_line", line) for line in grid_lines)
    for item in prepared:
        hazards.extend(("box_outline", band) for band in _outline_bands(item["rect"], thickness))
    # Fixed before any glyph is placed, so recomputing a legend entry's
    # hazards after the loop (below) sees the same frame/grid/outline result
    # placement time did; only which OTHER glyphs and box interiors count
    # against each entry changes between the two passes.
    static_hazards = list(hazards)
    # Ranked last in HAZARD_KINDS, so a box's own interior never outweighs a
    # real occlusion risk -- it only breaks ties between candidates that are
    # otherwise equally good. Per box so each box excludes only its own
    # interior; sitting inside a NEIGHBOUR's interior still counts against it.
    box_interiors = [("box_interior", other["rect"]) for other in prepared]

    content = _rgb_array(image)
    legend = []
    for index, item in enumerate(prepared):
        number = index + 1
        left, top, right, bottom = item["rect"]
        glyph_size = _glyph_size(number, label_scale)
        own_box_interiors = [h for i, h in enumerate(box_interiors) if i != index]
        origin_x, origin_y, placement, _unavoidable_at_placement_time = _place_glyph(
            item["rect"], source.size, glyph_size, thickness, frame_margin, hazards + own_box_interiors
        )
        glyph_rect = (origin_x, origin_y, origin_x + glyph_size[0], origin_y + glyph_size[1])
        local_luma = _glyph_luminance(content, (origin_x, origin_y), glyph_size)
        glyph_rgb = (0, 0, 0) if local_luma >= 128.0 else (255, 255, 255)
        _draw_glyph(
            draw,
            (origin_x, origin_y),
            number,
            label_scale,
            _colour_for_mode(source, glyph_rgb),
        )
        hazards.append(("glyph", glyph_rect))
        legend.append(
            {
                "number": number,
                "requested_index": item["requested_index"],
                "source": item["source"],
                "label": item.get("label"),
                "fractional": [round(value, 6) for value in item["box"]],
                "pixel_rect": [left, top, right, bottom],
                "glyph_colour": _hex(glyph_rgb),
                "glyph_placement": placement,
                "glyph_rect": list(glyph_rect),
                "glyph_hazards": None,  # filled in below, once every glyph's final rect is known
            }
        )

    # Hazard reporting is symmetric: if numeral A's footprint overlaps
    # numeral B's, both must say so, not only whichever was placed second.
    # Recomputed here, once every glyph's final position is fixed, against
    # every OTHER glyph and every box interior but this entry's own -- this
    # never revisits a placement decision made above, only what each legend
    # entry is told about the rect it ended up with.
    flags = set()
    for index, entry in enumerate(legend):
        glyph_rect = tuple(entry["glyph_rect"])
        other_glyphs = [
            ("glyph", tuple(other["glyph_rect"])) for other_index, other in enumerate(legend) if other_index != index
        ]
        own_box_interiors = [h for i, h in enumerate(box_interiors) if i != index]
        counts = _hazard_counts(glyph_rect, static_hazards + other_glyphs + own_box_interiors)
        unavoidable = [kind for kind in HAZARD_KINDS if counts[kind]]
        entry["glyph_hazards"] = [HAZARD_FLAGS[kind] for kind in unavoidable]
        flags.update(entry["glyph_hazards"])
        if entry["glyph_placement"] == "clamped":
            flags.add(CLAMPED_FLAG)
    return source, legend, lines_drawn, sorted(flags)


def _argument_parser():
    """Build the CLI because a stable surface makes annotation reproducible.

    It performs no semantic validation of its own: every rejection is
    centralized in ``main`` so that each one keeps stdout empty and exits with
    status 2, which argparse's own error path would not do.
    """
    parser = argparse.ArgumentParser(description="Draw readable numbered boxes on an image copy.")
    parser.add_argument("image", help="source image path")
    parser.add_argument("--out", required=True, help="output PNG path")
    parser.add_argument("--box", action="append", default=[], help="fractional L,T,R,B box; repeatable")
    parser.add_argument("--label", action="append", default=[], help="legend label paired by --box order")
    parser.add_argument("--grid", default="4x3", help="grid as COLSxROWS (default 4x3)")
    parser.add_argument("--from-json", dest="from_json", help="pil_structure_diff JSON payload")
    parser.add_argument("--label-scale", type=int, default=DEFAULT_LABEL_SCALE)
    parser.add_argument("--thickness", type=int, default=DEFAULT_THICKNESS)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv=None):
    """Run the annotator because agents need a safe JSON-producing CLI.

    It refuses every malformed or unsafe request with exit status 2 and no
    stdout, and only prints JSON after the complete PNG has been written.
    """
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        source_path = Path(args.image)
        output_path = Path(args.out)
        if source_path.resolve() == output_path.resolve():
            raise ValueError("output must not replace the source")
        if output_path.exists() and not args.overwrite:
            raise ValueError("output exists; pass --overwrite to replace it")
        if len(args.label) > len(args.box):
            raise ValueError("more labels than --box arguments")
        grid = parse_grid(args.grid)
        if not (1 <= args.label_scale <= MAX_LABEL_SCALE):
            raise ValueError("label scale is out of range")
        if not (1 <= args.thickness <= MAX_THICKNESS):
            raise ValueError("thickness is out of range")

        with Image.open(source_path) as opened:
            image = opened.copy()
        source_sha = sha256_file(source_path)
        boxes = []
        for index, text in enumerate(args.box):
            boxes.append(
                {
                    "box": parse_fractional_bbox(text),
                    "source": "--box",
                    "requested_index": index,
                    "label": args.label[index] if index < len(args.label) else None,
                }
            )
        if args.from_json:
            boxes.extend(_load_structure_boxes(Path(args.from_json)))

        annotated, legend, lines_drawn, flags = annotate(
            image, boxes, grid, args.label_scale, args.thickness
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        annotated.save(output_path, format="PNG")
        output_sha = sha256_file(output_path)
        payload = {
            "tool": "pil_annotate",
            "version": TOOL_VERSION,
            "parameters": {
                "grid": {"cols": grid[0], "rows": grid[1]},
                "label_scale": args.label_scale,
                "thickness": args.thickness,
                "from_json": str(Path(args.from_json)) if args.from_json else None,
                "overwrite": args.overwrite,
            },
            "source": {"path": str(source_path), "size": list(image.size), "sha256": source_sha},
            "output": {"path": str(output_path), "size": list(annotated.size), "sha256": output_sha},
            "legend": legend,
            "grid": {"cols": grid[0], "rows": grid[1], "lines_drawn": lines_drawn},
            "flags": flags,
            "interpretation_limits": INTERPRETATION_LIMITS,
        }
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (OSError, ValueError, KeyError, TypeError, RegionError, json.JSONDecodeError) as error:
        print(f"pil_annotate: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
