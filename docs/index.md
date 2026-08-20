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
- [Phase 3 handoff](phase3-handoff.md) — **start here if you are picking up
  phase 3.** What remains (Track A5, Track B), the conventions this repository
  enforces, what to reuse, the Blender acceptance corpus that already exists,
  and how work is reviewed here.
- [Phase 3 scope](phase3-scope.md) — closed loops between measurement and vision:
  native-resolution crops, readable overlays, image metadata, region-scoped
  metrics, discrimination-gated new metrics, and the Blender character-sheet loop.
  Awaiting sign-off.

## Summary

Six Pillow-backed CLI tools give a coding agent quantitative, diffable
measurements of an image, complementing rather than replacing native multimodal
vision:

- `pil_palette_diff` — colour palettes (CIEDE2000 + hue census), colour-scheme
  comparison
- `pil_structure_diff` — grid statistics, perceptual hashes, changed-region boxes
- `pil_contract_verdict` — declared-intent verdicts (SATISFIED / VIOLATED /
  UNMEASURABLE) with detection limits, aggregated worst-case across view pairs
- `pil_crop` — native-resolution crop of a fractional region; integer upscale
  only, so it magnifies without inventing detail
- `pil_annotate` — numbered boxes and gridlines on a copy, so a model can point
  at a region precisely; legibility verified by read-back, not by assertion
- `pil_image_info` — the file facts an image never carries into a vision
  encoder: true dimensions, alpha presence *and* use, EXIF, ICC, DPI, frames

Both diff tools also accept `--region` to scope every metric to a fractional
box, byte-equal to pre-cropping with `pil_crop`.

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
— zero full-frame false alarms, detection limits shown to transfer.

**0.4.0** — the alpha/coverage fix, phase 3 Track A, and foreground
recalibration, built to [`aaa-build-plan.md`](aaa-build-plan.md):

- Foreground statistics on an alpha-derived mask are **coverage-weighted**.
  Error against alpha-weighted truth fell from −4.3…−42.8 to **±0.0004**, and
  the placement instability — a quarter-pixel re-render of an unchanged object
  moving the reading by up to 21.6 code values — is **gone** (±0.0006). Full
  frame is byte-identical.
- **Phase 2's last open gate is closed.** The alpha path now has calibrated
  thresholds of its own (luminance **0.997**, n=380, α=0.01 over 19 RGBA
  scenes), and foreground thresholds are [split by mask source](../runs/2026-08-20-foreground-recalibration/README.md).
  The estimate path also moved from n=100/α=0.05 on one scene to n=400/α=0.01
  over four.
- Three new tools (`pil_crop`, `pil_annotate`, `pil_image_info`) and `--region`
  on both diff tools. Every one of them failed its first critic on something its
  own green suite did not test; the findings and fixes are in the commit log.

Phase 3 Track A landed; Track B (Blender mesh statistics, matched-view
rendering) remains scoped and unstarted.

## Open items

- ~~**The alpha foreground path is uncalibrated.**~~ **Closed in 0.4.0.** An
  RGBA control family over the 19-scene `ALPHA_CORPUS` gives the alpha path its
  own thresholds (n=380, α=0.01); `jpeg_reencode` is skipped by design and the
  skip is recorded, `rescale_roundtrip` resamples premultiplied, and the
  RGB-only recipes assert alpha is byte-identical so a geometry perturbation
  cannot masquerade as a colour one. See the
  [recalibration bundle](../runs/2026-08-20-foreground-recalibration/README.md).
- **Validation covers one asset from one pipeline.** The thresholds transferred
  to real production renders, but a second corpus from a different renderer
  would be needed to claim generalisation. **This still stands, and now also
  applies to the alpha path**, whose controls are synthetic corpus scenes rather
  than production RGBA renders.
- **The foreground *estimate* path is still placement-dominated**, just less so.
  Widening the control scenes cut `rescale_roundtrip`'s median-to-next dominance
  from 18.9× to ~3×, confirming its grip was about thin objects specifically —
  but its threshold (34.166 for luminance) remains far above the non-placement
  floor (1.452). Read `threshold_foreground_estimate_no_placement` when the
  change you care about does not move the object on the pixel grid.
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
