---
name: bootstrap
description: Find the Blender install blender-inspect will use and check its version, or diagnose why a blender_* tool refused. Use for setup, a missing or wrong Blender, or before the first inspection run.
---

# blender-inspect bootstrap

The plugin has no Python dependencies: its `scripts/blender_*.py` wrappers use
only the standard library (Python 3.11+) and shell out to Blender. Setup is
finding a working Blender. Resolve `<plugin-root>` two levels above this file.

## Discovery order

The first configured source decides, and a value that does not resolve is an
error, never quietly replaced by another Blender:

1. `--blender-executable` on the tool
2. the `BLENDER_INSPECT_BLENDER` environment variable
3. `C:/Program Files/Blender Foundation/Blender 5.2/blender.exe`
4. `blender` on `PATH`

## Check

Resolve Blender the way every tool does, then ask it for its version:

```sh
python3 - <<'PY'
import subprocess, sys
sys.path.insert(0, "<plugin-root>/scripts")
import blender_common
path, reason = blender_common.resolve_blender_executable(None)
print(path or reason)
if path:
    print(subprocess.run([path, "--version"], capture_output=True, text=True, timeout=60).stdout.splitlines()[0])
PY
```

On Windows use `py -3` instead of `python3`. Pass a path instead of `None` to
test a specific executable. The plugin is tested with **Blender 5.2**; report
any other version and treat results on it as unverified, because render-engine
and pass behaviour changes between releases.

Finish with a real probe. `blender_mesh.py <scene.blend>` launches Blender with
`--factory-startup --background` and reports `blender_version` in its payload,
so a success proves the whole path.

## Read a refusal

A tool that cannot answer exits **2** with empty stdout and one stderr line.
Report that line verbatim; do not infer success.

- "blender executable not found; searched ..." means nothing resolved. Ask for
  the install path, then set `BLENDER_INSPECT_BLENDER` or pass
  `--blender-executable`.
- "does not resolve to a file" names the source that holds the bad value; fix
  that value rather than falling back to another Blender.
- "blend file not found", or a named object that is not a render-visible mesh:
  fix the input.
- A Blender failure line means the `.blend` could not be opened or the probe
  crashed. Do not retry blindly; report it.

## Boundaries

Do not install or download Blender, and do not edit the user's PATH or
environment without being asked. A status question does not authorise changes.
`--factory-startup` is deliberate, so user add-ons and preferences never load;
do not remove it. Report the resolved path, the version, and whether the probe
ran.
