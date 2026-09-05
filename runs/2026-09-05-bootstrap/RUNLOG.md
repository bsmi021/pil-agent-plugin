# Platform bootstrap and skill

## Requested outcome

Create a platform-aware dependency bootstrap, an agent skill to operate it,
and a check that distinguishes prior successful setup from present readiness.
Acceptance: core local installation, opt-in extras/system OCR, read-only check,
repeat-run skip, stale/broken receipt detection, and no success receipt on
failed setup. Preserve earlier Windows diagnostics work and version/calibration.

## Implementation

- Added scripts/pil_bootstrap.py, a Python 3.11+ stdlib launcher. Uses uv when
  available or venv/pip otherwise, installing pyproject dependency ranges into
  the plugin-local .venv without modifying lockfiles or removing other extras.
- Core checks import and version constraints in the target interpreter, not
  the launcher. Optional OCR/embedding reuse the existing image-free diagnostics.
- OCR installation selects winget, Homebrew, apt-get, or dnf by host platform
  and availability. No system installation is performed by check. Existing
  broken OCR environment overrides refuse instead of reinstalling blindly.
- Successful selected setup records .venv/pil-agent-bootstrap.json atomically.
  Every check re-probes live dependencies. Receipts bind the plugin path,
  manifest hash, platform/architecture, interpreter path, and capability flags.
- Added skills/bootstrap/SKILL.md with check-first interpretation, scoped
  installation, plugin-copy resolution, and missing-model/platform guidance.
  It is automatically discovered through the existing skills directory.
- Added README bootstrap commands and state/exit-code documentation. Python
  3.11+ and a platform package manager are prerequisites; Blender, model weights,
  and the Windows VC++ prerequisite remain separately configured.
- Existing script capabilities, calibration, model gates, and version unchanged.

## Verification

- Red: new bootstrap tests initially failed collection because the script did
  not exist (red.txt).
- `uv run --no-sync python -m pytest tests/test_bootstrap.py tests/test_dependency_process.py -q`
  -> **36 passed** (green.txt): 13 bootstrap and 23 prior process-contract tests.
  Includes actual isolated Windows venv creation and repeat-install skip with
  empty test requirements (no package downloads), receipt corruption/staleness,
  missing dependencies, version refusal, and failed installer/post-install probe.
- Platform package-manager tests use command-selection doubles. macOS/Linux
  package managers and real system OCR installation were not executed.
- `uv run --no-sync python -m pytest tests/test_packaging_conformance.py -k 'skills_live or discovers_the_shared_skills or skill_frontmatter' -q`
  -> **6 passed, 32 deselected** (skill-packaging.txt).
- skill-creator quick_validate.py -> Skill is valid! (skill-validation.txt).
- Live script check in the current checkout -> exit 2, ready=true,
  previously_run=false, already_bootstrapped=false, correctly distinguishing
  existing usable Pillow/NumPy from a recorded bootstrap (live-check.json).
- git diff --check passed (Windows line-ending notices only).

No full suite, coverage, global/system installations, model downloads, commits,
publication, or installed plugin-cache updates were performed. New script and
skill are authored and locally verified in the existing working branch.
