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
from types import SimpleNamespace
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
             preprocessing=None, profile="imagenet"):
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
            "preprocessing_profile": profile,
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
    # Whichever model is configured, the payload states its gate status:
    # an advertised capability, or that nothing is advertised for it.
    limits = payload_1["interpretation_limits"]
    assert limits == pil_embed.interpretation_limits(
        payload_1["engine"]["model_sha256"]
    )
    assert any(
        "ADVERTISES" in entry or "NOT been discrimination-gated" in entry
        for entry in limits
    )


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


class TestPreprocessingProfiles:
    """A model must be fed the preprocessing it was trained with.

    A wrong profile degrades quietly rather than failing -- the measured
    control still separated its pair families on a narrower margin -- so
    the profile is explicit, named in every payload, and part of the
    comparability key that compare refuses across.
    """

    def test_profiles_are_distinct_specs(self):
        imagenet = pil_embed.PREPROCESSING_PROFILES["imagenet"]
        clip = pil_embed.PREPROCESSING_PROFILES["clip"]
        assert imagenet != clip
        assert imagenet["normalize_mean"] != clip["normalize_mean"]
        assert imagenet["resample"] != clip["resample"]
        # Both crop to the same square: size alone cannot catch a mismatch,
        # which is exactly why the profile name is recorded.
        assert imagenet["center_crop"] == clip["center_crop"] == 224

    def test_default_profile_is_the_historical_preprocessing(self, monkeypatch):
        monkeypatch.delenv(pil_embed.PREPROCESSING_ENV_VAR, raising=False)
        assert pil_embed.PREPROCESSING is pil_embed.PREPROCESSING_PROFILES["imagenet"]
        assert pil_embed._resolve_preprocessing(None)[0] == "imagenet"

    def test_env_var_selects_profile(self, monkeypatch):
        monkeypatch.setenv(pil_embed.PREPROCESSING_ENV_VAR, "clip")
        name, spec = pil_embed._resolve_preprocessing(None)
        assert name == "clip"
        assert spec is pil_embed.PREPROCESSING_PROFILES["clip"]

    def test_explicit_flag_beats_env_var(self, monkeypatch):
        monkeypatch.setenv(pil_embed.PREPROCESSING_ENV_VAR, "clip")
        assert pil_embed._resolve_preprocessing("imagenet")[0] == "imagenet"

    def test_unknown_profile_is_refused(self):
        with pytest.raises(pil_embed.EmbedError) as excinfo:
            pil_embed._resolve_preprocessing("resnet-ish")
        assert "clip" in str(excinfo.value) and "imagenet" in str(excinfo.value)

    def test_model_input_size_mismatch_is_refused(self):
        class _Session:
            def get_inputs(self):
                return [SimpleNamespace(shape=["batch", 3, 299, 299])]

        with pytest.raises(pil_embed.EmbedError):
            pil_embed._check_model_input(
                _Session(), pil_embed.PREPROCESSING_PROFILES["clip"]
            )

    def test_model_input_size_match_is_accepted(self):
        class _Session:
            def get_inputs(self):
                return [SimpleNamespace(shape=["batch_size", 3, 224, 224])]

        pil_embed._check_model_input(
            _Session(), pil_embed.PREPROCESSING_PROFILES["clip"]
        )

    def test_compare_refuses_across_profiles(self, capsys, tmp_path):
        a = _payload(tmp_path, "a.json", [1.0, 0.0], profile="imagenet")
        b = _payload(
            tmp_path,
            "b.json",
            [1.0, 0.0],
            preprocessing=dict(pil_embed.PREPROCESSING_PROFILES["clip"]),
            profile="clip",
        )
        code, payload = _run(
            capsys, "compare", "--fingerprint-a", str(a), "--fingerprint-b", str(b)
        )
        assert code == 2
        assert payload is None


class TestGateVerdictsAreModelScoped:
    """No model inherits another model's advertised capability."""

    def test_gated_model_carries_its_own_verdict(self):
        limits = pil_embed.interpretation_limits(pil_embed.MOBILENET_V2_12_SHA256)
        assert any("mobilenetv2-12" in entry and "ADVERTISES" in entry for entry in limits)
        assert any("DEMOTED" in entry for entry in limits)

    def test_clip_model_carries_a_different_verdict(self):
        mobilenet = pil_embed.interpretation_limits(pil_embed.MOBILENET_V2_12_SHA256)
        clip = pil_embed.interpretation_limits(pil_embed.CLIP_VIT_B32_VISUAL_SHA256)
        assert clip != mobilenet
        assert any("CLIP" in entry for entry in clip)

    def test_ungated_model_advertises_nothing(self):
        limits = pil_embed.interpretation_limits("f" * 64)
        assert any("NOT been discrimination-gated" in entry for entry in limits)
        assert not any("ADVERTISES" in entry for entry in limits)

    def test_every_gated_model_has_a_run_directory_reference(self):
        for verdict in pil_embed.GATE_VERDICTS.values():
            assert "runs/" in verdict


@needs_engine
def test_profile_changes_the_fingerprint(capsys, image):
    """Same model, same bytes, different profile -> different vector."""
    _code, imagenet = _run(capsys, "embed", str(image), "--preprocessing", "imagenet")
    _code, clip = _run(capsys, "embed", str(image), "--preprocessing", "clip")
    assert imagenet["parameters"]["preprocessing_profile"] == "imagenet"
    assert clip["parameters"]["preprocessing_profile"] == "clip"
    assert (
        imagenet["images"]["a"]["fingerprint"]["unit_values"]
        != clip["images"]["a"]["fingerprint"]["unit_values"]
    )


@needs_engine
def test_payload_reports_whether_the_model_is_gated(capsys, image):
    _code, payload = _run(capsys, "embed", str(image))
    gated = payload["engine"]["model_sha256"] in pil_embed.GATE_VERDICTS
    assert payload["engine"]["model_gated"] is gated
    assert ("model_not_gated" in payload["flags"]) is not gated
