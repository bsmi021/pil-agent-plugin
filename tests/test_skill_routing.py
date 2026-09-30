"""FR-SKL-03: pil-agent-plugin's skills and the image-comparison-analyst agent
route Blender work to blender-inspect and concept depth to `pil_depth`, and no
longer recommend the deprecated `pil_blender_*` copies without saying so.

The prose is pinned where it can drift from the code: every tool named exists,
the deprecated set comes from the capability catalog, and the `pil_depth`
subcommands quoted are real.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pil_capabilities import catalog

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS = REPO_ROOT / "skills"
AGENT = REPO_ROOT / "agents" / "image-comparison-analyst.md"
BLENDER_INSPECT_SCRIPTS = REPO_ROOT / "plugins" / "blender-inspect" / "scripts"

ROUTED = ["image-analysis", "image-measurement", "multiview-reconstruction"]


def text_of(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def routed_files():
    return [SKILLS / name / "SKILL.md" for name in ROUTED] + [AGENT]


def deprecated_tools():
    return {t["name"]: t["replacement"] for t in catalog()["tools"] if t.get("deprecated")}


def paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def test_the_deprecated_set_is_the_four_moved_tools():
    assert set(deprecated_tools()) == {
        "pil_blender_mesh",
        "pil_blender_fit",
        "pil_blender_render",
        "pil_multiview_render",
    }


@pytest.mark.parametrize("path", routed_files(), ids=lambda p: p.stem if p.name != "SKILL.md" else p.parent.name)
def test_a_deprecated_tool_is_only_named_where_the_paragraph_says_it_is_deprecated(path):
    for paragraph in paragraphs(text_of(path)):
        for name in deprecated_tools():
            if re.search(rf"\b{name}\b", paragraph):
                flat = " ".join(paragraph.split())
                assert "deprecated" in flat.lower(), f"{path.name}: {name} named without a deprecation note"


@pytest.mark.parametrize("path", routed_files(), ids=lambda p: p.stem if p.name != "SKILL.md" else p.parent.name)
def test_every_blender_inspect_tool_named_exists(path):
    named = set(re.findall(r"\bblender_[a-z_]+(?=\.py)", text_of(path)))
    shipped = {p.stem for p in BLENDER_INSPECT_SCRIPTS.glob("blender_*.py")}
    assert named <= shipped, named - shipped


@pytest.mark.parametrize("path", routed_files(), ids=lambda p: p.stem if p.name != "SKILL.md" else p.parent.name)
def test_every_pil_tool_named_exists(path):
    named = set(re.findall(r"\bpil_[a-z_]+(?=\.py)", text_of(path)))
    shipped = {p.stem for p in (REPO_ROOT / "scripts").glob("pil_*.py")}
    assert named <= shipped, named - shipped


@pytest.mark.parametrize("path", routed_files(), ids=lambda p: p.stem if p.name != "SKILL.md" else p.parent.name)
def test_blender_work_is_routed_to_blender_inspect(path):
    text = " ".join(text_of(path).split())
    assert "blender-inspect" in text
    assert re.search(r"(when|if|unless)[^.]*(installed|not installed)", text), (
        "the route must say it applies only when blender-inspect is installed"
    )


def test_image_analysis_routes_defects_to_the_model_inspection_skill():
    text = " ".join(text_of(SKILLS / "image-analysis" / "SKILL.md").split())
    assert "`model-inspection`" in text and "`blender-model-inspector`" in text
    for tool in ("blender_fit.py", "blender_mesh.py", "blender_mesh_audit.py"):
        assert tool in text
    assert "not improvise" in text


def test_multiview_reconstruction_names_the_blender_inspect_stages_and_the_handoff():
    text = " ".join(text_of(SKILLS / "multiview-reconstruction" / "SKILL.md").split())
    assert "`blender_fit.py` (`blender-inspect`)" in text
    assert "`blender_multiview_render.py` (`blender-inspect`)" in text
    assert '{"payload": PATH}' in text
    assert "both stages must be external" in text


@pytest.mark.parametrize("path", [SKILLS / "image-analysis" / "SKILL.md", SKILLS / "image-measurement" / "SKILL.md", AGENT], ids=["analysis", "measurement", "analyst"])
def test_concept_depth_is_routed_to_pil_depth_with_its_limits(path):
    text = " ".join(text_of(path).split())
    assert "pil_depth.py" in text
    assert "relative" in text
    assert "metric" in text
    assert not re.search(r"pil_depth[^.]*\bis metric\b", text)


def test_depth_guidance_carries_the_u6_reading_rules():
    text = " ".join(text_of(SKILLS / "image-analysis" / "SKILL.md").split())
    assert "Spearman" in text and "AbsRel" in text
    assert "no pass/fail thresholds" in text


def test_pil_depth_subcommands_quoted_exist():
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "pil_depth.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    text = text_of(SKILLS / "image-analysis" / "SKILL.md")
    for sub in ("estimate", "compare"):
        assert sub in out.stdout
        assert f"`{sub}`" in text


def test_the_analyst_keeps_its_scope_limit_and_points_at_the_inspector_agent():
    text = " ".join(text_of(AGENT).split())
    assert "a render cannot answer this" in text
    assert "`blender-model-inspector`" in text
    assert "Do not offer edge density" in text
