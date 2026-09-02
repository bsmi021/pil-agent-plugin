#!/usr/bin/env python3
"""Print the README Status entry for one version, as release notes.

The repository already keeps a per-release paragraph under `## Status` in
README.md, so the release notes are written once, by a human, in the place
contributors read -- not duplicated into a changelog that drifts.

With --title, prints just the release title in this repository's existing
form -- "0.6.0 - Constrained multiview reconstruction" -- taken from the
same Status headline, so a generated release is titled like the six
hand-made ones before it.
"""

from __future__ import annotations

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
        rf"\*\*{re.escape(version)}\s*[\u2014-]\s*(.+?)\.?\*\*", entry, flags=re.S
    )
    if not headline:
        return None
    words = " ".join(headline.group(1).split())
    return f"{version} \u2014 {words[:1].upper()}{words[1:]}"


def main(argv):
    if len(argv) == 3 and argv[1] == "--title":
        text = README.read_text(encoding="utf-8")
        name = title(argv[2], text)
        if not name:
            print(
                f"No `## Status` entry for {argv[2]} in README.md.", file=sys.stderr
            )
            return 1
        print(name)
        return 0
    if len(argv) != 2:
        print("usage: release_notes.py [--title] <version>", file=sys.stderr)
        return 2
    version = argv[1]
    notes = extract(version, README.read_text(encoding="utf-8"))
    if not notes:
        # A release with no notes is worse than a loud failure here: the
        # Status entry is the one place this repo documents what shipped.
        print(
            f"No `## Status` entry for {version} in README.md. Add one before "
            "releasing.",
            file=sys.stderr,
        )
        return 1
    print(notes)
    print()
    print(
        f"Full diff: https://github.com/{__import__('os').environ.get('GITHUB_REPOSITORY', 'bsmi021/pil-agent-plugin')}/commits/v{version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
