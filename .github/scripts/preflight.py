#!/usr/bin/env python3
"""Run the release checks CI would run, locally, before a PR is opened.

In order, stopping at the first failure and printing its output:

1. `check_version_bump.py <base> HEAD`: every plugin whose shipped paths
   changed has a new version.
2. `uv lock --check`: the lockfile matches pyproject.toml.
3. `release_notes.py` (notes and title) for every marketplace plugin's current
   version, with UTF-8 output forced: each plugin's README has the Status
   entry the release workflow will publish.
4. The packaging conformance tests.

Standard library only, so it runs under any `python`; the conformance tests
run through `uv run`. The harness finalize step and the release unit call it:

    python .github/scripts/preflight.py [--base origin/main]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_SCRIPTS = REPO_ROOT / ".github" / "scripts"
DEFAULT_BASE = "origin/main"

sys.path.insert(0, str(CI_SCRIPTS))
import release_plan  # noqa: E402

sys.path.remove(str(CI_SCRIPTS))


def steps(base):
    """[(label, command, extra environment)] in the order they must pass."""
    python = sys.executable
    utf8 = {"PYTHONIOENCODING": "utf-8"}
    planned = [
        (
            "version bump",
            [python, str(CI_SCRIPTS / "check_version_bump.py"), base, "HEAD"],
            {},
        ),
        ("lockfile", ["uv", "lock", "--check"], {}),
    ]
    for name, version, _tag, readme in release_plan.releases(REPO_ROOT):
        notes = [python, str(CI_SCRIPTS / "release_notes.py")]
        planned.append(
            (f"release notes {name} {version}", [*notes, "--readme", readme, version], utf8)
        )
        planned.append(
            (
                f"release title {name} {version}",
                [*notes, "--title", "--readme", readme, version],
                utf8,
            )
        )
    planned.append(
        (
            "packaging conformance",
            [
                "uv", "run", "pytest", "-q", "-p", "no:cacheprovider",
                "tests/test_packaging_conformance.py",
            ],
            {},
        )
    )
    return planned


def run(planned, runner=subprocess.run, out=None):
    """Run each step; return 0, or the first failing step's non-zero code."""
    out = out or sys.stdout
    for label, cmd, extra_env in planned:
        out.write(f"== {label}: {' '.join(cmd)}\n")
        out.flush()
        try:
            proc = runner(
                cmd,
                cwd=REPO_ROOT,
                env={**os.environ, **extra_env},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            out.write(f"{exc}\n== FAILED: {label} (could not start)\n")
            return 1
        for stream in (proc.stdout, proc.stderr):
            if stream:
                out.write(stream if stream.endswith("\n") else stream + "\n")
        if proc.returncode != 0:
            out.write(f"== FAILED: {label} (exit {proc.returncode})\n")
            return proc.returncode
    out.write("== preflight passed\n")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(
        prog="preflight.py", description="Run the release checks in order."
    )
    parser.add_argument(
        "--base",
        default=DEFAULT_BASE,
        help=f"ref the version-bump check compares HEAD against (default {DEFAULT_BASE})",
    )
    args = parser.parse_args(argv[1:])
    # Release notes carry em dashes; a cp1252 console must not crash on them.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return run(steps(args.base))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
