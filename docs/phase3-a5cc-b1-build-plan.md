# Phase 3 build plan — A5 connected components, B1 Blender mesh statistics

Status: **design only, awaiting execution.** Nothing here has been implemented.
Author role: architect/coordinator. This build has three documented incidents of
two agents destroying one file's work ([`docs/phase3-handoff.md`](phase3-handoff.md)
§8) — the file-ownership register in §1 is a hard constraint, not advice.

Scope decision, stated up front: [`docs/phase3-scope.md`](phase3-scope.md) lists
three WP A5 candidates and three WP B items. This plan builds **one A5 candidate
(connected-component instance counting) through the full WP2 gate, and B1
(Blender mesh statistics)**, and defers the rest with reasons, not silence:

- **Silhouette shape descriptors** and **projection-profile alignment + WCAG
  contrast** are not started this round. Silhouette descriptors are already
  flagged in phase3-scope.md's open question 3 as possibly not worth building at
  all; projection alignment targets a UI-review use case with no corpus lined up
  yet. Building three candidates' full WP2 calibration in one pass, sharing one
  serialized calibration pipeline, risks doing all three superficially rather
  than one rigorously — and this repository's own rule is "never claim more than
  you measured." One candidate done to gate-passing standard is worth more than
  three done to a lower one.
- **B2 (matched-view render orchestration)** and **B3 (the revision loop)**
  explicitly depend on B1 passing its own gate first
  (phase3-scope.md's sequencing table). They are not started until B1 lands.
  If this session does not reach them, `docs/index.md` records that plainly —
  "Track A blocked on Track B" is a named failure mode only when it is a
  *scheduling* accident, not when B2/B3 are honestly sequenced after B1.

Why these two specifically: A5 connected-component counting already has a
ready-made ground-truth scene (`calibration/scenes.py::multipart_object`, three
separated blobs, exact known count) and needs pure Pillow+numpy, matching the
dependency constraint. B1 has a ready-made real-world acceptance corpus with
recorded ground truth (`C:\Projects\tms-heim\art\skeleton-crusaders\swordsman\runs\*\parts.json`)
and Blender 5.1 is actually installed on this machine
(`C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`), satisfying the
gate phase3-scope.md says B1 is untestable without.

---

## 1. Dependency graph and file ownership

### 1.1 Graph

```
   WA5-CC  connected-component counting        WB1  Blender mesh statistics
   (pil_common.py, pil_structure_diff.py,      (pil_blender_mesh.py new,
    calibration/*, detection_limits.json)       pil_contract_verdict.py)
        |                                              |
        +-------------------+-------------------------+
                             |
                             v
                        WREL  docs + index status (coordinator, last)
```

WA5-CC and WB1 touch **disjoint files** (verified in §1.2) and run fully in
parallel. Each is internally sequential — do not fan either one out further;
WA5-CC's calibration pipeline (`scenes.py` → `measure.py` → `derive.py` →
`distill_detection_limits.py` → `run_all.py`) is a genuine dependency chain
where each stage reads the previous stage's output, and splitting it across
multiple writers is exactly the collision pattern §8 of the handoff warns about.
One agent, one item, start to finish.

WREL is the coordinator (this session), not a delegated agent, and runs last —
it only touches documentation status, never `scripts/` or `calibration/`.

### 1.2 File ownership register

