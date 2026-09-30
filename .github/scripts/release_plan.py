#!/usr/bin/env python3
"""List the marketplace plugins whose current version has no release tag yet.

release.yml's tag loop reads this script's output, so the loop and any dry
simulation of it run the same code. One tab-separated line per pending
release, in marketplace order:

    <name>\t<version>\t<name>--v<version>\t<path to that plugin's README.md>

The version is the plugin's own `.claude-plugin/plugin.json`; tags follow the
repository's `<plugin name>--v<version>` form. `--all` ignores existing tags,
which is the dry simulation: it shows every tag a fresh repository would get.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

MARKETPLACE = Path(".claude-plugin/marketplace.json")


def _plugin_dir(source):
    prefix = source.strip()
    if prefix.startswith("./"):
        prefix = prefix[2:]
    return Path(prefix.strip("/") or ".")


def releases(root=Path(".")):
    """[(name, version, tag, readme)] for every plugin the marketplace lists."""
    market = json.loads((root / MARKETPLACE).read_text(encoding="utf-8"))
    found = []
    for entry in market["plugins"]:
        directory = _plugin_dir(entry["source"])
        manifest = json.loads(
            (root / directory / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        name, version = manifest["name"], manifest["version"]
        readme = (directory / "README.md").as_posix()
        found.append((name, version, f"{name}--v{version}", readme))
    return found


def pending(plan, existing_tags):
    return [release for release in plan if release[2] not in set(existing_tags)]


def existing_tags():
    out = subprocess.run(
        ["git", "tag", "--list"], check=True, capture_output=True, text=True
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main(argv):
    parser = argparse.ArgumentParser(
        prog="release_plan.py",
        description="Print the marketplace plugins whose version is not tagged yet.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="ignore existing tags (dry simulation of a first release)",
    )
    args = parser.parse_args(argv[1:])

    plan = releases()
    todo = plan if args.all else pending(plan, existing_tags())
    for release in todo:
        print("\t".join(release))
    released = [release[2] for release in plan if release not in todo]
    for tag in released:
        print(f"{tag} already exists; nothing to release.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
