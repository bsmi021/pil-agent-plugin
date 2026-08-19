# Audit: conformance with Agent Plugins 1.0.0

Date: 2026-08-18
Standard: <https://agent-plugins.org/specification> (v1.0.0; the specification
states no publication date)
Schema: <https://agent-plugins.org/schemas/1.0.0/plugin.schema.json>

## What the standard requires

Agent Plugins is a vendor-neutral package format governed by a TSC with members
from Amazon, Cursor, Microsoft, OpenAI and Vercel. A conforming package is a
directory containing:

- `plugin.json` at the **root** — required; a **closed** schema whose only
  required fields are `$schema` (a `const`) and `name`.
- `skills/<name>/SKILL.md` — optional; discovered only as *immediate* children,
  each conforming to the Agent Skills specification.
- `mcp.json` at the root — optional; inline MCP config in the manifest is
  prohibited.
- `<reverse.domain.namespace>/` directories and an `extensions` manifest object —
  optional, client-owned.

Undefined top-level directories are not an error: clients discover from fixed
locations only and must ignore what they do not implement (§6.2, §11.3).

## Findings before the audit

| # | Finding | Severity | Clause |
|---|---|---|---|
| 1 | No root `plugin.json`. Only `.claude-plugin/plugin.json` (Claude Code-native), which no Agent Plugins client reads. | **Blocking** — package was not loadable as an Agent Plugin at all | §5.1 |
| 2 | Manifest carried no `$schema`, a required `const` field. | **Blocking** | §5.2 |
| 3 | Manifest carried `displayName`, absent from the closed schema. Non-fatal to clients (report-and-ignore) but non-conformant. | Minor | §5.2 |
| 4 | `skills/image-measurement/SKILL.md` — already conformant. `name` 17 chars, matches parent directory; `description` 556 of 1024 chars; frontmatter carries only the two required keys. | **Pass** | §7.1 |
| 5 | `agents/image-comparison-analyst.md` — Claude Code subagents have no portable equivalent in 1.0.0. Legal as an undefined top-level directory; ignored by conforming clients. | Informational | §6.2 |
| 6 | `SKILL.md` instructs the agent to use `${CLAUDE_PLUGIN_ROOT}`. Agent Plugins names the equivalent `${PLUGIN_ROOT}`. Not a schema violation — skill bodies are free-form — but it made the skill unusable verbatim outside Claude Code. | Minor | §9.1 |
| 7 | No `mcp.json`, and no MCP config smuggled into the manifest. | **Pass** | §7.2 |

## Changes made

Additive migration, which is the strategy the standard's own example repository
recommends: portable files land alongside the native ones, and the existing
client integration is left untouched.

1. Added root `plugin.json` with `$schema` pinned to the 1.0.0 identifier,
   mirroring the native manifest's metadata. `displayName` omitted (closed
   schema); `extensions` omitted rather than inventing a namespace this project
   does not own.
2. `.claude-plugin/plugin.json` left unchanged, so Claude Code installation is
   unaffected.
3. `SKILL.md` now names `${PLUGIN_ROOT}` as the portable equivalent of
   `${CLAUDE_PLUGIN_ROOT}`.
4. Added `tests/test_packaging_conformance.py` — 16 tests covering the closed
   manifest schema, the `name` pattern, absence of invented extension
   namespaces, skill discovery at the fixed location, Agent Skills frontmatter
   constraints, and drift between the two manifests.
5. README gained a **Standards conformance** section stating the claim and its
   two limits.

## Verification

```
$ uv run --with jsonschema python -c "<validate plugin.json against live schema>"
VALID against live https://agent-plugins.org/schemas/1.0.0/plugin.schema.json

$ claude plugin validate .
✔ Validation passed

$ claude plugin validate . --strict
✔ Validation passed

$ uv run pytest -q
47 passed, 6 skipped
```

The six skips are the pre-existing reference-image tests, which skip when
`PIL_AGENT_REFERENCE_IMAGE` is unset. See the root README.

## Not claimed

- No third-party certification exists for Agent Plugins 1.0.0; there is no
  registry or badge programme. The claim here is schema validation plus a
  clause-by-clause read of the specification.
- Claude Code does not natively parse the portable manifest; it installs from
  `.claude-plugin/plugin.json`. The root manifest serves other Agent Plugins
  clients and tooling.
