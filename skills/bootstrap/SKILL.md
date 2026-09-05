---
name: bootstrap
description: Install or diagnose PIL Agent Plugin dependencies on Windows, macOS, and Linux, and check whether bootstrap has already completed for the current plugin environment. Use for initial plugin setup, missing dependencies, or bootstrap status requests.
---

# PIL Agent Plugin bootstrap

Use the bundled [bootstrap script](../../scripts/pil_bootstrap.py). Resolve the
plugin root two levels above the directory containing this SKILL.md, rather
than assuming the user's working directory is the plugin checkout. Operate on
the plugin copy that will actually run the image tools. Installation in a source
clone does not configure a separate installed plugin cache.

## Check first

Use an available Python 3.11+ interpreter. The script itself needs only stdlib;
it checks and installs packages in `<plugin-root>/.venv`, independently of the
launcher's packages. On Windows use `py -3` or a verified Python executable;
on macOS/Linux use `python3`. An existing plugin venv interpreter also works.

```powershell
py -3 '<plugin-root>\scripts\pil_bootstrap.py' check
```

```sh
python3 '<plugin-root>/scripts/pil_bootstrap.py' check
```

Select only the capabilities needed for the request, using the same flags for
check and install:

- No flags: Pillow and NumPy.
- `--ocr`: also Tesseract and English language-data diagnostics.
- `--embedding`: also ONNX Runtime and diagnostics for the configured model.
- `--reconstruction`: also OpenCV and SciPy. Blender is separately installed
  and is outside this bootstrap's scope.
- `--model '<existing-file.onnx>' --preprocessing imagenet|clip`: implies
  embedding; overrides `PIL_AGENT_EMBED_MODEL` and
  `PIL_AGENT_EMBED_PREPROCESSING` for this invocation.

Interpret the JSON and exit code together:

- `already_bootstrapped: true`, exit 0: a matching successful receipt exists and
  all selected live probes pass. Proceed without reinstalling.
- `previously_run: true` alone is historical evidence, not readiness. A stale
  manifest, changed selection, or broken dependency requires attention.
- `ready: true` with no matching receipt: dependencies work but bootstrap has
  not recorded this configuration. A status-only request needs no installation.
- Exit 2 with status JSON: inspect `checks` for the current failure. `check`
  does not install anything or write a receipt.
- Exit 2 with empty stdout: read the named error on stderr. Do not infer success
  from installer output or a receipt alone.

## Install when setup is requested

If the user requested setup or authorized repairing missing dependencies, run
the same command with `install` in place of `check`. A status question alone
does not authorize installation. Honor existing authorization without asking
again. Core-only is the default; do not install all extras speculatively.

```powershell
py -3 '<plugin-root>\scripts\pil_bootstrap.py' install --ocr
py -3 '<plugin-root>\scripts\pil_bootstrap.py' check --ocr
```

The script uses uv when available, otherwise venv/pip. OCR installation uses
winget on Windows, Homebrew on macOS, or apt-get/dnf on Linux (root/sudo may be
needed). Package-manager prompts belong to the user; do not bypass elevation
restrictions or silently change package managers after an installer failure.
If Python or the platform package manager is unavailable, report that specific
prerequisite and consult the [README setup section](../../README.md#bootstrap)
instead of repeatedly retrying.

Model weights are never downloaded. Use the user's model and matching profile;
if absent, explain that Python packages may have installed but embedding setup
cannot complete until the model is supplied. A wrong profile can pass the
shape check; diagnostics do not establish model quality or recalibrate claims.
Keep capabilities, model gates, calibration artifacts, and version unchanged.

A receipt is written to `.venv/pil-agent-bootstrap.json` only after all selected
checks pass. It is local state and must not be committed or manually fabricated.
On failure, resolve the named cause before retrying; preserve existing working
dependencies and do not delete the venv as a routine repair step. Report selected
capabilities, the interpreter/environment used, the live check outcome, and any
remaining model or system prerequisite.
