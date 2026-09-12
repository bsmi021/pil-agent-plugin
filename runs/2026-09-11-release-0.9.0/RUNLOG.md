# Release 0.9.0

## Objective

Land the 0.9.0 work — which existed only as an uncommitted working tree — as a
single commit on a branch off `origin/main`, verify it with a full suite run,
and open the PR that lets `release.yml` cut the tag and GitHub Release.

## Starting state

- Latest GitHub release: **0.8.0** (`pil-agent-plugin--v0.8.0` = `957cb3e`, Sep 5).
- `origin/main` = `957cb3e`; local `main` = `0063970`, 13 behind.
- Checked out on `codex/windows-ocr-embed-setup`, 2 unpushed commits (plugin eval suite).
- All 0.9.0 work uncommitted: 37 modified tracked files, 26 untracked
  (9 new scripts, 5 new test files, `docs/verification-0.9.0.md`, a 21 MB
  evidence run). Version already bumped to 0.9.0 in all five manifests.
- Snapshot: `prerun-gitstatus.txt`.

## Actions

1. Confirmed no file overlap between the 2 eval-suite commits and the dirty
   working tree, so the 0.9.0 work could move to a fresh branch cleanly.
2. Fast-forwarded local `main` to `957cb3e` (verified ancestry first).
3. Created `feat/release-0.9.0` from `origin/main`; confirmed the working tree
   carried over with no file lost.
4. Staged everything: 250 files, **5.7 MB**. Run PNGs and the `.onnx` model are
   excluded by the existing `.gitignore` rules; the largest single object is the
   2.7 MB `proof-v2/local-diff/delta-e.npy` evidence array. Tracking the
   evidence run follows repository precedent (109 files under `runs/` are
   already tracked) and keeps the links in `docs/verification-0.9.0.md` live.
5. Committed as `d1994b3`.
6. Pre-flight checks for the release workflow: `README.md` has the `## Status`
   entry for 0.9.0 that becomes the release notes, and all 30
   `scripts/pil_*.py` report `TOOL_VERSION = "0.9.0"`, which
   `tests/test_packaging_conformance.py` and the tagging job both require.
7. Ran the full suite the way `ci.yml` does
   (`uv run --extra reconstruction --extra embedding pytest -q`), with
   Tesseract on PATH so the OCR tests execute rather than skip.

## Outcome

**795 passed, 11 skipped, 3 deprecation warnings in 297.85s. Exit code 0.**
Receipt: `tests.txt`; skip reasons: `skip-reasons.txt`.

All 11 skips are environmental and expected:

- 5 in `tests/test_embed.py` -- `PIL_AGENT_EMBED_MODEL` is unset. Model weights
  are caller-supplied and never bundled, and `ci.yml` deliberately leaves the
  variable unset, so CI skips the same five. The Sep 5 run downloaded the
  pinned MobileNet into that run only, which is why it reported
  799 passed / 6 skipped -- 5 more passes, 5 fewer skips.
- 6 in `tests/test_palette_diff.py` and `tests/test_structure_diff.py` -- the
  private historical reference image is not on this machine. Same 6 the Sep 5
  runlog recorded.

Collection is 806 here against 805 in the last Sep 5 run (and 794 in the run
before it, which that session's own log says was followed by tightened guards
and new tests); the extra test is consistent with one landing after that final
run. Zero failures either way.

Release-pipeline pre-flight, run before asking to push:

- `.github/scripts/check_version_bump.py 957cb3e HEAD` -> `Version moved to 0.9.0. OK.`
- `.github/scripts/release_notes.py 0.9.0` extracts cleanly; title will be
  "0.9.0 -- Explicit measurement workflows and agent tooling". It first raised
  `UnicodeEncodeError` locally on the Delta-E character -- that is this Windows
  console's cp1252 codepage, not the script; re-running with
  `PYTHONIOENCODING=utf-8` succeeds, and CI runs UTF-8 on ubuntu-latest.
- All 30 `scripts/pil_*.py` report `TOOL_VERSION = "0.9.0"`; `README.md` has the
  `## Status` entry the release notes are extracted from.

## Remote

Pushed `feat/release-0.9.0` and opened
[PR #13](https://github.com/bsmi021/pil-agent-plugin/pull/13) against `main`.

All four required checks pass (receipt: `ci-checks.txt`,
[workflow run](https://github.com/bsmi021/pil-agent-plugin/actions/runs/34663755055)):

| Check | Result | Time |
|---|---|---|
| release version bumped | pass | 6s |
| tests (py3.11) | pass | 2m46s |
| tests (py3.12) | pass | 2m1s |
| tests (py3.13) | pass | 2m41s |

The PR reports `mergeable=MERGEABLE` but `mergeStateStatus=BLOCKED` with
`reviewDecision=REVIEW_REQUIRED`. That comes from the repository ruleset
`basic` (id 22153065, active), not from classic branch protection -- the
`branches/main/protection` endpoint returns 404. This is the same state PR #12
was in: it shows `reviewDecision=REVIEW_REQUIRED` and was merged by `bsmi021`
on Sep 5 using the owner bypass. Merging is Brian's action and was not
performed here.

On merge, `release.yml` fires on the push to `main`, tags
`pil-agent-plugin--v0.9.0` and cuts the release titled
"0.9.0 -- Explicit measurement workflows and agent tooling" from the README
`## Status` entry.

## Notes / follow-ups

- The 2 eval-suite commits on `codex/windows-ocr-embed-setup` remain unpushed
  and unreleased; they need their own PR.
- 6 prunable worktrees remain under `C:\Projects\pil-agent-plugin\wt-*`
  (leftover Phase 3). Not touched.
- Per `docs/verification-0.9.0.md`, the calibration corpora are synthetic
  execution checks; transfer to real photographic or concept-art domains is
  not claimed.
