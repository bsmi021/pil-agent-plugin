"""Packaging conformance.

The plugin ships four manifests on purpose:

- ``plugin.json``               — the portable Agent Plugins 1.0.0 manifest
                                  (https://agent-plugins.org/specification)
- ``.claude-plugin/plugin.json``— Claude Code's native manifest
- ``.claude-plugin/marketplace.json`` — the repository's marketplace, because the
                                  Claude Code CLI installs from marketplaces and
                                  cannot install from a bare directory path. It
                                  lists this plugin and blender-inspect
                                  (``plugins/blender-inspect/``), which carries
                                  its own four-manifest set.
- ``.codex-plugin/plugin.json`` — Codex-native manifest and interface metadata

Each plugin's manifests describe the same package, so they will drift unless
something checks them. The tests near the end apply the cross-manifest checks to
every plugin the marketplace lists.
These tests encode the parts of Agent Plugins 1.0.0 that a plugin author can
violate, so the conformance claim in the README travels with the code rather
than being a one-time assertion.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PORTABLE_MANIFEST = REPO_ROOT / "plugin.json"
NATIVE_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
CODEX_MANIFEST = REPO_ROOT / ".codex-plugin" / "plugin.json"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"

SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

# Closed schema: Agent Plugins 1.0.0 permits exactly these top-level fields.
ALLOWED_MANIFEST_KEYS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
ALLOWED_AUTHOR_KEYS = {"name", "email", "url"}

# plugin.schema.json: ^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$
PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")

# Agent Skills specification: 1-64 chars, lowercase alphanumeric and hyphens,
# no leading/trailing hyphen, no consecutive hyphens.
SKILL_NAME_RE = re.compile(r"^(?!.*--)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


@pytest.fixture(scope="module")
def portable():
    return json.loads(PORTABLE_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def native():
    return json.loads(NATIVE_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def codex():
    return json.loads(CODEX_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def listing():
    """The marketplace's entry for this plugin."""
    market = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    entries = [p for p in market["plugins"] if p["name"] == "pil-agent-plugin"]
    assert len(entries) == 1, "marketplace must list this plugin exactly once"
    return entries[0]


def skill_dirs():
    return sorted(p for p in (REPO_ROOT / "skills").iterdir() if p.is_dir())


# --- Agent Plugins 1.0.0: manifest -----------------------------------------


def test_portable_manifest_exists_at_plugin_root():
    """The spec fixes the portable manifest at the root, not in a subdirectory."""
    assert PORTABLE_MANIFEST.is_file()


def test_schema_declares_agent_plugins_1_0_0(portable):
    """`$schema` is a const in the schema, not merely a hint."""
    assert portable["$schema"] == SCHEMA_ID


def test_manifest_uses_only_permitted_top_level_fields(portable):
    """The manifest schema is closed; `displayName` and friends belong in
    `extensions` or in the client-native manifest, never at the root."""
    assert set(portable) <= ALLOWED_MANIFEST_KEYS


def test_name_satisfies_schema_pattern(portable):
    name = portable["name"]
    assert 1 <= len(name) <= 64
    assert PLUGIN_NAME_RE.match(name)


def test_author_object_uses_only_permitted_keys(portable):
    author = portable.get("author", {})
    assert set(author) <= ALLOWED_AUTHOR_KEYS
    assert all(isinstance(v, str) for v in author.values())


def test_no_invented_extension_namespaces(portable):
    """`extensions` keys are reverse-domain namespaces owned by the client that
    defines them. This package claims none, so the key must be absent rather
    than guessed at."""
    assert "extensions" not in portable


# --- Agent Plugins 1.0.0: component discovery ------------------------------


def test_skills_live_in_the_fixed_location():
    """Skills are discovered only as immediate children of `skills/` holding a
    regular `SKILL.md`. Clients must not search deeper, so a nested skill would
    silently vanish."""
    dirs = skill_dirs()
    assert dirs, "no skills found under skills/"
    for d in dirs:
        assert (d / "SKILL.md").is_file(), f"{d.name} has no SKILL.md at its root"


