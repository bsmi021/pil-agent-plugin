# Phase 3 build plan — Track A5 candidates + Track B1

Status: **design only, awaiting first implementer round.** Nothing here has been
implemented. Author role: architect, following `docs/phase3-handoff.md`'s
instruction to design before code and `docs/aaa-build-plan.md`'s precedent for
what a gradeable plan looks like in this repository.

Target: this branch (`feat/phase3-track-a5-b1`), released later at whatever
version the release step picks. **No manifest version bump happens here** —
new tools declare `TOOL_VERSION = "0.4.0"`, matching the current
`plugin.json`/`.claude-plugin/plugin.json` version, because
`test_every_shipped_tool_declares_the_manifest_version` checks new tools
against the *current* manifest, and bumping the manifest is the release step's
job, not this build's (`docs/phase3-handoff.md` §3, §9).

Everything here inherits the repository's discipline: *never claim more than
you measured*. Demotion is an accepted outcome for any A5 candidate — the
scope document says so explicitly, and `docs/phase3-scope.md` open question 3
already names "components and alignment profiles only" as a legitimate
result.

---

## 1. Scope decision, made once so four agents don't each guess differently

**In this build:**

- **A5-CC** — connected-component instance counting, per-blob area/centroid/bbox.
- **A5-SD** — silhouette shape descriptors (fill ratio, perimeter²/area,
  orientation histogram), gated on mask-quality flags.
- **A5-PA** — projection-profile alignment ("aligned within N px") plus WCAG
  contrast-ratio arithmetic.
- **B1** — Blender mesh statistics: polygon/vertex counts, object/material
  inventory, bounding dimensions, run headless against the real swordsman
  acceptance corpus.

**Explicitly out of this build**, named so absence reads as a decision, not an
oversight:

- **B2/B3** — sequenced strictly after B1 passes its gate (`docs/phase3-scope.md`
  §Sequencing). Not started until B1 is D-complete below. If this build ends
  before B1 lands cleanly, B2/B3 are not attempted and the residual is stated
  in `docs/index.md`, not silently dropped.
- **Contract-verdict integration for A5.** `pil_contract_verdict.py`'s
  `MEASURABLE`/refused-predicate registry is not extended for A5-CC/SD/PA in
  this build. A1–A4 shipped as standalone CLIs without contract integration
  (`pil_crop`, `pil_annotate`, `pil_image_info` are not in the predicate
  registry either); A5 follows the same precedent. Wiring new predicates is a
  real, separate design decision (what predicate name? what role — expected or
  invariant? does aggregation change?) and doing it three times in parallel,
  in one shared file, is exactly the collision this plan exists to avoid. It
  is left as a named follow-up in `docs/index.md`, not attempted here.
- **B1 is the one exception** to the no-contract-integration rule, because
  `docs/phase3-scope.md`'s own WP B1 test requirement is explicit:
  `geometry.*` must resolve to `SATISFIED`/`VIOLATED` with scene data and stay
  `UNMEASURABLE` without it. That is a one-writer change to one file (see §3).

**Why A5 does not touch the shared calibration pipeline.** `calibration/derive.py`,
`measure.py`, `scenes.py`, `perturb.py`, `run_all.py`, `distill_detection_limits.py`
and `scripts/detection_limits.json` are a single interconnected pipeline that
three parallel candidates would otherwise all need to extend — exactly the
"two writers, one file" incident this repository has hit three times
(`docs/phase3-handoff.md` §8). Instead, **each A5 candidate owns a new,
self-contained calibration script** (`calibration/<name>_gate.py`) that:

- imports `bootstrap_quantile` and `alpha_for` from `calibration/derive.py`
  **read-only** (they are pure functions with no shared state — reuse per
  `docs/phase3-handoff.md` §4, not reinvention);
- imports scene builders from `calibration/scenes.py` and perturbation ops
  from `calibration/perturb.py` **read-only**;
