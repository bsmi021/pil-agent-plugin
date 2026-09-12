---
name: bootstrap
description: Check or install PIL Agent Plugin dependencies for a requested capability. Use for setup, missing-dependency repair, or bootstrap status.
---

# PIL Agent Plugin bootstrap

Use the bundled `scripts/pil_bootstrap.py` against the plugin copy that will
actually run the tools. Resolve the plugin root two levels above this file;
do not assume the current working directory or modify a separate source clone.

## Choose the requested capability

- No flags: Pillow and NumPy.
- `--comparison`: scikit-image for standard SSIM.
- `--ocr`: Tesseract and English language data.
- `--embedding`: ONNX Runtime and the caller's model.
- `--reconstruction`: OpenCV and SciPy. Blender remains a separate install.
- `--mcp`: the optional stdio adapter dependency.
- `--model <file> --preprocessing imagenet|clip`: select and diagnose an
  existing embedding model; this implies `--embedding`.

Use the same capability flags for `check` and `install`. See the
[setup workflow](../../docs/measurement-workflows.md) for host configuration.

## Check before changing the environment

Run `check` with Python 3.11 or newer. The script uses only the standard
library and probes `<plugin-root>/.venv` independently of the launcher's
packages.

```powershell
py -3 '<plugin-root>\scripts\pil_bootstrap.py' check --ocr
```

```sh
python3 '<plugin-root>/scripts/pil_bootstrap.py' check --ocr
```

Interpret the exit code and JSON together:

- Exit 0 with `already_bootstrapped: true`: the matching receipt and live
  probes pass.
- `ready: true` without a matching receipt: dependencies work; a status-only
  request is complete and does not require installation.
- `previously_run: true` alone is historical, not current readiness.
- Exit 2 with JSON: report the named failed check.
- Exit 2 with empty stdout: report the named stderr error; do not infer success.

## Install only when the request includes setup or repair

Replace `check` with `install`, preserving the selected flags, then rerun
`check`. Existing authorization for setup is enough; a status question alone
does not authorize installation. Install only requested capabilities.

The script prefers uv and otherwise uses venv/pip. OCR may invoke the platform
package manager and can require an interactive elevation prompt. Do not bypass
that prompt, switch package managers silently after a failure, delete a working
venv as routine repair, or fabricate the local receipt.

Model weights are not downloaded. If a model or matching preprocessing profile
is absent, distinguish successful package setup from incomplete embedding
readiness. Diagnostics validate loading and declared input shape, not model
quality or calibration.

Finish by reporting the active plugin root, interpreter, selected capabilities,
live check result, and any remaining model or system prerequisite. The detailed
manual setup alternatives are in the [README](../../README.md#bootstrap).
