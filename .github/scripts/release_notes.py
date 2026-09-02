#!/usr/bin/env python3
"""Print the README Status entry for one version, as release notes.

The repository already keeps a per-release paragraph under `## Status` in
README.md, so the release notes are written once, by a human, in the place
contributors read -- not duplicated into a changelog that drifts.
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


def main(argv):
    if len(argv) != 2:
        print("usage: release_notes.py <version>", file=sys.stderr)
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
