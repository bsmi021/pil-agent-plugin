# pil-agent-plugin documentation

Last updated: 2026-08-18

## Contents

- [Design rationale and findings](design-rationale.md) — why the tools are shaped
  the way they are, and what the phase 1 experiment established.
- [Tool reference](../README.md#tools) — CLI usage and output fields.
- [Phase 1 evidence bundle](../runs/2026-08-18-pil-agent-plugin-phase1/README.md)
  — the RED/GREEN ledger, metric discrimination matrix and raw JSON outputs.
- [Phase 2 scope](phase2-scope.md) — proposed work packages: perceptual colour
  distance, threshold calibration, contract-driven verdicts, multi-pair
  aggregation. Awaiting sign-off.
- [Phase 2 research: colour and calibration](research-phase2-colour-and-calibration.md)
  — CIEDE2000 formulation and verification data, LCh versus HSV bucketing, and
  calibration methodology. In progress.

## Summary

Two Pillow-backed CLI tools give a coding agent quantitative, diffable
measurements of an image, complementing rather than replacing native multimodal
vision:

- `pil_palette_diff` — colour palettes, hue census, colour-scheme comparison
- `pil_structure_diff` — grid statistics, perceptual hashes, changed-region boxes

Both are deterministic and emit JSON.

## Status

Phase 1 complete: tools built and validated, plugin packaged,
`claude plugin validate --strict` passing, 37 tests green.

Phase 2 scoped and awaiting sign-off. Its four work packages absorb what phase 1
deferred — threshold calibration and perceptual colour distance — and add the
contract-driven verdict layer that phase 1's discrimination findings showed was
necessary.

## Open items

Tracked in detail in [phase2-scope.md](phase2-scope.md); summarised here.

- **Thresholds are uncalibrated.** Accent HSV bounds and hue-shift margins were
  validated against one image and two derived variants. Phase 2 WP2.
- **Palette distance is Euclidean RGB**, which is not perceptually uniform and is
  currently demoted to supporting detail. Phase 2 WP1, gated on verifying
  CIEDE2000 against published test data.
- **No notion of intended versus unintended change.** Raw metrics cannot express
  "this was supposed to change and this was not". Phase 2 WP3.
- **Geometry questions remain unanswerable from pixels.** Agreed to add an
  optional Blender mesh-statistics tool in phase 3; until then `geometry.*`
  predicates must return `UNMEASURABLE` rather than approximate.
