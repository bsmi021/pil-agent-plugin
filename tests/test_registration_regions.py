import numpy as np
import pytest
from PIL import Image, ImageDraw
from pil_register import register_images
from pil_diff_regions import compare_images


def pair(tmp_path):
    rng = np.random.default_rng(17)
    a = Image.fromarray(rng.integers(30, 220, (96, 128, 3), dtype="uint8"))
    b = Image.new("RGB", a.size)
    b.paste(a, (5, -3))
    pa, pb = tmp_path / "a.png", tmp_path / "b.png"
    a.save(pa)
    b.save(pb)
    return pa, pb


def test_translation_retains_raw_comparison_and_overlap(tmp_path):
    a, b = pair(tmp_path)
    result = register_images(a, b, max_shift=10)
    assert result["status"] == "REGISTERED"
    assert result["candidate_to_reference"][0][2] == pytest.approx(-5, abs=0.2)
    assert result["candidate_to_reference"][1][2] == pytest.approx(3, abs=0.2)
    assert result["aligned"]["delta_e_mean"] < result["raw"]["delta_e_mean"] / 10
    assert result["aligned"]["delta_e_mean"] < 0.05
    assert 0.85 < result["valid_overlap_fraction"] < 1


def test_registration_refuses_out_of_bounds_and_constant(tmp_path):
    a, b = pair(tmp_path)
    assert register_images(a, b, max_shift=2)["status"] == "UNMEASURABLE"
    Image.new("RGB", (128, 96), "gray").save(a)
    Image.new("RGB", (128, 96), "gray").save(b)
    assert register_images(a, b)["status"] == "UNMEASURABLE"


def test_local_changes_have_separate_native_boxes(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (600, 600), "gray").save(a)
    im = Image.open(a).copy()
    draw = ImageDraw.Draw(im)
    draw.rectangle((10, 10, 12, 12), fill="red")
    draw.rectangle((500, 500, 503, 503), fill="blue")
    im.save(b)
    result, _, _ = compare_images(a, b, delta_e_threshold=2)
    assert result["changed_pixels"] == 25
    assert len(result["regions"]) == 2
    assert sorted(r["bbox_pixels"] for r in result["regions"]) == [
        [10, 10, 13, 13],
        [500, 500, 504, 504],
    ]


def test_alpha_change_and_dimension_refusal(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGBA", (20, 20), (0, 0, 0, 0)).save(a)
    Image.new("RGBA", (20, 20), (0, 0, 0, 255)).save(b)
    result, _, _ = compare_images(a, b)
    assert result["changed_pixels"] == 400
    Image.new("RGB", (10, 20)).save(b)
    with pytest.raises(ValueError, match="dimensions"):
        compare_images(a, b)


@pytest.mark.parametrize("motion,scale", [("rigid", 1.0), ("affine", 1.02)])
def test_explicit_rigid_and_affine_models_improve_known_warps(tmp_path, motion, scale):
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(42)
    pixels = cv2.GaussianBlur(
        rng.integers(0, 255, (160, 160, 3), dtype="uint8"), (5, 5), 1
    )
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.fromarray(pixels).save(a)
    matrix = cv2.getRotationMatrix2D((80, 80), 1.5, scale)
    matrix[:, 2] += [2, -1]
    Image.fromarray(cv2.warpAffine(pixels, matrix, (160, 160))).save(b)
    result = register_images(a, b, motion=motion, max_shift=15)
    assert result["status"] == "REGISTERED"
    assert result["aligned"]["delta_e_mean"] < result["raw"]["delta_e_mean"] / 2
