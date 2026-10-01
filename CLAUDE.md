# pil-agent-plugin

Marketplace with two plugins: `pil-agent-plugin` (repo root) and `blender-inspect` (`plugins/blender-inspect/`).

## Commands
- `uv run --extra reconstruction --extra embedding pytest -q -p no:cacheprovider tests plugins/blender-inspect/tests` - full suite (~14 min with Blender 5.2; Blender and real-depth-model tests skip in CI)
- `python .github/scripts/preflight.py --base origin/main` - run before every PR: version bump, `uv lock --check`, release notes for both plugins, packaging conformance
- `python .github/scripts/release_plan.py [--all]` - which plugin versions release.yml would publish (needs `gh`; `--all` is offline)

## Versions and releases
- A plugin needs a version bump only when its shipped paths change: `scripts/`, `skills/`, `schemas/`, `agents/`, manifests, `pyproject.toml`. Tests, docs, README and `.github/` don't need a bump.
- Each README's `## Status` entry for the version is published verbatim as that release's notes on merge, so keep it accurate.
- release.yml tags `<plugin>--v<version>` per marketplace plugin and marks only `pil-agent-plugin` releases as Latest.

## Merging
- The `basic` ruleset requires 1 approving review and the maintainer can't self-approve: merge with `gh pr merge <N> --admin --merge` (merge commits, not squash).

## Gotchas
- `claude plugin eval` refuses Bash-granting cases on Windows (no sandbox backend), so blender-inspect evals can't run here and aren't shipped.
- Deprecated `pil_*` Blender tools are behaviour-frozen except `pil_multiview_render`, which got the camera `Matrix` fix in 0.10.0.
