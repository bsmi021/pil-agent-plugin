"""blender_render_kernels: the numeric helpers behind `blender_inspect_render`.

They run inside Blender in production, but need only numpy, so they are tested
here on any Python that has it."""

import math
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blender_render_kernels as K  # noqa: E402


# --- Cryptomatte ---------------------------------------------------------------


@pytest.mark.parametrize(
    "data, seed, expected",
    [
        (b"", 0, 0x00000000),
        (b"", 1, 0x514E28B7),
        (b"test", 0, 0xBA6BD213),
        (b"Hello, world!", 0, 0xC0363E43),
        (b"The quick brown fox jumps over the lazy dog", 0, 0x2E4FF723),
    ],
)
def test_murmur3_matches_the_published_vectors(data, seed, expected):
    assert K.murmur3_32(data, seed) == expected


def test_cryptomatte_ids_match_what_blender_writes_for_object_names():
    # Read back from an EEVEE CryptoObject00 pass in Blender 5.2 (2026-09-30 spike).
    assert K.cryptomatte_id_bits("Center") == 0x6E9AD537
    assert K.cryptomatte_id_bits("Side") == 0xC6A7F314


def test_cryptomatte_id_is_never_nan_inf_or_denormal():
    for index in range(2000):
        bits = K.cryptomatte_id_bits(f"Object.{index:04d}")
        value = np.array([bits], dtype=np.uint32).view(np.float32)[0]
        assert np.isfinite(value) and value != 0.0
        assert 1 <= (bits >> 23) & 0xFF <= 254


def test_object_palette_is_distinct_stable_and_never_black():
    names = [f"Obj{i}" for i in range(40)]
    palette = K.object_palette(names)

    assert set(palette) == set(names)
    assert len(set(palette.values())) == 40
    assert (0, 0, 0) not in palette.values()
    assert palette == K.object_palette(list(reversed(names)))


def test_decode_object_ids_labels_pixels_and_counts_unmatched():
    ids = np.array([[1, 2, 9], [0, 1, 2]], dtype=np.uint32)
    foreground = np.array([[True, True, True], [False, True, True]])

    labels, unmatched = K.decode_object_ids(ids, foreground, {1: "A", 2: "B"})

    assert labels.tolist() == [["A", "B", None], [None, "A", "B"]]
    assert unmatched == 1


# --- images ---------------------------------------------------------------------


@pytest.mark.parametrize("channels", [3, 4])
def test_png_round_trips_and_carries_no_metadata_chunks(tmp_path, channels):
    rng = np.random.default_rng(3)
    pixels = rng.integers(0, 256, size=(7, 11, channels), dtype=np.uint8)
    path = tmp_path / "x.png"

    K.write_png(path, pixels)

    assert np.array_equal(K.read_png(path), pixels)
    data = path.read_bytes()
    assert data.startswith(bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)))
    for chunk in (b"tEXt", b"zTXt", b"iTXt", b"tIME"):
        assert chunk not in data


def test_png_output_is_a_pure_function_of_the_pixels(tmp_path):
    pixels = np.arange(4 * 5 * 4, dtype=np.uint8).reshape(4, 5, 4)
    K.write_png(tmp_path / "a.png", pixels)
    K.write_png(tmp_path / "b.png", pixels)
    assert (tmp_path / "a.png").read_bytes() == (tmp_path / "b.png").read_bytes()


def test_png_refuses_a_bad_shape(tmp_path):
    with pytest.raises(ValueError, match="shaped"):
        K.write_png(tmp_path / "x.png", np.zeros((4, 4), dtype=np.uint8))


def test_heat_colours_run_from_the_near_stop_to_the_far_stop():
    colours = K.heat_colours(np.array([0.0, 1.0, -3.0, 9.0]))

    assert colours[0].tolist() == list(K.HEATMAP_STOPS[0][1])
    assert colours[1].tolist() == list(K.HEATMAP_STOPS[-1][1])
    assert colours[2].tolist() == colours[0].tolist()
    assert colours[3].tolist() == colours[1].tolist()


def test_depth_legend_lists_near_far_and_evenly_spaced_ticks_in_scene_units():
    legend = K.depth_legend(2.0, 6.0, ticks=5)

    assert legend["near"] == 2.0 and legend["far"] == 6.0
    assert legend["unit"] == "scene units"
    assert [t["depth"] for t in legend["ticks"]] == [2.0, 3.0, 4.0, 5.0, 6.0]
    assert legend["ticks"][0]["rgb"] == list(K.HEATMAP_STOPS[0][1])