- invokes its own new tool as a **CLI subprocess**, the same way
  `calibration/measure.py` invokes the existing tools and the same way an
  agent invokes any tool — never by importing the tool's functions
  (`calibration/measure.py`'s own docstring: "Calibrating the importable
  functions would calibrate something the caller never runs");
- writes its own dated run bundle under `runs/`, with its own
  `n`, `alpha`, and published detection limit;
- does **not** write to `scripts/detection_limits.json` — a candidate that
  ships carries its own calibrated threshold as a module constant in its own
  tool file (the same pattern `pil_palette_diff.py` uses for
  `HUE_SHIFT_MIN_ABSOLUTE`), cited back to its own gate bundle in
  `interpretation_limits`. This keeps every A5 tool self-contained and
  removes the shared-file collision entirely, at the cost of not sharing
  calibration infrastructure across candidates — an explicit tradeoff, not an
  oversight.

**Scale invariance, decided per candidate** (`docs/phase3-handoff.md` §3 requires
this be explicit, not assumed):

- **A5-CC** runs on the **native-resolution** foreground mask (via
  `pil_common.foreground_mask` on the full-resolution `load_rgb_alpha` output,
  not `to_working`). Downsampling risks merging or splitting blobs that are
  genuinely separate at native resolution, which would corrupt the exact
  thing this metric answers. Consequence, stated in the tool's own
  `interpretation_limits`: component **count** and pixel **area** are
  **not** scale-invariant and are only comparable between two renders of
  matching resolution; centroid and bbox are additionally reported as
  frame-fractional coordinates for cross-resolution comparability of
  *position*, not of *size*.
- **A5-SD** runs on the **fixed working-resolution copy**
  (`pil_common.WORKING_LONG_EDGE`), the same convention `pil_structure_diff.py`
  already uses for its grid statistics. Perimeter measured on a pixel grid is
  resolution-sensitive by construction (staircase artifacts), so measuring at
  a fixed working size is what makes two different-resolution renders of the
  same object comparable at all. Fill ratio is scale-invariant in principle;
  it is measured at working resolution anyway, for one mask definition across
  both descriptors.
- **A5-PA** runs at **native resolution** per view (pixel-level alignment is
  the point), and reports both a raw pixel margin and a frame-fractional
  margin. The "aligned within N px" verdict is only meaningful when both
  images share the compared axis's pixel dimension; the payload states the
  compared resolutions explicitly and the tool refuses (not silently
  degrades) a pixel-tolerance verdict when they differ, offering only the
  fractional margin in that case. WCAG contrast is a per-pixel colour
  property and carries no scale-invariance question.

---

## 2. Dependency graph and file ownership register

### 2.1 Graph

```
   A5-CC        A5-SD        A5-PA        B1
  (new files)  (new files)  (new files)  (new files + one
     |             |             |        owned edit to
     |             |             |        pil_contract_verdict.py)
     +------+------+------+------+
                   |
                   v
              INTEGRATE (coordinator only)
       README.md, SKILL.md, docs/index.md,
       docs/phase3-scope.md status table
```

All four implementer items start immediately and run fully in parallel —
every file each creates is new, except B1's one exclusive edit to
`pil_contract_verdict.py`, which no other item touches. **INTEGRATE runs last,
by the coordinator alone**, after every item that shipped has passed its
critic round, for the same reason `docs/aaa-build-plan.md`'s W7 runs last and
owns `README.md`/`SKILL.md`/`docs/` alone: those files would otherwise be a
four-way collision.

| Item | Starts after | Runs in parallel with | Blocks |
|---|---|---|---|
| A5-CC | — | A5-SD, A5-PA, B1 | INTEGRATE |
| A5-SD | — | A5-CC, A5-PA, B1 | INTEGRATE |
| A5-PA | — | A5-CC, A5-SD, B1 | INTEGRATE |
| B1 | — | A5-CC, A5-SD, A5-PA | INTEGRATE, B2/B3 (future) |
| INTEGRATE | all four report | — | — |

### 2.2 File ownership register

| Item | Files it may create or modify (exhaustive) |
|---|---|
| **A5-CC** | `scripts/pil_components.py` (new), `tests/test_components.py` (new), `calibration/components_gate.py` (new), `runs/2026-08-2X-components-discrimination/**` (new bundle) |
| **A5-SD** | `scripts/pil_silhouette.py` (new), `tests/test_silhouette.py` (new), `calibration/silhouette_gate.py` (new), `runs/2026-08-2X-silhouette-discrimination/**` (new bundle) |
| **A5-PA** | `scripts/pil_alignment.py` (new), `tests/test_alignment.py` (new), `calibration/alignment_gate.py` (new), `runs/2026-08-2X-alignment-discrimination/**` (new bundle) |
| **B1** | `scripts/pil_blender_mesh.py` (new), `tests/test_blender_mesh.py` (new), `scripts/pil_contract_verdict.py` (**only** the `geometry.*` predicate block, the `MEASURABLE`/refused-predicate registry entries, and `GEOMETRY_REFUSAL`'s scope — nothing else in that file), `runs/2026-08-2X-blender-mesh-validation/**` (new bundle; the swordsman corpus itself stays external at `C:\Projects\tms-heim\...` and is never copied into this repo) |
| **INTEGRATE** | `README.md`, `skills/image-measurement/SKILL.md`, `docs/index.md`, `docs/phase3-scope.md` (status table only), `docs/phase3-build-plan.md` (status updates only) |

**Files no implementer item may touch:** `scripts/pil_common.py`,
`scripts/pil_palette_diff.py`, `scripts/pil_structure_diff.py`,
`scripts/pil_region.py`, `scripts/pil_color.py`, `scripts/pil_crop.py`,
`scripts/pil_annotate.py`, `scripts/pil_image_info.py`,
`calibration/scenes.py`, `calibration/perturb.py`, `calibration/derive.py`,
`calibration/measure.py`, `calibration/run_all.py`,
`calibration/distill_detection_limits.py`, `calibration/alpha_truth.py`,
`calibration/lch.py`, `calibration/validate_real.py`,
`scripts/detection_limits.json`, `tests/conftest.py`, and every existing test
file. If an item believes it needs to edit one of these, that is a design
escape and comes back to the coordinator rather than being done unilaterally.

### 2.3 Collision register

| File | Contending items | Resolution |
|---|---|---|
| `scripts/pil_contract_verdict.py` | B1 only | No collision — B1 is the sole writer, and no A5 item touches this file (see §1). |
| `README.md` / `SKILL.md` / `docs/index.md` | all four, indirectly (each wants to announce its own capability) | **Reserved for INTEGRATE.** No implementer item edits these; each reports its result (shipped/demoted, with numbers) back to the coordinator, who writes the doc update once, after all four have reported. |
| `calibration/derive.py`'s `bootstrap_quantile`/`alpha_for` | A5-CC, A5-SD, A5-PA (all import) | **Read-only import by all three; the file itself is on the no-touch list.** A candidate that believes it needs a change to `bootstrap_quantile` files it back to the coordinator rather than editing it. |
| `calibration/scenes.py`'s `blob_object`/`multipart_object` | A5-CC (imports as no-op-perturbation base), A5-SD (imports as mask source) | **Read-only import.** Neither may add a `count` parameter or otherwise modify these builders. A5-CC's "a component was added/removed" corpus half is necessarily new logic — see §3.1's note on this — and lives entirely in `calibration/components_gate.py`, not in `scenes.py`. |

---

## 3. Per-item specification

Each item follows the existing tools' conventions exactly: JSON on stdout with
`tool`, `version`, `parameters`, `interpretation_limits`; byte-identical
output across repeated runs; exit 2 with **empty stdout** and no partial file
on every rejection path; `TOOL_VERSION = "0.4.0"`; determinism proven by a
test that actually re-runs the tool, never by comparing a value to itself.

### 3.1 A5-CC — `scripts/pil_components.py`, connected-component instance counting

**Deliverables:**

- A pure-numpy two-pass union-find connected-component labeller over the
  native-resolution foreground mask (`pil_common.foreground_mask`). `scipy.ndimage.label`
  is barred — Pillow and numpy are the only runtime dependencies
  (`README.md#requirements`, `docs/phase3-scope.md` WP A5).
- Per-blob: pixel area, area as a fraction of frame, centroid (frame-fractional),
  tight bbox (both pixel and frame-fractional).
- A minimum-blob-area floor (in pixels, and its frame-fraction equivalent),
  below which a blob is treated as noise and excluded — anti-aliasing residue
  and single stray pixels must not inflate the count. The floor's value comes
  from the gate below, not a guess.
- `--foreground` mask-quality flags reused verbatim from `pil_common`
  (`foreground_too_small`, `foreground_mask_empty`): an empty or too-small
  mask returns zero components with the flag set, never a fabricated count
  and never a crash.

**Calibration gate (`calibration/components_gate.py`):**

- **No-op control set**: `blob_object` (1 true component) and
  `multipart_object` (3 true components) from `calibration/scenes.py`, each
  run through `calibration/perturb.py`'s existing no-op-class operations
  (`rescale_roundtrip`, `jpeg_reencode` at high quality, small `gaussian_blur`,
  small `add_noise`) — the count the tool reports must not move.
- **Real-change set**: this needs component-count ground truth
  `calibration/scenes.py` does not parametrize (`multipart_object` is fixed at
  exactly three parts with no `count` argument, and modifying it is on the
  no-touch list — §2.3). `calibration/components_gate.py` therefore draws its
  own minimal component-count fixtures directly with `PIL.ImageDraw` (adding,
  removing, splitting, or merging a rounded-rectangle blob against the same
  `PREVIEW_BG`/palette conventions `blob_object`/`multipart_object` already
  use, so the fixtures are visually consistent with the rest of the corpus
  without editing the frozen file). State this explicitly in the gate
  bundle's README as a deliberate scope boundary, not a hidden reimplementation.
- Derive the minimum reliably-detected blob area (as a frame fraction) via
  `bootstrap_quantile` on the no-op set's largest spuriously-counted blob
  area, at `alpha_for(n)`. Publish `n`, `alpha`, the derived floor, and its CI.
- **Demotion path**: if the floor derived from the no-op set is not smaller
  than a blob size anyone plausibly cares about (i.e., the noise floor and the
  useful signal overlap), report that finding with numbers and do not ship —
  same standard as `base_palette_distance`/`entropy_delta`'s prior demotions.

**Acceptance criteria:** byte-determinism; the two-pass union-find reproduces
a hand-countable synthetic fixture (a fixed small grid image with a known
component count, asserted against the literal integer, not a threshold);
`multipart_object` reports exactly 3 components above the derived floor; an
empty mask reports 0 components and the empty-mask flag, never a crash;
`scripts/pil_components.py` contains no `scipy` import (grep-checked, mirroring
D7's style).

**How a plausible implementation could be wrong:** a two-pass union-find that
handles 4-connectivity when 8-connectivity was intended (or vice versa) will
silently miscount on diagonal-touching blobs — the hand-countable fixture must
include a diagonal-adjacency case specifically, not just axis-aligned blobs.

### 3.2 A5-SD — `scripts/pil_silhouette.py`, silhouette shape descriptors

**Deliverables:** fill ratio (mask area / bbox area), perimeter²/area, and an
orientation histogram, all computed on the working-resolution foreground mask
(§1). **Gated harder than the rest** per `docs/phase3-handoff.md` §5: every
descriptor must degrade to "not reported" (not a fabricated number) under the
existing mask-quality flags `foreground_too_small`, `foreground_mask_empty`,
`background_dominant` — reuse these flags from `pil_common`, do not
reimplement the thresholds that produce them.

**Calibration gate (`calibration/silhouette_gate.py`):** same no-op-control
methodology as §3.1, but the *real-change* question is different and harder:
does a shape-descriptor delta usefully disagree with vision on a genuine
shape change, without also firing on pose/rotation/resampling noise? Test
against `blob_object` under `perturb.py`'s `translate` and a range of
rotations if the tool supports them, versus a genuinely reshaped fixture
(aspect ratio changed, or a notch cut into the blob). Publish the response
curve and detection limit exactly as §3.1.

**This candidate has a documented escape hatch, use it honestly.**
`docs/phase3-scope.md` open question 3 and `docs/phase3-handoff.md` §5 both
flag that the field trial *deliberately declined* general proportion
measurement as too easily misleading outside single-figure-on-flat-backdrop
cases, and both name "components and alignment profiles only, leaving
silhouette shape to vision" as an acceptable outcome. **If the gate does not
produce a detection limit meaningfully smaller than pose/rotation noise for
any of the three descriptors, demote that descriptor (or all three) and say
so with numbers** — do not ship a descriptor whose noise floor swallows its
signal just because code exists for it.

**Acceptance criteria:** byte-determinism; each descriptor's own
`interpretation_limits` entry names which mask-quality flag suppresses it;
fill ratio on a fixture with known geometry (e.g. a filled circle inscribed
in a known bbox) matches the closed-form expectation within a stated
tolerance; a `background_dominant`-flagged input reports all three
descriptors as `null` with the flag cited, never a number.

### 3.3 A5-PA — `scripts/pil_alignment.py`, projection-profile alignment + WCAG contrast

**Deliverables:**

- Edge-map row and column sums (reuse `pil_common.edge_magnitude`, do not
  reimplement it) yielding baseline positions, margins, and an
  "aligned within N px" verdict between two images' profiles.
- WCAG 2.x contrast-ratio arithmetic: relative luminance from sRGB per the
  published formula, contrast ratio `(L1+0.05)/(L2+0.05)`, for a caller-named
  pair of regions or the two images' dominant foreground/background.
  **Verified against the standard's published worked examples**, the same
  way `pil_color.py`'s CIEDE2000 is verified against all 34 Sharma reference
  values — this half needs no discrimination gate, since it is a fixed public
  formula, not a judgment call. State this distinction explicitly in the
  tool's own docstring so a reader does not assume both halves were
  statistically calibrated.

**Calibration gate (`calibration/alignment_gate.py`), alignment half only:**
derive the sub-pixel jitter noise floor of baseline/margin detection under
`perturb.py`'s no-op set (particularly `rescale_roundtrip` and small
`translate`), publish it in pixels as the detection limit for "aligned within
N px", and set the default N no smaller than that floor. The contrast half
needs a verification bundle (worked-example reproduction), not a gate bundle.

**Acceptance criteria:** byte-determinism; WCAG contrast ratio reproduces
every published worked example within the tolerance CIEDE2000's test uses;
projection-profile margins on a fixture pre-cropped to known coordinates match
the expected pixel positions exactly; a pixel-tolerance verdict between two
different-resolution images is refused (not silently computed) per §1's scale
note, with the fractional margin still reported.

### 3.4 B1 — `scripts/pil_blender_mesh.py`, Blender mesh statistics

**Architecture:** a subprocess wrapper. `bpy` cannot be imported outside
Blender's own bundled interpreter, so `pil_blender_mesh.py` (running under the
repo's Python 3.11+/uv venv, per every other tool) invokes
`blender.exe --background <scene.blend> --python <embedded-script>` as a
subprocess and parses the embedded script's stdout, the same "invoke as a
CLI, parse output" shape `calibration/measure.py` already uses for this
repo's own tools. Blender's executable path is not assumed to be on `PATH`;
accept `--blender-executable` with a documented default search (including the
Windows install location) and fail cleanly — exit 2, empty stdout — with a
named reason when Blender cannot be found, which is exactly the
"absence of Blender is a clean UNMEASURABLE, never an error" contract from
`docs/phase3-scope.md` WP B1, expressed at the CLI-tool layer.

**Deliverables:** per mesh object — polygon count, vertex count, material
slots — and scene-level bounding dimensions, all read from Blender's own
scene data, never from pixels (`README.md#scope-limit-these-tools-do-not-measure-geometry`
is a hard boundary this tool exists to satisfy, not cross). JSON on stdout in
the standard shape.

**`pil_contract_verdict.py` change (the one shared-file edit this build
makes):** when the caller supplies scene-stats JSON (from this tool) for one
or both sides of a pair, `geometry.poly_count.decrease` (and the analogous
predicates already named in the refusal string) resolve to `SATISFIED` /
`VIOLATED` by comparing the supplied counts. Without scene-stats JSON,
`geometry.*` **stays UNMEASURABLE exactly as it refuses today** — this
refusal is load-bearing and tested (`docs/phase3-handoff.md` §6); B1 adds a
new path into measurability, it does not weaken the existing refusal as a
default.

**Test corpus — external, never committed, tests skip cleanly when absent:**
mirror the `PIL_AGENT_REFERENCE_IMAGE` pattern in `tests/conftest.py` exactly
— a `PIL_AGENT_BLENDER_CORPUS` (or similar) environment variable pointing at
`C:\Projects\tms-heim\art\skeleton-crusaders\swordsman`, tests that read
`parts.json` ground truth from that corpus skip when the variable or Blender
itself is absent, and every claim this build makes about the corpus (which
part, which revision, which numbers) is verified against the real
`parts.json` files at implementation time, not copied from
`docs/phase3-handoff.md` §6 without checking — that document is a briefing,
not verified ground truth in itself. Label any pair fed in reverse order
(whole-model decreases are only available backwards, per handoff §6) as
reverse-ordered in the run bundle, explicitly.

**Acceptance criteria:** `geometry.*` resolves to a real verdict with scene
data supplied and stays `UNMEASURABLE` without it (both cases tested); a
missing Blender executable is a clean exit 2 with empty stdout, not a
traceback; the tool's own bundle in `runs/2026-08-2X-blender-mesh-validation/`
reproduces the corpus's genuine polycount decrease as `VIOLATED` (or
`SATISFIED` for an invariant framed the other way — pick one framing and
state it), the no-change control part as `SATISFIED` on an invariant, and the
round-trip pair likewise — with the real numbers quoted, not summarized.

**How a plausible implementation could be wrong:** Blender's Python API
differs across major versions in exactly the kind of way that breaks a script
written against stale documentation (attribute renames between the 2.8x/3.x/4.x/5.x
API lines) — verify the embedded script's exact calls against Blender 5.1
specifically (the version installed here), not against remembered API from an
older version.

---

## 4. Definition of DONE

Gradeable without asking the coordinator. Every line is a command, a file, or
a number.

**D1 — the suite.** `uv run python -m pytest` reports 0 failed, 0 errors, 0
xpassed, 0 xfailed for every test that does not require an absent external
resource (Blender, the swordsman corpus, `PIL_AGENT_REFERENCE_IMAGE`) — those
report as skipped, with the skip reason named, on a machine lacking them, but
**not** on this machine, where Blender and the corpus are both present, so
their tests must actually run and pass here.

**D2 — nothing outside the ownership register was touched.**
`git log --oneline --name-only` on this branch shows no file modified by an
item that does not own it in §2.2. In particular: `scripts/pil_common.py`,
`scripts/pil_palette_diff.py`, `scripts/pil_structure_diff.py`, and every
`calibration/*.py` file except the four new `*_gate.py` scripts are
byte-identical to `main`.

**D3 — every shipped candidate's calibration is graded against its own
published numbers, not a vibe.** Each `runs/2026-08-2X-*-discrimination/`
bundle that backs a shipped tool states `n`, `alpha`, and the derived
threshold/floor with its bootstrap CI, and the tool's own
`interpretation_limits` cites that bundle by path.

**D4 — demotion is recorded, not silently absorbed.** For any A5 candidate
that does not ship, `docs/index.md`'s open-items list states which candidate,
why (with the noise-floor-vs-signal numbers that decided it), and points at
the gate bundle that established it — the same standard `base_palette_distance`
and `entropy_delta`'s prior demotions were held to.

**D5 — rejection hygiene holds.** Every new tool exits 2 with byte-empty
stdout and writes no partial file on every documented rejection path,
verified by a test that actually asserts on `proc.stdout == ""`, not just on
the return code.

**D6 — `TOOL_VERSION` matches the manifest.** `test_every_shipped_tool_declares_the_manifest_version`
passes with every new tool included in its glob discovery (it is glob-driven
over `scripts/pil_*.py`, so this is automatic once a new tool exists — verify
it actually ran against the new files by checking the test's collected count
grew).

**D7 — B1's contract-verdict change is narrow.** `git diff main -- scripts/pil_contract_verdict.py`
touches only the `geometry.*` predicate resolution path and the refused/measurable
registry — no other predicate's behaviour changes, verified by re-running
every existing `tests/test_contract_verdict.py` case unmodified and green.

**D8 — B1's corpus claims are verified, not quoted from the handoff doc.**
The run bundle's numbers for `SKS_Garment_Tabard_01`, the no-change control,
the round-trip pair, and the forward-increase case are each traced to a real
`parts.json` (or `.blend`, for the round-trip) path and quoted exactly, with
any discrepancy from `docs/phase3-handoff.md` §6's claims called out rather
than silently corrected in place.

**D9 — the residuals are published.** Whatever did not fully land in this
build — B2/B3 not started, a demoted A5 candidate, a corpus number that
didn't match the handoff doc's claim, a Blender API quirk that limited scope
— is written down in `docs/index.md` with numbers, per
`docs/phase3-handoff.md` §9's D12 standard. A green suite with an unstated gap
is not done.

---

## 5. Orchestration mechanics

Per `docs/phase3-handoff.md` §8 and prior-session memory, followed exactly:

- Launch every implementer via the Puppetmaster **CLI**, not the MCP
  `start_*` tools (they have silently dropped launches before while `doctor`
  stayed green). Pass `--allow-dirty` on both subcommands.
- Treat a launch as real only once job registration appears — it can lag
  several minutes. Never relaunch something that merely *looks* stalled
  without checking both job registration and process ancestry.
- Workers report and stop. The coordinator has the only hand on git: workers
  do not merge, do not touch files outside their own ownership row, and do
  not decide what ships — they report their gate result and evidence, and the
  coordinator (with an independent critic pass) decides.
- **Independent critic per item, before it counts as done.** The critic
  executes the tool and its failure paths, re-runs it for determinism,
  reproduces the reported numbers rather than accepting them, and — for A5-CC
  in particular — is handed a synthetic image with a known component count
  and asked to say what the tool reports, blind to the implementation.
  `docs/phase3-handoff.md` §7: every 0.4.0 tool failed its first critic: this
  build should not expect its first round to be the last.
