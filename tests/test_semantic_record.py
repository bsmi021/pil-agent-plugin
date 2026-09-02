"""Contract tests for the semantic record tool: seal, verify, compare."""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pil_semantic_record  # noqa: E402


def _run(capsys, *argv):
    code = pil_semantic_record.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out) if out else None


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "img.png"
    Image.new("RGB", (200, 100), (30, 60, 90)).save(path)
    return path


@pytest.fixture
def claims_file(tmp_path):
    path = tmp_path / "claims.json"
    path.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "kind": "text_transcription",
                        "value": "THE CHANDELIER",
                        "region_fractional": [0.2, 0.1, 0.6, 0.3],
                        "confidence": "high",
                        "evidence": "read from a native-resolution crop",
                    },
                    {"kind": "landmark", "value": "The Cosmopolitan of Las Vegas"},
                    {"kind": "scene", "value": "casino interior"},
                ]
            }
        )
    )
    return path


def _seal(capsys, image, claims_file, tmp_path, name="record.json", **extra):
    argv = ["seal", str(image), "--claims", str(claims_file)]
    for key, value in extra.items():
        argv += [f"--{key.replace('_', '-')}", value]
    code, payload = _run(capsys, *argv)
    assert code == 0
    record_path = tmp_path / name
    record_path.write_text(json.dumps(payload))
    return payload, record_path


def test_seal_is_deterministic_and_binds_image(capsys, image, claims_file, tmp_path):
    payload_1, _ = _seal(capsys, image, claims_file, tmp_path)
    payload_2, _ = _seal(capsys, image, claims_file, tmp_path)
    assert payload_1 == payload_2
    record = payload_1["record"]
    assert record["schema"] == "semantic-record-v1"
    assert record["source"] == "vision_claim"
    assert record["image"]["size"] == [200, 100]
    assert len(record["image"]["sha256"]) == 64
    assert len(record["record_id"]) == 64
    regioned = [c for c in record["claims"] if c["region_fractional"] is not None]
    assert regioned[0]["resolved_pixel_rect"] == [40, 10, 120, 30]


def test_attribution_excluded_from_record_id(capsys, image, claims_file, tmp_path):
    plain, _ = _seal(capsys, image, claims_file, tmp_path)
    attributed, _ = _seal(
        capsys, image, claims_file, tmp_path, claimant="vision model", claimed_at="2026-08-31"
    )
    assert attributed["record"]["claimant"] == "vision model"
    assert attributed["record"]["record_id"] == plain["record"]["record_id"]


def test_verify_accepts_matching_file(capsys, image, claims_file, tmp_path):
    _payload, record_path = _seal(capsys, image, claims_file, tmp_path)
    code, payload = _run(capsys, "verify", str(image), "--record", str(record_path))
    assert code == 0
    assert payload["verification"]["verified"] is True
    assert payload["verification"]["failures"] == []


def test_verify_refuses_different_bytes(capsys, image, claims_file, tmp_path):
    _payload, record_path = _seal(capsys, image, claims_file, tmp_path)
    other = tmp_path / "other.png"
    Image.new("RGB", (200, 100), (31, 60, 90)).save(other)
    code, payload = _run(capsys, "verify", str(other), "--record", str(record_path))
    assert code == 1
    assert payload["verification"]["verified"] is False
    assert any("sha256_mismatch" in f for f in payload["verification"]["failures"])


def test_tampered_record_is_rejected(capsys, image, claims_file, tmp_path):
    payload, record_path = _seal(capsys, image, claims_file, tmp_path)
    tampered = payload["record"]
    tampered["claims"][0]["value"] = "A DIFFERENT SIGN"
    record_path.write_text(json.dumps({"record": tampered}))
    code, out = _run(capsys, "verify", str(image), "--record", str(record_path))
    assert code == 2
    assert out is None


def test_compare_reports_agreement_without_scores(capsys, image, claims_file, tmp_path):
    _p, record_a = _seal(capsys, image, claims_file, tmp_path, name="a.json")
    claims_b = tmp_path / "claims_b.json"
    claims_b.write_text(
        json.dumps(
            {
                "claims": [
                    # Case/whitespace variant of the same landmark: must match.
                    {"kind": "landmark", "value": "the  cosmopolitan of las vegas"},
                    {"kind": "scene", "value": "hotel lobby"},
                    {"kind": "object", "value": "giant red stiletto sculpture"},
                ]
            }
        )
    )
    _p, record_b = _seal(capsys, image, claims_b, tmp_path, name="b.json")
    code, payload = _run(
        capsys, "compare", "--record-a", str(record_a), "--record-b", str(record_b)
    )
    assert code == 0
    comparison = payload["comparison"]
    assert comparison["same_image_bytes"] is True
    kinds = comparison["claims_by_kind"]
    assert kinds["landmark"]["matched"] == ["The Cosmopolitan of Las Vegas"]
    assert kinds["scene"]["only_a"] == ["casino interior"]
    assert kinds["scene"]["only_b"] == ["hotel lobby"]
    assert kinds["object"]["only_b"] == ["giant red stiletto sculpture"]
    assert "similarity" not in json.dumps(comparison)


def test_invalid_claims_are_rejected_with_reason(capsys, tmp_path, image):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"claims": [{"kind": "geometry", "value": "12 polygons"}]}))
    code, out = _run(capsys, "seal", str(image), "--claims", str(bad))
    assert code == 2
    assert out is None

    bad.write_text(json.dumps({"claims": [{"kind": "scene", "value": "x", "region_fractional": [0.5, 0.1, 0.2, 0.9]}]}))
    code, _ = _run(capsys, "seal", str(image), "--claims", str(bad))
    assert code == 2


def test_interpretation_limits_present(capsys, image, claims_file, tmp_path):
    payload, _ = _seal(capsys, image, claims_file, tmp_path)
    limits = payload["interpretation_limits"]
    assert any("VISION CLAIM" in entry for entry in limits)
    assert any("BINDING, not truth" in entry for entry in limits)
