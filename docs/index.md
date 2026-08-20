# pil-agent-plugin documentation

Last updated: 2026-08-19

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
- [Phase 2 scope](phase2-scope.md) — perceptual colour distance, threshold
  calibration, contract-driven verdicts, multi-pair aggregation.
  **Implemented in 0.3.0**; two gates recorded open.
- [Phase 2 research: colour and calibration](research-phase2-colour-and-calibration.md)
  — CIEDE2000 formulation and verification data, LCh versus HSV bucketing, and
  calibration methodology.
- [Phase 2 calibration bundle](../runs/2026-08-19-phase2-calibration/README.md)
  — Neyman–Pearson thresholds with n/α/CI, detection limits per metric per
  perturbation, LCh hue-family boundaries, constant verdicts and their
  application (one rejected, reason recorded in `scripts/pil_common.py`).
- [Phase 2 real-image validation](../runs/2026-08-20-phase2-real-validation/README.md)
  — the synthetic-derived thresholds run against a real production revision
  corpus: zero full-frame false alarms, published detection limits shown to
  transfer, support gating quiet on real cross-render pairs.
- [Phase 3 scope](phase3-scope.md) — closed loops between measurement and vision:
  native-resolution crops, readable overlays, image metadata, region-scoped
  metrics, discrimination-gated new metrics, and the Blender character-sheet loop.
  Awaiting sign-off.

## Summary

Three Pillow-backed CLI tools give a coding agent quantitative, diffable
measurements of an image, complementing rather than replacing native multimodal
vision:

- `pil_palette_diff` — colour palettes (CIEDE2000 + hue census), colour-scheme
  comparison
- `pil_structure_diff` — grid statistics, perceptual hashes, changed-region boxes
- `pil_contract_verdict` — declared-intent verdicts (SATISFIED / VIOLATED /
  UNMEASURABLE) with detection limits, aggregated worst-case across view pairs

All are deterministic and emit JSON.

## Status

Phase 1 complete: tools built and validated, plugin packaged,
`claude plugin validate --strict` passing, and the package
conforming to [Agent Plugins 1.0.0](https://agent-plugins.org/specification)
(see [Standards conformance](../README.md#standards-conformance)).

Phase 2 implemented (0.3.0): CIEDE2000 verified against all 34 published
reference values; thresholds calibrated by Neyman–Pearson over synthetic ground
truth with published detection limits; contract-driven verdicts with a refuse
list that never approximates; worst-case multi-pair aggregation. Thresholds
were then [validated against a real production corpus](../runs/2026-08-20-phase2-real-validation/README.md)
— zero full-frame false alarms, detection limits shown to transfer. One gate
stays open: the alpha foreground path is uncalibrated.

Phase 3 scoped and awaiting sign-off.

## Open items

- **The alpha foreground path is uncalibrated** — every calibration scene and
  every validation render is opaque; only the border-median colour path is
  measured. The one phase 2 gate still open.
- **Validation covers one asset from one pipeline.** The thresholds transferred
  to real production renders, but a second corpus from a different renderer
  would be needed to claim generalisation.
- **Two foreground-mode accent metrics sit closest to their budget**
  (`accent_palette_distance` and its ΔE2000 form, ~0.08–0.09 exceedance against
  a 0.10 limit). Widen these first if a future corpus pushes them over.
- **HSV vs LCh accent gate is compared, not ranked.** Deciding needs a
  ground-truth notion of "is this pixel an accent"; LCh remains opt-in
  (`--accent-space lch`).
- **Two new tool candidates surfaced by the field trial.** Region cutting at
  matched silhouette-bbox fractions, and backdrop-excluded foreground sampling.
  Both were written as throwaway harness code to review a game asset, both were
  load-bearing, and one of them changed a headline conclusion. Rationale in the
  [trial bundle](../runs/2026-08-18-skeleton-warrior-asset-review/README.md#what-this-exercise-wants-from-phase-2).
  **Landed in 0.2.0** as `--foreground` on both tools: alpha- or
  border-median-OKLab masking, bbox registration, support-gated cell scoring,
  and the `background_dominant` / `accent_support_low` flag family — prompted
  by a second production miss (two different swords scoring 0.991 full-frame).
  **Region cutting is now scoped** as [phase 3](phase3-scope.md) Track A: a
  `--region FRACTIONAL_BBOX` flag on both tools (WP A4) plus a standalone
  `pil_crop` (WP A1). Until those ship and pass their gates, the plugin still
  has no region-cutting capability.
- **Geometry questions remain unanswerable from pixels.** Agreed to add an
  optional Blender mesh-statistics tool in phase 3; until then `geometry.*`
  predicates must return `UNMEASURABLE` rather than approximate.
