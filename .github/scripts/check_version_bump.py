#!/usr/bin/env python3
"""Fail a PR that ships new behaviour without moving the release version.

The packaging conformance test already proves each plugin's four manifests
and every TOOL_VERSION in its scripts agree with each other. It cannot see the
one thing that actually breaks releases: a PR that changes shipped code and
leaves the version alone, so the release workflow finds no new tag to cut
and the published version silently describes older behaviour.

The repository's marketplace lists more than one plugin, and each is released
on its own tag, so each is checked independently: a change to one plugin's
shipped paths requires a bump of that plugin's version, and of no other. A
path belongs to the plugin whose marketplace `source` directory is the longest
prefix of it, so the root plugin (`source: "./"`) never owns anything under
`plugins/`.

Only paths that end up in front of a user count as shipped code. Tests,
evidence bundles under runs/, and CI plumbing do not.
"""

from __future__ import annotations

import json
import subprocess
import sys

MARKETPLACE = ".claude-plugin/marketplace.json"
MANIFEST = ".claude-plugin/plugin.json"

# Relative to each plugin's own directory. Changing any of these changes what
# an installed plugin does. Extension is not a useful filter here: under
# skills/ and agents/ the Markdown IS the product, so everything under these
# prefixes counts. Prose that ships with the repo rather than the plugin
# (README.md, docs/, runs/) is not listed, and neither are tests, evals or CI
# plumbing.
SHIPPED_PREFIXES = ("scripts/", "skills/", "schemas/", "agents/")
SHIPPED_FILES = (MANIFEST, "plugin.json", ".codex-plugin/plugin.json", "pyproject.toml")


def _run(*args):
    return subprocess.run(
        args, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def _show(ref, path):
    try:
        return _run("git", "show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None


def _plugin_dir(source):
    """Marketplace `source` as a repo-relative directory prefix ('' for root)."""
    prefix = source.strip()
    if prefix.startswith("./"):
        prefix = prefix[2:]
    prefix = prefix.strip("/")
    return f"{prefix}/" if prefix else ""


def plugins_at(ref):
    """[(name, directory prefix)] for every plugin the marketplace lists at ref."""
    blob = _show(ref, MARKETPLACE)
    if blob is None:
        return [("root", "")]
    return [(p["name"], _plugin_dir(p["source"])) for p in json.loads(blob)["plugins"]]


def _version_at(ref, prefix):
    blob = _show(ref, f"{prefix}{MANIFEST}")
    return json.loads(blob).get("version") if blob else None


def _changed_files(base, head):
    diff = _run("git", "diff", "--name-only", f"{base}...{head}")
    return [line for line in diff.splitlines() if line]


def owner(path, plugins):
    """The plugin whose directory is the longest prefix of ``path``, or None."""
    owners = [(prefix, name) for name, prefix in plugins if path.startswith(prefix)]
    return max(owners, key=lambda item: len(item[0]))[1] if owners else None


def is_shipped(relative):
    return relative.startswith(SHIPPED_PREFIXES) or relative in SHIPPED_FILES


def shipped_changes(changed, plugins):
    """{plugin name: [changed shipped paths]}, each path counted for one plugin."""
    result = {name: [] for name, _ in plugins}
    prefixes = dict(plugins)
    for path in changed:
        name = owner(path, plugins)
        if name is not None and is_shipped(path[len(prefixes[name]):]):
            result[name].append(path)
    return {name: sorted(paths) for name, paths in result.items()}


def _bump_hint(name, prefix):
    where = prefix or "the repository root"
    lines = [
        f"Bump {name} in every place that carries its version (under {where}):",
        f"  - {prefix}{MANIFEST}, {prefix}plugin.json, {prefix}.codex-plugin/plugin.json",
        f"  - its entry in {MARKETPLACE}",
        f"  - TOOL_VERSION in every {prefix}scripts/*.py that declares one",
        f"  - a Status entry in {prefix}README.md",
    ]
    if not prefix:
        lines.insert(3, "  - pyproject.toml, and docs/index.md's Status entry")
    return "\n".join(lines)


def main(argv):
    if len(argv) != 3:
        print("usage: check_version_bump.py <base-sha> <head-sha>", file=sys.stderr)
        return 2
    base, head = argv[1], argv[2]

    plugins = plugins_at(head)
    changes = shipped_changes(_changed_files(base, head), plugins)
    failed = []
    for name, prefix in plugins:
        shipped = changes[name]
        if not shipped:
            print(f"{name}: no shipped code changed; a version bump is not required.")
            continue
        base_version = _version_at(base, prefix)
        head_version = _version_at(head, prefix)
        print(f"{name}: {prefix}{MANIFEST}: {base_version} (base) -> {head_version} (head)")
        print("  Shipped paths changed:")
        for path in shipped:
            print(f"    {path}")
        if head_version and head_version != base_version:
            print(f"  Version moved to {head_version}. OK.")
            continue
        failed.append(name)
        print(
            f"\n{name}: this PR changes shipped code but leaves the version at "
            f"{base_version}. The release workflow has nothing to tag on merge.\n"
            + _bump_hint(name, prefix)
            + "\ntests/test_packaging_conformance.py verifies the manifests and "
            "TOOL_VERSIONs agree.\n",
            file=sys.stderr,
        )

    if failed:
        print(f"Version bump missing for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
