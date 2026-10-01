"""Contract tests for pil_depth: estimate, compare, diagnose.

ONNX is stubbed: a fake ``onnxruntime`` module whose session returns a
known depth map, so preprocessing, payloads, alignment maths and every refusal
run in CI without onnxruntime or a model file. The real-model smoke test is
skipped unless PIL_AGENT_DEPTH_MODEL points at a Depth Anything V2 Small file.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pil_depth  # noqa: E402
import pil_mask  # noqa: E402

REAL_MODEL = os.environ.get(pil_depth.MODEL_ENV_VAR)
needs_real_model = pytest.mark.skipif(
    not (
        importlib.util.find_spec("onnxruntime") is not None
        and REAL_MODEL
        and Path(REAL_MODEL).is_file()
    ),
    reason=f"requires onnxruntime and {pil_depth.MODEL_ENV_VAR} pointing at a depth ONNX model",
)


# ------------------------------------------------------------------ stubs


class FakeSession:
    """Records what it is fed; returns a fixed (or computed) depth map."""

    last = None

    def __init__(self, path, sess_options=None, providers=None, *, shape, output=None):
        self.path = path
        self.providers = providers
        self.shape = shape
        self.output = output
        self.fed = []

    def get_inputs(self):
        return [SimpleNamespace(name="pixel_values", shape=self.shape)]

    def get_outputs(self):
        return [SimpleNamespace(name="predicted_depth", shape=["batch", "h", "w"])]

    def run(self, names, feeds):
        tensor = feeds["pixel_values"]
        self.fed.append(tensor)
        _, _, h, w = tensor.shape
        if self.output is not None:
            return [self.output(tensor)]
        # a horizontal ramp in the fed resolution: larger to the right
        ramp = np.tile(np.linspace(1.0, 9.0, w, dtype=np.float32), (h, 1))
        return [ramp[None, ...]]


def install_fake_runtime(monkeypatch, shape=("batch", 3, "height", "width"), output=None):
    holder = {}

    def factory(path, sess_options=None, providers=None):
        session = FakeSession(path, sess_options, providers, shape=list(shape), output=output)
        holder["session"] = session
        return session

    runtime = SimpleNamespace(
        __version__="0.0-test",
        SessionOptions=lambda: SimpleNamespace(intra_op_num_threads=None, inter_op_num_threads=None),
        InferenceSession=factory,
    )
    monkeypatch.setattr(pil_depth, "_load_runtime", lambda: runtime)
    return holder


def run(capsys, *argv):
    code = pil_depth.main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out else None), captured.err


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"not a real model; the runtime is stubbed")
    return path


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "concept.png"
    Image.new("RGB", (200, 100), (120, 60, 30)).save(path)
    return path


# -------------------------------------------------------- preprocessing


def test_target_size_dynamic_keeps_aspect_and_multiple_of_14():
    spec = pil_depth.PREPROCESSING_PROFILES["depth-anything-v2"]
    (w, h), mode = pil_depth._target_size(200, 100, spec, None)
    assert mode == "keep_aspect_lower_bound"
    assert h == 518 and w % 14 == 0 and abs(w / h - 2.0) < 0.03
    (w, h), _ = pil_depth._target_size(300, 400, spec, None)
    assert w == 518 and h % 14 == 0 and h >= 518


def test_target_size_caps_extreme_aspect():
    spec = pil_depth.PREPROCESSING_PROFILES["depth-anything-v2"]
    (w, h), _ = pil_depth._target_size(10000, 100, spec, None)
    assert max(w, h) <= spec["max_long_side"]
    assert w % 14 == 0 and h % 14 == 0


def test_target_size_static_model_wins():
    spec = pil_depth.PREPROCESSING_PROFILES["depth-anything-v2"]
    assert pil_depth._target_size(200, 100, spec, (518, 518)) == ((518, 518), "model_static_input")


def test_preprocess_tensor_is_normalised_nchw_float32():
    spec = pil_depth.PREPROCESSING_PROFILES["depth-anything-v2"]
    rgb = Image.new("RGB", (64, 64), (255, 0, 0))
    tensor, fed, _ = pil_depth._preprocess(rgb, spec, (28, 28))
    assert tensor.shape == (1, 3, 28, 28) and tensor.dtype == np.float32
    assert fed == (28, 28)
    mean, std = spec["normalize_mean"], spec["normalize_std"]
    for channel, value in enumerate((1.0, 0.0, 0.0)):
        expected = (value - mean[channel]) / std[channel]
        assert np.allclose(tensor[0, channel], expected, atol=1e-5)


def test_static_input_size_reads_declared_shape():
    static = SimpleNamespace(get_inputs=lambda: [SimpleNamespace(shape=[1, 3, 42, 70])])
    dynamic = SimpleNamespace(get_inputs=lambda: [SimpleNamespace(shape=["b", 3, "h", "w"])])
    assert pil_depth._static_input_size(static) == (70, 42)
    assert pil_depth._static_input_size(dynamic) is None


# ---------------------------------------------------------------- estimate


def test_estimate_payload_files_and_statement(monkeypatch, capsys, model, image, tmp_path):
    holder = install_fake_runtime(monkeypatch)
    out = tmp_path / "out"
    code, payload, err = run(capsys, "estimate", image, "--model", model, "--output-dir", out)
    assert code == 0 and err == ""
    assert payload["tool"] == "pil_depth" and payload["command"] == "estimate"
    assert payload["version"] == pil_depth.TOOL_VERSION
    assert payload["depth_kind"] == "relative_inverse_depth"
    assert payload["metric"] is False
    assert "not metric" in payload["statement"].lower()
    assert "larger value = nearer" in payload["convention"]
    limits = " ".join(payload["interpretation_limits"])
    assert "RELATIVE" in limits and "NOT METRIC" in limits and "larger = nearer" in limits

    import hashlib

    assert payload["engine"]["model_sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()
    assert payload["engine"]["providers"] == ["CPUExecutionProvider"]
    assert holder["session"].providers == ["CPUExecutionProvider"]
    assert payload["parameters"]["preprocessing_profile"] == "depth-anything-v2"
    assert payload["parameters"]["preprocessing"] == pil_depth.PREPROCESSING_PROFILES["depth-anything-v2"]
    assert payload["parameters"]["input_size_mode"] == "keep_aspect_lower_bound"
    assert payload["flags"] == ["model_not_verified"]
    assert payload["image"]["size"] == [200, 100]

    npy = Path(payload["files"]["npy"])
    depth = np.load(npy)
    assert depth.shape == (100, 200) and depth.dtype == np.float32
    assert payload["depth"]["shape"] == [100, 200]
    # the fake ramp is 1..9 across the fed width: resampled back, still monotone in x
    assert depth[0, 0] < depth[0, -1]
    assert payload["depth"]["min"] == pytest.approx(float(depth.min()), abs=1e-5)
    png = Image.open(payload["files"]["heatmap_png"])
    assert png.size == (200, 100) and png.mode == "RGB"
    fed = holder["session"].fed[0]
    assert fed.shape[0:2] == (1, 3) and fed.shape[2] == 518 and fed.shape[3] % 14 == 0


def test_estimate_resamples_only_when_sizes_differ(monkeypatch, capsys, model, tmp_path):
    install_fake_runtime(monkeypatch, shape=(1, 3, 56, 56),
                         output=lambda t: np.linspace(0, 1, 56 * 56, dtype=np.float32).reshape(1, 56, 56))
    square = tmp_path / "sq.png"
    Image.new("RGB", (56, 56), (10, 10, 10)).save(square)
    code, payload, _ = run(capsys, "estimate", square, "--model", model, "--output-dir", tmp_path / "o")
    assert code == 0
    assert payload["parameters"]["output_resampling"] == "none"
    assert payload["parameters"]["input_size_mode"] == "model_static_input"
    assert payload["parameters"]["fed_size"] == [56, 56]


def test_estimate_env_model_and_deterministic(monkeypatch, capsys, model, image, tmp_path):
    install_fake_runtime(monkeypatch)
    monkeypatch.setenv(pil_depth.MODEL_ENV_VAR, str(model))
    _, first, _ = run(capsys, "estimate", image, "--output-dir", tmp_path / "a")
    _, second, _ = run(capsys, "estimate", image, "--output-dir", tmp_path / "b")
    for payload, name in ((first, "a"), (second, "b")):
        for key in ("npy", "npy_sha256", "heatmap_png"):
            payload["files"].pop(key)
    assert first == second


def _selection_mask(tmp_path, image, box):
    """A real selection-mask-v1 manifest bound to ``image`` (via pil_mask)."""
    spec = {"name": "left", "polygons": [[[box[0], box[1]], [box[2], box[1]],
                                          [box[2], box[3]], [box[0], box[3]]]]}
    out = tmp_path / "sel" / "left.json"
    out.parent.mkdir()
    pil_mask.create_mask(image, spec, out)
    return out


def test_estimate_mask_scopes_stats(monkeypatch, capsys, model, image, tmp_path):
    install_fake_runtime(monkeypatch)
    manifest = _selection_mask(tmp_path, image, (0, 0, 49, 99))
    code, masked, _ = run(capsys, "estimate", image, "--model", model, "--mask", manifest,
                          "--output-dir", tmp_path / "m")
    assert code == 0
    _, full, _ = run(capsys, "estimate", image, "--model", model, "--output-dir", tmp_path / "f")
    assert masked["depth"]["stats_scope"] == "mask" and full["depth"]["stats_scope"] == "full_frame"
    assert masked["depth"]["max"] < full["depth"]["max"]  # ramp: the left quarter is farther
    assert masked["parameters"]["mask"]["name"] == "left"
    # the .npy is the unscaled model output either way
    assert np.array_equal(np.load(masked["files"]["npy"]), np.load(full["files"]["npy"]))


def test_estimate_mask_bound_to_other_image_is_refused(monkeypatch, capsys, model, image, tmp_path):
    install_fake_runtime(monkeypatch)
    manifest = _selection_mask(tmp_path, image, (0, 0, 49, 99))
    other = tmp_path / "other.png"
    Image.new("RGB", (200, 100), (1, 2, 3)).save(other)
    code, payload, err = run(capsys, "estimate", other, "--model", model, "--mask", manifest,
                             "--output-dir", tmp_path / "o")
    assert code == 2 and payload is None and err.startswith("pil_depth:")


def test_estimate_refusals(monkeypatch, capsys, model, image, tmp_path):
    install_fake_runtime(monkeypatch)
    monkeypatch.delenv(pil_depth.MODEL_ENV_VAR, raising=False)
    code, payload, err = run(capsys, "estimate", image, "--output-dir", tmp_path / "o")
    assert (code, payload) == (2, None) and pil_depth.MODEL_ENV_VAR in err and err.count("\n") == 1
    code, payload, err = run(capsys, "estimate", image, "--model", tmp_path / "nope.onnx",
                             "--output-dir", tmp_path / "o")
    assert (code, payload) == (2, None) and "not found" in err
    code, payload, err = run(capsys, "estimate", tmp_path / "missing.png", "--model", model,
                             "--output-dir", tmp_path / "o")
    assert (code, payload) == (2, None) and "cannot read image" in err


def test_estimate_runtime_missing_is_a_refusal(monkeypatch, capsys, model, image, tmp_path):
    def missing():
        raise pil_depth.DepthError("onnxruntime is unavailable; install the embedding extra")

    monkeypatch.setattr(pil_depth, "_load_runtime", missing)
    code, payload, err = run(capsys, "estimate", image, "--model", model, "--output-dir", tmp_path / "o")
    assert (code, payload) == (2, None) and "onnxruntime is unavailable" in err


def test_estimate_run_time_model_error_is_a_refusal(monkeypatch, capsys, model, image, tmp_path):
    """A model that loads but rejects the input at run time (ORT raises its own
    native exception types) exits 2 with one line, not a traceback."""

    class OrtInvalidArgument(Exception):
        pass

    def reject(tensor):
        raise OrtInvalidArgument(
            "[ONNXRuntimeError] : 2 : INVALID_ARGUMENT : Got invalid dimensions for input: "
            "pixel_values\n index: 1 Got: 3 Expected: 1"
        )

    install_fake_runtime(monkeypatch, output=reject)
    code, payload, err = run(capsys, "estimate", image, "--model", model, "--output-dir", tmp_path / "o")
    assert (code, payload) == (2, None)
    assert err.startswith("pil_depth: depth model model.onnx failed on a ") and err.count("\n") == 1
    assert "INVALID_ARGUMENT" in err and not list((tmp_path / "o").glob("*"))


def test_estimate_refuses_bad_model_outputs(monkeypatch, capsys, model, image, tmp_path):
    install_fake_runtime(monkeypatch, output=lambda t: np.zeros((1, 3, 4, 4), dtype=np.float32))
    code, payload, err = run(capsys, "estimate", image, "--model", model, "--output-dir", tmp_path / "a")
    assert (code, payload) == (2, None) and "expected a single 2-D" in err

    install_fake_runtime(monkeypatch, output=lambda t: np.full((1, 8, 8), np.nan, dtype=np.float32))
    code, _, err = run(capsys, "estimate", image, "--model", model, "--output-dir", tmp_path / "b")
    assert code == 2 and "non-finite" in err

    install_fake_runtime(monkeypatch, output=lambda t: np.ones((1, 8, 8), dtype=np.float32))
    code, _, err = run(capsys, "estimate", image, "--model", model, "--output-dir", tmp_path / "c")
    assert code == 2 and "constant depth map" in err

    install_fake_runtime(monkeypatch, shape=(1, 3))
    code, _, err = run(capsys, "estimate", image, "--model", model, "--output-dir", tmp_path / "d")
    assert code == 2 and "NCHW" in err


def test_estimate_never_overwrites(monkeypatch, capsys, model, image, tmp_path):
    install_fake_runtime(monkeypatch)
    out = tmp_path / "out"
    assert run(capsys, "estimate", image, "--model", model, "--output-dir", out)[0] == 0
    code, payload, err = run(capsys, "estimate", image, "--model", model, "--output-dir", out)
    assert (code, payload) == (2, None) and "refusing to overwrite" in err


def test_diagnose(monkeypatch, capsys, model):
    install_fake_runtime(monkeypatch, shape=(1, 3, 518, 518))
    code, payload, _ = run(capsys, "diagnose", "--model", model)
    assert code == 0
    assert payload["static_input_size"] == [518, 518]
    assert payload["model_known"] is False and len(payload["model_sha256"]) == 64
    assert payload["preprocessing_profile"] == "depth-anything-v2"


# -------------------------------------------------------------- compare


def _scene_depth(h=64, w=64):
    """A tilted plane: depth 2..6 left to right, NaN outside a central disc."""
    xs = np.linspace(2.0, 6.0, w)
    depth = np.tile(xs, (h, 1))
    yy, xx = np.mgrid[0:h, 0:w]
    disc = (yy - h / 2) ** 2 + (xx - w / 2) ** 2 <= (0.45 * min(h, w)) ** 2
    depth[~disc] = np.nan
    return depth


def _save(path, array):
    np.save(path, array)
    return path


def test_rank_average_and_spearman_with_ties():
    assert list(pil_depth.rank_average([10, 20, 20, 30])) == [1.0, 2.5, 2.5, 4.0]
    assert pil_depth.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert pil_depth.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    # known case with ties: ranks (1, 2.5, 2.5, 4) vs (1, 2, 3, 4)
    a, b = np.array([1.0, 2.5, 2.5, 4.0]), np.array([1.0, 2.0, 3.0, 4.0])
    expected = np.corrcoef(a, b)[0, 1]
    assert pil_depth.spearman([10, 20, 20, 30], [1, 2, 3, 4]) == pytest.approx(expected)
    with pytest.raises(pil_depth.DepthError):
        pil_depth.spearman([1, 1, 1], [1, 2, 3])


def test_fit_scale_shift_recovers_exact_parameters():
    x = np.linspace(0.1, 1.0, 50)
    s, t = pil_depth.fit_scale_shift(x, 3.5 * x - 0.25)
    assert s == pytest.approx(3.5) and t == pytest.approx(-0.25)


def test_compare_exact_recovery(capsys, tmp_path):
    render = _scene_depth()
    truth = 1.0 / render
    concept = (truth - 0.05) / 4.0  # concept = (y - t)/s  =>  y = 4*concept + 0.05
    code, payload, err = run(
        capsys, "compare", _save(tmp_path / "c.npy", concept), _save(tmp_path / "r.npy", render),
        "--output-dir", tmp_path / "out",
    )
    assert code == 0 and err == ""
    alignment, metrics = payload["alignment"], payload["metrics"]
    assert alignment["scale"] == pytest.approx(4.0, rel=1e-6)
    assert alignment["shift"] == pytest.approx(0.05, rel=1e-6)
    assert alignment["space"] == "inverse_depth"
    assert alignment["valid_pixels"] == int(np.isfinite(render).sum())
    assert metrics["spearman"] == pytest.approx(1.0)
    assert metrics["absrel"] == pytest.approx(0.0, abs=1e-6)
    assert metrics["absrel_inverse_depth"] == pytest.approx(0.0, abs=1e-6)
    assert metrics["r_squared_inverse_depth"] == pytest.approx(1.0)
    assert payload["flags"] == []
    assert "Scale and shift are fitted away" in " ".join(payload["interpretation_limits"])
    assert "RELATIVE" in payload["statement"]
    png = Image.open(payload["disagreement"]["png"])
    assert png.size == (64, 64)


def test_compare_absrel_on_a_known_case(capsys, tmp_path):
    # render depth 2 and 4; the concept says the same ordering but with the
    # far value pushed to a 3rd value so the fit cannot be exact.
    render = np.array([[2.0, 4.0]] * 200)  # 400 pixels, two depths
    y = 1.0 / render
    concept = np.array([[1.0, 0.0]] * 200)
    concept[:100, 1] = 0.5  # half the far pixels disagree
    code, payload, _ = run(capsys, "compare", _save(tmp_path / "c.npy", concept),
                           _save(tmp_path / "r.npy", render), "--output-dir", tmp_path / "o")
    assert code == 0
    x, yy, d = concept.ravel(), y.ravel(), render.ravel()
    s, t = pil_depth.fit_scale_shift(x, yy)
    aligned = s * x + t
    expected = float(np.mean(np.abs(1.0 / aligned - d) / d))
    assert payload["metrics"]["absrel"] == pytest.approx(expected, abs=1e-5)
    assert payload["metrics"]["absrel"] > 0.01


def test_compare_flags_inverted_relation(capsys, tmp_path):
    render = _scene_depth()
    concept = -(1.0 / render)  # nearer is SMALLER: an inverted map
    code, payload, _ = run(capsys, "compare", _save(tmp_path / "c.npy", concept),
                           _save(tmp_path / "r.npy", render), "--output-dir", tmp_path / "o")
    assert code == 0
    # concept = -y fits with scale -1: flagged
    assert payload["alignment"]["scale"] == pytest.approx(-1.0)
    assert "inverted_relation" in payload["flags"]
    assert payload["metrics"]["spearman"] == pytest.approx(-1.0)


def test_compare_reports_worst_region_and_direction(capsys, tmp_path):
    render = _scene_depth(96, 96)
    concept = 1.0 / render
    concept[10:34, 60:90] += 0.25  # concept says "nearer" in the upper right
    code, payload, _ = run(capsys, "compare", _save(tmp_path / "c.npy", concept),
                           _save(tmp_path / "r.npy", render), "--output-dir", tmp_path / "o",
                           "--grid", 6, "--top", 2)
    assert code == 0
    regions = payload["disagreement"]["regions"]
    assert len(regions) == 2 and [r["rank"] for r in regions] == [1, 2]
    left, top, right, bottom = regions[0]["bbox_fractional"]
    assert 0.5 <= left < right <= 1.0 and 0.0 <= top < bottom <= 0.5
    assert regions[0]["reading"] == "concept nearer than render"
    assert regions[0]["mean_error"] >= regions[1]["mean_error"]


def test_compare_resamples_matching_aspect(capsys, tmp_path):
    render = _scene_depth(64, 64)
    small = np.array(Image.fromarray((1.0 / np.where(np.isfinite(render), render, 1e9)).astype(np.float32),
                                     mode="F").resize((32, 32)))
    code, payload, _ = run(capsys, "compare", _save(tmp_path / "c.npy", small),
                           _save(tmp_path / "r.npy", render), "--output-dir", tmp_path / "o")
    assert code == 0
    resampling = payload["parameters"]["resampling"]
    assert resampling["applied"] is True and resampling["method"] == "bilinear"
    assert resampling["concept_shape"] == [32, 32] and resampling["render_shape"] == [64, 64]
    assert payload["metrics"]["spearman"] > 0.9


def test_compare_refuses_aspect_mismatch(capsys, tmp_path):
    render = _scene_depth(64, 64)
    concept = np.random.default_rng(0).random((64, 96))
    code, payload, err = run(capsys, "compare", _save(tmp_path / "c.npy", concept),
                             _save(tmp_path / "r.npy", render), "--output-dir", tmp_path / "o")
    assert (code, payload) == (2, None) and "shape mismatch" in err and "aspect" in err
    assert not (tmp_path / "o").exists()


def test_compare_mask_selects_pixels(capsys, tmp_path):
    render = _scene_depth(64, 64)
    concept = 1.0 / render
    concept[:, :20] = np.random.default_rng(1).random((64, 20))  # garbage on the left
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[:, 24:] = 255
    mask_path = tmp_path / "m.png"
    Image.fromarray(mask).save(mask_path)
    args = ("compare", _save(tmp_path / "c.npy", concept), _save(tmp_path / "r.npy", render))
    _, unmasked, _ = run(capsys, *args, "--output-dir", tmp_path / "u")
    code, masked, _ = run(capsys, *args, "--mask", mask_path, "--output-dir", tmp_path / "m")
    assert code == 0
    assert masked["metrics"]["absrel_inverse_depth"] < unmasked["metrics"]["absrel_inverse_depth"]
    assert masked["metrics"]["absrel"] == pytest.approx(0.0, abs=1e-5)
    assert masked["alignment"]["valid_pixels"] < unmasked["alignment"]["valid_pixels"]
    assert masked["parameters"]["mask"]["selected_pixels"] == 64 * 40


def test_compare_mask_refusals(capsys, tmp_path):
    render = _scene_depth(64, 64)
    args = ("compare", _save(tmp_path / "c.npy", 1.0 / render), _save(tmp_path / "r.npy", render))
    wrong = tmp_path / "wrong.png"
    Image.fromarray(np.full((32, 32), 255, dtype=np.uint8)).save(wrong)
    code, payload, err = run(capsys, *args, "--mask", wrong, "--output-dir", tmp_path / "o")
    assert (code, payload) == (2, None) and "must be a raster at the render's size" in err
    empty = tmp_path / "empty.png"
    Image.fromarray(np.zeros((64, 64), dtype=np.uint8)).save(empty)
    code, _, err = run(capsys, *args, "--mask", empty, "--output-dir", tmp_path / "o")
    assert code == 2 and "selects no pixels" in err
    tiny = np.zeros((64, 64), dtype=np.uint8)
    tiny[:5, :5] = 255
    tiny_path = tmp_path / "tiny.png"
    Image.fromarray(tiny).save(tiny_path)
    code, _, err = run(capsys, *args, "--mask", tiny_path, "--output-dir", tmp_path / "o")
    assert code == 2 and "valid in both maps" in err


def test_compare_refusals(capsys, tmp_path):
    render = _scene_depth(64, 64)
    out = tmp_path / "o"
    good = _save(tmp_path / "c.npy", 1.0 / render)
    r = _save(tmp_path / "r.npy", render)

    code, payload, err = run(capsys, "compare", tmp_path / "missing.npy", r, "--output-dir", out)
    assert (code, payload) == (2, None) and "cannot read concept depth" in err

    _save(tmp_path / "vec.npy", np.arange(10.0))
    code, _, err = run(capsys, "compare", tmp_path / "vec.npy", r, "--output-dir", out)
    assert code == 2 and "2-D numeric" in err

    (tmp_path / "bad.npy").write_bytes(b"garbage")
    code, _, err = run(capsys, "compare", tmp_path / "bad.npy", r, "--output-dir", out)
    assert code == 2 and "cannot read concept depth" in err

    code, _, err = run(capsys, "compare", _save(tmp_path / "flat.npy", np.full((64, 64), 0.3)), r,
                       "--output-dir", out)
    assert code == 2 and "concept depth is constant" in err

    code, _, err = run(capsys, "compare", good, _save(tmp_path / "flat_r.npy", np.full((64, 64), 3.0)),
                       "--output-dir", out)
    assert code == 2 and "render depth is constant" in err

    all_nan = np.full((64, 64), np.nan)
    code, _, err = run(capsys, "compare", good, _save(tmp_path / "nan.npy", all_nan), "--output-dir", out)
    assert code == 2 and "valid in both maps" in err

    code, _, err = run(capsys, "compare", _save(tmp_path / "inf.npy", np.full((64, 64), np.inf)), r,
                       "--output-dir", out)
    assert code == 2 and "infinite" in err

    code, _, err = run(capsys, "compare", good, r, "--output-dir", out, "--grid", 0)
    assert code == 2 and "--grid" in err
    assert not out.exists()


def test_compare_never_overwrites(capsys, tmp_path):
    render = _scene_depth(64, 64)
    args = ("compare", _save(tmp_path / "c.npy", 1.0 / render), _save(tmp_path / "r.npy", render),
            "--output-dir", tmp_path / "o")
    assert run(capsys, *args)[0] == 0
    code, payload, err = run(capsys, *args)
    assert (code, payload) == (2, None) and "refusing to overwrite" in err


def test_compare_estimate_roundtrip(monkeypatch, capsys, model, image, tmp_path):
    """estimate's .npy is directly consumable by compare."""
    install_fake_runtime(monkeypatch)
    _, est, _ = run(capsys, "estimate", image, "--model", model, "--output-dir", tmp_path / "e")
    depth = np.load(est["files"]["npy"])
    render = np.tile(1.0 / np.linspace(0.2, 0.9, depth.shape[1]), (depth.shape[0], 1))
    code, payload, _ = run(capsys, "compare", est["files"]["npy"], _save(tmp_path / "r.npy", render),
                           "--output-dir", tmp_path / "c")
    assert code == 0 and payload["metrics"]["spearman"] > 0.99


