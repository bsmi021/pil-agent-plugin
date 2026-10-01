#!/usr/bin/env python
"""Numeric helpers for `blender_inspect_render.py`, imported by its Blender probe.

The probe runs inside Blender's bundled Python, which has numpy but no Pillow.
Everything that turns Blender's float passes into files lives here so the same
code can be unit-tested on any Python that has numpy: PNG writing (stdlib
`zlib` and `struct`, so no colour management touches a normal or ID value),
the depth heat-map with its printed near/far legend, camera framing, and the
Cryptomatte object hash.

This is a shared module, not a tool: it declares no `TOOL_VERSION`. numpy is
imported lazily, so importing this module never needs it.
"""

from __future__ import annotations

import math
import struct
import zlib

SENSOR_WIDTH_MM = 36.0

# Depth heat-map colour stops, near to far. Hot colours are close, cool are far.
HEATMAP_NAME = "depth-heat-v1"
HEATMAP_STOPS = (
    (0.00, (255, 244, 120)),
    (0.20, (255, 150, 30)),
    (0.40, (215, 50, 50)),
    (0.60, (140, 30, 130)),
    (0.80, (50, 60, 190)),
    (1.00, (25, 150, 200)),
)

LEGEND_STRIP_ROWS = 36
LEGEND_BACKGROUND = (20, 20, 24)


def _numpy():
    import numpy

    return numpy


# --- camera framing -----------------------------------------------------------


def _normalize(v):
    length = math.sqrt(sum(c * c for c in v))
    if not math.isfinite(length) or length <= 1e-12:
        raise ValueError("cannot normalise a zero-length vector")
    return tuple(c / length for c in v)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def camera_basis(direction, up_hint):
    """``(toward_camera, right, up)`` unit vectors for a view.

    ``direction`` points from the subject to the camera, as in the
    `render-views-v1` manifest. The camera looks along ``-toward_camera``.
    """
    toward = _normalize(direction)
    forward = tuple(-c for c in toward)
    right = _normalize(_cross(forward, _normalize(up_hint)))
    up = _normalize(_cross(right, forward))
    return toward, right, up


