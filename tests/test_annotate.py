"""Behavioural tests for the W4 grounding overlay.

Each test names the concrete regression it protects so a green result cannot
be mistaken for proof of an unrelated property.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "pil_annotate.py"
STRUCTURE_DIFF = REPO_ROOT / "scripts" / "pil_structure_diff.py"
BOX_COLOUR = (255, 170, 0)


def _run(source, output, *args):
    """Invoke W4 in a subprocess because the contract is about CLI boundaries."""
    command = [sys.executable, str(SCRIPT), str(source), "--out", str(output), *map(str, args)]
    return subprocess.run(command, capture_output=True, text=True)


def _save_image(path, image):
    """Write a synthetic fixture because tests must be reproducible without binaries."""
    image.save(path, format="PNG")
    return path


def _structure_diff_payload(tmp_path):
    """Run the real producer because a hand-written dict cannot catch a field rename.

    A4.6 says "on a real pil_structure_diff payload": an inline json.dumps would
    keep passing after the diff tool renamed most_divergent_cells, while every
    real invocation silently drew nothing.
    """
    reference = _save_image(tmp_path / "reference.png", Image.new("RGB", (200, 150), (200, 200, 200)))
    changed = Image.new("RGB", (200, 150), (200, 200, 200))
    ImageDraw.Draw(changed).rectangle([120, 90, 180, 140], fill=(20, 60, 180))
    variant = _save_image(tmp_path / "variant.png", changed)
    result = subprocess.run(
        [sys.executable, str(STRUCTURE_DIFF), str(reference), str(variant)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    path = tmp_path / "structure.json"
    path.write_text(result.stdout, encoding="utf-8")
    return path, json.loads(result.stdout)


def _overlap(first, second):
    """Re-derive rect intersection in the test because the tool's own helper is what is on trial."""
    return (
        first[0] < second[2]
        and second[0] < first[2]
        and first[1] < second[3]
        and second[1] < first[3]
    )


def _outline_bands(rect, thickness):
    """Re-derive where Pillow inks a box outline because a glyph must not land on it.

    ``rectangle(..., width=t)`` strokes inward, so the ink is four t-deep bands
    rather than the whole rect.
    """
    left, top, right, bottom = rect
    depth = max(1, min(thickness, right - left, bottom - top))
    return [
        (left, top, left + depth, bottom),
        (right - depth, top, right, bottom),
        (left, top, right, top + depth),
        (left, bottom - depth, right, bottom),
    ]


def _assert_placement_names_are_geometric_facts(legend):
    """Assert A4.5 over a whole legend because a placement label may not outrun its pixels.

    ``outside_*`` must be disjoint from the box it labels, ``inside_*`` must be
    contained by it, and any other geometry must be admitted as ``clamped``.
    """
    for entry in legend:
        box = entry["pixel_rect"]
        glyph = entry["glyph_rect"]
        placement = entry["glyph_placement"]
        if placement.startswith("outside_"):
            assert not _overlap(glyph, box), f"{placement} glyph {glyph} intersects box {box}"
        elif placement.startswith("inside_"):
            assert (
                box[0] <= glyph[0]
                and box[1] <= glyph[1]
                and glyph[2] <= box[2]
                and glyph[3] <= box[3]
            ), f"{placement} glyph {glyph} is not inside box {box}"
        else:
            assert placement == "clamped", f"unrecognised placement {placement!r}"


def _box_colour_pixels_under(image, glyph_rect):
    """Count outline ink inside a numeral's footprint because that is what made it unreadable.

    The critic measured 82 such pixels through a superimposed numeral; counting
    them is a direct, pixel-level restatement of the defect.
    """
    left, top, right, bottom = glyph_rect
    pixels = image.convert("RGB").load()
    return sum(
        1
        for y in range(top, bottom)
        for x in range(left, right)
        if pixels[x, y] == BOX_COLOUR
    )


def test_shuffled_boxes_have_identical_png_and_position_stable_numbers(tmp_path):
    """WHY: input-order numbering would make two agents disagree about box 1."""
    # Arrange
    source = _save_image(tmp_path / "source.png", Image.new("RGB", (400, 300), (30, 30, 30)))
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"

    # Act
    result_a = _run(source, first, "--box", "0.60,0.10,0.90,0.40", "--box", "0.10,0.50,0.40,0.90")
    result_b = _run(source, second, "--box", "0.10,0.50,0.40,0.90", "--box", "0.60,0.10,0.90,0.40")

    # Assert
    assert result_a.returncode == 0, result_a.stderr
    assert result_b.returncode == 0, result_b.stderr
    assert first.read_bytes() == second.read_bytes()
    legend_a = json.loads(result_a.stdout)["legend"]
    legend_b = json.loads(result_b.stdout)["legend"]
    assert [entry["requested_index"] for entry in legend_a] != [entry["requested_index"] for entry in legend_b]
    assert [entry["pixel_rect"] for entry in legend_a] == [entry["pixel_rect"] for entry in legend_b]


