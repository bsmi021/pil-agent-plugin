# Phase 3 build plan — Track B2 (matched-view render orchestration) + Track B3 (the revision loop)

Status: **design only, awaiting first implementer round.** Nothing here has been
implemented. Author role: architect, following the same process as
[`docs/phase3-build-plan.md`](phase3-build-plan.md) (Track A5 + B1) — design
before code, gradeable acceptance criteria, one coordinator hand on git.

Target: this branch (`feat/phase3-track-b2-b3`). No manifest version bump — new
tools declare `TOOL_VERSION = "0.4.0"`, matching the current
`plugin.json`/`.claude-plugin/plugin.json`, exactly as Track A5/B1 did.

---

## 1. What is actually being built, and the one decision that shapes everything

Track B1 shipped: `pil_blender_mesh.py` reads real scene geometry, and
`pil_contract_verdict.py`'s `geometry.*` predicates resolve when scene stats
are supplied. B2 and B3 are the two remaining phase-3 items, and B1's landing
unblocks both.

**B2 — matched-view render orchestration.** Render front, side and back from a
`.blend` scene, register each against a caller-supplied reference image for
that same view, and report per-view 1:1 comparison — refusing a view rather
than warping the render or the metrics to force a match. Per
`docs/phase3-scope.md`'s WP B2, this is motivated by a real production failure:
the field trial rendered a T-pose against an A-pose concept view and got
`structural_similarity 0.900` alongside `aspect_ratio_mismatch` and
`resolution_mismatch` — numbers that were *correctly discarded* because they
described framing and pose, not the model, and the trial covered only one
front view, leaving back and profile unverified.

**B3 — the revision loop.** Compose B2's matched-view pairs into one
`pil_contract_verdict --pairs` invocation, so a whole character-sheet review is
a single contract evaluated over N registered view pairs, worst-case
aggregated exactly as WP4 already guarantees for any other multi-pair
comparison. B3 does not reimplement aggregation — it wires B2's output into
the aggregation `pil_contract_verdict` already ships.

**The decision that shapes both items: B2 does not attempt to reproduce a
reference image's exact camera framing.** It renders a canonical, auto-framed
view (front/side/back, camera position derived from the scene's own bounding
box, exactly the pattern `pil_blender_mesh.py` already uses for bounding
dimensions) and leans on `pil_structure_diff --foreground`'s **existing,
already-tested** bbox registration to make position and scale comparable
between a render and a reference at different resolutions and framings. This
is not new machinery — it is the same masking/registration the field trial
itself used, and precisely what `docs/phase3-scope.md`'s WP A4 rationale calls
"the single highest-leverage thing in the harness." B2's own job is narrower
and mechanical: produce the three views, run the comparison, and **refuse**
(not silently approximate) when a view genuinely cannot be produced or matched
— an empty/degenerate mesh for that facing, a reference image so distorted
`--foreground` itself flags `background_dominant` or an empty mask, or a
caller-requested view with no supplied reference.

