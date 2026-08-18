# Phase 1 — PIL-augmented image analysis for agents: prototype and validation

**Date:** 2026-08-18
**Question:** does wrapping Pillow in scripted tools give a coding agent genuinely
new signal beyond its native multimodal vision, and can that signal support both
fuzzy and exact image comparison?
**Verdict:** qualified yes. Native vision wins on semantics; PIL wins on
*quantitative, diffable* colour and structure. The naive version of the idea —
"PIL helps the agent see more" — is disproved. See `10-metric-discrimination-matrix.md`.

## Method

Two subagents analysed the same reference image
(`~/Downloads/image (1).png`, 1672×941 RGB PNG) under different constraints:

- **vision-baseline** — native multimodal perception only. Scripting and shell
  tools were prohibited.
- **pil-augmented** — native vision plus ad-hoc Pillow/numpy scripts.

Their reports were compared, the residual gap was turned into a tool contract,
and the contract was implemented test-first.

## Ledger

| # | Step | Evidence | Result |
|---|------|----------|--------|
| 1 | RED — 24 tests encoding the tool contract, written before any implementation | `01-red-initial.txt` | 24 failed |
| 2 | GREEN — `pil_common`, `pil_palette_diff`, `pil_structure_diff` implemented | `03-green-full-suite.txt` | 24 passed |
| 3 | Measured reference palettes; found accent palette contained no cyan/green/blue/purple | `04-reference-palette.json` | Gap identified |
| 4 | RED — 3 tests for hue-family enumeration | `02-red-hue-families.txt` | 3 failed |
| 5 | GREEN — `hue_families` added | `03-green-full-suite.txt` | 27 passed |
| 6 | Comparison run against two derived variants; `hue_families_lost` failed to fire on a real recolour | `06`–`09` JSON | Gap identified |
| 7 | RED — 3 tests for magnitude-based shift detection | `02-red-hue-families.txt` | 3 failed |
| 8 | GREEN — `hue_families_diminished` / `accent_hue_shift_detected` added | `03-green-full-suite.txt` | **30 passed** |
| 9 | Discrimination analysis across all metrics | `10-metric-discrimination-matrix.md` | 4 of 11 metrics separate the cases |
| 10 | Hardening — synthetic reference fixture, red-wrap branch cover, argument-order semantics | `03-green-full-suite.txt` | **37 passed** |
| 11 | Mutation check — two deliberate defects injected to prove the new tests bite | see below | Both caught |
| 12 | Clean-machine run with reference-gated tests excluded | `11-clean-machine-suite.txt` | 31 passed, 6 deselected |
| 13 | Plugin packaging authored against a verified structure spec | `12-plugin-validate.txt` | `claude plugin validate` passed, incl. `--strict` |

Full-suite command: `uv run pytest -v` → **37 passed**.

### Mutation check (step 11)

The step-10 tests pin already-implemented behaviour, so they passed on first run
and had no RED phase. To show they are not vacuous, two defects were injected and
reverted:

1. Removed the wrapped upper branch of the red hue family
   (`("red", ((0,10),(246,255)))` → `((0,10),)`) →
   `test_red_family_wrap_branch_is_counted_as_red` failed.
2. Restricted the hue census to red and orange only → 5 tests failed across
   `TestSyntheticReferenceRegressions` and `TestArgumentOrder`.

Both reverted; suite returned to 37 passed.

### Suite strength without the reference image (step 12)

Six tests are gated on the reference image existing. Their two strongest
assertions — full hue-family enumeration and detection of a dropped accent hue —
are duplicated unskipped against `synthetic_reference()`, a fixture reproducing
the trait that matters (near-black bulk, one dominant accent, several sub-1%
secondary hues). A clean checkout therefore still guards every regression found
here; the gated tests add real-image confirmation only.

### Argument-order semantics

`accent_hue_shift_detected` and both palette distances are symmetric under
swapping A and B. The direction words are not, by design: `hue_families_lost`,
`_gained`, `_diminished`, `_amplified` and `hue_family_fraction_deltas` all
describe what B did relative to A. Pinned by `TestArgumentOrder`.

## What each approach contributed

**Native vision alone got, unassisted:** complete text transcription; semantic
layout (two-column poster-plus-dashboard split); object and icon inventory;
genre identification (Warhammer 40K pastiche over a SaaS dashboard); and a
correct reading of the pipeline-status colour language
(white→red→amber→cyan→green). It also self-reported its own uncertainty.

**PIL added, non-redundantly:** exact hex values with area coverage; luminance,
saturation and entropy statistics; per-cell structural statistics on a fractional
grid; perceptual hashes; changed-region bounding boxes; and hue-family census.

