"""Tests for the blender-inspect skills and agent (FR-SKL-01, FR-SKL-02).

The skills are prose, so these tests pin what can drift from the code: every tool
named exists, every flag quoted is a real flag, the defect classes, render modes,
presets and schema names match the tools' own constants, the bootstrap snippet
actually runs, and the inspector agent stays read-only. Everything is hermetic
(no Blender needed): `--help` and the discovery snippet run without a launch.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import blender_common  # noqa: E402
import blender_depth_order  # noqa: E402
import blender_inspect_render  # noqa: E402
import blender_mesh_audit  # noqa: E402

SKILLS = PLUGIN / "skills"
AGENT = PLUGIN / "agents" / "blender-model-inspector.md"
INSPECTION = SKILLS / "model-inspection" / "SKILL.md"
BOOTSTRAP = SKILLS / "bootstrap" / "SKILL.md"

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TOOL_SCRIPTS = sorted(
    p.stem
    for p in SCRIPTS.glob("blender_*.py")
    if p.stem not in {"blender_common"} and not p.stem.endswith("_kernels")
)
# Flags that belong to Blender or the launcher, not to a tool's own parser.
LAUNCHER_FLAGS = {"--factory-startup", "--background", "--version"}


def front_matter(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    assert match, f"{path.name}: must open with YAML frontmatter"
    return yaml.safe_load(match.group(1)), text[match.end():]


def body(path):
    return front_matter(path)[1]


def help_flags(script):
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / f"{script}.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return set(re.findall(r"--[a-z][a-z0-9-]*", out.stdout))


@pytest.fixture(scope="module")
def known_flags():
    flags = set(LAUNCHER_FLAGS)
    for script in TOOL_SCRIPTS:
        flags |= help_flags(script)
    return flags


# --- the skills ---------------------------------------------------------------


def test_the_plugin_ships_exactly_the_two_skills():
    names = sorted(p.name for p in SKILLS.iterdir() if p.is_dir())
    assert names == ["bootstrap", "model-inspection"]


@pytest.mark.parametrize("skill", ["model-inspection", "bootstrap"])
def test_skill_frontmatter_conforms(skill):
    front, text = front_matter(SKILLS / skill / "SKILL.md")
    assert set(front) <= {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    assert front["name"] == skill
    assert SKILL_NAME_RE.match(front["name"]) and len(front["name"]) <= 64
    description = front["description"]
    assert 1 <= len(description) <= 1024
    assert "Use when" in description or "Use for" in description, "description must say when to use it"
    assert text.strip(), "skill body is empty"


@pytest.mark.parametrize("path", [INSPECTION, BOOTSTRAP, AGENT], ids=lambda p: p.parent.name + "/" + p.name)
def test_relative_links_resolve(path):
    for target in re.findall(r"\]\((?!https?:|#|mailto:)([^)#]+)", body(path)):
        assert (path.parent / target).resolve().exists(), f"{path.name}: broken link {target}"


def test_model_inspection_states_the_loop_in_order():
    text = body(INSPECTION)
    order = ["Audit", "Look", "Order", "Fix", "Re-audit"]
    positions = [text.index(f"**{step}**") for step in order]
    assert positions == sorted(positions)
    assert "audit -> " not in text  # the loop is numbered steps, not an arrow chain
    assert "Never report a fix from the edit alone" in text


def test_every_tool_the_skills_name_exists_and_the_loop_tools_are_all_named():
    text = body(INSPECTION) + body(BOOTSTRAP) + body(AGENT)
    named = set(re.findall(r"\bblender_[a-z_]+(?=\.py)", text))
    assert named <= set(TOOL_SCRIPTS), named - set(TOOL_SCRIPTS)
    # every shipped tool is routed to in the inspection skill's table
    assert set(TOOL_SCRIPTS) <= set(re.findall(r"\bblender_[a-z_]+(?=\.py)", body(INSPECTION)))


def test_every_flag_the_skills_quote_is_a_real_flag(known_flags):
    text = body(INSPECTION) + body(BOOTSTRAP) + body(AGENT)
    quoted = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", text))
    unknown = quoted - known_flags
    assert not unknown, f"flags that no tool accepts: {sorted(unknown)}"
    assert {"--pairs", "--max-locations", "--output-dir", "--views", "--modes", "--mode"} <= quoted


def test_defect_classes_match_the_audit_tool():
    text = body(INSPECTION)
    for name in blender_mesh_audit.DEFECT_CLASSES:
        assert f"`{name}`" in text, f"defect class {name} is not described"
    assert "ten" in text and len(blender_mesh_audit.DEFECT_CLASSES) == 10


def test_render_modes_presets_and_schemas_match_the_tools():
    text = body(INSPECTION)
    for mode in blender_inspect_render.MODES:
        assert f"| `{mode}` |" in text, f"render mode {mode} has no table row"
    for preset in blender_inspect_render.PRESETS:
        assert f"`{preset}`" in text
    for schema in (
        blender_mesh_audit.SCHEMA,
        blender_inspect_render.SCHEMA,
        blender_depth_order.SCHEMA,
    ):
        assert schema in text
    # inspect-render-v1 has no schema file (FR-REN has no schema row)
    for schema in (blender_mesh_audit.SCHEMA, blender_depth_order.SCHEMA):
        assert (PLUGIN / "schemas" / f"{schema}.schema.json").is_file()


def test_model_inspection_carries_the_claims_the_handoffs_require():
    """The facts earlier units established that an agent must not get wrong."""
    text = " ".join(body(INSPECTION).split())
    # matcap shows a flipped face as a hole; the normal pass does not show it
    assert "flipped face renders as a **hole**" in text
    assert "does not show a flipped face" in text
    # read the counts, not only `clean`
    assert "Read the counts, not only `clean`" in text
    # depth order hygiene
    assert "`front_fraction`" in text and "`method: vertices`" in text
    assert "negative `range_gap` and a small positive `depth_gap`" in text
    assert "front = -Y" in text
    # heat-map colours and the planar definition of depth
    assert "near is warm, far is cool" in text and "planar" in text
    # what the layers cannot establish
    assert "What each layer does not establish" in text
    assert "inspecting never edits" in text


# --- the bootstrap skill ----------------------------------------------------------


def test_bootstrap_lists_the_discovery_order_the_code_uses():
    text = body(BOOTSTRAP)
    positions = [
        text.index("`--blender-executable`"),
        text.index("`BLENDER_INSPECT_BLENDER`"),
        text.index(blender_common.WINDOWS_BLENDER_5_2),
        text.index("`blender` on `PATH`"),
    ]
    assert positions == sorted(positions)
    assert "Blender 5.2" in text and "--factory-startup" in text


def test_bootstrap_snippet_runs_and_reports_a_path_or_the_reason(tmp_path):
    match = re.search(r"```sh\npython3 - <<'PY'\n(.*?)\nPY\n```", body(BOOTSTRAP), re.S)
    assert match, "the discovery snippet is missing"
    code = match.group(1).replace("<plugin-root>", PLUGIN.as_posix())
    done = subprocess.run(
        [sys.executable, "-"],
        input=code,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    first = done.stdout.splitlines()[0]
    resolved, reason = blender_common.resolve_blender_executable(None)
    assert first == (resolved or reason)
    if resolved:
        assert done.stdout.splitlines()[1].startswith("Blender ")


# --- the agent ---------------------------------------------------------------------


def test_agent_frontmatter_and_skill_reference():
    front, _ = front_matter(AGENT)
    assert front["name"] == AGENT.stem == "blender-model-inspector"
    assert 1 <= len(front["description"]) <= 1024
    assert "Reports findings only" in front["description"]
    assert front["skills"] in {p.name for p in SKILLS.iterdir() if p.is_dir()}
    # `bootstrap` also exists in pil-agent-plugin, so it is not preloaded by name
    assert front["skills"] == "model-inspection"


def test_agent_is_read_only():
    front, text = front_matter(AGENT)
    tools = {t.strip() for t in front["tools"].split(",")}
    assert tools == {"Bash", "Read", "Glob", "Grep"}
    assert not tools & {"Write", "Edit", "NotebookEdit"}
    flat = " ".join(text.split())
    assert "never repair" in flat
    assert "apply-copy" in flat, "the agent must forbid the one mode that writes a .blend"
    assert "scratch directory" in flat


def test_agent_runs_the_loop_and_reports_locations_and_evidence():
    flat = " ".join(body(AGENT).split())
    for tool in ("blender_mesh_audit.py", "blender_inspect_render.py", "blender_depth_order.py"):
        assert tool in flat
    positions = [flat.index(s) for s in ("**Audit.**", "**Look.**", "**Order.**", "**Reconcile.**")]
    assert positions == sorted(positions)
    assert "world-space location" in flat and "evidence image path" in flat
    assert "Do not invent a defect" in flat
