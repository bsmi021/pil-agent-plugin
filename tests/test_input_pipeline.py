import json
from types import SimpleNamespace

import pytest
from PIL import Image, ImageCms

from pil_normalize import normalize_image
from pil_mask import create_mask, read_mask
from pil_pipeline import measure
import pil_embed


def test_display_orientation_and_alpha(tmp_path):
    path = tmp_path / "rotated.png"
    image = Image.new("RGBA", (60, 30), (30, 60, 90, 123))
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif)
    stored, _ = normalize_image(path, "stored")
    display, metadata = normalize_image(path, "display")
    assert stored.size == (60, 30)
    assert display.size == (30, 60)
    assert display.getpixel((0, 0)) == (30, 60, 90, 123)
    assert metadata["orientation_applied"] == 6
    assert metadata["source_to_output"] == [[0, -1, 30], [1, 0, 0], [0, 0, 1]]


def test_icc_conversion_and_invalid_profile(tmp_path):
    path = tmp_path / "lab.tif"
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
    Image.new("LAB", (8, 8), (128, 128, 128)).save(path, icc_profile=profile.tobytes())
    converted, metadata = normalize_image(path, "display")
    assert abs(converted.getpixel((0, 0))[0] - 119) <= 2
    assert metadata["colour_transform_applied"]
    bad = tmp_path / "bad.png"
    Image.new("RGB", (8, 8)).save(bad, icc_profile=b"bad")
    with pytest.raises(ValueError, match="ICC"):
        normalize_image(bad, "display")


def test_mask_holes_binding_and_transparent_support(tmp_path):
    path = tmp_path / "a.png"
    im = Image.new("RGBA", (20, 20), (255, 0, 0, 128))
    im.save(path)
    spec = {
        "name": "part",
        "polygons": [[[2, 2], [17, 2], [17, 17], [2, 17]]],
        "subtract_polygons": [[[7, 7], [12, 7], [12, 12], [7, 12]]],
    }
    manifest = create_mask(path, spec, tmp_path / "selection.json")
    mask, _ = read_mask(tmp_path / "selection.json", path)
    assert mask[3, 3] and not mask[8, 8] and not mask[0, 0]
    assert manifest["selection_semantics"] == "binary_selection_not_alpha_coverage"
    im.putpixel((0, 0), (0, 0, 0, 0))
    im.save(path)
    with pytest.raises(ValueError, match="binding"):
        read_mask(tmp_path / "selection.json", path)


def test_pipeline_isolates_clutter_and_preserves_colour(tmp_path):
    path = tmp_path / "a.png"
    im = Image.new("RGB", (80, 80), "blue")
    im.paste("red", (20, 20, 60, 60))
    im.save(path)
    create_mask(
        path,
        {"name": "red", "polygons": [[[20, 20], [59, 20], [59, 59], [20, 59]]]},
        tmp_path / "mask.json",
    )
    result = measure("pil_palette_diff", path, mask_a=tmp_path / "mask.json")
    assert result["result"]["images"]["a"]["base_palette"][0]["hex"] == "#ff0000"
    assert result["inputs"]["a"]["mask"]["source"] == "explicit_selection"
    assert result["calibration_status"] == "requires_pipeline_specific_profile"


def test_wrong_profile_cannot_advertise_clip_claim(tmp_path):
    payload = {
        "tool": "pil_embed",
        "engine": {"model_sha256": pil_embed.CLIP_VIT_B32_VISUAL_SHA256},
        "parameters": {"preprocessing": pil_embed.PREPROCESSING_PROFILES["imagenet"]},
        "images": {
            "a": {"sha256": "fixture", "fingerprint": {"unit_values": [1.0, 0.0]}}
        },
    }
    path = tmp_path / "fp.json"
    path.write_text(json.dumps(payload))
    result = pil_embed.run_compare(
        SimpleNamespace(fingerprint_a=path, fingerprint_b=path)
    )
    assert not result["engine"]["model_gated"]
    assert "model_configuration_not_gated" in result["flags"]
    assert not any("ADVERTISES" in line for line in result["interpretation_limits"])


@pytest.mark.parametrize("orientation", range(1, 9))
def test_all_orientation_matrices_map_source_pixels(tmp_path, orientation):
    path = tmp_path / "orientation.png"
    image = Image.new("RGB", (5, 3))
    for y in range(3):
        for x in range(5):
            image.putpixel((x, y), (x * 30, y * 60, 20))
    exif = Image.Exif()
    exif[274] = orientation
    image.save(path, exif=exif)
    result, metadata = normalize_image(path, "display")
    matrix = metadata["source_to_output"]
    for y in range(3):
        for x in range(5):
            mapped = [
                sum(row[i] * value for i, value in enumerate((x + 0.5, y + 0.5, 1)))
                for row in matrix
            ]
            assert result.getpixel((int(mapped[0]), int(mapped[1])))[
                :3
            ] == image.getpixel((x, y))


def test_full_alpha_selection_preserves_original_coverage_weighting(tmp_path):
    path = tmp_path / "alpha.png"
    image = Image.new("RGBA", (40, 40), (255, 0, 0, 128))
    image.save(path)
    selection = tmp_path / "mask.json"
    create_mask(
        path,
        {"name": "all", "polygons": [[[0, 0], [39, 0], [39, 39], [0, 39]]]},
        selection,
    )
    selected = measure("pil_palette_diff", path, mask_a=selection)
    original = measure("pil_palette_diff", path, arguments=["--foreground"])
    assert (
        selected["result"]["images"]["a"]["luminance"]
        == original["result"]["images"]["a"]["luminance"]
    )


def test_malformed_selection_refuses_without_traceback(tmp_path, capsys):
    from pil_pipeline import main

    image = tmp_path / "a.png"
    Image.new("RGB", (20, 20), "red").save(image)
    selection = tmp_path / "mask.json"
    selection.write_text("[]")
    code = main(
        [
            "--tool",
            "pil_palette_diff",
            "--image-a",
            str(image),
            "--mask-a",
            str(selection),
        ]
    )
    output = capsys.readouterr()
    assert code == 2 and output.out == ""
    assert "Traceback" not in output.err