**Segmenting a multi-view reference sheet is explicitly not this tool's job**
(`docs/phase3-scope.md`, "Agreed approach... rather than segmenting a sheet
heuristically"). The caller supplies one reference image per view, already
separated. See §5 for how this build's own test corpus satisfies that without
committing derived images anywhere.

---

## 2. The render-determinism question, settled in writing before any code

`docs/index.md`'s open items (added when B1 shipped) named this explicitly:
*"Blender renders are not byte-deterministic cross-machine, and this repo's
core contract is byte determinism, so B2's determinism claim needs to be
scoped explicitly in writing before code."* Here is that scoping:

- **Render engine: Blender Workbench (`BLENDER_WORKBENCH`), not Cycles or
  EEVEE.** Workbench is rasterized, not path-traced — no Monte Carlo sampling,
  no denoiser, nothing seeded-but-still-nondeterministic-in-practice across
  driver/GPU versions. This matches the *existing* production precedent
  already in the tms-heim corpus: `runs/compare-reference-2026-08-16/wb_*.png`
  in the brute asset's own history was rendered exactly this way, by hand,
  before this tool existed.
- **CPU rendering only** (`scene.render.engine` on Workbench defaults to CPU
  rasterization; explicitly do not opt into GPU compute for this render
  engine). Removes GPU-driver-version variance from the determinism question
  entirely.
- **The claim actually made and tested: byte-identical PNG output across two
  renders of the same `.blend` scene, same view, same machine, same Blender
  install.** This is testable and this build tests it (§4.1 acceptance
  criteria). **The claim NOT made: cross-machine byte-identical rendering.**
  Different Blender builds/OS/font-rendering-for-any-UI-overlay could
  legitimately differ. `interpretation_limits` on every B2 payload must state
  this distinction explicitly — "render determinism is same-machine,
  same-install; comparison metrics on a fixed pair of images remain fully
  deterministic everywhere, as they already are for every other tool in this
  repository."
- **Consequence for testing:** the *comparison metrics* (from
  `pil_structure_diff`) are deterministic and portable the moment two images
  exist, exactly like every other tool here — that part of the byte-determinism
  contract is untouched. What's scoped down is only the *render* step that
  produces one side of the pair.

---

## 3. Dependency graph and file ownership register

### 3.1 Graph

```
        B1 (shipped)
         |
         v
   B2  pil_blender_render.py   (new files only)
         |
         v
   B3  pil_character_sheet_review.py   (new files only, composes B1+B2+existing
                                          pil_contract_verdict --pairs)
         |
         v
   INTEGRATE (coordinator only) — README.md, SKILL.md, docs/index.md,
                                    docs/phase3-scope.md status table
```

**Sequential, not parallel.** Unlike A5's three independent candidates, B3
needs B2's real, working output to test against — this is stated in
`docs/phase3-scope.md` itself ("B2 and B3 are only worth starting once B1 can
answer a geometry question" / "B3 ... composes all of the above"). Do not
launch B3 until B2 has passed its own critic round.

### 3.2 File ownership register

| Item | Files it may create or modify (exhaustive) |
|---|---|
| **B2** | `scripts/pil_blender_render.py` (new), `tests/test_blender_render.py` (new), `runs/2026-08-2X-blender-render-validation/**` (new bundle) |
| **B3** | `scripts/pil_character_sheet_review.py` (new), `tests/test_character_sheet_review.py` (new), `runs/2026-08-2X-character-sheet-loop/**` (new bundle) |
| **INTEGRATE** | `README.md`, `skills/image-measurement/SKILL.md`, `docs/index.md`, `docs/phase3-scope.md` (status table only), `docs/phase3-b2-b3-build-plan.md` (status updates only) |

**Files neither item may touch:** everything under `scripts/` and
`calibration/` that already exists, `tests/conftest.py`, every existing test
file, `scripts/pil_contract_verdict.py` (B3 *invokes* it via `--pairs`, exactly
as any external caller would — it does not edit it), and any manifest. Both
items are new-file-only, so there is no collision register beyond "B3 must not
start before B2 lands," which is a sequencing rule, not a file conflict.

---

## 4. Per-item specification

Both follow every existing tool's shape: JSON on stdout with `tool`, `version`,
`parameters`, `interpretation_limits`; byte-identical output across repeated
runs *given the same input images* (§2 scopes what "repeated runs" means for
the render step itself); exit 2 with **empty stdout** and no partial file on
every rejection path; `TOOL_VERSION = "0.4.0"`.

### 4.1 B2 — `scripts/pil_blender_render.py`

**CLI surface:**

```
pil_blender_render.py <scene.blend> --view {front,side,back} --out render.png
                       [--blender-executable PATH] [--resolution 1024]
                       [--reference REF.png]   # optional: also runs the
                                                # pil_structure_diff --foreground
                                                # comparison and includes it
```

**Deliverables:**

- Headless Blender Workbench render of one named view (`front`/`side`/`back`),
  auto-framed from the scene's mesh bounding box. `pil_blender_mesh.py`
  (read-only — it is not in this item's file-ownership row) exposes
  `resolve_blender_executable(explicit)` for the executable-search convention
  and a sentinel-delimited subprocess-communication pattern
  (`_extract_probe_payload`/`probe_blend`) for talking to the embedded
  Blender-side script; import and reuse these directly rather than
  reinventing executable resolution. The bounding-box *computation* itself
  runs inside Blender's own embedded Python (a separate interpreter, invoked
  fresh per subprocess call) so it cannot be imported across that boundary —
  duplicate `gather()`'s bound-box corner logic in this tool's own embedded
  script, keeping the same world-space axis-aligned approach, rather than
  inventing a different one.
  View-to-camera-angle convention (document explicitly in the tool's own
  docstring and in `interpretation_limits`): **front** = camera on −Y looking
  +Y (or whatever this scene's existing convention is — verify against the
  brute/swordsman corpus's own established front-facing orientation rather
  than assuming; do not guess), **side** = camera on +X or −X rotated 90°
  from front (pick one, document which, and state it is the caller's
  responsibility to know which physical side their reference image shows),
  **back** = camera opposite front. Orthographic camera, framed so the full
  bounding box fits with a documented margin (e.g. 10%).
  Blender's executable path search mirrors `pil_blender_mesh.py` exactly
  (`--blender-executable`, default search including
  `C:/Program Files/Blender Foundation/Blender 5.1/blender.exe`); missing
  Blender is a clean exit 2 with a named reason, never a traceback.
- With `--reference`, additionally invoke `scripts/pil_structure_diff.py
  --foreground` (as a CLI subprocess — this is a production composition, not a
  calibration harness, but subprocess keeps this tool from silently drifting
  out of sync with the real CLI contract the same way B1 chose subprocess for
  Blender) between the fresh render and the reference image, and fold the
  result into the payload under a `comparison` key.
- **Refusal, not warping, when a view cannot be matched:** if the scene has no
  mesh geometry at all (empty/degenerate scene) for the requested view, or if
  `--reference` is supplied and the resulting `pil_structure_diff --foreground`
  call itself reports `foreground_mask_empty` or `foreground_too_small` on
  either side, do not report a fabricated or best-effort comparison — set
  `comparison.refused = true` with a `refused_reason` string, and every numeric
  comparison field is `null` (mirrors the `pil_contract_verdict.py`
  `UNMEASURABLE` refusal pattern already established, applied here at the
  render-orchestration layer instead of the verdict layer).
- `interpretation_limits` states, verbatim, the render-determinism scope from
  §2 above, plus the camera-convention statement, plus (when `--foreground`'s
  own `aspect_ratio_mismatch`/`resolution_mismatch` flags fire on a
  *non-refused* comparison) that those flags describe framing/pose, not the
  model — quoting the field trial's own finding
  (`docs/phase3-scope.md` WP B2) so a caller does not over-read them.

**Acceptance criteria:**

- Two renders of the same `(.blend, view)` pair on this machine are
  byte-identical PNGs (§2's tested claim).
- Rendering each of the three brute-corpus views (`front`, `side`, `back`)
  against the real reference crops derived from
  `C:\Projects\tms-heim\art\skeleton-crusaders\brute\references\skeletal-brute-tpose-turnaround-lowpoly-2026-08-15.png`
  (a 1536×1024 2×2 grid: top-left quadrant = front, top-right = side,
  bottom-left = back, bottom-right = three-quarter — crop these four
  quadrants **at test time**, in memory or to a pytest tempdir, never commit
  a derived crop into this repo or copy one back into `tms-heim`; this mirrors
  B1's "external corpus, read-only, never copied" rule exactly) produces a
  comparison that does **not** refuse and does **not** fire
  `aspect_ratio_mismatch`/`resolution_mismatch` (§4.1's whole point — prove the
  bbox-registration approach actually closes the gap the field trial's T-pose
  failure exposed, on a real character, not just a synthetic fixture).
- A synthetic degenerate-scene fixture (empty `.blend` or one with no mesh
  objects) produces a clean refusal, not a crash, for every view.
- A synthetic reference image that is mostly background (`--foreground`'s own
  `background_dominant`/`foreground_mask_empty` path) produces
  `comparison.refused = true`, not a fabricated similarity number.
- Missing Blender executable → exit 2, empty stdout, no traceback (same test
  pattern B1 already established; reuse its approach, do not reinvent).

**How a plausible implementation could be wrong:** guessing the front/back
camera axis instead of verifying it against how the corpus scenes are actually
authored will silently render a *back* view labelled `front` — the acceptance
criteria above only catch this because they compare against the *real* front
reference crop and would fail loudly if the axis were swapped, so do not skip
that specific check by substituting a synthetic fixture for convenience.

### 4.2 B3 — `scripts/pil_character_sheet_review.py`

**CLI surface:**

```
pil_character_sheet_review.py <scene.blend> --contract contract.json
    --view front:REF_FRONT.png --view side:REF_SIDE.png --view back:REF_BACK.png
    [--blender-executable PATH]
```

**Deliverables:**

- For each `--view NAME:REF_PATH`, invoke `pil_blender_render.py` (subprocess,
  same reasoning as B2's own choice) to render that view and compare it
  against `REF_PATH`.
- Build a `--pairs` manifest (the same shape `pil_contract_verdict.py --pairs`
  already consumes) from the resulting `(render, reference)` file pairs, and
  invoke `pil_contract_verdict.py --contract contract.json --pairs
  manifest.json --foreground` as a subprocess, capturing its aggregated
  verdict.
- **A view B2 refused must not silently drop out of the aggregate.** Per-view
  refusal propagates as `UNMEASURABLE` for every predicate on that pair, not
  an omitted row — WP4's worst-case rule (`UNMEASURABLE` beats `SATISFIED`)
  already handles this correctly *if* the refused pair is actually included in
  the manifest; the acceptance criteria below pin exactly this, because
  silently dropping a refused view from the manifest would be the easy,
  wrong implementation.
- Output: the full `pil_contract_verdict --pairs` payload, plus a
  `per_view_renders` block naming which rendered file backs which named view,
  for traceability back to `runs/2026-08-2X-character-sheet-loop/`.

**Acceptance criteria:**

- **WP4's rule holds across views, proven on real renders, not a synthetic
  aggregation-only unit test** (that unit test already exists in
  `tests/test_contract_verdict.py` — this test proves the *whole pipeline*
  preserves the property): three real view pairs from the brute corpus where
  two match well and one is deliberately substituted with a mismatched image
  (e.g. the `side` reference swapped for the `back` render) → the aggregate
  verdict for an invariant like `layout.composition_preserved` is `VIOLATED`,
  not averaged into a passing score by the two good views.
- All three real, correctly-matched brute views → the aggregate is
  `SATISFIED` for `layout.composition_preserved` (comparing the actual model
  against its own reference sheet should, unsurprisingly, satisfy an invariant
  that the model matches its own concept art — if it does not, that is a
  finding to report honestly, not a test to relax).
- A view whose reference file does not exist / cannot be read is refused at
  the B2 layer and still appears as an `UNMEASURABLE` entry in the aggregate,
  never silently excluded from the pair count.
- Byte-determinism of the final JSON payload given fixed input images (the
  render step's own determinism scope from §2 still applies underneath, but
  once images exist, this tool's output is fully deterministic like every
  other tool here).

**How a plausible implementation could be wrong:** building the `--pairs`
manifest by filtering out anything that looks like it might cause trouble
(a refused view, a missing file) is the single easiest way to make this
tool's tests pass for the wrong reason — it would make the aggregate look
healthier than it is. The acceptance criteria above exist specifically to
catch that.

---

## 5. Definition of DONE

**D1 — the suite.** `uv run python -m pytest` reports 0 failed, 0 errors for
every test not requiring an absent external resource (Blender, the brute
corpus) — those skip, with a named reason, on a machine lacking them, and
**actually run and pass** here, where both are present (same pattern B1
established via `PIL_AGENT_BLENDER_CORPUS`-style detection; reuse it for the
brute corpus path, do not invent a second convention).

**D2 — nothing outside the ownership register was touched.** `git log
--oneline --name-only` on this branch shows no file modified by an item that
does not own it in §3.2.

**D3 — the determinism claim is exactly as scoped in §2, no more.** No
docstring, comment, or `interpretation_limits` entry claims cross-machine
render determinism. The same-machine claim is backed by an actual test that
re-renders and byte-diffs, not a comparison to itself.

**D4 — refusal is real, not cosmetic.** Every refusal path (missing Blender,
degenerate scene, unmatchable reference) is verified by running it, exits/
behaves as specified, and — for B3 specifically — a refused view is proven
(by test) to still count in the aggregate rather than vanish from it.

**D5 — the field-trial regression is closed, on the real asset it was found
on.** The T-pose-vs-A-pose failure mode (`docs/phase3-scope.md` WP B2's
motivating example) does not need to be literally reproduced, but B2's
bbox-registration approach must be shown, on the real brute corpus, to avoid
the specific symptom: a real render compared against its real matching
reference does not spuriously fire `aspect_ratio_mismatch`/
`resolution_mismatch`.

**D6 — residuals are published, not buried.** Anything not fully resolved —
a camera-convention assumption that could not be verified against corpus
metadata, a view that could not be reliably rendered, a determinism edge case
— is stated with numbers in the run bundle and in `docs/index.md`, per this
repository's standing rule (`docs/phase3-handoff.md` §2, §9 D12).

---

## 6. Orchestration mechanics

Identical to `docs/phase3-build-plan.md` §5 and `docs/phase3-handoff.md` §8,
followed exactly: Puppetmaster CLI (not MCP `start_*`), `--allow-dirty`,
`--permission-mode bypassPermissions` (required for headless Bash execution —
`acceptEdits` alone does not unlock Bash in this environment, learned the hard
way during the A5/B1 build), launched via a backgrounded shell so a tool
timeout cannot kill the orchestrator mid-job, generous `--timeout-seconds`
given Blender startup cost. B2 first, coordinator-verified (own venv, own
test run, full-suite regression check) before B3 is launched. Coordinator
holds the only hand on git throughout: workers report and stop, the
coordinator commits after independently reproducing the reported numbers.