# ---------------------------------------------------------------- real model


@needs_real_model
def test_real_model_diagnose(capsys):
    code, payload, _ = run(capsys, "diagnose", "--model", REAL_MODEL)
    assert code == 0 and len(payload["model_sha256"]) == 64


@needs_real_model
def test_real_model_estimate_is_relative_inverse_depth_with_bright_near(tmp_path, capsys):
    # A bright disc on a dark ground, with a lower-half "floor" gradient: the model
    # must return a finite HxW map at the image size and rank the nearer floor above the top.
    h, w = 224, 336
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (70, 70, 75)
    yy = np.linspace(0.3, 1.0, h // 2, dtype=np.float32)
    img[h // 2:] = (yy[:, None, None] * np.array([200, 170, 130])).astype(np.uint8)
    image = tmp_path / "scene.png"
    Image.fromarray(img).save(image)
    code, payload, _ = run(capsys, "estimate", image, "--model", REAL_MODEL, "--output-dir", tmp_path / "out")
    assert code == 0
    depth = np.load(payload["files"]["npy"])
    assert depth.shape == (h, w) and depth.dtype == np.float32 and np.isfinite(depth).all()
    assert payload["metric"] is False and payload["depth_kind"] == "relative_inverse_depth"
    assert payload["parameters"]["fed_size"][0] % 14 == 0 and payload["parameters"]["fed_size"][1] % 14 == 0
    assert depth[-h // 8:].mean() > depth[:h // 8].mean()  # larger = nearer: the floor foreground beats the wall top
    if payload["engine"]["model_sha256"] in pil_depth.KNOWN_MODELS:
        assert payload["flags"] == [] and payload["engine"]["model_known"] is True


def test_known_models_are_full_sha256_digests_with_a_note():
    for digest, note in pil_depth.KNOWN_MODELS.items():
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
        assert "Depth Anything V2 Small" in note and "Apache-2.0" in note


def test_verified_model_sha_clears_the_unverified_flag(monkeypatch, capsys, tmp_path, model):
    install_fake_runtime(monkeypatch, shape=(1, 3, 518, 518))
    import hashlib
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setitem(pil_depth.KNOWN_MODELS, digest, "Depth Anything V2 Small test entry (Apache-2.0)")
    image = tmp_path / "i.png"
    Image.fromarray(np.full((40, 60, 3), 90, np.uint8)).save(image)
    code, payload, _ = run(capsys, "estimate", image, "--model", model, "--output-dir", tmp_path / "o")
    assert code == 0
    assert payload["flags"] == [] and payload["engine"]["model_known"] is True
    assert payload["engine"]["model_note"].startswith("Depth Anything V2 Small")


# ------------------------------------------------ bootstrap / catalog / docs


def test_capabilities_lists_pil_depth(monkeypatch, tmp_path):
    import pil_capabilities

    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    monkeypatch.setenv(pil_depth.MODEL_ENV_VAR, str(model))
    tools = {t["name"]: t for t in pil_capabilities.catalog()["tools"]}
    tool = tools["pil_depth"]
    assert tool["mutates"] is True and tool["deprecated"] is False
    assert set(tool["requirements"]) == {"onnxruntime", "depth_model"}
    assert tool["requirements"]["depth_model"] is True
    assert set(tool["cli_schema"]["commands"]) == {"diagnose", "estimate", "compare"}
    monkeypatch.setenv(pil_depth.MODEL_ENV_VAR, str(tmp_path / "missing.onnx"))
    tools = {t["name"]: t for t in pil_capabilities.catalog()["tools"]}
    assert tools["pil_depth"]["requirements"]["depth_model"] is False


def test_depth_model_setting_reaches_tool_subprocesses(monkeypatch):
    from pil_environment import tool_environment

    monkeypatch.setenv(pil_depth.MODEL_ENV_VAR, "depth.onnx")
    assert tool_environment()[pil_depth.MODEL_ENV_VAR] == "depth.onnx"


def _bootstrap_root(tmp_path, monkeypatch):
    import pil_bootstrap

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11"\ndependencies = []\n'
        '[project.optional-dependencies]\nembedding = ["onnxruntime>=1.17,<2"]\n'
        'reconstruction = []\n'
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    # a stand-in for pil_depth.py that reports its own argv
    (scripts / "pil_depth.py").write_text(
        "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}))\n"
    )
    monkeypatch.setattr(pil_bootstrap, "venv_python", lambda _: Path(sys.executable))
    real_probe = pil_bootstrap.probe
    # only the depth stand-in is really executed; the dependency probe is doubled
    monkeypatch.setattr(pil_bootstrap, "probe",
                        lambda c: real_probe(c) if "pil_depth.py" in " ".join(map(str, c))
                        else {"ok": True, "details": {}})
    return pil_bootstrap


def test_bootstrap_depth_check_runs_diagnose_with_the_model(tmp_path, monkeypatch, capsys):
    bootstrap = _bootstrap_root(tmp_path, monkeypatch)
    code = bootstrap.main(["check", "--depth", "--depth-model", "D:/m/depth.onnx"], root=tmp_path)
    status = json.loads(capsys.readouterr().out)
    assert "depth" in status["checks"] and "embedding" not in status["checks"]
    assert status["checks"]["depth"]["details"]["argv"] == ["diagnose", "--model", "D:/m/depth.onnx"]
    assert "depth" in status["configuration"]["extras"]
    del code


def test_bootstrap_depth_model_implies_depth_and_needs_no_embedding_model(tmp_path, monkeypatch, capsys):
    bootstrap = _bootstrap_root(tmp_path, monkeypatch)
    bootstrap.main(["check", "--depth-model", "x.onnx"], root=tmp_path)
    status = json.loads(capsys.readouterr().out)
    assert "depth" in status["checks"]
    # without --depth the check is absent and unchanged
    bootstrap.main(["check"], root=tmp_path)
    assert "depth" not in json.loads(capsys.readouterr().out)["checks"]


def test_bootstrap_depth_pulls_in_onnxruntime_requirement(tmp_path, monkeypatch):
    bootstrap = _bootstrap_root(tmp_path, monkeypatch)
    args = SimpleNamespace(depth=True, ocr=False, model=None, preprocessing=None, depth_model=None)
    requirements, signature = bootstrap.configuration(tmp_path, args)
    assert requirements == ["onnxruntime>=1.17,<2"]
    assert signature["extras"] == ["depth"]
    both = SimpleNamespace(depth=True, embedding=True, ocr=False)
    requirements, _ = bootstrap.configuration(tmp_path, both)
    assert requirements == ["onnxruntime>=1.17,<2"]  # not duplicated


def test_bootstrap_depth_refuses_when_diagnose_fails(tmp_path, monkeypatch, capsys):
    import pil_bootstrap

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11"\ndependencies = []\n'
        '[project.optional-dependencies]\nembedding = []\n'
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "pil_depth.py").write_text(
        "import sys\nprint('pil_depth: no depth model given', file=sys.stderr)\nsys.exit(2)\n"
    )
    monkeypatch.setattr(pil_bootstrap, "venv_python", lambda _: Path(sys.executable))
    real_probe = pil_bootstrap.probe
    monkeypatch.setattr(pil_bootstrap, "probe",
                        lambda c: real_probe(c) if "pil_depth.py" in " ".join(map(str, c)) else {"ok": True, "details": {}})
    code = pil_bootstrap.main(["check", "--depth"], root=tmp_path)
    status = json.loads(capsys.readouterr().out)
    assert code == 2 and status["checks"]["depth"]["ok"] is False
    assert "no depth model given" in status["checks"]["depth"]["reason"]


def test_readme_documents_the_depth_model_and_its_licence():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = text[text.index("### Concept-image depth"):text.index("## Worked example")]
    for needle in ("Depth Anything V2", "Small", "Apache-2.0", "CC-BY-NC", "PIL_AGENT_DEPTH_MODEL",
                   "not metric", "sha256", "--depth"):
        assert needle in section or needle in text, needle
    assert "Apache-2.0" in section and "Base, Large and Giant" in section
