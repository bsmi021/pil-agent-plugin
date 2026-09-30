"""Tests for the release plumbing under `.github/scripts/`.

The repository's marketplace lists two plugins, each released on its own
`<name>--v<version>` tag, so every script here must treat them independently.
The version-bump check runs against synthetic git repositories built in
tmp_path (never against this checkout, whose root plugin legitimately awaits
its bump), with identity and signing forced off so the tests do not depend on
the machine's git configuration.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_SCRIPTS = REPO_ROOT / ".github" / "scripts"
CHECK_VERSION_BUMP = CI_SCRIPTS / "check_version_bump.py"

GIT = [
    "git",
    "-c", "user.name=ci-test",
    "-c", "user.email=ci-test@example.invalid",
    "-c", "commit.gpgsign=false",
    "-c", "core.autocrlf=false",
]


def _git(repo, *args):
    return subprocess.run(
        [*GIT, *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write(repo, relative, text):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manifest(name, version):
    return json.dumps({"name": name, "version": version}, indent=2) + "\n"


def _set_version(repo, prefix, name, version):
    _write(repo, f"{prefix}.claude-plugin/plugin.json", _manifest(name, version))


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def two_plugin_repo(tmp_path):
    """A minimal copy of this repository's two-plugin layout, committed once."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write(
        repo,
        ".claude-plugin/marketplace.json",
        json.dumps(
            {
                "name": "pil-agent-plugin",
                "plugins": [
                    {"name": "pil-agent-plugin", "source": "./"},
                    {"name": "blender-inspect", "source": "./plugins/blender-inspect"},
                ],
            },
            indent=2,
        ),
    )
    _set_version(repo, "", "pil-agent-plugin", "0.9.6")
    _write(repo, "scripts/pil_tool.py", 'TOOL_VERSION = "0.9.6"\n')
    _write(repo, "README.md", "# root\n")
    _set_version(repo, "plugins/blender-inspect/", "blender-inspect", "0.1.0")
    _write(repo, "plugins/blender-inspect/scripts/blender_tool.py", 'TOOL_VERSION = "0.1.0"\n')
    _write(repo, "plugins/blender-inspect/README.md", "# blender-inspect\n")
    base = _commit(repo, "base")
    return repo, base


