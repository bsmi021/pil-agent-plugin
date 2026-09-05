"""Bootstrap lifecycle tests; installers are doubled, probes use child Python."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import pil_bootstrap as bootstrap


@pytest.fixture
def root(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11"\ndependencies = []\n'
        '[project.optional-dependencies]\nembedding = []\nreconstruction = []\n')
    return tmp_path


def fake_install(monkeypatch, root):
    # Use a real interpreter for probes without installing any packages.
    monkeypatch.setattr(bootstrap, "venv_python", lambda _: Path(sys.executable))
    calls = []
    monkeypatch.setattr(bootstrap, "install_python", lambda *args: calls.append(args))
    return calls


def test_check_missing_is_read_only(root, capsys):
    before = list(root.rglob("*"))
    assert bootstrap.main(["check"], root=root) == 2
    status = json.loads(capsys.readouterr().out)
    assert not status["previously_run"]
    assert not status["ready"]
    assert list(root.rglob("*")) == before


def test_success_receipt_repeat_and_stale(root, monkeypatch, capsys):
    calls = fake_install(monkeypatch, root)
    assert bootstrap.main(["install"], root=root) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["already_bootstrapped"]
    receipt = bootstrap.receipt_path(root).read_bytes()
    assert len(calls) == 1
    assert bootstrap.main(["install"], root=root) == 0
    capsys.readouterr()
    assert len(calls) == 1
    assert bootstrap.receipt_path(root).read_bytes() == receipt
    with (root / "pyproject.toml").open("a") as stream:
        stream.write("\n# revised dependency specification\n")
    assert bootstrap.main(["check"], root=root) == 2
    stale = json.loads(capsys.readouterr().out)
    assert stale["previously_run"] and not stale["already_bootstrapped"]


def test_failed_install_never_records_success(root, monkeypatch, capsys):
    fake_install(monkeypatch, root)
    def fail(*args):
        raise bootstrap.BootstrapError("installer failed")
    monkeypatch.setattr(bootstrap, "install_python", fail)
    assert bootstrap.main(["install"], root=root) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "installer failed" in captured.err
    assert not bootstrap.receipt_path(root).exists()


def test_deleted_dependency_invalidates_receipt(root, monkeypatch, capsys):
    fake_install(monkeypatch, root)
    assert bootstrap.main(["install"], root=root) == 0
    capsys.readouterr()
    monkeypatch.setattr(bootstrap, "probe", lambda *args: {"ok": False, "reason": "missing"})
    assert bootstrap.main(["check"], root=root) == 2
    status = json.loads(capsys.readouterr().out)
    assert status["previously_run"] and not status["ready"]


@pytest.mark.parametrize("platform,manager,expected", [
    ("Windows", "winget", ["install", "--id", "UB-Mannheim.TesseractOCR", "--exact"]),
    ("Darwin", "brew", ["install", "tesseract"]),
    ("Linux", "apt-get", ["install", "-y", "tesseract-ocr", "tesseract-ocr-eng"]),
    ("Linux", "dnf", ["install", "-y", "tesseract", "tesseract-langpack-eng"]),
])
def test_platform_install_command(monkeypatch, platform, manager, expected):
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: name if name == manager else None)
    assert bootstrap.ocr_install_command(platform, elevated=True) == [manager, *expected]


def test_unsupported_platform_refuses(monkeypatch):
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: None)
    with pytest.raises(bootstrap.BootstrapError, match="package manager"):
        bootstrap.ocr_install_command("unknown", elevated=True)


def test_real_cli_check_without_venv(root):
    scripts = root / "scripts"
    scripts.mkdir()
    entry = scripts / "pil_bootstrap.py"
    entry.write_bytes(Path(bootstrap.__file__).read_bytes())
    result = subprocess.run([sys.executable, str(entry), "check"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert json.loads(result.stdout)["previously_run"] is False
    assert not (root / ".venv").exists()


def test_real_install_repeat_and_corrupt_receipt(root):
    scripts = root / "scripts"
    scripts.mkdir()
    entry = scripts / "pil_bootstrap.py"
    entry.write_bytes(Path(bootstrap.__file__).read_bytes())
    def invoke(command):
        return subprocess.run([sys.executable, str(entry), command],
                              capture_output=True, text=True, timeout=60)
    # Empty requirements isolate actual venv/installer lifecycle from network.
    first = invoke("install")
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["already_bootstrapped"]
    receipt = bootstrap.receipt_path(root)
    recorded = receipt.read_bytes()
    repeated = invoke("install")
    assert repeated.returncode == 0, repeated.stderr
    assert repeated.stderr == ""
    assert receipt.read_bytes() == recorded
    receipt.write_text("broken JSON")
    checked = invoke("check")
    assert checked.returncode == 2
    status = json.loads(checked.stdout)
    assert status["ready"] and not status["previously_run"]


def test_probe_detects_incompatible_installed_version():
    result = bootstrap.probe([sys.executable, "-I", "-B", "-c", bootstrap.PROBE,
                              json.dumps([["numpy>=9999"], bootstrap.MODULES])])
    assert not result["ok"]
    assert "does not satisfy numpy>=9999" in result["reason"]


def test_failed_post_install_probe_leaves_no_receipt(root, monkeypatch, capsys):
    fake_install(monkeypatch, root)
    monkeypatch.setattr(bootstrap, "probe", lambda *args: {"ok": False, "reason": "DLL missing"})
    assert bootstrap.main(["install"], root=root) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "DLL missing" in captured.err
    assert not bootstrap.receipt_path(root).exists()