def test_every_glyph_is_five_by_seven_and_the_legend_text_is_covered():
    for char, rows in K._GLYPHS.items():
        assert len(rows) == K.GLYPH_HEIGHT and all(len(row) == K.GLYPH_WIDTH for row in rows), char
    for text in ("near 13.92", "far 2.5e+03", "far -0.001"):
        assert set(text) <= K.GLYPH_CHARACTERS
    with pytest.raises(ValueError, match="no glyph"):
        K.text_mask("Q")


def test_text_mask_size_scales_with_the_text():
    one = K.text_mask("0", scale=1)
    assert one.shape == (K.GLYPH_HEIGHT, K.GLYPH_WIDTH)
    three = K.text_mask("012", scale=2)
    assert three.shape == (K.GLYPH_HEIGHT * 2, (3 * (K.GLYPH_WIDTH + 1) - 1) * 2)
    assert three.any()


def test_depth_heatmap_is_the_depth_image_plus_a_legend_strip_with_readable_text():
    depth = np.full((20, 40), 1e10)
    depth[5:15, 10:30] = np.linspace(4.0, 8.0, 20)[None, :]
    foreground = depth < 1e8

    image = K.depth_heatmap(depth, foreground, 4.0, 8.0)

    assert image.shape == (20 + K.LEGEND_STRIP_ROWS, 40, 4)
    assert image[5, 10, :3].tolist() == list(K.HEATMAP_STOPS[0][1])          # nearest pixel
    assert image[5, 29, :3].tolist() == list(K.HEATMAP_STOPS[-1][1])         # farthest pixel
    assert image[0, 0, 3] == 0 and image[0, 0, :3].tolist() == [0, 0, 0]     # background: transparent
    strip = image[20:]
    assert (strip[..., 3] == 255).all()
    assert (strip[..., :3] == 255).all(axis=-1).any()                         # white legend text
    assert (strip[3:11, 8:32, :3] != K.LEGEND_BACKGROUND).any()               # the colour bar


def test_legend_strip_puts_near_left_and_far_right():
    strip = K.legend_strip(400, 1.0, 9.0)
    text_rows = strip[14:28, :, :3]
    white = (text_rows == 255).all(axis=-1)
    columns = np.flatnonzero(white.any(axis=0))

    assert columns.min() < 60 and columns.max() > 340


def test_normal_image_encodes_world_normals_as_n_plus_one_over_two():
    normals = np.zeros((1, 3, 3), dtype=np.float32)
    normals[0, 0] = (0, 0, 1)
    normals[0, 1] = (-1, 0, 0)
    foreground = np.array([[True, True, False]])

    image = K.normal_image(normals, foreground)

    assert image[0, 0].tolist() == [128, 128, 255, 255]
    assert image[0, 1].tolist() == [0, 128, 128, 255]
    assert image[0, 2].tolist() == [0, 0, 0, 0]


def test_gray_image_maps_values_and_makes_the_background_transparent():
    image = K.gray_image(np.array([[0.0, 0.5, 1.0, 1.0]]), np.array([[True, True, True, False]]))

    assert image[0, :, 0].tolist() == [0, 128, 255, 0]
    assert image[0, :, 3].tolist() == [255, 255, 255, 0]


# --- camera framing --------------------------------------------------------------


def _project(point, centre, distance, direction, up_hint, lens, width, height):
    """Normalised device coordinates of ``point`` for the tool's perspective camera."""
    toward, right, up = K.camera_basis(direction, up_hint)
    offset = np.asarray(point, dtype=float) - np.asarray(centre, dtype=float)
    depth = distance - offset @ np.asarray(toward)
    tx, ty = K.half_fov_tangents(lens, width, height)
    return (offset @ np.asarray(right)) / (depth * tx), (offset @ np.asarray(up)) / (depth * ty), depth


def _box_points(centre, half):
    return [
        (centre[0] + sx * half[0], centre[1] + sy * half[1], centre[2] + sz * half[2])
        for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)
    ]


VIEWS = [((0, -1, 0), (0, 0, 1)), ((1, 0, 0), (0, 0, 1)), ((-1, -1, 0.7), (0, 0, 1)), ((0.3, 1, 1.4), (0, 0, 1))]


@pytest.mark.parametrize("size", [(1024, 1024), (1600, 900), (600, 1000)])
def test_perspective_distance_frames_every_point_in_every_view_with_the_margin(size):
    width, height = size
    points = _box_points((5, 2, 1), (3, 1, 2)) + _box_points((-1, 4, 0), (0.5, 0.5, 4))
    centre = ((min(p[i] for p in points) + max(p[i] for p in points)) / 2 for i in range(3))
    centre = tuple(centre)
    margin = 0.1

    distance = K.perspective_distance(np.array(points), centre, VIEWS, 50.0, width, height, margin)

    worst = 0.0
    for direction, up_hint in VIEWS:
        for point in points:
            x, y, depth = _project(point, centre, distance, direction, up_hint, 50.0, width, height)
            assert depth > 0
            worst = max(worst, abs(x), abs(y))
    # Everything is inside the frame, and the most extreme point sits at 1 / (1 + margin).
    assert worst == pytest.approx(1 / (1 + margin), rel=1e-9)