def _check(repo, base, head):
    return subprocess.run(
        [sys.executable, str(CHECK_VERSION_BUMP), base, head],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


# --- check_version_bump.py: AC-PKG-02's four synthetic diffs -----------------


def test_root_only_change_without_bump_fails(two_plugin_repo):
    repo, base = two_plugin_repo
    _write(repo, "scripts/pil_tool.py", 'TOOL_VERSION = "0.9.6"\nCHANGED = 1\n')
    head = _commit(repo, "root change")

    proc = _check(repo, base, head)

    assert proc.returncode == 1
    assert "Version bump missing for: pil-agent-plugin" in proc.stderr
    assert "blender-inspect: no shipped code changed" in proc.stdout


def test_new_plugin_only_change_without_bump_fails(two_plugin_repo):
    repo, base = two_plugin_repo
    _write(repo, "plugins/blender-inspect/scripts/blender_tool.py", 'TOOL_VERSION = "0.1.0"\nX = 1\n')
    head = _commit(repo, "new plugin change")

    proc = _check(repo, base, head)

    assert proc.returncode == 1
    assert "Version bump missing for: blender-inspect" in proc.stderr
    # plugins/ is never the root plugin's shipped code.
    assert "pil-agent-plugin: no shipped code changed" in proc.stdout
    assert "plugins/blender-inspect/.claude-plugin/plugin.json" in proc.stderr


def test_root_only_change_with_bump_passes(two_plugin_repo):
    repo, base = two_plugin_repo
    _write(repo, "scripts/pil_tool.py", 'TOOL_VERSION = "0.10.0"\n')
    _set_version(repo, "", "pil-agent-plugin", "0.10.0")
    head = _commit(repo, "root change with bump")

    proc = _check(repo, base, head)

    assert proc.returncode == 0, proc.stderr
    assert "pil-agent-plugin: .claude-plugin/plugin.json: 0.9.6 (base) -> 0.10.0 (head)" in proc.stdout


def test_new_plugin_only_change_with_bump_passes(two_plugin_repo):
    repo, base = two_plugin_repo
    _write(repo, "plugins/blender-inspect/scripts/blender_tool.py", 'TOOL_VERSION = "0.1.1"\n')
    _set_version(repo, "plugins/blender-inspect/", "blender-inspect", "0.1.1")
    head = _commit(repo, "new plugin change with bump")

    proc = _check(repo, base, head)

    assert proc.returncode == 0, proc.stderr
    assert "blender-inspect: plugins/blender-inspect/.claude-plugin/plugin.json: 0.1.0 (base) -> 0.1.1 (head)" in proc.stdout


# --- check_version_bump.py: the edges around those four ----------------------


def test_bumping_one_plugin_does_not_excuse_the_other(two_plugin_repo):
    repo, base = two_plugin_repo
    _write(repo, "scripts/pil_tool.py", 'TOOL_VERSION = "0.9.6"\nCHANGED = 1\n')
    _write(repo, "plugins/blender-inspect/skills/x/SKILL.md", "---\nname: x\n---\n")
    _set_version(repo, "plugins/blender-inspect/", "blender-inspect", "0.1.1")
    head = _commit(repo, "both change, one bump")

    proc = _check(repo, base, head)

    assert proc.returncode == 1
    assert "Version bump missing for: pil-agent-plugin" in proc.stderr
    assert "blender-inspect" not in proc.stderr.splitlines()[-1]


def test_unshipped_changes_need_no_bump(two_plugin_repo):
    repo, base = two_plugin_repo
    _write(repo, "README.md", "# root, reworded\n")
    _write(repo, "tests/test_x.py", "def test(): pass\n")
    _write(repo, "plugins/blender-inspect/README.md", "# reworded\n")
    _write(repo, "plugins/blender-inspect/tests/test_bi_x.py", "def test(): pass\n")
    _write(repo, "plugins/blender-inspect/evals/case.yaml", "prompt: x\n")
    head = _commit(repo, "docs and tests only")

    proc = _check(repo, base, head)

    assert proc.returncode == 0, proc.stderr
    assert "pil-agent-plugin: no shipped code changed" in proc.stdout
    assert "blender-inspect: no shipped code changed" in proc.stdout


def test_a_newly_added_plugin_counts_as_bumped(tmp_path):
    """Before U1 merges, main has no blender-inspect manifest at all."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write(
        repo,
        ".claude-plugin/marketplace.json",
        json.dumps({"plugins": [{"name": "pil-agent-plugin", "source": "./"}]}),
    )
    _set_version(repo, "", "pil-agent-plugin", "0.9.6")
    base = _commit(repo, "base")
    _write(
        repo,
        ".claude-plugin/marketplace.json",
        json.dumps(
            {
                "plugins": [
                    {"name": "pil-agent-plugin", "source": "./"},
                    {"name": "blender-inspect", "source": "./plugins/blender-inspect"},
                ]
            }
        ),
    )
    _set_version(repo, "plugins/blender-inspect/", "blender-inspect", "0.1.0")
    _write(repo, "plugins/blender-inspect/scripts/blender_common.py", "import sys\n")
    head = _commit(repo, "add plugin")

    proc = _check(repo, base, head)

    assert proc.returncode == 0, proc.stderr
    assert "None (base) -> 0.1.0 (head)" in proc.stdout


def test_path_ownership_uses_the_longest_source_prefix():
    sys.path.insert(0, str(CI_SCRIPTS))
    try:
        import check_version_bump as cvb
    finally:
        sys.path.remove(str(CI_SCRIPTS))
    plugins = [("root", ""), ("bi", "plugins/blender-inspect/")]

    assert cvb.owner("scripts/pil_a.py", plugins) == "root"
    assert cvb.owner("plugins/blender-inspect/scripts/b.py", plugins) == "bi"
    assert cvb.owner("plugins/other/scripts/c.py", plugins) == "root"
    assert cvb._plugin_dir("./") == "" and cvb._plugin_dir("./plugins/x") == "plugins/x/"
    assert cvb.shipped_changes(
        ["plugins/blender-inspect/scripts/b.py", "plugins/other/scripts/c.py", "pyproject.toml"],
        plugins,
    ) == {"root": ["pyproject.toml"], "bi": ["plugins/blender-inspect/scripts/b.py"]}
