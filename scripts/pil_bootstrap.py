#!/usr/bin/env python3
"""Install/check a plugin-local environment using only Python 3.11+ stdlib."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
MODULES = {"pillow": "PIL", "numpy": "numpy", "onnxruntime": "onnxruntime",
           "opencv-python-headless": "cv2", "scipy": "scipy"}

# Executed in the target environment, never the bootstrap launcher's environment.
# The repository's requirements use numeric >= and < constraints. Refuse other
# syntax instead of silently ignoring a future dependency constraint.
PROBE = """
import importlib, importlib.metadata, json, re, sys
if sys.version_info < (3, 11):
    raise RuntimeError('target environment requires Python 3.11+')
requirements, modules = json.loads(sys.argv[1])
versions = {}
for requirement in requirements:
    match = re.fullmatch(r'([A-Za-z0-9_-]+)(.*)', requirement)
    name, constraints = match.groups()
    importlib.import_module(modules[name.lower()])
    value = importlib.metadata.version(name)
    actual = tuple(int(x) for x in value.split('.'))
    for constraint in filter(None, constraints.split(',')):
        bound = re.fullmatch(r'(>=|<)([0-9.]+)', constraint.strip())
        if not bound:
            raise RuntimeError('unsupported requirement: ' + requirement)
        operator, number = bound.groups()
        target = tuple(int(x) for x in number.split('.'))
        if not (actual >= target if operator == '>=' else actual < target):
            raise RuntimeError(name + ' ' + value + ' does not satisfy ' + requirement)
    versions[name] = value
print(json.dumps(versions))
"""


class BootstrapError(Exception):
    pass


def venv_python(root):
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def receipt_path(root):
    return root / ".venv" / "pil-agent-bootstrap.json"


def probe(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=90)
        if result.returncode:
            return {"ok": False, "reason": result.stderr.strip()[-2000:]}
        return {"ok": True, "details": json.loads(result.stdout)}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}


def configuration(root, args):
    manifest = (root / "pyproject.toml").read_bytes()
    project = tomllib.loads(manifest.decode("utf-8"))["project"]
    extras = [name for name in ("embedding", "reconstruction") if getattr(args, name)]
    requirements = list(project["dependencies"])
    for extra in extras:
        requirements.extend(project["optional-dependencies"][extra])
    signature = {"schema": 1, "root": str(root.resolve()),
                 "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                 "platform": platform.system(), "machine": platform.machine(),
                 "extras": extras, "ocr": args.ocr,
                 "python": str(venv_python(root))}
    return requirements, signature


def status(root, args):
    requirements, signature = configuration(root, args)
    python = str(venv_python(root))
    checks = {"python_dependencies": probe(
        [python, "-I", "-B", "-c", PROBE, json.dumps([requirements, MODULES])])}
    if args.ocr:
        checks["ocr"] = probe([python, "-B", str(root / "scripts/pil_ocr.py"), "--diagnose"])
    if args.embedding:
        command = [python, "-B", str(root / "scripts/pil_embed.py"), "diagnose"]
        if args.model:
            command.extend(["--model", args.model])
        if args.preprocessing:
            command.extend(["--preprocessing", args.preprocessing])
        checks["embedding"] = probe(command)
    receipt = None
    try:
        receipt = json.loads(receipt_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    ready = all(check["ok"] for check in checks.values())
    matches = isinstance(receipt, dict) and receipt.get("configuration") == signature
    return {"tool": "pil_bootstrap", "ready": ready,
            "previously_run": isinstance(receipt, dict) and "completed_at" in receipt,
            "receipt_matches": matches, "already_bootstrapped": ready and matches,
            "receipt_path": str(receipt_path(root)), "configuration": signature,
            "checks": checks}


def execute(command):
    print("pil_bootstrap: " + subprocess.list2cmdline(list(map(str, command))), file=sys.stderr)
    # Keep installers' progress out of the machine-readable stdout channel.
    result = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr)
    if result.returncode:
        raise BootstrapError(f"installer exited {result.returncode}; rerun after resolving its error")


def install_python(root, requirements):
    python = venv_python(root)
    uv = shutil.which("uv")
    if not python.is_file():
        if uv:
            execute([uv, "venv", "--python", sys.executable, str(root / ".venv")])
        else:
            execute([sys.executable, "-m", "venv", str(root / ".venv")])
    if requirements:
        if uv:
            execute([uv, "pip", "install", "--python", str(python), *requirements])
        else:
            execute([str(python), "-m", "ensurepip"])
            execute([str(python), "-m", "pip", "install", *requirements])


def ocr_install_command(system, elevated=False):
    choices = {
        "Windows": [("winget", ["install", "--id", "UB-Mannheim.TesseractOCR", "--exact"])],
        "Darwin": [("brew", ["install", "tesseract"])],
        "Linux": [("apt-get", ["install", "-y", "tesseract-ocr", "tesseract-ocr-eng"]),
                  ("dnf", ["install", "-y", "tesseract", "tesseract-langpack-eng"])],
    }
    for manager, arguments in choices.get(system, []):
        executable = shutil.which(manager)
        if executable:
            prefix = []
            if system == "Linux" and not elevated:
                sudo = shutil.which("sudo")
                if not sudo:
                    raise BootstrapError("OCR installation requires root or sudo")
                prefix = [sudo]
            return [*prefix, executable, *arguments]
    raise BootstrapError("no supported package manager found; install Tesseract "
                         "using winget (Windows), brew (macOS), apt-get or dnf (Linux)")


def install(root, args):
    before = status(root, args)
    if before["already_bootstrapped"]:
        return before
    requirements, _signature = configuration(root, args)
    # Reapply the declared constraints when the receipt is absent/stale. Install
    # adds selected extras without uninstalling previously installed extras.
    install_python(root, requirements)
    current = status(root, args)
    if args.ocr and not current["checks"]["ocr"]["ok"]:
        # A stale custom path/data override needs correction, not another install.
        if os.environ.get("PIL_AGENT_TESSERACT") or os.environ.get("TESSDATA_PREFIX"):
            raise BootstrapError(current["checks"]["ocr"]["reason"])
        elevated = hasattr(os, "geteuid") and os.geteuid() == 0
        execute(ocr_install_command(platform.system(), elevated=elevated))
        current = status(root, args)
    if not current["ready"]:
        reasons = [f"{name}: {check.get('reason')}" for name, check in current["checks"].items()
                   if not check["ok"]]
        raise BootstrapError("setup incomplete; " + "; ".join(reasons))
    receipt = receipt_path(root)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    data = {"completed_at": datetime.now(timezone.utc).isoformat(),
            "configuration": current["configuration"]}
    temporary = receipt.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(receipt)
    current.update(previously_run=True, receipt_matches=True, already_bootstrapped=True)
    return current


def main(argv=None, *, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "install"])
    parser.add_argument("--ocr", action="store_true", help="include Tesseract (English data)")
    parser.add_argument("--embedding", action="store_true", help="include runtime and configured ONNX model check")
    parser.add_argument("--reconstruction", action="store_true", help="include OpenCV and SciPy")
    parser.add_argument("--model", help="existing ONNX model path; implies --embedding")
    parser.add_argument("--preprocessing", choices=["imagenet", "clip"], help="model profile")
    args = parser.parse_args(argv)
    if args.model or args.preprocessing:
        args.embedding = True
    try:
        if sys.version_info < (3, 11):
            raise BootstrapError("Python 3.11+ is required")
        result = status(root, args) if args.command == "check" else install(root, args)
    except (BootstrapError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"pil_bootstrap: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["already_bootstrapped"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