def test_mcp_configuration_is_absent_not_misplaced():
    """This package ships no MCP servers. If that changes, the config belongs in
    `mcp.json` at the root — inline MCP config in the manifest is prohibited."""
    assert not (REPO_ROOT / "mcp.json").exists()
    assert "mcpServers" not in json.loads(PORTABLE_MANIFEST.read_text(encoding="utf-8"))


def test_codex_manifest_discovers_the_shared_skills(codex):
    assert codex["name"] == "pil-agent-plugin"
    assert codex["skills"] == "./skills/"
    assert codex["interface"]["displayName"] == "PIL Image Measurement"


# --- Agent Skills specification --------------------------------------------


@pytest.mark.parametrize("skill_dir", skill_dirs(), ids=lambda p: p.name)
def test_skill_frontmatter_conforms(skill_dir):
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    assert match, "SKILL.md must open with YAML frontmatter"
    front = yaml.safe_load(match.group(1))

    assert {"name", "description"} <= set(front), "name and description are required"
    assert set(front) <= {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }

    name = front["name"]
    assert 1 <= len(name) <= 64
    assert SKILL_NAME_RE.match(name)
    assert name == skill_dir.name, "name must match the parent directory name"

    description = front["description"]
    assert 1 <= len(description) <= 1024


# --- Portable and client-native manifests must agree -----------------------


@pytest.mark.parametrize(
    "field", ["name", "version", "description", "homepage", "repository", "license"]
)
def test_manifests_agree_on_shared_metadata(portable, native, field):
    assert portable[field] == native[field], (
        f"{field} differs between plugin.json and .claude-plugin/plugin.json"
    )


def test_manifests_agree_on_author_and_keywords(portable, native):
    assert portable["author"] == native["author"]
    assert portable["keywords"] == native["keywords"]


@pytest.mark.parametrize("field", ["name", "version", "homepage", "repository", "license"])
def test_codex_manifest_agrees_on_shared_identity(portable, native, codex, field):
    assert codex[field] == portable[field] == native[field]


def test_codex_manifest_agrees_on_author(portable, codex):
    assert codex["author"] == portable["author"]


# --- the marketplace listing must agree with both manifests -----------------


def test_marketplace_source_points_at_this_repository(listing):
    """`source: "./"` makes the repository root both the marketplace and the
    plugin, which is what lets `claude plugin marketplace add <repo>` work on a
    fresh clone with no extra layout."""
    assert listing["source"] == "./"


@pytest.mark.parametrize("field", ["name", "version", "description", "license", "homepage"])
def test_marketplace_listing_matches_manifests(listing, portable, native, field):
    """Portable, Claude-native, and marketplace metadata must not drift.

    `claude plugin tag` refuses to tag when plugin.json and the marketplace
    entry disagree, so drift here breaks releases rather than merely being
    untidy. Codex-native identity is checked separately above.
    """
    assert listing[field] == portable[field] == native[field], (
        f"{field} differs between marketplace.json and the plugin manifests"
    )


def test_marketplace_listing_matches_author(listing, portable):
    assert listing["author"] == portable["author"]


def test_marketplace_listing_matches_keywords(listing, portable, native):
    assert listing["keywords"] == portable["keywords"] == native["keywords"]


def test_marketplace_display_name_matches_native_manifest(listing, native):
    """`displayName` is a Claude Code concept. Agent Plugins' closed schema has no
    such field, so the portable manifest legitimately omits it and this is pinned
    against the native manifest alone."""
    assert listing["displayName"] == native["displayName"]
    assert "displayName" not in json.loads(PORTABLE_MANIFEST.read_text(encoding="utf-8"))


def test_marketplace_declares_its_schema(listing):
    """The marketplace manifest carries Claude Code's schema identifier, the same
    way plugin.json carries the Agent Plugins one."""
    market = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert market["$schema"] == "https://anthropic.com/claude-code/marketplace.schema.json"