| Item | Files it may create or modify (exhaustive) |
|---|---|
| **WA5-CC** | `scripts/pil_common.py` (new function only — append, do not touch existing functions), `scripts/pil_structure_diff.py` (new payload fields + new `INTERPRETATION_LIMITS` entries), `tests/test_connected_components.py` (new), `calibration/scenes.py` (append new builder(s) only, existing builders frozen), `calibration/measure.py` (append), `calibration/derive.py` (append), `calibration/distill_detection_limits.py` (append), `calibration/run_all.py` (append to README template), `scripts/detection_limits.json` (generated — new top-level key only), `runs/2026-08-2X-connected-components-calibration/**` (new bundle) |
| **WB1** | `scripts/pil_blender_mesh.py` (new), `tests/test_blender_mesh.py` (new), `scripts/pil_contract_verdict.py` (the `geometry` family's refusal/evaluation path only — see §3.3), `tests/test_contract_verdict.py` (extend `TestRefuseList` geometry cases + add new geometry-verdict tests; do not touch non-geometry tests), `runs/2026-08-2X-blender-mesh-corpus/**` (new bundle) |
| **WREL** (coordinator) | `docs/index.md`, `docs/phase3-scope.md` (status table only) |

Files **neither item may touch**: everything else, including
`scripts/pil_color.py`, `scripts/pil_palette_diff.py`, `scripts/pil_crop.py`,
`scripts/pil_annotate.py`, `scripts/pil_image_info.py`, `scripts/pil_region.py`,
`calibration/alpha_truth.py`, `calibration/perturb.py` (WA5-CC **reads** it for
existing perturbation families; it does not modify it), every existing test file
not named above, every historical `runs/**` bundle, and all manifest files
(`plugin.json`, `.claude-plugin/*`, `pyproject.toml`) — **no version bump this
round**; new/changed tools stay at `TOOL_VERSION = "0.4.0"`, matching the current
manifests, per the handoff's "do not bump versions yourself" rule.

### 1.3 Collision check

WA5-CC's files and WB1's files share zero entries — confirmed by inspection of
§1.2. The only shared *resource* is the working tree and the test suite: both
items run `uv run pytest` against a partially-modified tree while the other is
mid-edit. That is safe because neither reads the other's in-progress files, but
**each item's own final acceptance run must happen after the other item is also
merged**, to catch an accidental cross-file collision the register missed
(§4 D1).

---

## 2. WA5-CC — connected-component instance counting

### 2.1 Decision and justification

Per [`docs/phase3-scope.md`](phase3-scope.md#wp-a5--discrimination-gated-metric-candidates):
"how many separate objects are here, and where — a question vision answers well
but cannot localise numerically." Implementation constraint: **pure-numpy
two-pass union-find** — `scipy.ndimage.label` is barred by the Pillow+numpy-only
dependency rule.

### 2.2 The primitive — `pil_common.py`

Add one new function (append-only; do not modify any existing function in this
file — it is shared with every other tool and this build does not own it beyond
this one addition):

```python
def connected_components(mask):
    """Label connected components of a boolean HxW mask via 4-connectivity.

    Returns (labels, count): labels is an int32 HxW array, 0 = background,
    1..count = component ids in a deterministic order (first-seen in
    row-major scan). Pure two-pass union-find in numpy — no scipy.
    """
```

Determinism requirement: label numbering must be a pure function of the mask's
pixel content (row-major first-seen order), not of any iteration order that
could vary across numpy versions or platforms. Test this directly — build a
mask by hand, assert the exact label array, not just the count.

### 2.3 The payload — `pil_structure_diff.py`

New fields, computed only under `--foreground` (the metric is meaningless
without a mask) and only on the single-image or per-image-of-a-pair path,
matching how `edge_mean` etc. already work per-image:

- `instance_count` (int)
- `components`: list of `{area_fraction, centroid_fractional: [x, y], bbox_fractional: [l, t, r, b]}`, one per component, sorted by descending area (a stable, content-derived order — not label id, which is scan-order and less useful to a caller)

Gate on the existing mask-quality flags: if `foreground_too_small` or
`foreground_mask_empty` is set, emit `instance_count: null`,
`components: null`, not a number computed on an unreliable mask. This mirrors
phase3-scope.md's explicit requirement for the harder-gated silhouette
descriptors, and there is no reason component counting gets to skip it — an
unreliable mask makes component counting meaningless too.

New `INTERPRETATION_LIMITS` entry stating: 4-connectivity only (a diagonal-only
junction is two components, not one — state this, do not silently pick
8-connectivity to "fix" it); anti-aliased edges at low `background_delta` can
bridge visually separate objects into one component — name the perturbation
family and detection limit that bounds this once §2.4 measures it.

### 2.4 Calibration — adapting WP2 to a discrete metric

WP2's procedure ([`docs/phase2-scope.md`](phase2-scope.md#wp2--threshold-calibration))
is written for continuous metrics: threshold = bootstrap-CI upper bound of
`Q(1-alpha)` over a no-change control set, detection limit = smallest
ground-truth perturbation exceeding it. `instance_count` is discrete, so this
needs one honest adaptation, written down in the calibration bundle rather than
silently reinterpreted:

1. Use `calibration/scenes.py::multipart_object` (three separated blobs, exact
   known count = 3) as the no-change control. Define the calibration statistic
   as `abs(instance_count - 3)`.
2. Run the **existing** perturbation families already implemented in
   `calibration/perturb.py` (additive_noise, gaussian_blur, jpeg_reencode,
   rescale_roundtrip, translation — reuse, do not invent new ones) over
   `multipart_object` at the magnitudes the existing pipeline already sweeps.
3. The no-op statistic (perturbation magnitude 0) should be exactly 0 with zero
   variance if the implementation is correct — if it is not exactly 0, that is
   a bug in the primitive or the scene, not a calibration finding; fix it before
   proceeding, do not paper over it with a nonzero threshold.
4. Threshold is therefore `0` by construction (the Neyman–Pearson procedure
   degenerates cleanly when the control distribution is a point mass — state
   this explicitly rather than mechanically applying a bootstrap CI to a
   zero-variance sample, which would be calibration theatre).
5. Detection limit per perturbation family = the smallest tested magnitude at
   which `abs(instance_count - 3) > 0` first occurs. Some families may never
   trigger it at any tested magnitude (report "not resolved at any tested
   magnitude", matching the existing `detection_limits.json` vocabulary exactly
   — see the `accent_fraction_delta_abs` entries for the string format to
   copy).
6. Write the result into `scripts/detection_limits.json` under a new top-level
   key (e.g. `instance_count_delta`) following the exact existing schema:
   `detection_limits` (per perturbation family) + `provenance` (`n`, `alpha`,
   `scenes`). Extend `calibration/distill_detection_limits.py` to emit it and
   `calibration/run_all.py`'s README renderer to report it, mirroring how the
   existing metrics are handled — do not hand-write the JSON.

**Demotion is explicitly permitted.** If step 5 finds the metric never usefully
disagrees with vision (every perturbation family "not resolved", or resolved
only at magnitudes no real render would exhibit), say so in the bundle and do
not wire the fields into the shipped payload behind a false claim of
usefulness — report the finding, mark the candidate demoted in `docs/index.md`,
and leave `instance_count`/`components` out of `pil_structure_diff.py`'s
payload (or behind an explicit opt-in flag, architect's call at that point, not
pre-decided here since it depends on the measurement).

### 2.5 Deliverables

- `pil_common.connected_components` with hand-constructed-mask unit tests
  (exact label array, not just count) in `tests/test_connected_components.py`.
- `pil_structure_diff.py --foreground` payload carries `instance_count` /
  `components` (or is honestly demoted — §2.4).
- Calibration bundle `runs/2026-08-2X-connected-components-calibration/README.md`
  stating the adaptation in §2.4 explicitly, with n, the perturbation families
  swept, and the detection limit (or "not resolved") per family.
- `scripts/detection_limits.json` gains the new key via the real pipeline
  (`calibration/run_all.py`), not hand-edited.

### 2.6 Acceptance criteria

- `connected_components` byte/array-identical across runs; a mask with a known
  hand-drawn layout (e.g. two L-shaped regions touching only diagonally)
  produces the documented 4-connectivity answer, asserted against the literal
  expected label grid.
- `multipart_object` at zero perturbation reports `instance_count == 3` with
  centroids/bboxes matching the scene builder's known component geometry to a
  stated pixel tolerance.
- Existing `tests/test_structure_diff.py` (owned by no item in this build,
  read-only reference) still passes unmodified — new fields are additive.
- `--foreground` off, or mask-quality flags set: new fields are `null`, never a
  fabricated number.
- Calibration bundle exists, states its adaptation of WP2 explicitly, and its
  numbers are reproduced by re-running `calibration/run_all.py`, not merely
  reported.

---

## 3. WB1 — Blender mesh statistics

### 3.1 Decision and justification

Per [`docs/phase3-handoff.md`](phase3-handoff.md#6-track-b--the-part-that-needs-a-blender-scene)
and [`docs/phase3-scope.md`](phase3-scope.md#wp-b1--blender-mesh-statistics):
a standalone script, scene data only, never pixels — `geometry.*` predicates in
`pil_contract_verdict.py` must resolve to real verdicts when scene stats are
supplied, and stay `UNMEASURABLE` (never an error, never a pixel-derived guess)
when they are not.

**The acceptance corpus already exists and must not be recreated or
regenerated**, per the run-bundles-are-evidence rule: real `.blend` files under
`C:\Projects\tms-heim\art\skeleton-crusaders\swordsman\` with matching
`parts.json` per-part topology ground truth. The corpus stays external to this
repository (it lives in a different project) — never copy `.blend` files into
`pil-agent-plugin`; the tool reads whatever path it is pointed at.

**Label reverse-ordered pairs as reverse-ordered.** Per handoff §6: genuine
*decreases* are only available by feeding the swordsman's revision history
backwards (`rev3-faceting-20260818` → `rev2-lower-20260818` reads as a
787→1350-poly *increase* forward; treated backwards it is the −42% decrease
case). Any ledger entry using this pair must say so explicitly — do not present
a reverse-fed pair as if it were the asset's forward history.

### 3.2 The script — `scripts/pil_blender_mesh.py`

Model this on `scripts/pil_image_info.py` (the newest, simplest 0.4.0 tool —
`TOOL_VERSION`, `argparse`, one JSON payload with `tool`/`version`/`parameters`/
`interpretation_limits`, byte-deterministic, exit non-zero with **empty
stdout** on rejection).

This script does **not** run inside this process — it must invoke Blender
headless, since polygon/vertex counts are scene data only `bpy` can read:

```
blender.exe --background <path.blend> --python <this repo's helper script or -c '<code>'> -- <args>
```

Deliverables:
- Accepts one or two `.blend` paths (mirroring the diff tools' one-or-two
  positional pattern).
- Per scene: object inventory (name, polygon count, vertex count, material
  slots), bounding dimensions, and a total.
- Two-scene mode: per-object delta where names match across both files (the
  swordsman corpus's object names are stable across revisions per `parts.json`
  — use that as the join key), `UNMEASURABLE`-equivalent (a `null` delta with a
  reason) for objects present in only one scene.
- Absence of Blender (executable not found at the configured/default path, or
  `--background` invocation fails) is a clean, documented refusal with a
  specific reason — never a pixel-derived approximation, never a bare
  traceback. Mirror the `PIL_AGENT_REFERENCE_IMAGE`-absent skip pattern from
  `tests/conftest.py`: tests that need real Blender skip with a clear reason
  when it is unavailable, and the tool itself refuses cleanly, not silently.
- Determinism: given the same `.blend` file, byte-identical JSON across runs.
  Blender's own scene evaluation order is the risk here — sort object names
  before emitting.

### 3.3 Wiring into `pil_contract_verdict.py`

Today (`scripts/pil_contract_verdict.py:766-768`), `geometry` is in
`REFUSED_FAMILIES` unconditionally — every `geometry.*` predicate is refused
before the `MEASURABLE` registry is even consulted
(`_refusal_for`, lines 781-787). This refusal is **load-bearing and tested**
(`tests/test_contract_verdict.py::TestRefuseList`) and must not weaken for
calls that supply no scene evidence — `test_the_refuse_list_is_advertised_in_parameters`
(line 164) asserts `refused == {"geometry.*", "identity.same_character",
"style.*"}` with no mesh-stats flag passed, and that must keep passing exactly
as written for the no-evidence case.

The change: add an optional CLI input (e.g. `--mesh-stats-a FILE
--mesh-stats-b FILE`, loading `pil_blender_mesh.py`'s own JSON output) that,
when present, removes `geometry` from the refused-family check and adds real
`geometry.poly_count.decrease` / `geometry.poly_count(N)` /
`geometry.topology_preserved` handlers to `MEASURABLE`, evaluated against the
supplied mesh evidence instead of the pixel-diff evidence every other predicate
uses. Structurally:

- `_refusal_for` (or its caller) must consult whether mesh evidence was
  supplied before refusing the `geometry` family — refuse only when it was not.
- New handlers follow the existing `(handler, takes_argument)` registry shape
  (line 747) and return `SATISFIED`/`VIOLATED` per the module's existing
  `_item(...)` convention (see `_p_layout_region_changed` for the shape of a
  parameterised handler), never a number in a refusal (existing
  `test_a_refusal_carries_no_numbers` must keep passing when no mesh evidence
  is given).
- Without `--mesh-stats-*`, behavior is **byte-identical to today** — this is
  the D2-style "pre-existing behavior unchanged" guarantee from the 0.4.0 plan,
  applied here: every existing `pil_contract_verdict.py` test not in the
  geometry family must pass unmodified.

Update `tests/test_contract_verdict.py`: the geometry cases in
`TestRefuseList` (lines 89-90, 114, 154-162) stay exactly as written — they
test the no-evidence path, which does not change. Add a new test class
(e.g. `TestGeometryVerdicts`) exercising the swordsman corpus: feed
`SKS_Garment_Tabard_01`'s two states (fed backwards per §3.1's labelling rule)
through `pil_blender_mesh.py` then `pil_contract_verdict.py --mesh-stats-a
--mesh-stats-b`, assert `geometry.poly_count.decrease` resolves `SATISFIED`
against the real 1350→787 numbers, and assert the no-change control
(`SKS_Garment_UnderMailSkirt_01`, 362 polys across all three revisions) resolves
correctly on an invariant predicate.

### 3.4 Deliverables

- `scripts/pil_blender_mesh.py`, `tests/test_blender_mesh.py` (skips cleanly
  when Blender is not at the configured path — CI/fresh-clone safe).
- `pil_contract_verdict.py`'s `geometry` family resolves real verdicts when
  mesh evidence is supplied, refuses exactly as before when it is not.
- `runs/2026-08-2X-blender-mesh-corpus/README.md`: which swordsman pairs were
  used, which were fed reverse-ordered and labelled as such, the real numbers
  reproduced from `pil_blender_mesh.py`'s own output (not copied from
  `parts.json` — the tool must independently reproduce those numbers from the
  `.blend` files, and the bundle records whether it matched `parts.json`
  exactly or diverged and why).

### 3.5 Acceptance criteria

- `geometry.*` stays `UNMEASURABLE` with no mesh evidence — every existing
  `TestRefuseList` case, byte-for-byte as written.
- With real swordsman `.blend` files: `geometry.poly_count.decrease` resolves
  `SATISFIED` on the genuine −42% Tabard case, `VIOLATED` on a forward increase
  (e.g. MailSleeve 352→1132), correctly `UNMEASURABLE` if the tool honestly
  cannot open a `.blend` (missing Blender, corrupt file) — never a fabricated
  verdict.
- No-change control (`UnderMailSkirt`, 362 polys × 3 revisions) resolves an
  invariant predicate as `SATISFIED` with a stated detection limit of exactly
  0 polys (scene data is exact — no measurement noise to bound), which is
  itself worth stating plainly since it is a sharper guarantee than any
  pixel-derived metric in this repository can offer.
- `pil_blender_mesh.py` byte-deterministic across two runs on the same
  `.blend` file.
- Every other `pil_contract_verdict.py` test (palette.*, layout.*, exact.*,
  identity.silhouette_preserved, style refusals, aggregation) passes unmodified.

---

## 4. Definition of DONE for this build

**D1 — the suite.** `uv run python -m pytest` reports 0 failed, 0 errors, all
previously-passing tests still pass (Blender-dependent tests skip cleanly if
run on a machine without Blender at the configured path; on this machine they
must not skip — Blender 5.1 is present). Run this **after both items are
merged**, not just after each individually (§1.3).

**D2 — no unrelated field changes value.** Every pre-existing
`pil_structure_diff.py` and `pil_contract_verdict.py` payload key that existed
before this build carries the same value on inputs that do not exercise the new
functionality (no `--foreground` new fields absent or accounted for; no
`--mesh-stats-*` unchanged geometry refusal). `interpretation_limits` is
append-only, as in the 0.4.0 plan's D2 carve-out.

**D3 — graded against truth.** WA5-CC's `multipart_object` count and geometry
centroids/bboxes are asserted against literal expected values, not thresholds.
WB1's `geometry.poly_count.decrease` is asserted against the real swordsman
`parts.json` numbers, not a synthetic stand-in.

**D4 — file ownership held.** `git log --oneline --name-only` on this branch
shows no file touched by an item that does not own it in §1.2.

**D5 — residuals published, not buried.** The two calibration/corpus bundles
(§2.5, §3.4) exist and state their numbers, including the WP2 discrete-metric
adaptation (§2.4) and any divergence between `pil_blender_mesh.py`'s numbers
and `parts.json` (§3.4). `docs/index.md` records, in these exact terms: A5
connected-component counting shipped or demoted (with the finding either way);
silhouette descriptors and projection-profile alignment not started, with the
one-sentence reason from this plan's front matter; B1 shipped; B2/B3 not
started, sequenced after B1 per phase3-scope.md.

**D6 — no version bump.** All `TOOL_VERSION` constants and manifest versions
stay `0.4.0`. `test_packaging_conformance.py` (glob-driven) passes unmodified
against the new `pil_blender_mesh.py` file.

**D7 — `claude plugin validate --strict` passes** after both items land.
