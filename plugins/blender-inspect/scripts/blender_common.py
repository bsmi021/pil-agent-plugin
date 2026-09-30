#!/usr/bin/env python
"""Shared Blender plumbing for every `blender_*.py` tool in blender-inspect.

`bpy` exists only inside Blender's bundled interpreter, so each tool is a
stdlib-only host-side wrapper that writes a probe script, runs Blender
headless on it, and parses the probe's JSON out of Blender's stdout. This
module owns the parts every tool must do identically:

- **Discovery.** `--blender-executable` wins; then the
  `BLENDER_INSPECT_BLENDER` environment variable; then the Windows Blender 5.2
  install path; then `blender` on PATH. An explicit path or environment value
  that does not resolve is a refusal, never a silent fallback to another
  Blender.
- **Launch.** Always `--factory-startup --background` (user add-ons such as
  Tripo3D must not load) and `--python-exit-code 1`, so a probe that raises
  makes Blender exit non-zero instead of 0. Blender reads its arguments in
  order: the `.blend` comes first, the exit-code flag before `--python`, and
  the probe's own arguments after `--`.
- **Sentinels.** The probe brackets its JSON between two sentinel lines so it
  can be lifted out of Blender's startup and shutdown chatter. Exactly one
  well-formed block is accepted; anything else means the probe did not finish
  cleanly.
- **Refusal contract.** When a tool cannot answer it exits 2 with byte-empty
  stdout and a one-line reason on stderr, the same contract as
  `pil_blender_mesh`.

This is a shared module, not a tool: it declares no `TOOL_VERSION`. Each
`blender_*.py` tool declares its own, as in the root plugin.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WINDOWS_BLENDER_5_2 = "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
PATH_BLENDER = "blender"

HEADLESS_FLAGS = ("--factory-startup", "--background")
PYTHON_EXIT_CODE = ("--python-exit-code", "1")

DEFAULT_TIMEOUT_SECONDS = 300


def discovery_order():
    """Human-readable search order, for `--help` text and refusal reasons."""
    return (
        "--blender-executable, $BLENDER_INSPECT_BLENDER, "
        f"{WINDOWS_BLENDER_5_2}, then {PATH_BLENDER!r} on PATH"
    )


def _resolve(candidate):
    """An existing file path, or a command name found on PATH, else None."""
    path = Path(candidate)
    if path.is_file():
        return str(path)
    return shutil.which(candidate)


def resolve_blender_executable(explicit=None):
    """Return ``(path, None)`` for a runnable Blender, or ``(None, reason)``.

    The first configured source decides: a `--blender-executable` or
    `BLENDER_INSPECT_BLENDER` value that does not resolve is refused with a
    reason naming it, rather than quietly replaced by some other Blender.
    """
    if explicit:
        found = _resolve(explicit)
        if found:
            return found, None
        return None, f"--blender-executable does not resolve to a file: {explicit}"

    from_env = os.environ.get("BLENDER_INSPECT_BLENDER")
    if from_env:
        found = _resolve(from_env)
        if found:
            return found, None
        return None, f"BLENDER_INSPECT_BLENDER does not resolve to a file: {from_env}"

    if Path(WINDOWS_BLENDER_5_2).is_file():
        return WINDOWS_BLENDER_5_2, None

    found = shutil.which(PATH_BLENDER)
    if found:
        return found, None
    return None, f"blender executable not found; searched {discovery_order()}"


def add_blender_arguments(parser):
    """The `--blender-executable` and `--timeout` options every tool takes."""
    parser.add_argument(
        "--blender-executable",
        help=f"path to the Blender executable; when absent, searches {discovery_order()}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"seconds to wait for Blender (default {DEFAULT_TIMEOUT_SECONDS})",
    )


def sentinels(tool):
    """The begin/end marker lines bracketing ``tool``'s probe payload."""
    tag = re.sub(r"[^A-Z0-9]+", "_", tool.upper())
    return f"<<<BLENDER_INSPECT_{tag}_BEGIN>>>", f"<<<BLENDER_INSPECT_{tag}_END>>>"


# Prepended to every probe. It gives the probe `ARGS` (its arguments after
# `--`) and `emit(payload)`, which writes the one sentinel-wrapped JSON block.
_PROBE_HEADER = '''\
import json as _bi_json
import sys as _bi_sys

BEGIN = {begin!r}
END = {end!r}
ARGS = _bi_sys.argv[_bi_sys.argv.index("--") + 1:] if "--" in _bi_sys.argv else []


def emit(payload):
    _bi_sys.stdout.write(BEGIN + "\\n")
    _bi_sys.stdout.write(_bi_json.dumps(payload, sort_keys=True, allow_nan=False))
    _bi_sys.stdout.write("\\n" + END + "\\n")
    _bi_sys.stdout.flush()


'''


def probe_source(tool, body):
    """Full probe script: the shared header, then the tool's own ``body``."""
    begin, end = sentinels(tool)
    return _PROBE_HEADER.format(begin=begin, end=end) + body


def build_command(blender_executable, probe_path, blend=None, script_args=()):
    """The headless Blender command line, in the order Blender needs it."""
    cmd = [str(blender_executable), *HEADLESS_FLAGS]
    if blend is not None:
        cmd.append(str(blend))
    cmd += [*PYTHON_EXIT_CODE, "--python", str(probe_path)]
    if script_args:
        cmd += ["--", *(str(a) for a in script_args)]
    return cmd


def extract_payload(stdout, tool):
    """The probe's JSON object, or None unless exactly one clean block exists."""
    begin, end = sentinels(tool)
    pattern = re.escape(begin) + r"\r?\n(.*?)\r?\n" + re.escape(end)
    matches = re.findall(pattern, stdout or "", flags=re.S)
    if len(matches) != 1:
        return None
    try:
        payload = json.loads(matches[0])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _last_line(text):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else "no output"


def run_probe(
    blender_executable,
    tool,
    body,
    blend=None,
    script_args=(),
    timeout=DEFAULT_TIMEOUT_SECONDS,
    runner=subprocess.run,
):
    """Run ``body`` inside headless Blender and return ``(payload, error)``.

    Exactly one is set. ``error`` is a one-line reason for any failure: spawn
    error, timeout, non-zero exit (a probe that raised, via
    `--python-exit-code 1`), or no single sentinel-wrapped JSON block.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=f"_{tool}_probe.py", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(probe_source(tool, body))
        probe_path = handle.name

    where = f" on {blend}" if blend is not None else ""
    try:
        cmd = build_command(blender_executable, probe_path, blend, script_args)
        try:
            proc = runner(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return None, f"blender timed out after {timeout}s{where}"
        except OSError as exc:
            return None, f"blender spawn failed: {exc}"
    finally:
        try:
            os.unlink(probe_path)
        except OSError:
            pass

    if proc.returncode != 0:
        detail = _last_line(proc.stderr or proc.stdout)
        return None, f"blender exited {proc.returncode}{where}: {detail}"

    payload = extract_payload(proc.stdout, tool)
    if payload is None:
        return None, f"probe emitted no sentinel-wrapped payload{where}"
    return payload, None


def refuse(tool, reason):
    """The refusal contract: nothing on stdout, one line on stderr, exit 2.

    Returns 2 so a tool's ``main`` can ``return refuse(...)``.
    """
    one_line = " ".join(str(reason).split())
    sys.stderr.write(f"{tool}: {one_line}\n")
    sys.stderr.flush()
    return 2


def write_payload(payload, stream=None):
    """Deterministic JSON on stdout (sorted keys, no NaN), newline-terminated."""
    stream = stream or sys.stdout
    json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
    stream.write("\n")
