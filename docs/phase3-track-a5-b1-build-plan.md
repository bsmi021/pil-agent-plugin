# Phase 3 build plan — Track A5 (shape metrics) and Track B1 (Blender mesh statistics)

Status: **design only, awaiting execution.** Nothing here has been implemented.
Author role: architect. Every work item below is written to be executed by a
different agent, so the file-ownership register in §1 is a hard constraint —
the previous build (`docs/aaa-build-plan.md`) lost work three times to two
agents editing one file, and `docs/phase3-handoff.md` §8 records three more
multi-agent incidents since. Read that section before executing anything here.

This plan inherits the repository's one rule: **never claim more than you
measured.** Where a design choice below rests on something I could not verify
without running code, it is marked *(verify empirically; report the answer)*
rather than asserted.

## 0. Scope decision — what this build attempts, and what it defers

`docs/phase3-scope.md` lists three A5 candidates and three Track B items. This
build does **not** attempt all six. Attempting six work items that each carry
a design-then-code-then-critic-loop, one of which (A5) requires re-deriving
calibration thresholds through a pipeline with per-metric hand-written special
cases, is not achievable to a critic-passed state in one build — and shipping
half of six unfinished is worse than shipping two finished and saying so
plainly (`phase3-scope.md` §"What would make phase 3 a failure": *"Track A
blocked on Track B... is a scheduling failure, not a technical one"* — the
inverse holds too: overcommitting a build's scope is a scheduling failure,
not a reason to ship thin).

**In this build:**

- **A5 — shape metrics.** Connected-component instance counting *and*
  silhouette shape descriptors, in one new tool (`pil_shape_metrics.py`),
  carried through WP2's real calibration methodology to a published
  detection limit or an explicit, recorded demotion. These two are paired
  because both read the same foreground mask and the scope document gates
  them by the same mechanism (mask-quality flags).
- **B1 — Blender mesh statistics.** A new standalone tool
  (`pil_blender_mesh_stats.py`) plus the minimum contract-layer change that
  lets `geometry.*` resolve to a real verdict when scene evidence is
  supplied, validated against the real swordsman acceptance corpus at
  `C:\Projects\tms-heim\art\skeleton-crusaders\swordsman\`.

**Explicitly deferred, with reasons — not silently dropped:**

- **A5 — projection-profile alignment + WCAG contrast.** Different domain
  (UI review, not object/asset review), and its CLI surface is
  underspecified in `phase3-scope.md` (open question #1 — region-vs-foreground
  composition is explicitly unresolved there). It needs its own design pass
  before any agent writes code against it, and folding that design pass into
  this build would double A5's already-large calibration surface. Deferred
  for schedule, not rejected.
- **B2 — matched-view render orchestration**, **B3 — the revision loop.**
  `phase3-scope.md`'s own gate table makes both depend on B1 landing first.
  B1 is being built in this same session, so there is no prior art to build
  B2 against yet, and B2 additionally carries an unresolved design question
  this plan will not answer by fiat: Blender renders are not byte-deterministic
  across machines/GPUs, and this repository's core contract is byte
  determinism (`design-rationale.md`). Settling what B2's determinism claim
  is scoped to is real design work for a future session, not a checkbox here.

If time permits after A5 and B1 both reach a critic-passed state, B2 design
(not implementation) may be attempted as a stretch item; it is not part of
this build's definition of done.

---

## 1. Dependency graph, file ownership, and the collision register

### 1.1 Graph

```
        W1  pil_shape_metrics.py + tests   (new files only)
         |
         v
        W3  A5 calibration integration     (single owner, serialized)
         |
         |         W2  pil_blender_mesh_stats.py + tests +
         |             geometry evidence in pil_contract_verdict.py
         |             (new files, plus one additive change to a shared file)
         |             |
         +-------------+
                        |
                        v
                       W4  docs + wrap-up
