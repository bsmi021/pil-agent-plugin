"""Contract tests for the unified one-call image profile tool."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pil_image_analyze  # noqa: E402
import pil_image_info  # noqa: E402
import pil_palette_diff  # noqa: E402
import pil_structure_diff  # noqa: E402
from pil_common import dhash, hamming, load_rgb, to_working  # noqa: E402


def _run(capsys, *argv):
    code = pil_image_analyze.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out), out


@pytest.fixture
def ui_image(tmp_path):
    img = Image.new("RGB", (320, 240), (12, 12, 16))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 300, 60], fill=(30, 30, 38))
    draw.rectangle([25, 70, 90, 85], fill=(0, 200, 210))
    draw.ellipse([250, 200, 300, 220], fill=(200, 60, 30))
    path = tmp_path / "ui.png"
    img.save(path)
    return path


@pytest.fixture
def recolour_image(tmp_path, ui_image):
    img = Image.open(ui_image).convert("RGB")
    draw = ImageDraw.Draw(img)
    # Luminance-matched to the cyan it replaces (ITU-R 601-2 luma ~141), so
    # the recolour is invisible to every luminance-based metric by design.
    draw.rectangle([25, 70, 90, 85], fill=(250, 105, 45))
    path = tmp_path / "ui_recolour.png"
    img.save(path)
    return path


@pytest.fixture
def object_image(tmp_path):
    img = Image.new("RGB", (256, 256), (110, 110, 115))
    draw = ImageDraw.Draw(img)
    draw.polygon([(120, 30), (136, 30), (132, 200), (124, 200)], fill=(190, 195, 205))
    draw.rectangle([100, 200, 156, 215], fill=(120, 80, 30))
    path = tmp_path / "object.png"
    img.save(path)
    return path


def test_deterministic_output(capsys, ui_image):
    code_1, _payload, text_1 = _run(capsys, str(ui_image))
    code_2, _payload, text_2 = _run(capsys, str(ui_image))
    assert code_1 == code_2 == 0
    assert text_1 == text_2


def test_profile_contract(capsys, ui_image):
    code, payload, _ = _run(capsys, str(ui_image))
    assert code == 0
    assert payload["tool"] == "pil_image_analyze"
    profile = payload["images"]["a"]
    for block in ("file", "colour", "structure", "fingerprints", "tonal", "channels", "detail", "flags"):
        assert block in profile
    assert payload["diff"] is None
    assert payload["interpretation_limits"][0].startswith("This tool composes")


def test_composed_blocks_match_standalone_tools(capsys, ui_image):
    _code, payload, _ = _run(capsys, str(ui_image))
    profile = payload["images"]["a"]

    standalone_colour = pil_palette_diff.analyse(
        str(ui_image), 8, 100, 60, False, 0.035, "hsv", None, "frame"
    )
    standalone_structure, _working = pil_structure_diff.analyse(str(ui_image), 4, 3)
    standalone_file = pil_image_info.inspect_image(str(ui_image))

    # Round-trip through JSON so tuple/list representation matches.
    assert profile["colour"] == json.loads(json.dumps(standalone_colour))
    assert profile["structure"] == json.loads(json.dumps(standalone_structure))
    assert profile["file"] == json.loads(json.dumps(standalone_file))


def test_fingerprints_are_hex_and_match_raw_hashes(capsys, ui_image):
    _code, payload, _ = _run(capsys, str(ui_image))
    fp = payload["images"]["a"]["fingerprints"]
    for scope in ("full_frame", "subject"):
        for name in ("dhash", "ahash"):
            value = fp[scope][name]
            assert len(value) == 16
            int(value, 16)
    working = to_working(load_rgb(str(ui_image)))
    raw = dhash(working)
    assert fp["full_frame"]["dhash"] == np.packbits(raw.ravel()).tobytes().hex()
    assert pil_image_analyze.hex_hamming(fp["full_frame"]["dhash"], fp["full_frame"]["dhash"]) == 0


def test_cross_run_fingerprint_distance_matches_pairwise_hamming(capsys, tmp_path, ui_image):
    rescaled = tmp_path / "ui_small.png"
    Image.open(ui_image).resize((160, 120), Image.LANCZOS).save(rescaled)

    _c, payload_a, _ = _run(capsys, str(ui_image))
    _c, payload_b, _ = _run(capsys, str(rescaled))
    hex_a = payload_a["images"]["a"]["fingerprints"]["full_frame"]["dhash"]
    hex_b = payload_b["images"]["a"]["fingerprints"]["full_frame"]["dhash"]

    working_a = to_working(load_rgb(str(ui_image)))
    working_b = to_working(load_rgb(str(rescaled)))
    expected = hamming(dhash(working_a), dhash(working_b))
    assert pil_image_analyze.hex_hamming(hex_a, hex_b) == expected


def test_tonal_statistics_exact(capsys, tmp_path):
    arr = np.zeros((100, 100), dtype=np.uint8)
    arr[50:, :] = 255
    path = tmp_path / "half.png"
    Image.fromarray(arr).convert("RGB").save(path)

    _code, payload, _ = _run(capsys, str(path))
    tonal = payload["images"]["a"]["tonal"]
    assert tonal["min"] == 0
    assert tonal["max"] == 255
    assert tonal["clipped_black_fraction"] == 0.5
    assert tonal["clipped_white_fraction"] == 0.5
    assert tonal["near_black_fraction"] == 0.5
    assert tonal["near_white_fraction"] == 0.5
    assert tonal["percentiles"]["p25"] == 0.0
    assert tonal["percentiles"]["p75"] == 255.0


def test_channel_statistics_exact(capsys, tmp_path):
    img = Image.new("RGB", (10, 10))
    pixels = img.load()
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (7, 7, 7)]
    for y in range(10):
        for x in range(10):
            pixels[x, y] = colours[(x + y) % 4]
    path = tmp_path / "four.png"
    img.save(path)
    _code, payload, _ = _run(capsys, str(path))
    channels = payload["images"]["a"]["channels"]
    assert channels["unique_colours"] == 4
    assert channels["all_channels_equal"] is False

    grey = tmp_path / "grey.png"
    Image.fromarray(np.full((8, 8), 77, dtype=np.uint8)).convert("RGB").save(grey)
    _code, payload, _ = _run(capsys, str(grey))
    assert payload["images"]["a"]["channels"]["all_channels_equal"] is True
    assert payload["images"]["a"]["channels"]["unique_colours"] == 1


def test_pair_diff_matches_standalone_diffs(capsys, ui_image, recolour_image):
    _code, payload, _ = _run(capsys, str(ui_image), str(recolour_image))
    diff = payload["diff"]

    colour_a = pil_palette_diff.analyse(str(ui_image), 8, 100, 60, False, 0.035, "hsv", None, "frame")
    colour_b = pil_palette_diff.analyse(str(recolour_image), 8, 100, 60, False, 0.035, "hsv", None, "frame")
    expected_colour = pil_palette_diff.build_diff(colour_a, colour_b, foreground=False)

    structure_a, working_a = pil_structure_diff.analyse(str(ui_image), 4, 3)
    structure_b, working_b = pil_structure_diff.analyse(str(recolour_image), 4, 3)
    expected_structure = pil_structure_diff.build_diff(
        structure_a, structure_b, working_a, working_b, foreground=False
    )

    assert diff["colour"] == json.loads(json.dumps(expected_colour))
    assert diff["structure"] == json.loads(json.dumps(expected_structure))
    assert diff["file"]["identical_bytes"] is False
    assert diff["file"]["size_match"] is True
    # The flagship case: hashes blind to a recolour the colour block catches.
    assert diff["fingerprints"]["full_frame_dhash_distance"] == 0
    assert diff["colour"]["accent_hue_shift_detected"] is True


def test_unreadable_input_degrades_per_image(capsys, tmp_path, ui_image):
    bad = tmp_path / "bad.txt"
    bad.write_text("not an image")
    code, payload, _ = _run(capsys, str(bad), str(ui_image))
    assert code == 1
    assert payload["images"]["a"]["file"]["readable"] is False
    assert payload["images"]["a"]["flags"] == ["unreadable"]
    assert payload["images"]["a"]["colour"] is None
    assert payload["images"]["b"]["file"]["readable"] is True
    assert payload["diff"] is None


def test_region_matches_precropped_file(capsys, tmp_path, ui_image):
    region = [0.1, 0.25, 0.4, 0.5]
    _code, payload, _ = _run(capsys, str(ui_image), "--region", "0.1,0.25,0.4,0.5")
    with Image.open(ui_image) as img:
        width, height = img.size
        rect = pil_structure_diff.resolve_pixel_rect(region, (width, height))
        cropped = img.convert("RGB").crop(rect)
    crop_path = tmp_path / "crop.png"
    cropped.save(crop_path)
    _code, crop_payload, _ = _run(capsys, str(crop_path))

    for block in ("tonal", "channels", "detail"):
        assert payload["images"]["a"][block] == crop_payload["images"]["a"][block]
    assert (
        payload["images"]["a"]["fingerprints"]["full_frame"]
        == crop_payload["images"]["a"]["fingerprints"]["full_frame"]
    )


def test_foreground_subject_fingerprint_differs_from_frame(capsys, object_image):
    _code, payload, _ = _run(capsys, str(object_image), "--foreground")
    fp = payload["images"]["a"]["fingerprints"]
    assert fp["subject"] != fp["full_frame"]
    assert payload["images"]["a"]["colour"]["foreground"]["applied"] is True


def test_flags_aggregate_across_blocks(capsys, object_image):
    _code, payload, _ = _run(capsys, str(object_image))
    profile = payload["images"]["a"]
    expected = sorted(
        set(profile["file"]["flags"])
        | set(profile["colour"]["flags"])
        | set(profile["structure"]["flags"])
    )
    assert profile["flags"] == expected
    assert "background_dominant" in profile["flags"]


def test_interpretation_limits_compose_and_gate_alpha(capsys, tmp_path, ui_image):
    _code, payload, _ = _run(capsys, str(ui_image))
    limits = payload["interpretation_limits"]
    for entry in pil_image_analyze.INTERPRETATION_LIMITS:
        assert entry in limits
    for entry in pil_image_info.INTERPRETATION_LIMITS:
        assert entry in limits
    assert pil_palette_diff.ALPHA_INTERPRETATION_LIMITS[0] not in limits

    sprite = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sprite)
    draw.ellipse([16, 16, 48, 48], fill=(40, 160, 90, 200))
    sprite_path = tmp_path / "sprite.png"
    sprite.save(sprite_path)
    _code, payload, _ = _run(capsys, str(sprite_path), "--foreground")
    assert pil_palette_diff.ALPHA_INTERPRETATION_LIMITS[0] in payload["interpretation_limits"]
