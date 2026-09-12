## Changes

Release 0.8.0 adds a plugin-local dependency bootstrap and a check-first bootstrap skill. Setup supports core Python dependencies plus optional OCR, embedding, and reconstruction; successful runs record a receipt, while status checks re-probe live dependencies before reporting readiness.

OCR discovers standard Windows Tesseract installations and supports environment overrides and image-free diagnostics. Embedding runtime/DLL and model-load failures return exit 2 with empty stdout. PowerShell installation and model/profile configuration are documented. Model weights remain caller-supplied; embedding capability claims and calibration results are unchanged.

All manifests, marketplace entries, tool versions, and the project lockfile version are aligned at 0.8.0. README Status supplies the automatic release notes.

## Validation

- Packaging publication gate: 38 passed.
- Focused bootstrap/dependency process tests: 36 passed, including real isolated Windows venv creation and repeat-run verification.
- Skill validation and discovery/frontmatter checks passed.
- Version-bump gate and release-note extraction passed.
- Full-suite CI is required before merge. System installers for macOS/Linux are command-selection tested, not executed on those hosts.
