# Publication status

- Committed and pushed 527b774 (implementation/release) and dbedca0
  (release-version test expectations and test-log whitespace normalization).
- PR: https://github.com/bsmi021/pil-agent-plugin/pull/12
- CI run 33965711836 passed all jobs. Python 3.11, 3.12, and 3.13 each reported
  738 passed, 40 skipped. Version-bump check passed.
- Initial local full suite: 756 passed, 18 skipped, four failures caused by
  old hardcoded 0.7.0 assertions. All four affected tests subsequently passed
  with the corrected 0.8.0 expectations (version-tests.txt and
  corpus-version-test.txt). No clean rerun of the entire local suite is claimed.
- Remote feature branch verified at dbedca023b0bc1057054bc509e0d0eb30a290e72.
- Merge blocked by GitHub REVIEW_REQUIRED. Auto-merge attempt also refused:
  auto-merge is not enabled for this repository. No settings were changed and
  no administrator bypass was attempted.
- PR remains OPEN; 0.8.0 tag/release has not been published. Required next step:
  reviewer approval, or explicit owner authorization for an administrator bypass.

## Authorized merge completed
User explicitly authorized the administrator bypass. PR #12 merged at 957cb3e5875e408a1da414c9fdb1501c47ce298a. Release workflow 33966007984 succeeded. Published non-draft release pil-agent-plugin--v0.8.0 at 2026-09-05T12:25:56Z. Remote main and release tag verified.
https://github.com/bsmi021/pil-agent-plugin/releases/tag/pil-agent-plugin--v0.8.0