def test_identical_invocations_have_identical_json_and_png(tmp_path):
    """WHY: timestamps, unordered data, or unstable PNG metadata would break reproducible evidence."""
    # Arrange
    source = _save_image(tmp_path / "source.png", Image.new("RGB", (120, 90), (70, 70, 70)))
    output = tmp_path / "annotated.png"
    arguments = ("--box", "0.10,0.10,0.80,0.80", "--grid", "3x2", "--overwrite")

    # Act
    first = _run(source, output, *arguments)
    first_bytes = output.read_bytes()
    second = _run(source, output, *arguments)
    second_bytes = output.read_bytes()

    # Assert
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    assert first_bytes == second_bytes


def test_source_bytes_and_payload_provenance_are_preserved(tmp_path):
    """WHY: drawing through an open image object must never mutate the source."""
    # Arrange
    source = _save_image(tmp_path / "source.png", Image.new("RGBA", (64, 48), (20, 40, 80, 255)))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "annotated.png"

    # Act
    result = _run(source, output, "--box", "0.20,0.20,0.80,0.80")

    # Assert
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert payload["source"]["sha256"] == before
    assert payload["output"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert payload["source"]["size"] == [64, 48]
    assert payload["output"]["size"] == [64, 48]


def test_local_glyph_contrast_is_not_global_contrast(tmp_path):
    """WHY: a global mean can choose white text on a bright local background."""
    # Arrange
    image = Image.new("RGB", (400, 300), (10, 10, 10))
    draw = ImageDraw.Draw(image)
    draw.rectangle([50, 78, 64, 98], fill=(255, 255, 255))
    source = _save_image(tmp_path / "contrast.png", image)
    output = tmp_path / "annotated.png"

    # Act
    result = _run(
        source,
        output,
        "--box",
        "0.125,0.333333,0.25,0.50",
        "--box",
        "0.625,0.333333,0.75,0.50",
    )

    # Assert
    assert result.returncode == 0, result.stderr
    colours = [entry["glyph_colour"] for entry in json.loads(result.stdout)["legend"]]
    assert colours == ["#000000", "#ffffff"]


def test_grid_is_under_box_outline_and_is_reported(tmp_path):
    """WHY: drawing gridlines after boxes would erase a boundary at coincident pixels."""
    # Arrange
    source = _save_image(tmp_path / "grid.png", Image.new("RGB", (400, 300), (40, 40, 40)))
    output = tmp_path / "annotated.png"

    # Act
    result = _run(source, output, "--grid", "4x3", "--box", "0.25,0.0,0.50,0.50", "--thickness", "1")

    # Assert
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["grid"] == {"cols": 4, "rows": 3, "lines_drawn": 5}
    assert Image.open(output).convert("RGB").getpixel((100, 100)) == (255, 170, 0)


def test_from_json_uses_only_the_two_declared_region_sources(tmp_path):
    """WHY: interpreting unrelated diff metrics would silently add boxes the caller did not request."""
    # Arrange
    source = _save_image(tmp_path / "source.png", Image.new("RGB", (200, 150), (80, 80, 80)))
    structure, payload = _structure_diff_payload(tmp_path)
    diff = payload["diff"]
    uninterpreted = set(diff) - {"most_divergent_cells", "changed_region_bbox_fractional"}
    expected = len(diff["most_divergent_cells"]) + (1 if diff["changed_region_bbox_fractional"] else 0)
    output = tmp_path / "annotated.png"

    # Act
    result = _run(source, output, "--from-json", structure)

    # Assert
    assert result.returncode == 0, result.stderr
    assert uninterpreted, "a payload with no other metrics could not show that they are ignored"
    assert expected > 1, "the fixture must produce both region kinds"
    legend = json.loads(result.stdout)["legend"]
    assert len(legend) == expected
    counted = [entry["source"] for entry in legend]
    assert counted.count("--from-json:most_divergent_cells") == len(diff["most_divergent_cells"])
    assert counted.count("--from-json:changed_region_bbox_fractional") == 1
    _assert_placement_names_are_geometric_facts(legend)


def test_rejections_have_status_two_and_empty_stdout(tmp_path):
    """WHY: malformed requests must not leak a half-built JSON document to an agent."""
    # Arrange
    source = _save_image(tmp_path / "source.png", Image.new("RGB", (32, 32), (0, 0, 0)))

    # Act
    bad_box = _run(source, tmp_path / "bad-box.png", "--box", "0,0,1.2,1")
    missing = _run(tmp_path / "missing.png", tmp_path / "missing-out.png", "--box", "0,0,1,1")
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"keep")
    overwrite = _run(source, existing, "--box", "0,0,1,1")

    # Assert
    for result in (bad_box, missing, overwrite):
        assert result.returncode == 2
        assert result.stdout == ""
    assert existing.read_bytes() == b"keep"


