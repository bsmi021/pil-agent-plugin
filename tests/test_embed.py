"""Contract tests for the embedding fingerprint tool.

Runtime-dependent tests skip cleanly without onnxruntime or without a model
(PIL_AGENT_EMBED_MODEL), mirroring the PIL_AGENT_REFERENCE_IMAGE and
Blender-install conventions. The compare path needs neither, so its
refusal contracts always run.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pil_embed  # noqa: E402

HAS_RUNTIME = importlib.util.find_spec("onnxruntime") is not None
MODEL = os.environ.get(pil_embed.MODEL_ENV_VAR)
HAS_MODEL = MODEL is not None and Path(MODEL).is_file()

needs_engine = pytest.mark.skipif(
    not (HAS_RUNTIME and HAS_MODEL),
    reason="requires onnxruntime (uv sync --extra embedding) and "
    f"{pil_embed.MODEL_ENV_VAR} pointing at an ONNX model",
)


def _run(capsys, *argv):
    code = pil_embed.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out) if out else None


def _payload(tmp_path, name, unit_values, model_sha="a" * 64, image_sha="b" * 64,
             preprocessing=None):
    payload = {
        "tool": "pil_embed",
        "version": pil_embed.TOOL_VERSION,
        "command": "embed",
        "engine": {
            "runtime": "onnxruntime test",
            "providers": ["CPUExecutionProvider"],
            "model_file": "test.onnx",
            "model_sha256": model_sha,
            "output_dim": len(unit_values),
        },
        "parameters": {
            "region": None,
            "preprocessing": preprocessing or dict(pil_embed.PREPROCESSING),
            "vector_decimals": pil_embed.VECTOR_DECIMALS,
        },
        "images": {
            "a": {
                "path": name,
                "sha256": image_sha,
                "size": [10, 10],
                "region": None,
                "fingerprint": {
                    "dim": len(unit_values),
                    "l2_norm": 1.0,
                    "unit_values": unit_values,
                },
            }
        },
        "diff": None,
        "flags": [],
        "interpretation_limits": [],
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "img.png"
    Image.new("RGB", (320, 240), (40, 90, 140)).save(path)
    return path


def test_missing_model_exits_2_with_empty_stdout(capsys, image, monkeypatch):
    monkeypatch.delenv(pil_embed.MODEL_ENV_VAR, raising=False)
    code, payload = _run(capsys, "embed", str(image))
    assert code == 2
    assert payload is None


def test_compare_identical_and_orthogonal_vectors(capsys, tmp_path):
    a = _payload(tmp_path, "a.json", [1.0, 0.0, 0.0], image_sha="1" * 64)
    b = _payload(tmp_path, "b.json", [1.0, 0.0, 0.0], image_sha="1" * 64)
    code, payload = _run(capsys, "compare", "--fingerprint-a", str(a), "--fingerprint-b", str(b))
    assert code == 0
    assert payload["comparison"]["cosine_similarity"] == 1.0
    assert payload["comparison"]["same_image_bytes"] is True

    c = _payload(tmp_path, "c.json", [0.0, 1.0, 0.0], image_sha="2" * 64)
    code, payload = _run(capsys, "compare", "--fingerprint-a", str(a), "--fingerprint-b", str(c))
    assert code == 0
    assert payload["comparison"]["cosine_similarity"] == 0.0
    assert payload["comparison"]["same_image_bytes"] is False


def test_compare_refuses_model_mismatch(capsys, tmp_path):
    a = _payload(tmp_path, "a.json", [1.0, 0.0], model_sha="a" * 64)
    b = _payload(tmp_path, "b.json", [1.0, 0.0], model_sha="c" * 64)
    code, payload = _run(capsys, "compare", "--fingerprint-a", str(a), "--fingerprint-b", str(b))
    assert code == 2
    assert payload is None


def test_compare_refuses_preprocessing_mismatch(capsys, tmp_path):
    a = _payload(tmp_path, "a.json", [1.0, 0.0])
    other = dict(pil_embed.PREPROCESSING, center_crop=128)
    b = _payload(tmp_path, "b.json", [1.0, 0.0], preprocessing=other)
    code, _ = _run(capsys, "compare", "--fingerprint-a", str(a), "--fingerprint-b", str(b))
    assert code == 2


def test_compare_refuses_dimension_mismatch(capsys, tmp_path):
    a = _payload(tmp_path, "a.json", [1.0, 0.0, 0.0])
    b = _payload(tmp_path, "b.json", [1.0, 0.0])
    code, _ = _run(capsys, "compare", "--fingerprint-a", str(a), "--fingerprint-b", str(b))
    assert code == 2


def test_compare_refuses_non_embed_payload(capsys, tmp_path, image):
    bogus = tmp_path / "bogus.json"
    bogus.write_text(json.dumps({"tool": "pil_palette_diff"}))
    a = _payload(tmp_path, "a.json", [1.0, 0.0])
    code, _ = _run(capsys, "compare", "--fingerprint-a", str(a), "--fingerprint-b", str(bogus))
    assert code == 2


@needs_engine
def test_embed_contract_and_determinism(capsys, image):
    code_1, payload_1 = _run(capsys, "embed", str(image))
    code_2, payload_2 = _run(capsys, "embed", str(image))
    assert code_1 == code_2 == 0
    assert payload_1 == payload_2
    fingerprint = payload_1["images"]["a"]["fingerprint"]
    assert fingerprint["dim"] == payload_1["engine"]["output_dim"]
    assert len(fingerprint["unit_values"]) == fingerprint["dim"]
    assert len(payload_1["engine"]["model_sha256"]) == 64
    assert any("DEMOTED" in entry for entry in payload_1["interpretation_limits"])


@needs_engine
def test_pair_cosine_matches_stored_compare(capsys, tmp_path, image):
    other = tmp_path / "other.png"
    Image.open(image).resize((160, 120), Image.LANCZOS).save(other)

    _code, pair_payload = _run(capsys, "embed", str(image), str(other))
    pair_cosine = pair_payload["diff"]["cosine_similarity"]
    assert pair_cosine > 0.99  # a rescale is the same image

    _code, payload_a = _run(capsys, "embed", str(image))
    _code, payload_b = _run(capsys, "embed", str(other))
    file_a = tmp_path / "fa.json"
    file_b = tmp_path / "fb.json"
    file_a.write_text(json.dumps(payload_a))
    file_b.write_text(json.dumps(payload_b))
    code, compare_payload = _run(
        capsys, "compare", "--fingerprint-a", str(file_a), "--fingerprint-b", str(file_b)
    )
    assert code == 0
    assert compare_payload["comparison"]["cosine_similarity"] == pair_cosine


@needs_engine
def test_region_changes_fingerprint(capsys, tmp_path):
    img = Image.new("RGB", (400, 400), (240, 240, 240))
    for x in range(200, 400):
        for y in range(200, 400):
            img.putpixel((x, y), (200, 30, 30))
    path = tmp_path / "quad.png"
    img.save(path)
    _code, full_payload = _run(capsys, "embed", str(path))
    _code, region_payload = _run(capsys, "embed", str(path), "--region", "0.5,0.5,1.0,1.0")
    assert region_payload["images"]["a"]["region"]["resolved_pixel_rect"] == [200, 200, 400, 400]
    assert (
        full_payload["images"]["a"]["fingerprint"]["unit_values"]
        != region_payload["images"]["a"]["fingerprint"]["unit_values"]
    )
