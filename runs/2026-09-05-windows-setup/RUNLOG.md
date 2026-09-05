# Windows OCR and embedding setup

## Brief and acceptance criteria

Implement Windows dependency discovery and image-free diagnostics on top of
PR #9. Preserve exit-2/empty-stdout refusals, verify missing and discovered
dependencies through real child processes, and document PowerShell installation
and model configuration. Keep embedding claims, calibration, and version intact.

## Work and outcomes

- Read parent and global AGENTS instructions. The repository began clean on
  main at 0063970, before PR #9. Fetched origin and created
  codex/windows-ocr-embed-setup at cb93179 (current origin/main, including the
  merged PR #9 and subsequent release fixes). The base already uses 0.7.0.
- Added OCR executable resolution: explicit flag, PIL_AGENT_TESSERACT, PATH,
  then four standard Windows install locations. Invalid overrides refuse.
  Added --diagnose for version and requested language-data checks, UTF-8
  subprocess decoding, and bounded diagnostic probes.
- Added embedding diagnose using the same runtime/model/session setup as embed.
  Runtime import/DLL and model-session failures become named exit-2 refusals.
  Diagnostics report configuration and explicitly do not establish inference
  or calibration success.
- Added tests/test_dependency_process.py with controlled dependency doubles
  launched in real child processes, plus Windows install-path resolver probes.
  These establish process/discovery contracts, not real-engine accuracy.
- Added README PowerShell commands for installation, explicit paths, language
  data, model/profile selection, hash inspection, user environment persistence,
  and the Windows runtime prerequisite. Checked Tesseract/ONNX Runtime official
  installation documentation and Microsoft winget package directories.
- Reviewed the final diff: embedding capability constants, preprocessing
  profiles, calibration artifacts, manifests, lockfile, and version unchanged.

## Verification

- Initial red: 14 failed, 2 passed (red.txt), before implementing diagnostics.
- Final focused command:
  `uv run --no-sync python -m pytest tests/test_dependency_process.py tests/test_ocr.py tests/test_embed.py -q`
- Result: **42 passed, 12 skipped** (green.txt). All 23 new process tests passed
  on Windows. The 12 skips are existing actual-engine tests: seven Tesseract
  tests and five embedding tests without the optional runtime/model setup.
- `git diff --check` passed; only Git's Windows line-ending notices appeared.
- No full suite, coverage, model downloads, system installations, calibration
  reruns, commit, or publication performed.

## Files

Implementation: scripts/pil_ocr.py, scripts/pil_embed.py.
Tests: tests/test_dependency_process.py. Documentation: README.md.
Session evidence: this directory, including before/after Git state and test logs.
