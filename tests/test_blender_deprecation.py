"""The four Blender tools that moved to blender-inspect stay behaviourally
unchanged here, but say they are deprecated: one line on stderr on every run,
the same line in `--help`, and `deprecated`/`replacement` in the capability
catalog. Hermetic: every run below refuses before Blender would start."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pil_capabilities import catalog

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
BLENDER_INSPECT_SCRIPTS = REPO_ROOT / "plugins" / "blender-inspect" / "scripts"

MOVED = {
    "pil_blender_mesh": "blender_mesh.py",
    "pil_blender_fit": "blender_fit.py",
    "pil_blender_render": "blender_render.py",
    "pil_multiview_render": "blender_multiview_render.py",
}

# Arguments that make each tool refuse on a missing .blend, after parsing.
REFUSING_ARGS = {
    "pil_blender_mesh": ["missing.blend", "--blender-executable", "{fake}"],
    "pil_blender_fit": ["missing.blend", "--body-object", "B", "--garment-object", "G", "--clearance", "0.01"],
    "pil_blender_render": ["missing.blend", "--view", "front", "--out", "{tmp}/o.png", "--blender-executable", "{fake}"],
    "pil_multiview_render": ["missing.blend", "--manifest", "m.json", "--output-dir", "{tmp}/out"],
}


def _run(tool, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / f"{tool}.py"), *args],
        capture_output=True, text=True, encoding="utf-8",
    )


@pytest.mark.parametrize("tool", sorted(MOVED))
def test_help_names_the_replacement(tool):
    proc = _run(tool, "--help")

    assert proc.returncode == 0
    assert f"deprecated: use blender-inspect/{MOVED[tool]} instead" in " ".join(proc.stdout.split())


@pytest.mark.parametrize("tool", sorted(MOVED))
def test_a_run_writes_one_deprecation_line_before_its_unchanged_refusal(tool, tmp_path):
    fake = tmp_path / "fake_blender.exe"
    fake.write_bytes(b"")
    args = [a.format(fake=fake, tmp=tmp_path) for a in REFUSING_ARGS[tool]]

    proc = _run(tool, *args)
    lines = proc.stderr.strip().splitlines()

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert len(lines) == 2, proc.stderr
    assert lines[0] == (
        f"{tool}: deprecated: use blender-inspect/{MOVED[tool]} instead; "
        "this copy is removed in the next minor release"
    )
    assert lines[1].startswith(f"{tool}: ")
    assert "not found" in lines[1]


def test_catalog_marks_exactly_the_moved_tools_deprecated_with_their_replacement():
    tools = {tool["name"]: tool for tool in catalog()["tools"]}

    for name, tool in tools.items():
        if name in MOVED:
            assert tool["deprecated"] is True, name
            assert tool["replacement"] == f"blender-inspect/{MOVED[name]}"
        else:
            assert tool["deprecated"] is False, name
            assert tool["replacement"] is None, name
    assert set(MOVED) <= set(tools)


@pytest.mark.parametrize("script", sorted(MOVED.values()))
def test_every_replacement_exists_in_blender_inspect(script):
    assert (BLENDER_INSPECT_SCRIPTS / script).is_file()
