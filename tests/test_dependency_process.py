"""Real CLI processes with controlled dependency doubles (no model downloads).

These check discovery and refusal, not OCR accuracy or embedding calibration.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def env(tmp_path):
    values = os.environ.copy()
    for key in ("PIL_AGENT_TESSERACT", "PIL_AGENT_EMBED_MODEL",
                "PIL_AGENT_EMBED_PREPROCESSING", "TESSDATA_PREFIX"):
        values.pop(key, None)
    values.update(PATH=str(tmp_path), PYTHONPATH=str(tmp_path),
                  ProgramFiles=str(tmp_path / "Program Files"),
                  **{"ProgramFiles(x86)": str(tmp_path / "Program Files x86"),
                     "LOCALAPPDATA": str(tmp_path / "Local")})
    return values


def run(tool, env, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / tool), *map(str, args)],
                          env=env, capture_output=True, text=True, timeout=30)


def refused(result, reason):
    assert result.returncode == 2, result.stderr
    assert result.stdout == ""
    assert reason in result.stderr
    assert "Traceback" not in result.stderr


def fake_tesseract(tmp_path):
    # An actual executable child process, including a path containing spaces.
    directory = tmp_path / "OCR install"
    directory.mkdir()
    if os.name == "nt":
        path = directory / "tesseract.cmd"
        path.write_text('@echo off\nif "%~1"=="--version" (\n'
                        'echo tesseract test\n) else (\n'
                        'echo List of available languages ^(1^):\necho eng\n)\n')
    else:
        path = directory / "tesseract"
        path.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then\n'
                        'echo "tesseract test"\nelse\necho eng\nfi\n')
        path.chmod(0o755)
    return path


def test_ocr_missing_process(env):
    refused(run("pil_ocr.py", env, "--diagnose"), "tesseract executable not found")


@pytest.mark.parametrize("source", ["explicit", "environment", "path"])
def test_ocr_discovered_process(env, tmp_path, source):
    executable = fake_tesseract(tmp_path)
    args = ["--diagnose"]
    if source == "explicit":
        env["PIL_AGENT_TESSERACT"] = str(tmp_path / "missing")
        args += ["--tesseract-executable", str(executable)]
    elif source == "environment":
        env["PIL_AGENT_TESSERACT"] = str(executable)
    else:
        env["PATH"] = str(executable.parent)
    result = run("pil_ocr.py", env, *args)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["engine"] == "tesseract test"
    assert "eng" in payload["languages"]


def test_ocr_bad_override_does_not_fall_back(env, tmp_path):
    env["PATH"] = str(fake_tesseract(tmp_path).parent)
    refused(run("pil_ocr.py", env, "--diagnose", "--tesseract-executable",
                tmp_path / "missing"), "tesseract executable not found")


def test_ocr_missing_language(env, tmp_path):
    env["PIL_AGENT_TESSERACT"] = str(fake_tesseract(tmp_path))
    refused(run("pil_ocr.py", env, "--diagnose", "--lang", "eng+deu"), "deu")


@pytest.mark.skipif(os.name != "nt", reason="Windows install-directory discovery")
@pytest.mark.parametrize("variable,suffix", [
    ("ProgramFiles", "Tesseract-OCR"),
    ("ProgramFiles(x86)", "Tesseract-OCR"),
    ("LOCALAPPDATA", "Programs/Tesseract-OCR"),
    ("LOCALAPPDATA", "Tesseract-OCR"),
])
def test_windows_install_discovery_process(env, variable, suffix):
    executable = Path(env[variable]) / suffix / "tesseract.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    # Probe resolution in a fresh interpreter; the placeholder is not an engine.
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
         "import pil_ocr; print(pil_ocr._find_tesseract(None))", str(SCRIPTS)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(executable)


@pytest.mark.parametrize("diagnose", [True, False])
def test_ocr_directory_override_is_refused(env, tmp_path, diagnose):
    args = ["--diagnose"] if diagnose else ["unused.png"]
    refused(run("pil_ocr.py", env, *args, "--tesseract-executable", tmp_path),
            "tesseract executable not found")


@pytest.mark.parametrize("error", ["ModuleNotFoundError('No module named onnxruntime')",
                                  "ImportError('DLL load failed')",
                                  "OSError('WinError 126')"])
@pytest.mark.parametrize("command", [["diagnose"], ["embed", "unused.png"]])
def test_embed_runtime_refusal_process(env, tmp_path, error, command):
    (tmp_path / "onnxruntime.py").write_text(f"raise {error}\n")
    refused(run("pil_embed.py", env, *command), "onnxruntime")


def runtime_double(tmp_path, fail=False):
    (tmp_path / "onnxruntime.py").write_text(
        "from types import SimpleNamespace\n"
        "__version__ = 'test'\n"
        "class SessionOptions: pass\n"
        "class InferenceSession:\n"
        " def __init__(self, *args, **kwargs):\n"
        + ("  raise RuntimeError('invalid ONNX model')\n" if fail else "  pass\n")
        + " def get_inputs(self):\n"
        "  return [SimpleNamespace(name='input', shape=[1,3,224,224])]\n")


def test_embed_missing_model_process(env, tmp_path):
    runtime_double(tmp_path)
    refused(run("pil_embed.py", env, "diagnose"), "no embedding model given")


def test_embed_missing_file_and_invalid_profile_process(env, tmp_path):
    runtime_double(tmp_path)
    model = tmp_path / "missing.onnx"
    refused(run("pil_embed.py", env, "diagnose", "--model", model),
            "embedding model not found")
    model.write_bytes(b"dependency double")
    env["PIL_AGENT_EMBED_PREPROCESSING"] = "invalid"
    refused(run("pil_embed.py", env, "diagnose", "--model", model),
            "unknown preprocessing profile")


def test_embed_discovered_model_process(env, tmp_path):
    runtime_double(tmp_path)
    model = tmp_path / "model with spaces.onnx"
    model.write_bytes(b"dependency double")
    env["PIL_AGENT_EMBED_MODEL"] = str(model)
    env["PIL_AGENT_EMBED_PREPROCESSING"] = "clip"
    result = run("pil_embed.py", env, "diagnose")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["preprocessing_profile"] == "clip"
    assert payload["model_path"] == str(model)
    assert payload["model_gated"] is False
    env["PIL_AGENT_EMBED_MODEL"] = str(tmp_path / "missing")
    result = run("pil_embed.py", env, "diagnose", "--model", model,
                 "--preprocessing", "imagenet")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["preprocessing_profile"] == "imagenet"


@pytest.mark.parametrize("command", [["diagnose"], ["embed", "unused.png"]])
def test_embed_invalid_model_process(env, tmp_path, command):
    runtime_double(tmp_path, fail=True)
    model = tmp_path / "broken.onnx"
    model.write_bytes(b"invalid")
    refused(run("pil_embed.py", env, *command, "--model", model), "cannot load ONNX model")