```

**W1 and W2 start immediately and run fully in parallel.** They share no
files: W1 only creates `scripts/pil_shape_metrics.py` and
`tests/test_shape_metrics.py`; W2 only creates
`scripts/pil_blender_mesh_stats.py`, its companion Blender-side extractor,
and two new test files, plus one **additive** change to
`scripts/pil_contract_verdict.py` (detailed in §3) that must not touch any
line an unrelated predicate depends on.

**W3 starts only after W1 is merged** — it measures W1's tool over a
synthetic corpus and cannot exist before that tool exists. W3 does not touch
any file W2 owns and can run concurrently with W2.

**W4 is last**, because it is the only item allowed to touch the shared docs
files (`README.md`, `docs/index.md`, `docs/phase3-scope.md`), and because its
"what shipped, what was deferred, what was demoted" section needs W3's actual
outcome (calibrated or demoted) and W2's actual validation numbers to write
honestly rather than speculatively.

| Item | Starts after | Runs in parallel with | Blocks |
|---|---|---|---|
| W1 | — | W2 | W3 |
| W2 | — | W1, W3 | W4 |
| W3 | W1 merged | W2 | W4 |
| W4 | W1, W2, W3 all merged | — | — |

### 1.2 File ownership register

One writer per file per item. The coordinator (not any worker) performs every
git commit and merge — workers report and stop (`phase3-handoff.md` §8).

| Item | Files it may create or modify (exhaustive) |
|---|---|
| **W1** | `scripts/pil_shape_metrics.py` (new), `tests/test_shape_metrics.py` (new) |
| **W2** | `scripts/pil_blender_mesh_stats.py` (new), `scripts/_blender_mesh_extractor.py` (new — no `pil_` prefix, deliberately: see §3.1), `tests/test_blender_mesh_stats.py` (new), `tests/test_geometry_scene_evidence.py` (new), `scripts/pil_contract_verdict.py` (**additive only** — new geometry handlers, new `--scene-stats-a/--scene-stats-b` args, new `PairEvidence` properties; see §3.4 for the exact no-touch boundary) |
| **W3** | `calibration/scenes.py`, `calibration/measure.py`, `calibration/derive.py` (only if a shape-metric-specific special case proves necessary — see §4.4), `calibration/distill_detection_limits.py` (only if metric-name aliasing proves necessary), `calibration/run_all.py`, `scripts/detection_limits.json` (generated), `runs/2026-08-2X-phase3-a5-shape-calibration/**` (new bundle) |
| **W4** | `docs/index.md`, `docs/phase3-scope.md` (status column only), `docs/phase3-handoff.md` (**append-only** — a pointer to this plan's outcome, not a rewrite), `README.md`, `skills/image-measurement/SKILL.md` (only if it lists tools by name — verify before editing) |

Files **no item may touch**: everything under `scripts/` and `tests/` not
named above, in particular `scripts/pil_common.py`, `scripts/pil_region.py`,
`scripts/pil_palette_diff.py`, `scripts/pil_structure_diff.py`,
`tests/test_contract_verdict.py` (W2 adds a **new** test file instead — see
§3.4), `tests/conftest.py`, every historical `runs/**` bundle, and
`docs/aaa-build-plan.md`. If an item believes it needs to edit one of these,
that is a design escape and comes back to the coordinator before any edit is
made.

### 1.3 Collision register

| File | Contending items | Resolution |
|---|---|---|
| `scripts/pil_contract_verdict.py` | W2 only | No collision by construction — W1/W3 never touch it. W2's own change must itself be additive (§3.4), because it is graded against the *existing* test suite passing unmodified. |
| `calibration/*` set | W3 only | No collision by construction — W1 does not touch calibration files; its job is done once its tool is correct and independently tested. |
| `scripts/detection_limits.json` | W3 (writer), everything else (readers) | Same rule as the 0.4.0 plan: nothing but the calibration pipeline ever hand-edits this file. |
| Docs (`README.md`, `docs/index.md`, `docs/phase3-scope.md`) | W4 only | W1/W2/W3 do not touch documentation; they report their outcome to the coordinator, who briefs W4. |

---

## 2. W1 — `scripts/pil_shape_metrics.py`

### 2.1 Decision and justification

One new tool, two related families of measurement, both computed from the
same foreground mask `pil_common.foreground_mask` already produces — reuse
it; do not invent a third mask definition (`phase3-handoff.md` §4).

- **Connected-component instance counting.** Per-blob area, centroid, and
  bounding box, via a **pure-numpy two-pass union-find** (scipy is barred —
  `README.md#requirements`, `phase3-scope.md` WP A5). Answers "how many
  separate objects, and where" — a question vision answers qualitatively but
  cannot localise numerically.
- **Silhouette shape descriptors**, computed on the foreground mask's overall
  silhouette (not per-blob): fill ratio (mask area / bbox area), perimeter²/area,
  and an orientation histogram (principal axis angle via image moments, or an
  angle-binned boundary histogram — pick one, document the choice and why in
  `interpretation_limits`).

### 2.2 The non-negotiable gate carried into the implementation

`phase3-scope.md` WP A5: *"Shape descriptors must stay gated on mask-quality
flags... These ship only if they also degrade honestly under the existing
mask-quality flags (`foreground_too_small`, `foreground_mask_empty`,
`background_dominant`), returning nothing rather than a number on an
unreliable mask."*

Concretely: when the input's foreground mask trips any of those flags (reuse
the existing thresholds and flag names from `pil_common.py` /
`pil_structure_diff.py` — do not redefine them), silhouette descriptor fields
are `null` in the payload, not a computed-but-untrustworthy number, and the
flag that suppressed them is named in `flags`. Connected-component counting
is not gated the same way — a count is meaningful even on a small mask — but
must still report `foreground_mask_empty` → zero blobs, not an error.

### 2.3 CLI surface

```
python pil_shape_metrics.py image.png [--foreground] [--background-delta D]
```

Single-image tool (not a diff tool — there is nothing to compare yet; A5's
metrics feed into contracts and calibration as single-image measurements,
matching how `pil_image_info.py` and the palette/structure tools' single-image
mode already work). `--foreground` reuses `pil_common.foreground_mask`
exactly as `pil_palette_diff.py`/`pil_structure_diff.py` do. Without
`--foreground`, run the same mask machinery anyway to report the flags
(mirroring how the diff tools estimate foreground coverage even when the
caller didn't ask for masking) but compute connected components over the
**whole frame** — a full-frame image is not a set of disjoint background
regions in any useful sense for counting.

*Deliberate scope cut, stated so it is not rediscovered as a gap later:* no
`--region` support in this build. `phase3-scope.md` open question #1 (region
relative to frame vs. foreground bbox) is unresolved, and resolving it for a
brand-new tool in the same build that also has to pass a calibration gate is
scope creep beyond what WP A5 literally asks for. Document this as an
explicit limitation in `interpretation_limits` and in the payload
(`region_supported: false` or equivalent), not silently.

### 2.4 JSON output shape

```json
{
  "tool": "pil_shape_metrics",
  "version": "0.4.0",
  "parameters": {"foreground": true, "background_delta": 6.0},
  "flags": ["background_dominant"],
  "components": {
    "count": 3,
    "instances": [
      {"area_px": 412, "centroid_fractional": [0.31, 0.44], "bbox_fractional": [0.28, 0.40, 0.34, 0.48]},
      "..."
    ],
    "min_area_px_floor": 4
  },
  "silhouette": {
    "fill_ratio": 0.62,
    "perimeter_sq_over_area": 18.4,
    "orientation_degrees": 12.5,
    "suppressed_by": null
  },
  "interpretation_limits": ["..."]
}
```

`min_area_px_floor`: connected components at 1-2 px are almost always
anti-aliasing noise, not a real object. Pick a floor (document the value and
why — likely tied to the *existing* `HUE_PRESENCE_MIN_PIXELS` precedent or a
fresh, measured choice), and report it in the payload so a caller knows the
counting rule, not just the count. `silhouette.suppressed_by` names the flag
that nulled the silhouette fields, or is `null` when they were computed.

### 2.5 Determinism and rejection discipline

Same contract as every other tool in this repo: byte-identical JSON across
repeated runs (component ordering must be **deterministically sorted** —
e.g. by bbox top-left, row-major — since a naive union-find can discover
components in scan order that is already deterministic, but state the
ordering rule explicitly and test it); exit 2 with empty stdout on an
unreadable file or a malformed argument.

### 2.6 Deliverables

- `scripts/pil_shape_metrics.py`
- `tests/test_shape_metrics.py` covering: byte-determinism; the union-find
  against hand-constructed fixtures with a known component count (isolated
  squares at known positions — assert exact count, exact per-blob area,
  exact centroid); the min-area floor excluding a 1px stray pixel but
  including a 5px blob; silhouette fields null under each of the three
  mask-quality flags, computed and correct otherwise; rejection paths (exit
  2, empty stdout) for a missing file and a corrupt image; a fixture at an
  odd resolution to catch off-by-one rounding in bbox/centroid fractions,
  matching `pil_region.py`'s own precedent for edge-case sizes.

### 2.7 How a plausible implementation could be wrong

- **Diagonal connectivity.** A two-pass union-find needs an explicit choice
  between 4-connectivity and 8-connectivity. Either is defensible; an
  undocumented choice is not. State it, and test a fixture where the two
  disagree (two squares touching only at a corner) so the choice is visible
  in the suite, not just in a comment.
- **Centroid on a mask that touches the image border.** `mask_bbox` in
  `pil_common.py` already handles this correctly for bounding boxes; verify
  centroid math does too rather than assuming an unclipped assumption holds.
- **Silhouette computed on a mask, not on the components.** The fill
  ratio/perimeter²/area/orientation describe the *whole* foreground
  silhouette (all components' union), not any single blob — a build that
  silently computes silhouette stats on "the largest component" instead of
  the full mask has quietly redefined the metric.

---

## 3. W2 — `scripts/pil_blender_mesh_stats.py` and geometry contract evidence

### 3.1 Decision and justification

A standalone script that shells out to a real Blender install (confirmed
present at `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe` on
this machine, headless-capable via `--background scene.blend --python
script.py -- <args>`) and reports polygon/vertex counts, object inventory,
and bounding dimensions **from scene data, never from pixels**
(`README.md#scope-limit-these-tools-do-not-measure-geometry`,
`phase3-scope.md` "Explicitly out of scope: Geometry inferred from pixels").

Two files, not one: `pil_blender_mesh_stats.py` is the agent-facing CLI —
it validates arguments, locates the Blender executable, invokes it as a
subprocess, and formats the JSON payload. `_blender_mesh_extractor.py` is
the small script that runs *inside* Blender's own Python (bpy is only
importable there) and does the actual scene walk. **Deliberately not named
`pil_*.py`** — it does not stand alone as an agent-facing tool, is not
runnable outside a Blender process, and must not be swept into the
glob-driven `TOOL_VERSION` conformance test (`tests/test_packaging_conformance.py`),
which would incorrectly demand it advertise its own versioned API.

### 3.2 Locating Blender

```
--blender-executable PATH   (highest priority)
$PIL_AGENT_BLENDER_EXECUTABLE env var
"blender" resolved on PATH
```

matching the existing `PIL_AGENT_REFERENCE_IMAGE` env-var precedent for
optional external dependencies (`tests/conftest.py`). Absence of all three is
a rejection (exit 2, empty stdout) for **this tool run directly** — it
cannot do its job without Blender, so failing loudly is correct here. This
is a different case from the contract layer's degradation (§3.4), where
absence of scene evidence must be a clean `UNMEASURABLE`, never an error —
do not conflate the two; they are different tools with different contracts.

### 3.3 CLI surface and JSON output

```
python pil_blender_mesh_stats.py scene.blend --out stats.json [--object NAME ...]
```

*(Verify empirically against the extractor: whether Blender's mesh
`polygons` count for these assets matches the `polys` figures already
recorded in `parts.json` for the corpus, or whether those were counted
post-triangulation/post-modifier. Report which convention matches, and if
neither matches exactly, report the discrepancy with numbers rather than
picking one silently — this is exactly the "grade against truth, not a
threshold" rule.)*

```json
{
  "tool": "pil_blender_mesh_stats",
  "version": "0.4.0",
  "parameters": {"scene": "scene.blend", "blender_version": "5.1.x"},
  "objects": {
    "SKS_Body_Neck_01": {
      "vertex_count": 136, "polygon_count": 100,
      "material_slots": 1, "bbox_dimensions": [0.0, 0.0, 0.0]
    }
  },
  "totals": {"vertex_count": 0, "polygon_count": 0, "object_count": 0},
  "interpretation_limits": [
    "Counts describe scene mesh data as Blender reports it; they are not a claim about a runtime/export-time triangulation unless the scene was measured post-export.",
    "..."
  ]
}
```

Deterministic across runs on the same file (Blender's own object-iteration
order is not guaranteed alphabetical — sort `objects` by name before
serialising, exactly as every other tool sorts its output).

### 3.4 The contract-layer change — additive, and bounded precisely

Today, `scripts/pil_contract_verdict.py` refuses **every** `geometry.*`
predicate unconditionally (`REFUSED_FAMILIES`, evaluated in `evaluate()`
before the `MEASURABLE` registry is even consulted). This build's job is to
make that refusal *conditional on scene evidence being supplied*, without
changing behaviour at all when it is not.

**Exact mechanism:**

1. `PairEvidence` gains two optional constructor inputs, `scene_stats_a` /
   `scene_stats_b` (paths, default `None`), and a `has_scene_stats` cached
   property plus `scene_a` / `scene_b` cached properties that lazily load
   and parse the JSON — only when both are present.
2. `main()` gains `--scene-stats-a PATH` / `--scene-stats-b PATH` for
   single-pair mode. For `--pairs` mode, `load_pairs()` accepts optional
   `"scene_a"` / `"scene_b"` keys per entry, resolved with the same
   `_resolve()` helper already used for `"a"`/`"b"` — do not write a second
   path-resolution function.
3. A small, explicit vocabulary — not a blanket lift of the family refusal —
   of what can now be answered:
   `geometry.poly_count.decrease`, `geometry.poly_count.increase`,
   `geometry.poly_count.unchanged`, `geometry.topology_preserved`, each
   accepting an optional parenthesised object-name argument (matching the
   existing parameterised-predicate style, e.g. `palette.hue_present(NAME)`)
   to scope to one named part; with no argument, compare the **sum across
   every object present in both scene-stats files** (and flag, in the
   finding's `detail`, any object present in one side's inventory but not
   the other's — a part added or removed is not silently ignored).
   `geometry.topology_preserved` is `SATISFIED` only when **both** vertex
   count and polygon count are exactly equal — document explicitly in
   `interpretation_limits` that count-equality is necessary but not
   sufficient for true topological identity (a same-count retopology would
   read as preserved; this predicate cannot see that).
4. `evaluate()` checks this new geometry vocabulary **before** consulting
   `_refusal_for()`, and only routes into it when `evidence.has_scene_stats`
   is true; when false, execution falls through to the existing
   `_refusal_for()` path completely unchanged — same string
   (`GEOMETRY_REFUSAL`), same `UNMEASURABLE`, same
   `parameters.predicates.refused` listing. Anything in the `geometry.*`
   family *not* in the new explicit vocabulary (e.g. some predicate name an
   implementer invents on the spot) stays refused even with scene stats
   present — the refuse-first-then-match discipline
   (`pil_contract_verdict.py` lines 790-826) is preserved, not routed
   around.

**The boundary that keeps this additive, restated as a test obligation:**
every existing test in `tests/test_contract_verdict.py` — including
`TestRefuseList` and its assertion that `geometry.*` predicates are
`UNMEASURABLE` with `"scene mesh statistics"` in the reason, run *without*
any `--scene-stats` flag — must still pass **unmodified**. W2 does not edit
that file at all; it proves the new behaviour with a **new** file,
`tests/test_geometry_scene_evidence.py`.

### 3.5 Deliverables

- `scripts/pil_blender_mesh_stats.py`, `scripts/_blender_mesh_extractor.py`
- `tests/test_blender_mesh_stats.py` — skip-gated (§3.6) real-corpus tests,
  plus unconditional tests for the "Blender not found" rejection path
  (achievable on any machine by pointing `--blender-executable` at a
  nonexistent path — does not require Blender to actually be absent).
- `tests/test_geometry_scene_evidence.py` — the new contract-layer behaviour,
  using fixture JSON scene-stats files (no Blender needed to test the
  contract layer itself, since it only ever reads the JSON W2's mesh-stats
  tool produces — write the fixtures by hand, matching the real schema).

### 3.6 Skip pattern for Blender/corpus absence

Mirror the existing `REFERENCE_IMAGE` class-level skip precedent
(`tests/test_palette_diff.py:374`, `tests/test_structure_diff.py:204`):

```python
BLENDER_EXECUTABLE = _locate_blender()  # None if not found anywhere
SWORDSMAN_CORPUS = Path(r"C:\Projects\tms-heim\art\skeleton-crusaders\swordsman")

@pytest.mark.skipif(
    BLENDER_EXECUTABLE is None or not SWORDSMAN_CORPUS.exists(),
    reason="Blender executable or swordsman corpus not present on this machine",
)
class TestRealCorpus:
    ...
```

so a fresh clone on a machine without Blender reports these as skipped, not
failed — and the corpus path is never hardcoded into anything committed as a
*requirement*, only as an optional real-world validation, consistent with
`phase3-handoff.md` §6's "Note the honest framing" and the rule that
`runs/**` evidence is not regenerated to fit new code.

### 3.7 Real-corpus validation (not just unit tests)

When the skip guard passes, assert against `parts.json` ground truth for at
least: `SKS_Garment_Tabard_01` at `rev2-lower-20260818` (787 polys) and
`rev3-faceting-20260818` (564 polys, per `docs/phase3-handoff.md` §6's
torso-depth figures — *(verify the exact number against the actual
`parts.json` file at test-write time; the handoff doc's prose numbers are a
pointer, not a substitute for reading the file)*; and
`SKS_Garment_UnderMailSkirt_01` holding at exactly 362 across all three
revisions as the no-change control. Because the forward whole-model
comparison only decreases when fed **backwards** (`phase3-handoff.md` §6),
any test exercising a whole-scene `geometry.poly_count.decrease` against
this corpus must feed the revisions in reverse order and say so in the test
name/docstring — do not relabel a reverse-ordered pair as forward.

### 3.8 How a plausible implementation could be wrong

- **Evaluated vs. raw mesh data.** Blender distinguishes `mesh.polygons`
  (whatever is currently in the mesh datablock) from the modifier-evaluated
  depsgraph result. If the corpus `.blend` files carry modifiers, these can
  disagree with `parts.json` sharply. This is the single most likely source
  of a "close but not exact" mismatch — measure it, don't assume it away.
- **Instanced/linked objects.** If any part is a linked duplicate, naively
  summing `len(obj.data.vertices)` per object can double-count shared mesh
  data. Verify against the corpus; the swordsman parts inventory suggests
  each named part is a distinct object, but confirm rather than assume.
- **Blender version drift.** The extractor's bpy API surface must match
  Blender 5.1 specifically (this machine's installed version) — do not write
  against a remembered older bpy API from training data without checking the
  installed version's actual API via `blender --version` / in-process
  introspection.

---

## 4. W3 — A5 calibration integration (single owner, serialized after W1)

### 4.1 What this item is, precisely

Carry `pil_shape_metrics.py`'s two metric families through WP2's real
methodology: synthetic ground-truth corpus → response curves →
Neyman-Pearson threshold at a published `n`/`α` → bootstrap-CI-bounded
detection limit → entry in `scripts/detection_limits.json`. This is the item
the advisor and `phase3-handoff.md` both flag as the real hazard: three
metrics calibrated in parallel would all write to the same generated file and
the same calibration modules. **There is exactly one metric family under
active calibration at a time in this build (shape metrics); do not further
parallelise within W3.**

### 4.2 Sequence (do not skip steps or reorder)

1. Add a new synthetic scene family to `calibration/scenes.py`: images with
   a controllable, known number of foreground blobs (e.g. N non-touching
   shapes at fixed positions), parameterised by blob count — this is the
   "ground truth" connected-components calibration needs, the same way
   existing scenes carry a known accent fraction or known recolour delta.
2. **Reuse existing perturbation recipes from `calibration/perturb.py`
   unmodified** as the "no genuine change" controls (rescale round-trip,
   sub-pixel shift, recolour-preserving-shape) — these must not move blob
   count, and asserting that they don't is itself a useful sanity check.
   Add a new perturbation only for the "genuine change" axis specific to
   this metric: blob count ±1, a blob split into two, two blobs merged —
   at graded magnitudes, mirroring how existing perturbations are graded
   (e.g. hue rotation by degree).
3. Wire `calibration/measure.py`'s metric extraction (`extract_metrics()`
   and the unit-building functions around it) to also invoke
   `pil_shape_metrics.py` over each corpus image and fold
   `components.count`, `silhouette.fill_ratio`,
   `silhouette.perimeter_sq_over_area` into the measurement records under
   their own column names.
4. Run the derivation (`calibration/derive.py`'s response-curve and
   Neyman-Pearson threshold functions) over the new columns. *(Verify
   empirically whether the existing generic functions handle these columns
   with no code change, or whether a metric-specific branch — like the
   `structural_similarity`/`entropy_delta`/`accent_hue_shift_detected`
   special cases already in `distill_detection_limits.py` — proves
   necessary. Do not add a special case pre-emptively; add one only if the
   generic path is measured to produce a wrong or missing entry.)*
5. Run `calibration/distill_detection_limits.py` to regenerate the candidate
   bundle, then promote it into `scripts/detection_limits.json` exactly as
   the existing `calibration/run_all.py` sequencing does for every other
   metric — do not hand-edit the JSON.
6. Write the discrimination-matrix-style bundle
   (`runs/2026-08-2X-phase3-a5-shape-calibration/README.md`), modelled on
   `runs/2026-08-18-pil-agent-plugin-phase1/10-metric-discrimination-matrix.md`:
   every candidate's response curve, its derived threshold, its published
   detection limit, `n`, `α`, and — this is not optional — an explicit
   pass/demote verdict per candidate with the number that decided it.

### 4.3 The gate, restated so it cannot be softened by drift

**Demotion is a valid, expected outcome, not a failure of this work item.**
If connected-component counting's detection limit is worse than any change a
real caller would care about, or if silhouette descriptors do not "usefully
disagree with vision" on any measured case, say so in the bundle and do not
ship the metric into `pil_contract_verdict.py`'s registry. A metric that
ships with a weak or unmeasured gate is a worse outcome than an honestly
demoted one — this has already happened twice in this repository
(`base_palette_distance`, `entropy_delta`), and it is the expected, correct
outcome here too if the numbers say so.

### 4.4 Deliverables

- New scene family in `calibration/scenes.py`, new perturbation recipe(s) in
  `calibration/perturb.py` (only the blob-count-changing ones — the no-change
  controls are reused, not rewritten).
- `calibration/measure.py` wired to extract the two new metric families.
- Regenerated `scripts/detection_limits.json` containing entries for
  whichever candidates pass, with real provenance (`n`, `α`, scenes, dominant
  control family — matching the existing schema exactly, per
  `distill_detection_limits.py`'s docstring).
- `runs/2026-08-2X-phase3-a5-shape-calibration/README.md` with the full
  discrimination record, including demoted candidates and why.
- If either candidate passes: the corresponding predicate(s) added to
  `pil_contract_verdict.py`'s `MEASURABLE` registry — **but this is a second,
  small, additive change to that file, and it must be sequenced after W2's
  change lands and rebased on it, never branched independently, to avoid the
  exact `pil_common.py`-style collision the 0.4.0 plan hit on that file.**
  If nothing passes, this step is skipped and the demotion is documented
  instead — not silently, and not as a failure.

### 4.5 How a plausible implementation could be wrong

- **Grading a metric against its own calibration's synthetic corpus only.**
  Phase 2's real corpus validation (`runs/2026-08-20-phase2-real-validation/`)
  exists because synthetic calibration systematically underestimates
  difficulty on correlated, semantic real change
  (`docs/phase2-scope.md` "Known limitations"). If time allows, spot-check
  the calibrated threshold against at least one real image pair (the
  swordsman renders, if any exist, or any other real asset pair) before
  calling the gate closed — if time does not allow, say so in the bundle
  rather than presenting synthetic-only calibration as equivalent to what
  phase 2 did.
- **Silently loosening the perimeter/area metric's scale sensitivity.**
  Perimeter² / area is scale-**invariant** by construction for a fixed
  shape, but the discretisation error of measuring a boundary on a raster
  grid is not — verify the response curve accounts for this at the
  resolutions the working-copy pipeline actually uses
  (`WORKING_LONG_EDGE`), not at full native resolution.

---

## 5. W4 — docs and wrap-up (last, after W1/W2/W3 all merged)

### 5.1 Deliverables

- `docs/index.md`: entry pointing at whichever `runs/2026-08-2X-*` bundles
  landed (shape-metrics calibration, mesh-stats validation), in the same
  style as the existing entries — link to evidence, don't restate it.
- `docs/phase3-scope.md`: update the status of A5 (partial — components/
  silhouette attempted with recorded outcome; alignment+contrast explicitly
  deferred) and B1 (landed, with the real-corpus validation pointer) without
  rewriting the scope document's own content — this is a status update, not
  a redesign.
- `docs/phase3-handoff.md`: **one appended pointer**, not a rewrite, noting
  what this build did and did not attempt, so the next agent picking up B2
  does not have to reconstruct that from git log.
- `README.md`: add `pil_shape_metrics` and `pil_blender_mesh_stats` to the
  "Choosing a metric" table and the tool inventory, following the existing
  entries' voice exactly.
- **Do not bump `TOOL_VERSION` or any manifest version.** New tools ship
  declaring `TOOL_VERSION = "0.4.0"`, matching the current manifest exactly
  — the glob-driven conformance test only requires agreement with the
  manifest, not a new version, and a version bump is a release-step decision
  this build does not make (`phase3-handoff.md` §3: *"Do not bump versions
  yourself — the release step owns that, across eleven places"*).

### 5.2 Acceptance criteria

- `claude plugin validate --strict` still passes.
- `test_packaging_conformance.py` still passes with the two new tools
  discovered by its glob and matching `0.4.0`.
- Every new capability claim in `README.md`/`docs/index.md` points at the
  `runs/**` bundle that established it, per the standing rule
  (`phase3-handoff.md` §9, D11 of the 0.4.0 DoD template).
- The deferred items (A5 alignment+contrast, B2, B3) are named as deferred
  **in the docs**, not just in this plan file — a future reader of
  `docs/index.md` alone should not have to find this build plan to learn
  that they were not attempted.

---

## 6. Definition of DONE for this build

Gradeable without asking me. Every line is a command, a file, or a number.

**D1 — the suite.** `uv run python -m pytest` reports 0 failed, 0 errors,
0 xpassed, 0 xfailed. Skips are only the pre-existing `REFERENCE_IMAGE`-gated
ones plus any new Blender/corpus-gated ones this build adds — every skip
must be named and explained, not merely tolerated.

**D2 — nothing outside the ownership register moved.**
`git log --oneline --name-only` on this branch shows no file modified by an
item that does not own it per §1.2. In particular:
`scripts/pil_common.py`, `scripts/pil_palette_diff.py`,
`scripts/pil_structure_diff.py`, and `tests/test_contract_verdict.py` show
**zero** diff against `main`.

**D3 — the contract-layer change is provably additive.** Every test in
`tests/test_contract_verdict.py` that existed before this build passes
unmodified, run with no `--scene-stats` flags, with byte-identical
assertions to what is on `main` today.

**D4 — the calibration outcome is real, not asserted.** For each of
connected-component counting and silhouette descriptors:
either (a) `scripts/detection_limits.json` carries an entry with non-null
`n`, `α`, and at least one non-"not resolved" detection limit, or (b)
`runs/2026-08-2X-phase3-a5-shape-calibration/README.md` states plainly that
it was demoted and cites the number that decided it. No third outcome
(shipped but uncalibrated) is acceptable.

**D5 — B1 is validated against real ground truth, not only synthetic
fixtures.** On a machine with Blender and the corpus present,
`tests/test_blender_mesh_stats.py::TestRealCorpus` (or equivalent) passes
and asserts exact equality against `parts.json` for at least three named
parts across at least two revisions, with the reverse-ordering note (§3.7)
honoured in any test that exercises the whole-model decrease case.

**D6 — geometry resolves, and still refuses correctly.** A hand-written
contract with `geometry.poly_count.decrease` against two fixture
scene-stats JSON files (one showing a real decrease, one showing no change)
resolves to `SATISFIED`/`VIOLATED` correctly with scene stats supplied, and
to `UNMEASURABLE` with `"scene mesh statistics"` in the reason with no
scene stats supplied — both cases in the same test file, so the boundary is
visible in one place.

**D7 — determinism.** Every new tool's output is byte-identical across two
runs on the same input, verified by an actual re-run in the test (not
compared to itself in one run — `phase3-handoff.md` §2's "determinism
theatre" warning).

**D8 — rejection hygiene.** Every new tool exits 2 with byte-empty stdout on
every rejection path, and this is asserted by a test that reads the actual
subprocess exit code and stdout bytes, not by inspecting a return value in
process.

**D9 — the residuals are published, not buried.** The deferred items (A5
alignment+contrast, B2, B3) are named with reasons in both this plan (§0,
already done) and in `docs/index.md`/`docs/phase3-scope.md` (W4's job). Any
open empirical question flagged in this plan with *(verify empirically)* has
its answer recorded somewhere in the shipped bundles — not left as an open
question that was quietly never answered.

---

## 7. Orchestration mechanics for whoever executes this plan

From `phase3-handoff.md` §8 and prior-session memory, restated here so the
coordinator does not have to hold it separately:

- Launch Puppetmaster jobs via the **CLI**, not the MCP `start_*` tools —
  those have silently dropped launches before while `doctor` stayed green.
  Only job registration (not the launch command's own exit code) proves a
  launch actually took.
- Pass `--allow-dirty` on both `codex` and `claude` subcommands; omitting it
  on `codex` fails instantly with `dirty_worktree` while the launcher still
  exits 0 — a silent no-op that looks like success.
- Never kill a launcher that merely looks stalled. Registration has lagged
  up to ~8 minutes in this environment before; check both job registration
  and process ancestry before concluding a launch failed.
- Workers implement and report; **the coordinator performs every commit**.
  One hand on git, always.
- Per work item: implement → an independent critic agent that *executes*
  the tool (runs it, runs its failure paths, re-runs it for determinism,
  reproduces any reported numbers rather than trusting them) → fix → repeat.
  Budget 2-3 rounds; every tool in the 0.4.0 build failed its first critic.
