# pil-agent-plugin documentation

Last updated: 2026-08-18

## Contents

- [Design rationale and findings](design-rationale.md) — why the tools are shaped
  the way they are, and what the phase 1 experiment established.
- [Tool reference](../README.md#tools) — CLI usage and output fields.
- [Phase 1 evidence bundle](../runs/2026-08-18-pil-agent-plugin-phase1/README.md)
  — the RED/GREEN ledger, metric discrimination matrix and raw JSON outputs.
- [Agent Plugins 1.0.0 conformance audit](../runs/2026-08-18-agent-plugins-standard-audit/README.md)
  — clause-by-clause findings against <https://agent-plugins.org/specification>,
  the additive migration applied, and its verification.
- [Field trial: game-asset review](../runs/2026-08-18-skeleton-warrior-asset-review/README.md)
  — the tools used on a real production task, including four visual conclusions
  the measurements overturned and three concrete asks for phase 2.
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
`claude plugin validate --strict` passing, 63 tests green, and the package
conforming to [Agent Plugins 1.0.0](https://agent-plugins.org/specification)
(see [Standards conformance](../README.md#standards-conformance)).

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
- **Two new tool candidates surfaced by the field trial.** Region cutting at
  matched silhouette-bbox fractions, and backdrop-excluded foreground sampling.
  Both were written as throwaway harness code to review a game asset, both were
  load-bearing, and one of them changed a headline conclusion. Rationale in the
  [trial bundle](../runs/2026-08-18-skeleton-warrior-asset-review/README.md#what-this-exercise-wants-from-phase-2).
- **Geometry questions remain unanswerable from pixels.** Agreed to add an
  optional Blender mesh-statistics tool in phase 3; until then `geometry.*`
  predicates must return `UNMEASURABLE` rather than approximate.
