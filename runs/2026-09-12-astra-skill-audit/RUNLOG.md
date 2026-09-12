# GPT-6 Astra skill audit

## Objective

Review OpenAI's “Rethinking skills and prompts for GPT-6 Astra” guidance and
update the PIL Agent Plugin skills where the guidance applies.

## Scope

- Audit the four shipped `skills/*/SKILL.md` files.
- Shorten activation descriptions and reduce unconditional context loading.
- Preserve measurement, calibration, refusal, provenance, and geometry proof
  boundaries.
- Validate only skill structure and packaging behavior affected by the edits.

## Progress

- Reviewed the official OpenAI article published 2026-09-11.
- Replaced broad activation descriptions with shorter, discriminating ones.
- Converted the large image-measurement manual into a compact decision router
  that points to existing task-specific documentation and tool discovery.
- Simplified the umbrella, reconstruction, and bootstrap entrypoints while
  preserving safety-critical and evidence-critical constraints.
- Rebased the work onto the released 0.9.0 `origin/main` as
  `chore/astra-skill-guidance` and prepared patch release 0.9.1.
- Aligned all release version carriers and added the 0.9.1 README/docs status.
- Corrected the release-note generator to link to this repository's actual
  `<plugin-name>--v<version>` tag convention.

## Verification

- `quick_validate.py`: all four skills valid.
- `uv run pytest tests/test_packaging_conformance.py -q`: 38 passed.
- `uv run --locked --extra reconstruction --extra embedding pytest -q`:
  788 passed, 18 skipped, 3 warnings in 2337.64 seconds, exit 0.
- `git diff --check`: passed; only expected LF-to-CRLF notices were emitted.
- Local documentation and sibling-skill link targets exist.

## Outcome

- Skill entrypoint lines reduced from 884 to 292.
- Frontmatter description words reduced from 258 to 100.
- No measurement algorithms, schemas, calibration data, model gates, or runtime
  behavior changed; public payload versions moved to 0.9.1 for the release.
- The bundled comparison-agent prompt remains a separate follow-up candidate;
  it was not edited in this skill-only scope.