def test_perspective_distance_is_locked_to_the_hardest_view():
    points = np.array(_box_points((0, 0, 0), (4, 0.2, 0.2)))
    wide = ((0, -1, 0), (0, 0, 1))      # sees the 8-wide length
    narrow = ((1, 0, 0), (0, 0, 1))     # sees it end on
    both = K.perspective_distance(points, (0, 0, 0), [wide, narrow], 50.0, 512, 512, 0.1)

    assert both == K.perspective_distance(points, (0, 0, 0), [wide], 50.0, 512, 512, 0.1)
    assert both > K.perspective_distance(points, (0, 0, 0), [narrow], 50.0, 512, 512, 0.1)


def test_a_longer_lens_needs_a_greater_distance():
    points = np.array(_box_points((0, 0, 0), (1, 1, 1)))
    near = K.perspective_distance(points, (0, 0, 0), VIEWS[:1], 35.0, 512, 512, 0.1)
    far = K.perspective_distance(points, (0, 0, 0), VIEWS[:1], 85.0, 512, 512, 0.1)
    assert far > near * 1.9


def test_the_camera_never_ends_up_inside_the_subject():
    points = np.array(_box_points((0, 0, 0), (1, 1, 1)))
    distance = K.perspective_distance(points, (0, 0, 0), VIEWS, 500.0, 512, 512, 0.0)
    assert distance >= math.sqrt(3) * 1.05


def test_half_fov_tangents_follow_blenders_sensor_fit():
    assert K.half_fov_tangents(50.0, 100, 100) == (0.36, 0.36)
    tx, ty = K.half_fov_tangents(50.0, 200, 100)
    assert (tx, ty) == (0.36, 0.18)
    tx, ty = K.half_fov_tangents(50.0, 100, 200)
    assert (tx, ty) == (0.18, 0.36)


@pytest.mark.parametrize("size", [(512, 512), (800, 400), (400, 800)])
def test_orthographic_scale_frames_every_point_with_the_margin(size):
    width, height = size
    points = np.array(_box_points((5, 2, 1), (3, 1, 2)))
    centre = (5, 2, 1)

    scale = K.orthographic_scale(points, centre, VIEWS, width, height, 0.1)

    half_long = scale / 2
    half_x, half_y = (half_long, half_long * height / width) if width >= height else (half_long * width / height, half_long)
    worst = 0.0
    for direction, up_hint in VIEWS:
        _a, x, y = K.view_extents(points, centre, direction, up_hint)
        worst = max(worst, float(np.max(np.abs(x))) / half_x, float(np.max(np.abs(y))) / half_y)
    assert worst == pytest.approx(1 / 1.1, rel=1e-9)


def test_camera_basis_is_orthonormal_right_handed_and_keeps_up_up():
    toward, right, up = K.camera_basis((-1, -1, 0.7), (0, 0, 1))
    t, r, u = map(np.asarray, (toward, right, up))

    assert np.linalg.norm(t) == pytest.approx(1) and np.linalg.norm(r) == pytest.approx(1) and np.linalg.norm(u) == pytest.approx(1)
    assert t @ r == pytest.approx(0, abs=1e-12) and t @ u == pytest.approx(0, abs=1e-12) and r @ u == pytest.approx(0, abs=1e-12)
    assert np.cross(r, u) == pytest.approx(t)      # +X right, +Y up, +Z toward the camera
    assert u[2] > 0


def test_camera_matrix_rows_place_the_camera_and_look_at_the_target():
    toward, right, up = K.camera_basis((0, -1, 0), (0, 0, 1))
    rows = np.array(K.camera_matrix_rows((1.0, -9.0, 2.0), toward, right, up))

    assert rows[:3, 3].tolist() == [1.0, -9.0, 2.0]
    assert rows[3].tolist() == [0, 0, 0, 1]
    forward_world = rows[:3, :3] @ np.array([0, 0, -1])
    assert forward_world == pytest.approx([0, 1, 0])           # looks along +Y at a subject behind it
    assert rows[:3, :3] @ np.array([1, 0, 0]) == pytest.approx([1, 0, 0])   # camera right is world +X for a -Y view
