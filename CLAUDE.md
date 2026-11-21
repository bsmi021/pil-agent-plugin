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

## SkillEvaluator (NVIDIA, 1.0.1)
- Tier 1/2 run on Windows: `skillevaluator validate <skill> --tiers 1 --no-autopilot`; Tier 2 needs `SKILL_EVAL_EMBEDDING_PROVIDER=nv_build` + `NVIDIA_API_KEY` (Windows User env, not inherited by the session shell)
- Tier 3 only works from WSL Ubuntu: on Windows, MAX_PATH (LongPathsEnabled=0) breaks staging, and Claude desktop's virtualized `%LOCALAPPDATA%` breaks secure_fs
- Set `SKILL_EVAL_LLM_REFUSAL_FALLBACK_MODEL=off`: 1.0.1 adds a claude-opus-4-8 fallback that the API rejects for claude-sonnet-5-5 (judge HTTP 400)
- Prebuild `se3-base:cc2.1.292` (python:3.12-slim + Claude Code 2.1.292) and use `evals/environment/Dockerfile` = `FROM se3-base:cc2.1.292`: in-container Claude Code install takes 10-24+ min and blows the agent setup timeout
- Staged datasets/runner live in `runs/2026-11-19-skillevaluator/` (`build_tier3.py`, `run_tier3_wsl.sh`); rename the two `bootstrap` skills (no plugin namespaces in the sandbox) and use `--skill-workspace-mode group` so siblings act as decoys
