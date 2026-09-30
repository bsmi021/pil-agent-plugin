#!/usr/bin/env python3
"""Print a plugin README's Status entry for one version, as release notes.

Each plugin keeps a per-release paragraph under `## Status` in its own
README.md, so the release notes are written once, by a human, in the place
contributors read -- not duplicated into a changelog that drifts. The
repository holds more than one plugin, so the README is an argument
(`--readme`, default the root plugin's README.md); the plugin's name for the
tag comes from the `.claude-plugin/plugin.json` beside that README.

With --title, prints just the release title in this repository's existing
form -- "0.6.0 - Constrained multiview reconstruction" -- taken from the
same Status headline, so a generated release is titled like the hand-made
ones before it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

README = Path("README.md")


def extract(version, text):
    """The `**<version> — ...**` paragraph, up to the next entry or heading."""
    start = re.search(
        rf"^\*\*{re.escape(version)}\s*[—-]", text, flags=re.MULTILINE
    )
    if not start:
        return None
    rest = text[start.start():]
    end = re.search(r"^(\*\*\d+\.\d+\.\d+\s*[—-]|## )", rest[1:], flags=re.MULTILINE)
    return (rest[: end.start() + 1] if end else rest).strip()


def title(version, text):
    """`0.7.0 - One-call profiling and the semantic layers`, or None."""
    entry = extract(version, text)
    if not entry:
        return None
    headline = re.match(
        rf"\*\*{re.escape(version)}\s*[—-]\s*(.+?)\.?\*\*", entry, flags=re.S
    )
    if not headline:
        return None
    words = " ".join(headline.group(1).split())
    return f"{version} — {words[:1].upper()}{words[1:]}"


def manifest_for(readme):
    """The Claude Code manifest of the plugin whose README this is."""
    return Path(readme).parent / ".claude-plugin" / "plugin.json"


def main(argv):
    parser = argparse.ArgumentParser(
        prog="release_notes.py",
        description="Print a plugin README's Status entry (or title) for one version.",
    )
    parser.add_argument("--title", action="store_true", help="print only the release title")
    parser.add_argument(
        "--readme",
        type=Path,
        default=README,
        help="the plugin's README.md (default: README.md, the root plugin)",
    )
    parser.add_argument("version")
    args = parser.parse_args(argv[1:])

    text = args.readme.read_text(encoding="utf-8")
    if args.title:
        name = title(args.version, text)
        if not name:
            print(
                f"No `## Status` entry for {args.version} in {args.readme}.",
                file=sys.stderr,
            )
            return 1
        print(name)
        return 0

    notes = extract(args.version, text)
    if not notes:
        # A release with no notes is worse than a loud failure here: the
        # Status entry is the one place this repo documents what shipped.
        print(
            f"No `## Status` entry for {args.version} in {args.readme}. Add one "
            "before releasing.",
            file=sys.stderr,
        )
        return 1
    print(notes)
    print()
    plugin_name = json.loads(manifest_for(args.readme).read_text(encoding="utf-8"))["name"]
    tag = f"{plugin_name}--v{args.version}"
    print(
        f"Full diff: https://github.com/"
        f"{os.environ.get('GITHUB_REPOSITORY', 'bsmi021/pil-agent-plugin')}"
        f"/commits/{tag}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
