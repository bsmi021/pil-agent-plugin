# Release 0.8.0

User authorized version update, commit, and merge of the diagnostics/bootstrap
work. Base is origin/main cb93179; working branch codex/windows-ocr-embed-setup.

Updated all release manifests, marketplace entries, pyproject/lockfile project
version, and existing tool version constants from 0.7.0 to 0.8.0. Added the
README Status entry consumed by the automatic release workflow. Dependency
pins, calibration evidence, and embedding capability claims are unchanged.

Pre-publication packaging gate: 38 passed. Release note extraction succeeded.
The earlier focused checks passed (36 process/bootstrap tests and skill
validation). A local full-suite run is underway; final CI results and merge/
release confirmation will be recorded in the task after publication. No full
suite pass is claimed by this pre-commit record.
