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


# --- release_notes.py: notes and title from a given plugin README -------------

RELEASE_NOTES = CI_SCRIPTS / "release_notes.py"
RELEASE_PLAN = CI_SCRIPTS / "release_plan.py"
MARKET_PLUGINS = [
    (entry["name"], REPO_ROOT / entry["source"])
    for entry in json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )["plugins"]
]


def _version(plugin_dir):
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


def _notes(*args, cwd=REPO_ROOT):
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("GITHUB_REPOSITORY", None)
    return subprocess.run(
        [sys.executable, str(RELEASE_NOTES), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


@pytest.mark.parametrize("name,plugin_dir", MARKET_PLUGINS, ids=[n for n, _ in MARKET_PLUGINS])
def test_release_notes_exist_for_each_plugins_current_version(name, plugin_dir):
    version = _version(plugin_dir)
    readme = (plugin_dir / "README.md").resolve()

    notes = _notes("--readme", str(readme), version)
    heading = _notes("--title", "--readme", str(readme), version)

    assert notes.returncode == 0, notes.stderr
    assert notes.stdout.startswith(f"**{version} \u2014 ")
    assert notes.stdout.rstrip().endswith(f"/commits/{name}--v{version}")
    assert heading.returncode == 0, heading.stderr
    assert heading.stdout.startswith(f"{version} \u2014 ")


def test_release_notes_default_to_the_root_readme():
    version = _version(REPO_ROOT)

    notes = _notes(version)

    assert notes.returncode == 0, notes.stderr
    assert notes.stdout.rstrip().endswith(f"/commits/pil-agent-plugin--v{version}")


def test_release_notes_refuse_a_version_without_a_status_entry():
    readme = REPO_ROOT / "plugins" / "blender-inspect" / "README.md"

    notes = _notes("--readme", str(readme), "9.9.9")
    heading = _notes("--title", "--readme", str(readme), "9.9.9")

    assert notes.returncode == 1 and notes.stdout == ""
    assert "No `## Status` entry for 9.9.9" in notes.stderr
    assert heading.returncode == 1


def test_release_notes_stop_at_the_next_entry(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(_manifest("demo", "0.2.0"))
    readme = tmp_path / "README.md"
    readme.write_text(
        "# demo\n\n## Status\n\n**0.2.0 \u2014 second.** Two.\n\n"
        "**0.1.0 \u2014 first.** One.\n\n## License\n",
        encoding="utf-8",
    )

    notes = _notes("--readme", str(readme), "0.1.0")
    heading = _notes("--title", "--readme", str(readme), "0.2.0")

    assert notes.stdout.startswith("**0.1.0 \u2014 first.** One.\n\nFull diff:")
    assert notes.stdout.rstrip().endswith("/commits/demo--v0.1.0")
    assert heading.stdout.strip() == "0.2.0 \u2014 Second"


# --- release_plan.py: release.yml's tag loop ---------------------------------


def _import(name):
    sys.path.insert(0, str(CI_SCRIPTS))
    try:
        return __import__(name)
    finally:
        sys.path.remove(str(CI_SCRIPTS))


def test_release_plan_yields_one_tag_per_marketplace_plugin():
    plan = _import("release_plan").releases(REPO_ROOT)

    assert [(name, tag) for name, _, tag, _ in plan] == [
        (name, f"{name}--v{_version(d)}") for name, d in MARKET_PLUGINS
    ]
    assert [readme for *_, readme in plan] == ["README.md", "plugins/blender-inspect/README.md"]


def test_release_plan_skips_plugins_whose_tag_exists():
    release_plan = _import("release_plan")
    plan = release_plan.releases(REPO_ROOT)
    root_tag = plan[0][2]

    assert release_plan.pending(plan, []) == plan
    assert release_plan.pending(plan, [root_tag, "v0.7.0"]) == plan[1:]
    assert release_plan.pending(plan, [tag for _, _, tag, _ in plan]) == []


def test_dry_simulation_of_the_tag_loop_yields_both_tags():
    """The CLI with --all is what release.yml's loop would do on a repository
    with no tags: one line per plugin, each with its own README."""
    proc = subprocess.run(
        [sys.executable, str(RELEASE_PLAN), "--all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    rows = [line.split("\t") for line in proc.stdout.splitlines()]
    assert [row[2] for row in rows] == [f"{n}--v{_version(d)}" for n, d in MARKET_PLUGINS]
    for name, version, tag, readme in rows:
        notes = _notes("--readme", readme, version)
        assert notes.returncode == 0, notes.stderr


# --- the workflows wire those scripts up --------------------------------------


def _workflow(name):
    import yaml

    return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def _run_lines(workflow):
    return "\n".join(
        step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]
    )


def test_release_workflow_loops_over_the_plan():
    runs = _run_lines(_workflow("release.yml"))

    assert "python .github/scripts/release_plan.py > RELEASE_PLAN.tsv" in runs
    assert 'release_notes.py --readme "$README" "$VERSION"' in runs
    assert 'release_notes.py --title --readme "$README" "$VERSION"' in runs
    assert 'git tag -a "$TAG"' in runs
    assert runs.count("done < RELEASE_PLAN.tsv") == 2
    assert "pytest -q tests/test_packaging_conformance.py" in runs


def test_ci_runs_both_plugins_tests_and_the_bump_check():
    runs = _run_lines(_workflow("ci.yml"))

    assert "pytest -q tests plugins/blender-inspect/tests" in runs
    assert "check_version_bump.py" in runs
