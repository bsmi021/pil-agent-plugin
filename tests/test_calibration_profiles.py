import pytest
from PIL import Image
from pil_calibrate import calibrate, evaluate_profile


def corpus(tmp_path):
    pairs = []
    for split in ["train", "validation"]:
        for i in range(3):
            a = tmp_path / f"{split}-{i}.png"
            b = tmp_path / f"{split}-{i}-changed.png"
            im = Image.new(
                "RGB",
                (32, 32),
                (50 + i * 10 + (30 if split == "validation" else 0), 70, 90),
            )
            im.save(a)
            im.paste("red", (0, 0, 8, 8))
            im.save(b)
            for changed, candidate in [(False, a), (True, b)]:
                pairs.append(
                    {
                        "source": f"{split}-{i}",
                        "split": split,
                        "a": str(a),
                        "b": str(candidate),
                        "changed": changed,
                        "perturbation": "patch",
                        "magnitude": 0.0625,
                    }
                )
    return {
        "schema": "calibration-corpus-v1",
        "domain": "screenshot",
        "input_mode": "stored",
        "pairs": pairs,
    }


def test_group_holdout_and_deterministic_profile(tmp_path):
    manifest = corpus(tmp_path)
    profile = calibrate(manifest)
    assert profile == calibrate(manifest)
    assert profile["validation"]["false_alarm_rate"] == 0
    assert profile["validation"]["miss_rate"] == 0
    assert profile["validation"]["accepted"]
    pair = manifest["pairs"][-1]
    result = evaluate_profile(profile, pair["a"], pair["b"], domain="screenshot")
    assert result["verdict"] == "CHANGE_DETECTED"
    assert result["legacy_structure"]["structural_similarity"] is not None


def test_source_leakage_and_profile_mismatch_refused(tmp_path):
    manifest = corpus(tmp_path)
    manifest["pairs"][-1]["source"] = "train-0"
    with pytest.raises(ValueError, match="source"):
        calibrate(manifest)
    manifest = corpus(tmp_path)
    profile = calibrate(manifest)
    pair = manifest["pairs"][0]
    with pytest.raises(ValueError, match="domain"):
        evaluate_profile(profile, pair["a"], pair["b"], domain="photograph")


def test_byte_leakage_refused(tmp_path):
    manifest = corpus(tmp_path)
    manifest["pairs"][-1]["a"] = manifest["pairs"][0]["a"]
    with pytest.raises(ValueError, match="bytes"):
        calibrate(manifest)
