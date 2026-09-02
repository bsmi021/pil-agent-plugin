#!/usr/bin/env python3
"""Fail a PR that ships new behaviour without moving the release version.

The packaging conformance test already proves the four manifests and every
``scripts/pil_*.py`` TOOL_VERSION agree with each other. It cannot see the
one thing that actually breaks releases: a PR that changes shipped code and
leaves the version alone, so the release workflow finds no new tag to cut
and the published version silently describes older behaviour.

Only paths that end up in front of a user count as shipped code. Tests,
evidence bundles under runs/, and CI plumbing do not.
"""

from __future__ import annotations

import json
import subprocess
import sys

MANIFEST = ".claude-plugin/plugin.json"

# Changing any of these changes what an installed plugin does. Extension is
# not a useful filter here: under skills/ and agents/ the Markdown IS the
# product, so everything under these prefixes counts. Prose that ships with
# the repo rather than the plugin (README.md, docs/, runs/) is not listed,
# and neither are tests or CI plumbing.
SHIPPED_PREFIXES = ("scripts/", "skills/", "schemas/", "agents/")
SHIPPED_FILES = (MANIFEST, "plugin.json", ".codex-plugin/plugin.json", "pyproject.toml")


def _run(*args):
    return subprocess.run(
        args, check=True, capture_output=True, text=True
    ).stdout.strip()


def _version_at(ref):
    try:
        blob = _run("git", "show", f"{ref}:{MANIFEST}")
    except subprocess.CalledProcessError:
        return None
    return json.loads(blob).get("version")


def _changed_files(base, head):
    diff = _run("git", "diff", "--name-only", f"{base}...{head}")
    return [line for line in diff.splitlines() if line]


def _is_shipped(path):
    return path.startswith(SHIPPED_PREFIXES) or path in SHIPPED_FILES


def main(argv):
    if len(argv) != 3:
        print("usage: check_version_bump.py <base-sha> <head-sha>", file=sys.stderr)
        return 2
    base, head = argv[1], argv[2]

    shipped = sorted(p for p in _changed_files(base, head) if _is_shipped(p))
    if not shipped:
        print("No shipped code changed; a version bump is not required.")
        return 0

    base_version = _version_at(base)
    head_version = _version_at(head)
    print(f"{MANIFEST}: {base_version} (base) -> {head_version} (head)")
    print("Shipped paths changed:")
    for path in shipped:
        print(f"  {path}")

    if head_version and head_version != base_version:
        print(f"\nVersion moved to {head_version}. OK.")
        return 0

    print(
        f"\nThis PR changes shipped code but leaves the version at "
        f"{base_version}.\n"
        "Bump it in every place that carries it, or the release workflow has "
        "nothing to tag on merge:\n"
        f"  - {MANIFEST}, plugin.json, .codex-plugin/plugin.json,\n"
        "    .claude-plugin/marketplace.json (two occurrences), pyproject.toml\n"
        "  - TOOL_VERSION in every scripts/pil_*.py\n"
        "  - a Status entry in README.md and docs/index.md\n"
        "tests/test_packaging_conformance.py verifies the first two groups.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