# --- Tool versions must agree with the manifests -----------------------------
#
# Added for the 0.4.0 release. The version string lives across the manifests,
# pyproject.toml (marketplace.json carries it twice), and one TOOL_VERSION per
# shipped CLI; the exact count grows as tools land. The manifest trio was guarded
# above; TOOL_VERSION was guarded nowhere, so a tool could ship announcing a
# version the package had never released and nothing would notice.
#
# The discovery is GLOB-DRIVEN on purpose. Listing the tools by hand would mean
# a future tool is unguarded by default -- the same "half-done" gap this test
# closes. A new scripts/pil_*.py that declares TOOL_VERSION is covered the
# moment it lands; one that declares none is ignored, since shared modules like
# pil_common and pil_region legitimately have no version of their own.

TOOL_VERSION_RE = re.compile(r'^TOOL_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)


def _declared_tool_versions():
    """{filename: version} for every shipped CLI that declares one."""
    found = {}
    for path in sorted((REPO_ROOT / "scripts").glob("pil_*.py")):
        match = TOOL_VERSION_RE.search(path.read_text(encoding="utf-8"))
        if match:
            found[path.name] = match.group(1)
    return found


def test_every_shipped_tool_declares_the_manifest_version(portable):
    """A tool announcing a version the package never released is a lie in the
    payload, and every tool stamps its version into the JSON it emits.

    Glob-driven so a tool added later cannot quietly escape the check.
    """
    # Arrange
    expected = portable["version"]
    declared = _declared_tool_versions()

    # Act / Assert
    assert declared, "expected at least one scripts/pil_*.py to declare TOOL_VERSION"
    mismatched = {name: got for name, got in declared.items() if got != expected}
    assert not mismatched, (
        f"TOOL_VERSION disagrees with plugin.json {expected!r}: {mismatched}"
    )


def test_the_version_check_actually_fails_when_a_version_is_reverted(tmp_path):
    """The guard above is only worth having if it can go red.

    D10 asks for the check to be *demonstrated* failing, not merely present, so
    this reproduces the exact drift it exists to catch -- one tool left behind
    at the previous version -- against a copy of the tree.
    """
    # Arrange: a scripts/ copy in which one tool is reverted to 0.3.0.
    scripts_copy = tmp_path / "scripts"
    scripts_copy.mkdir()
    reverted = None
    for path in sorted((REPO_ROOT / "scripts").glob("pil_*.py")):
        text = path.read_text(encoding="utf-8")
        if reverted is None and TOOL_VERSION_RE.search(text):
            text = TOOL_VERSION_RE.sub('TOOL_VERSION = "0.3.0"', text, count=1)
            reverted = path.name
        (scripts_copy / path.name).write_text(text, encoding="utf-8")
    assert reverted, "no tool declared TOOL_VERSION to revert"

    # Act: run the same discovery against the mutated copy.
    found = {}
    for path in sorted(scripts_copy.glob("pil_*.py")):
        match = TOOL_VERSION_RE.search(path.read_text(encoding="utf-8"))
        if match:
            found[path.name] = match.group(1)

    # Assert: the reverted tool is detected, so the guard is not vacuous.
    assert found[reverted] == "0.3.0"
    expected = json.loads(PORTABLE_MANIFEST.read_text(encoding="utf-8"))["version"]
    mismatched = {n: v for n, v in found.items() if v != expected}
    assert reverted in mismatched, (
        "the version guard failed to notice a reverted tool -- it is vacuous"
    )


# --- blender-inspect: the second plugin's package layout --------------------
#
# blender-inspect lives at plugins/blender-inspect/ and carries the same
# four-manifest set as the root plugin, plus the directories later units fill.
# Git keeps no empty directory, so skills/, agents/ and evals/ hold a .gitkeep
# until their content lands.

BLENDER_INSPECT = REPO_ROOT / "plugins" / "blender-inspect"


@pytest.mark.parametrize(
    "relative",
    [
        "plugin.json",
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        "README.md",
        "LICENSE",
        "PRIVACY.md",
        "assets/icon.svg",
        "skills",
        "agents",
        "scripts",
        "tests",
        "evals",
    ],
)
def test_blender_inspect_ships_the_required_layout(relative):
    assert (BLENDER_INSPECT / relative).exists(), f"plugins/blender-inspect/{relative} is missing"


def test_blender_inspect_readme_has_a_status_entry_for_its_version():
    """release_notes.py reads the release notes from this section."""
    version = json.loads((BLENDER_INSPECT / "plugin.json").read_text(encoding="utf-8"))["version"]
    readme = (BLENDER_INSPECT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"^## Status$", readme, re.MULTILINE)
    assert re.search(rf"^\*\*{re.escape(version)} \u2014 ", readme, re.MULTILINE)


def test_blender_inspect_codex_interface_points_at_bundled_files():
    codex = json.loads((BLENDER_INSPECT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    interface = codex["interface"]
    assert interface["displayName"] == "Blender Inspect"
    for key in ("composerIcon", "logo"):
        assert (BLENDER_INSPECT / interface[key]).is_file()
    assert interface["privacyPolicyURL"].endswith("/plugins/blender-inspect/PRIVACY.md")


# --- two plugins, one repository: marketplace and pytest wiring --------------


def test_marketplace_description_names_both_plugins():
    market = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    description = market["description"]
    assert "single-plugin" not in description.lower()
    for entry in market["plugins"]:
        assert entry["name"] in description


def test_pytest_collects_and_imports_both_plugins():
    import tomllib

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    options = config["tool"]["pytest"]["ini_options"]
    assert {"tests", "plugins/blender-inspect/tests"} <= set(options["testpaths"])
    assert {
        "tests",
        "scripts",
        "plugins/blender-inspect/tests",
        "plugins/blender-inspect/scripts",
    } <= set(options["pythonpath"])


def test_no_module_basename_collides_across_the_two_plugins():
    """Both scripts dirs and both tests dirs sit on one sys.path, so a shared
    basename would import the wrong module. The naming rule keeps them apart:
    blender_*.py scripts, test_bi_*.py tests and bi_* helpers."""
    new_scripts = {p.stem for p in (BLENDER_INSPECT / "scripts").glob("*.py")}
    new_tests = {p.stem for p in (BLENDER_INSPECT / "tests").glob("*.py")}
    assert all(name.startswith("blender_") for name in new_scripts)
    assert all(name.startswith(("test_bi_", "bi_")) for name in new_tests)

    root = {p.stem for d in ("scripts", "tests") for p in (REPO_ROOT / d).glob("*.py")}
    assert not (new_scripts | new_tests) & root


# --- every marketplace plugin: manifests, listing, tool versions, skills -----
#
# The checks above pin the root plugin in detail. These apply the rules that
# matter for releases to every plugin the marketplace lists, so blender-inspect
# (and any later plugin) is covered from the moment its entry lands: its four
# manifests agree, its listing matches them, its own scripts announce its own
# version, and its skills are discoverable under valid names.


def _marketplace_plugins():
    market = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    return [(entry, (REPO_ROOT / entry["source"]).resolve()) for entry in market["plugins"]]


MARKET_PLUGINS = _marketplace_plugins()
PLUGIN_IDS = [entry["name"] for entry, _ in MARKET_PLUGINS]


def _manifests(plugin_dir):
    read = lambda rel: json.loads((plugin_dir / rel).read_text(encoding="utf-8"))  # noqa: E731
    return read("plugin.json"), read(".claude-plugin/plugin.json"), read(".codex-plugin/plugin.json")


def _tool_versions(plugin_dir):
    """{filename: TOOL_VERSION} for every script in the plugin that declares one."""
    found = {}
    for path in sorted((plugin_dir / "scripts").glob("*.py")):
        match = TOOL_VERSION_RE.search(path.read_text(encoding="utf-8"))
        if match:
            found[path.name] = match.group(1)
    return found


def _plugin_skill_dirs():
    found = []
    for entry, plugin_dir in MARKET_PLUGINS:
        skills = plugin_dir / "skills"
        found += [(entry["name"], d) for d in sorted(skills.iterdir()) if d.is_dir()]
    return found


def test_marketplace_lists_both_plugins_once_each():
    assert sorted(PLUGIN_IDS) == ["blender-inspect", "pil-agent-plugin"]
    assert len(set(PLUGIN_IDS)) == len(PLUGIN_IDS)


@pytest.mark.parametrize("entry,plugin_dir", MARKET_PLUGINS, ids=PLUGIN_IDS)
def test_each_plugin_ships_four_agreeing_manifests(entry, plugin_dir):
    portable, native, codex = _manifests(plugin_dir)

    assert portable["$schema"] == SCHEMA_ID
    assert set(portable) <= ALLOWED_MANIFEST_KEYS
    assert set(portable.get("author", {})) <= ALLOWED_AUTHOR_KEYS
    assert PLUGIN_NAME_RE.match(portable["name"]) and len(portable["name"]) <= 64
    for field in ("name", "version", "homepage", "repository", "license"):
        assert portable[field] == native[field] == codex[field], field
    assert portable["description"] == native["description"]
    assert portable["author"] == native["author"] == codex["author"]
    assert portable["keywords"] == native["keywords"]
    assert codex["skills"] == "./skills/"
    for key in ("composerIcon", "logo"):
        assert (plugin_dir / codex["interface"][key]).is_file()


@pytest.mark.parametrize("entry,plugin_dir", MARKET_PLUGINS, ids=PLUGIN_IDS)
def test_each_marketplace_entry_matches_its_plugin(entry, plugin_dir):
    portable, native, _ = _manifests(plugin_dir)

    assert (REPO_ROOT / entry["source"]).resolve() == plugin_dir
    assert entry["name"] == plugin_dir.name or entry["source"] == "./"
    for field in ("name", "version", "description", "license", "homepage", "author", "keywords"):
        assert entry[field] == portable[field] == native[field], field
    assert entry["displayName"] == native["displayName"]


@pytest.mark.parametrize("entry,plugin_dir", MARKET_PLUGINS, ids=PLUGIN_IDS)
def test_each_plugins_tools_declare_its_own_version(entry, plugin_dir):
    """A blender_*.py tool carries blender-inspect's version, a pil_*.py tool
    the root plugin's; shared modules (pil_common, blender_common) declare none."""
    version = _manifests(plugin_dir)[0]["version"]
    mismatched = {n: v for n, v in _tool_versions(plugin_dir).items() if v != version}
    assert not mismatched, f"TOOL_VERSION disagrees with {entry['name']} {version!r}: {mismatched}"


def test_per_plugin_version_check_goes_red_on_a_stale_tool(tmp_path):
    """The per-plugin check must not be vacuous while blender-inspect has no
    versioned tool yet: a synthetic plugin with one stale tool is caught, and
    a versionless shared module is ignored."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "blender_fresh.py").write_text('TOOL_VERSION = "0.1.0"\n', encoding="utf-8")
    (scripts / "blender_stale.py").write_text('TOOL_VERSION = "0.0.9"\n', encoding="utf-8")
    (scripts / "blender_common.py").write_text("import sys\n", encoding="utf-8")

    found = _tool_versions(tmp_path)

    assert found == {"blender_fresh.py": "0.1.0", "blender_stale.py": "0.0.9"}
    assert {n for n, v in found.items() if v != "0.1.0"} == {"blender_stale.py"}


@pytest.mark.parametrize(
    "plugin,skill_dir", _plugin_skill_dirs(), ids=lambda v: v if isinstance(v, str) else v.name
)
def test_every_plugins_skills_have_valid_names(plugin, skill_dir):
    """Discovery reads skills/<name>/SKILL.md; the frontmatter name must match
    the directory and satisfy the Agent Skills name rule."""
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    assert match, f"{plugin}/{skill_dir.name}: SKILL.md must open with YAML frontmatter"
    front = yaml.safe_load(match.group(1))
    name = front["name"]
    assert name == skill_dir.name
    assert 1 <= len(name) <= 64 and SKILL_NAME_RE.match(name)
    assert 1 <= len(front["description"]) <= 1024