def test_right_edge_box_labels_outside_instead_of_lying_about_inside(tmp_path):
    """WHY: horizontal overflow alone used to drive the numeral inside, onto its own outline and a grid line, while the legend still called it "inside"."""
    # Arrange
    image = Image.new("RGB", (400, 300), (245, 245, 240))
    ImageDraw.Draw(image).rectangle([380, 80, 399, 200], fill=(200, 40, 40))
    source = _save_image(tmp_path / "right-edge.png", image)
    output = tmp_path / "annotated.png"

    # Act
    result = _run(source, output, "--box", "0.97,0.30,1.00,0.60", "--box", "0.10,0.13,0.50,0.67")

    # Assert
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    legend = payload["legend"]
    _assert_placement_names_are_geometric_facts(legend)
    edge = next(entry for entry in legend if entry["pixel_rect"] == [388, 90, 400, 180])
    assert edge["glyph_placement"] == "outside_top_shifted"
    assert not _overlap(edge["glyph_rect"], edge["pixel_rect"])
    # Clear of the frame border by a glyph cell, so a reader does not report the
    # numeral as clipped and offer a different digit -- which is what happened
    # when it was drawn flush at x == 400.
    assert 3 <= edge["glyph_rect"][0] and edge["glyph_rect"][2] <= 400 - 3
    assert edge["glyph_hazards"] == []
    assert payload["flags"] == []
    assert _box_colour_pixels_under(Image.open(output), edge["glyph_rect"]) == 0


def test_overlapping_boxes_do_not_superimpose_or_cut_their_numerals(tmp_path):
    """WHY: near-equal tops within a glyph width used to stack both numerals in one place, and the second outline then cut through the first."""
    # Arrange
    image = Image.new("RGB", (400, 300), (245, 245, 240))
    ImageDraw.Draw(image).rectangle([30, 10, 160, 140], fill=(120, 180, 220))
    source = _save_image(tmp_path / "overlapping.png", image)
    output = tmp_path / "annotated.png"

    # Act
    result = _run(source, output, "--grid", "1x1", "--box", "0.10,0.0,0.30,0.40", "--box", "0.12,0.0,0.40,0.50")

    # Assert
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    legend = payload["legend"]
    _assert_placement_names_are_geometric_facts(legend)
    first, second = (entry["glyph_rect"] for entry in legend)
    assert not _overlap(first, second), f"numerals {first} and {second} are superimposed"
    annotated = Image.open(output)
    for entry in legend:
        for other in legend:
            for band in _outline_bands(other["pixel_rect"], 2):
                assert not _overlap(entry["glyph_rect"], band)
        assert _box_colour_pixels_under(annotated, entry["glyph_rect"]) == 0
    assert payload["flags"] == []


def test_a_numeral_that_cannot_be_placed_clear_is_flagged_not_hidden(tmp_path):
    """WHY: a hardcoded empty flags list meant an unreadable overlay reported itself as clean."""
    # Arrange
    source = _save_image(tmp_path / "tiny.png", Image.new("RGB", (16, 24), (230, 230, 230)))
    output = tmp_path / "annotated.png"

    # Act
    result = _run(source, output, "--grid", "1x1", "--box", "0,0,1,1", "--box", "0.05,0,0.95,1")

    # Assert
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "glyph_overlaps_glyph" in payload["flags"]
    assert "glyph_clamped_into_frame" in payload["flags"]
    legend = payload["legend"]
    _assert_placement_names_are_geometric_facts(legend)
    assert [entry["glyph_placement"] for entry in legend] == ["clamped", "clamped"]
    assert "glyph_overlaps_glyph" in legend[1]["glyph_hazards"]
    # Numerals are painted after every outline, so glyph ink survives the collision.
    assert Image.open(output).convert("RGB").getpixel((7, 0)) == (0, 0, 0)


def test_module_uses_geometric_digits_without_font_dependency():
    """WHY: a font dependency would make the promised cross-machine PNG determinism false."""
    # Arrange
    source_text = SCRIPT.read_text(encoding="utf-8")

    # Act
    forbidden = "ImageFont"

    # Assert
    assert forbidden not in source_text
    assert "_DIGITS" in source_text