**PIL got wrong on its own:** its global 8-colour quantisation reported *zero*
vivid entries on this image, because 75% of pixels are near-black. Vision's
qualitative read ("dominated by red and cyan accents") was perceptually correct
where the naive measurement was not. Neither approach is a superset of the other.

**The decisive case:** cyan occupies 0.485% of the frame but encodes the TESTING
pipeline state. Vision named it correctly. Area-weighted quantisation dropped it
twice — once globally, then again inside the accent palette. Only an explicit
per-hue census recovers it. Semantic importance and pixel area are uncorrelated,
and no purely area-weighted metric can bridge that.

## Scope boundary recorded deliberately

Edge density and entropy are **2D image-complexity proxies and not geometry
measurements**. They cannot answer polygon-count or topology questions: shading,
normal maps, lighting and camera angle all move them independently of the
underlying mesh. A low-poly render with smooth shading can out-score a high-poly
render with flat shading. Polygon and topology questions must be answered from
the 3D scene's own mesh statistics, not from pixel analysis of a render. This
disclaimer is emitted in every `pil_structure_diff` payload
(`interpretation_limits`) and pinned by `TestScopeGuard`, so it travels with the
data rather than living only in documentation.

## In scope for this bundle

- `scripts/pil_common.py` — shared measurement primitives
- `scripts/pil_palette_diff.py` — colour palettes, hue families, colour-scheme diff
- `scripts/pil_structure_diff.py` — grid statistics, hashes, changed regions
- `tests/` — 30 tests; synthetic fixtures generated in-process, plus reference-image
  sanity checks that skip when the image is absent

## Packaging

Authored once a verified structure spec was available, then checked empirically
rather than trusted: `claude plugin validate .` passes in both normal and
`--strict` mode, both frontmatter blocks decode as UTF-8 and parse under
`yaml.safe_load`, and the scripts run correctly when invoked by absolute path as
they will be at plugin runtime via `${CLAUDE_PLUGIN_ROOT}`.

- `.claude-plugin/plugin.json` — manifest; component directories rely on
  auto-discovery rather than explicit paths, which removes a class of path error.
- `skills/image-measurement/SKILL.md` — tool usage, field-selection table, and
  the geometry anti-trigger.
- `agents/image-comparison-analyst.md` — vision-plus-measurement comparison
  method, restricted to read-only tools.

The geometry scope limit is stated in three places — the skill description, the
agent definition, and every `pil_structure_diff` payload — because the failure
mode is an agent reaching for edge density as a polygon-count proxy, and any
single statement of it can be missed.

## Deferred
- **Perceptual colour space.** Palette distance is Euclidean RGB, which is not
  perceptually uniform. CIELAB with ΔE2000 would be more faithful; deferred as
  it adds a dependency and the current metric is already demoted to supporting
  detail.
- **Blender mesh-statistics path** for genuine geometry comparison — the correct
  home for the polygon-count use case, out of scope here.
- **Threshold calibration.** `HUE_SHIFT_MIN_ABSOLUTE=0.02` / `MIN_RELATIVE=0.30`
  and the HSV accent thresholds were validated against one image and two derived
  variants. They need a wider corpus before being trusted as defaults.
- **Text-region localisation** was prototyped by the subagent (edge-density row
  bands) but not promoted: it is a weak heuristic and OCR is the better tool.

## Unexplained measurement delta

The tools report luminance mean 28.93 and entropy 5.975 on the reference image;
the prototyping subagent's ad-hoc scripts reported 28.6 and 5.945. Test
tolerances (±0.5, ±0.05) absorb both. The two deltas are same-signed and
proportionally similar, which looks systematic rather than like noise — a
candidate cause is alpha handling in `load_rgb`, or the prototype sampling the
image differently. Those scripts were deleted, so the difference cannot be
diffed directly. Recorded here so a future reader does not rediscover it as a
bug; not chased, because both figures agree to well within any threshold that
matters.

## Reproducing

```
uv sync
uv run pytest -v
uv run python scripts/pil_palette_diff.py "<image>"
uv run python scripts/pil_structure_diff.py "<ref>" "<candidate>" --grid 4x3
```

Environment: Windows 11, Python 3.13.7, Pillow 12.0.0, numpy 2.4.4, pytest 9.0.3,
uv 0.8.22. Paths with spaces or parentheses must be quoted — the reference image
is literally `image (1).png`.

**The images are not distributed.** The reference image and the two derived
variants are excluded from version control, because they derive from a private
source image. Every test that depends on a variant regenerates it in-process from
the reference, and the six tests needing the reference itself skip when it is
absent — see the "suite strength" note above. To re-run those against your own
image, set `PIL_AGENT_REFERENCE_IMAGE`. Path fields in the JSON evidence files
below were replaced with placeholders for the same reason.