def camera_matrix_rows(location, toward, right, up):
    """Row-major 4x4 ``matrix_world`` for a Blender camera at ``location``.

    Blender cameras look down their local -Z with +Y up. Assign the result as
    ``mathutils.Matrix(rows)``: a nested tuple is read column by column.
    """
    return [
        [right[0], up[0], toward[0], location[0]],
        [right[1], up[1], toward[1], location[1]],
        [right[2], up[2], toward[2], location[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def half_fov_tangents(lens_mm, width, height, sensor_width_mm=SENSOR_WIDTH_MM):
    """``(tan(half fov x), tan(half fov y))`` for a perspective camera.

    Blender's default sensor fit applies the sensor width to the longer side.
    """
    long_tan = (sensor_width_mm / 2.0) / lens_mm
    if width >= height:
        return long_tan, long_tan * height / width
    return long_tan * width / height, long_tan


def view_extents(points, centre, direction, up_hint):
    """Per-axis extents of ``points`` about ``centre`` for one view.

    Returns ``(a, x, y)`` numpy arrays: each point's coordinate along the
    toward-camera axis, and across the image horizontally and vertically.
    """
    np = _numpy()
    toward, right, up = camera_basis(direction, up_hint)
    offsets = np.asarray(points, dtype=np.float64) - np.asarray(centre, dtype=np.float64)
    return offsets @ np.asarray(toward), offsets @ np.asarray(right), offsets @ np.asarray(up)


def perspective_distance(points, centre, views, lens_mm, width, height, margin):
    """One camera distance that frames ``points`` in every view, in every mode.

    A point at offset ``(a, x, y)`` about ``centre`` is in frame when
    ``|x| <= tx * (D - a)`` and ``|y| <= ty * (D - a)``, where ``D`` is the
    camera's distance from ``centre`` and ``tx``, ``ty`` are the half-fov
    tangents shrunk by ``1 + margin``. The result is the largest ``D`` any view
    needs, so framing is locked across views, and it is never inside the
    subject's depth range.
    """
    np = _numpy()
    tx, ty = half_fov_tangents(lens_mm, width, height)
    tx /= 1.0 + margin
    ty /= 1.0 + margin
    distance = 0.0
    for direction, up_hint in views:
        a, x, y = view_extents(points, centre, direction, up_hint)
        distance = max(
            distance,
            float(np.max(a + np.abs(x) / tx)),
            float(np.max(a + np.abs(y) / ty)),
        )
    extent = float(np.max(np.linalg.norm(np.asarray(points) - np.asarray(centre), axis=1)))
    return max(distance, extent * 1.05)


def orthographic_scale(points, centre, views, width, height, margin):
    """One ``ortho_scale`` that frames ``points`` in every view.

    Blender applies ``ortho_scale`` to the longer image side.
    """
    np = _numpy()
    aspect = width / height
    scale = 0.0
    for direction, up_hint in views:
        _a, x, y = view_extents(points, centre, direction, up_hint)
        need_width = 2.0 * float(np.max(np.abs(x)))
        need_height = 2.0 * float(np.max(np.abs(y)))
        long_side = max(need_height * aspect, need_width) if width >= height else max(need_height, need_width / aspect)
        scale = max(scale, long_side)
    return scale * (1.0 + margin)


# --- Cryptomatte ---------------------------------------------------------------


def murmur3_32(data: bytes, seed: int = 0) -> int:
    """MurmurHash3 (x86, 32-bit), the hash Cryptomatte uses for names."""
    c1, c2 = 0xCC9E2D51, 0x1B873593
    h = seed & 0xFFFFFFFF
    blocks = len(data) // 4
    for i in range(blocks):
        k = struct.unpack_from("<I", data, i * 4)[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = data[blocks * 4:]
    k = 0
    if len(tail) >= 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def cryptomatte_id_bits(name: str) -> int:
    """The 32-bit pattern of the float ID Cryptomatte stores for ``name``.

    The hash's exponent is clamped to 1..254 so the float is never NaN, inf or
    denormal.
    """
    h = murmur3_32(name.encode("utf-8"))
    mantissa = h & 0x7FFFFF
    exponent = min(max((h >> 23) & 0xFF, 1), 254)
    sign = (h >> 31) << 31
    return (sign | (exponent << 23) | mantissa) & 0xFFFFFFFF


def object_palette(names):
    """Distinct, non-black RGB colours by sorted object name.

    Hue steps by the golden ratio and value alternates, so neighbours in the
    list stay far apart. Returns ``{name: (r, g, b)}``.
    """
    import colorsys

    colours = {}
    for index, name in enumerate(sorted(names)):
        hue = (index * 0.618033988749895) % 1.0
        value = 1.0 if index % 2 == 0 else 0.72
        r, g, b = colorsys.hsv_to_rgb(hue, 0.85, value)
        colours[name] = (round(r * 255), round(g * 255), round(b * 255))
    return colours


def decode_object_ids(id_bits, foreground, id_by_bits):
    """Map each foreground pixel's Cryptomatte ID to an object name.

    ``id_bits`` is a uint32 array of the top-ranked ID per pixel and
    ``id_by_bits`` maps float bit patterns to names. Returns
    ``(labels, unmatched)``: an object array of names (or None for background)
    and the count of foreground pixels whose ID matched no object.
    """
    np = _numpy()
    labels = np.full(id_bits.shape, None, dtype=object)
    matched = np.zeros(id_bits.shape, dtype=bool)
    for bits, name in id_by_bits.items():
        hit = foreground & (id_bits == np.uint32(bits))
        labels[hit] = name
        matched |= hit
    return labels, int(np.count_nonzero(foreground & ~matched))


# --- images ---------------------------------------------------------------------


def write_png(path, pixels):
    """Write an 8-bit RGB or RGBA PNG from a ``(H, W, 3|4)`` uint8 array.

    Only stdlib `zlib` and `struct`: no colour management, no metadata chunks,
    so the file is a pure function of the array.
    """
    np = _numpy()
    pixels = np.ascontiguousarray(pixels, dtype=np.uint8)
    if pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        raise ValueError("PNG pixels must be shaped (H, W, 3) or (H, W, 4)")
    height, width, channels = pixels.shape
    color_type = 2 if channels == 3 else 6
    raw = b"".join(b"\x00" + pixels[row].tobytes() for row in range(height))

    def chunk(kind, body):
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(data)


def read_png(path):
    """Decode a PNG written by `write_png` back to a uint8 array (for tests)."""
    np = _numpy()
    with open(path, "rb") as handle:
        data = handle.read()
    offset = 8
    idat = b""
    width = height = channels = 0
    while offset < len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            width, height, _depth, color_type, *_ = struct.unpack(">IIBBBBB", body)
            channels = 3 if color_type == 2 else 4
        elif kind == b"IDAT":
            idat += body
        offset += 12 + length
    raw = zlib.decompress(idat)
    stride = width * channels
    rows = [np.frombuffer(raw, dtype=np.uint8, count=stride, offset=row * (stride + 1) + 1) for row in range(height)]
    return np.stack(rows).reshape(height, width, channels)


def heat_colours(t):
    """RGB uint8 colours for ``t`` in [0, 1] (0 near, 1 far), shape ``(..., 3)``."""
    np = _numpy()
    t = np.clip(np.asarray(t, dtype=np.float64), 0.0, 1.0)
    stops = np.array([s for s, _ in HEATMAP_STOPS])
    channels = [np.interp(t, stops, [c[i] for _, c in HEATMAP_STOPS]) for i in range(3)]
    return np.rint(np.stack(channels, axis=-1)).astype(np.uint8)


def depth_legend(near, far, ticks=5):
    """The numeric legend for a heat-map: colour stops with their depths."""
    span = far - near
    return {
        "colormap": HEATMAP_NAME,
        "near": near,
        "far": far,
        "unit": "scene units",
        "ticks": [
            {
                "depth": near + span * i / (ticks - 1),
                "rgb": [int(c) for c in heat_colours(i / (ticks - 1))],
            }
            for i in range(ticks)
        ],
    }


_GLYPHS = {
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": (".###.", "#...#", "....#", "..##.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": (".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    "e": (".....", ".....", ".###.", "#...#", "#####", "#....", ".###."),
    "n": (".....", ".....", "####.", "#...#", "#...#", "#...#", "#...#"),
    "a": (".....", ".....", ".###.", "....#", ".####", "#...#", ".####"),
    "r": (".....", ".....", "#.##.", "##..#", "#....", "#....", "#...."),
    "f": ("..##.", ".#..#", ".#...", "###..", ".#...", ".#...", ".#..."),
    " ": (".....",) * 7,
}
GLYPH_CHARACTERS = frozenset(_GLYPHS)
GLYPH_WIDTH, GLYPH_HEIGHT = 5, 7


def text_mask(text, scale=2):
    """Boolean ``(H, W)`` bitmap of ``text`` in the built-in 5x7 font."""
    np = _numpy()
    unknown = set(text) - GLYPH_CHARACTERS
    if unknown:
        raise ValueError(f"no glyph for {sorted(unknown)}")
    columns = len(text) * (GLYPH_WIDTH + 1) - 1
    mask = np.zeros((GLYPH_HEIGHT, max(columns, 1)), dtype=bool)
    for index, char in enumerate(text):
        left = index * (GLYPH_WIDTH + 1)
        for row, line in enumerate(_GLYPHS[char]):
            for col, cell in enumerate(line):
                if cell == "#":
                    mask[row, left + col] = True
    return np.kron(mask, np.ones((scale, scale), dtype=bool))


def format_depth(value):
    """A short decimal for the legend: four significant figures."""
    return f"{value:.4g}"


def legend_strip(width, near, far, rows=LEGEND_STRIP_ROWS):
    """The ``(rows, width, 4)`` uint8 strip appended under a depth heat-map.

    A colour bar from near (left) to far (right), with ``near <n>`` under its
    left end and ``far <n>`` under its right end, in scene units.
    """
    np = _numpy()
    strip = np.zeros((rows, width, 4), dtype=np.uint8)
    strip[..., :3] = LEGEND_BACKGROUND
    strip[..., 3] = 255
    margin = min(8, width // 8)
    bar_left, bar_right = margin, width - margin
    if bar_right - bar_left >= 2:
        ramp = heat_colours(np.linspace(0.0, 1.0, bar_right - bar_left))
        strip[3:11, bar_left:bar_right, :3] = ramp[None, :, :]
    scale = 2 if width >= 300 else 1
    for text, align in ((f"near {format_depth(near)}", "left"), (f"far {format_depth(far)}", "right")):
        mask = text_mask(text, scale)
        top = 14 if scale == 2 else 16
        if top + mask.shape[0] > rows:
            continue
        left = bar_left if align == "left" else max(bar_right - mask.shape[1], 0)
        right = min(left + mask.shape[1], width)
        region = strip[top:top + mask.shape[0], left:right, :3]
        region[mask[:, : right - left]] = 255
    return strip


def depth_heatmap(depth, foreground, near, far):
    """RGBA uint8 heat-map of ``depth``, then the legend strip beneath it.

    Background pixels are transparent. The strip adds `LEGEND_STRIP_ROWS` rows
    below the ``(H, W)`` image, so the PNG is taller than the depth array.
    """
    np = _numpy()
    height, width = depth.shape
    span = far - near
    t = (np.where(foreground, depth, near) - near) / span if span > 0 else np.zeros_like(depth)
    image = np.zeros((height, width, 4), dtype=np.uint8)
    image[..., :3] = heat_colours(t)
    image[..., 3] = np.where(foreground, 255, 0)
    image[~foreground, :3] = 0
    return np.concatenate([image, legend_strip(width, near, far)], axis=0)


def normal_image(normals, foreground):
    """RGBA uint8 with each world-space normal encoded as ``(n + 1) / 2``."""
    np = _numpy()
    height, width, _ = normals.shape
    image = np.zeros((height, width, 4), dtype=np.uint8)
    encoded = np.rint((np.clip(normals, -1.0, 1.0) * 0.5 + 0.5) * 255.0)
    image[..., :3] = np.where(foreground[..., None], encoded, 0).astype(np.uint8)
    image[..., 3] = np.where(foreground, 255, 0)
    return image


def gray_image(values, foreground):
    """RGBA uint8 greyscale of ``values`` in [0, 1]; background transparent."""
    np = _numpy()
    height, width = values.shape
    image = np.zeros((height, width, 4), dtype=np.uint8)
    level = np.where(foreground, np.rint(np.clip(values, 0.0, 1.0) * 255.0), 0).astype(np.uint8)
    image[..., 0] = image[..., 1] = image[..., 2] = level
    image[..., 3] = np.where(foreground, 255, 0)
    return image
