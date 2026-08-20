# Phase 3 handoff — what remains, and how this repository expects to be worked

Written 2026-08-20, immediately after 0.4.0 shipped. You are picking up phase 3
with **Track A items 1–4 already landed and released**. This document is the
briefing for the rest.

Read it before `docs/phase3-scope.md`, then read that in full. This one tells
you *how* to work here; that one tells you *what* the remaining items are.

---

## 1. Where the work actually stands

| Item | State |
|---|---|
| Track A1 `pil_crop` | **Shipped 0.4.0.** Critic PASS. |
| Track A2 `pil_annotate` | **Shipped 0.4.0.** Critic PASS, legibility read-back verified. |
| Track A3 `pil_image_info` | **Shipped 0.4.0.** Took three fix rounds. |
| Track A4 `--region` | **Shipped 0.4.0** on both diff tools. |
| **Track A5 — discrimination-gated metrics** | **NOT STARTED.** Yours. |
| **Track B1 — Blender mesh statistics** | **NOT STARTED.** Yours. |
| **Track B2 — matched-view render orchestration** | **NOT STARTED.** Yours. |
| **Track B3 — character-sheet revision loop** | **NOT STARTED.** Yours, and it composes B1+B2 with the existing contract layer. |

Also unstarted and **deliberately deferred**, not forgotten: FFT
periodicity/tiling detection, blur/noise/compression quality statistics, and
corpus-scale hash indexing. `phase3-scope.md` records the reason — no field
trial has demanded them. Do not build them because they sound useful.

---

## 2. The one rule that governs everything here

> **Never claim more than you measured.**

Every convention below is a consequence of that rule. This repository has
repeatedly deleted or refused its own work for violating it, and reviewers here
treat an unearned claim as more serious than a bug — a bug is wrong, an unearned
claim makes every other claim unverifiable.

Concretely, in this codebase that means:

- A docstring may not describe behaviour the code does not have. Two shipped
  docstrings were found asserting things measurement falsified; both were
  treated as defects.
- An `interpretation_limits` entry may not be stronger than its evidence. One
  was *widened* beyond its spec into a false claim and had to be narrowed; one
  was false in the **spec itself** and the spec was corrected.
- A capability the harness provided is not a capability the plugin has. See
  `runs/2026-08-18-skeleton-warrior-asset-review/README.md`, which explicitly
  refuses to credit the plugin with region-cutting its harness did.
- If you could not verify something, **say so**. An honest "I could not run
  this" is fine and costs nothing. One agent reported "verification completed
  successfully" on a suite that could not even import; that cost a whole extra
  round and, worse, made its other claims worthless.
- When a measurement does not support a conclusion, report the measurement and
  say the conclusion is unsupported. `phase2-scope.md` §11 and the calibration
  bundles are full of open questions left explicitly open with the measurement
  that would close each one. That is the house style, not a failure to finish.

---

## 3. Non-negotiable technical constraints

- **Pillow and numpy only.** No scipy, no OpenCV, no scikit-image. Track A5's
  connected-component labelling must therefore be a two-pass union-find in
  numpy. This is checked; adding a dependency will be rejected.
- **Determinism.** Identical inputs produce byte-identical output, JSON and
  images alike. Sort keys, pin float formatting, seed every RNG explicitly
  (`numpy.random.Generator(PCG64(seed))` or the conftest LCG — never `random`
  global state). Assert it in a test that actually re-runs the tool; comparing a
  value to itself is "determinism theatre" and will be called out.
- **Rejection hygiene.** Every rejection path exits non-zero with **byte-empty
  stdout** and leaves no partial file. A malformed request must never produce
  half a JSON document. One exception exists and is deliberate:
  `pil_image_info` reports per-file (`readable: false` plus a reason, exit 1 if
  any file failed) so one bad file in a batch of thirty does not destroy the
  other twenty-nine.
- **Every payload carries `tool`, `version`, `parameters`, `interpretation_limits`.**
- **`TOOL_VERSION` must match the manifests.** This is now guarded by a
  glob-driven test in `tests/test_packaging_conformance.py` that covers any new
  `scripts/pil_*.py` automatically. Do not bump versions yourself — the release
  step owns that, across eleven places.
- **Scale invariance.** Structural statistics run on a fixed-size working copy
  over a *fractional* grid. If you add a metric, decide explicitly whether it is
  scale-invariant and say so in its limits.

---

## 4. What to reuse rather than reinvent

| Need | Use | Do not |
|---|---|---|
| Parse/resolve a fractional bbox | `scripts/pil_region.py` — frozen contract, 33 tests | write your own rounding |
| Foreground mask | `pil_common.foreground_mask` (alpha, else border-median OKLab) | invent a third definition |
| Alpha-weighted ground truth | `calibration/alpha_truth.py` | recompute it |
| Perceptual colour distance | `scripts/pil_color.py` CIEDE2000 (D65, verified against all 34 Sharma values) | Pillow's `convert("LAB")` — D50 and 8-bit |
| Deterministic test scenes | `calibration/scenes.py`, `tests/conftest.py` | commit binary fixtures |
| Thresholds + detection limits | `scripts/detection_limits.json` | hardcode a literal |

**Threshold selection is a trap.** Foreground thresholds are split by mask
source and differ by more than an order of magnitude:
`threshold_foreground_alpha` (0.997 for luminance) applies when the file carried
real transparency; `..._estimate` (34.166) when the mask came from the
border-median colour; `..._estimate_no_placement` (1.452) is the same derivation
with the resampling family excluded. Picking the wrong one by 34× is the easiest
mistake available in this codebase right now.

---

## 5. Track A5 specifically — the gate is real

A5 is *discrimination-gated*, and that gate has teeth. Phase 1 measured **11
metrics and kept 4**; the discrimination matrix in
`runs/2026-08-18-pil-agent-plugin-phase1/` shows one metric answering its
question **backwards**. So:

> A new metric earns its place only by **usefully disagreeing with vision**, and
> by passing the WP2 methodology in `docs/phase2-scope.md` §WP2 — Neyman–Pearson
> against a no-change control set, threshold as the bootstrap-CI upper bound on
> Q(1−α), with `n`, α and a **published detection limit** recorded.

Candidates, in the scope's order: connected-component instance counting with
per-blob area/centroid/bbox; silhouette shape descriptors on the 0.4.0
foreground mask (fill ratio, perimeter²/area, orientation histogram); projection
profile alignment for UI review; WCAG contrast ratios.

Two cautions the scope records:

- Shape descriptors must stay gated on mask-quality flags. The field trial
  **deliberately declined** general proportion measurement because it worked
  only on single figures on flat backdrops and would mislead elsewhere.
- **Demotion is an acceptable outcome.** If a metric's detection limit is worse
  than the smallest change anyone cares about, say so and do not ship it. That
  has already happened twice here (`base_palette_distance`, `entropy_delta`).

---

## 6. Track B — the part that needs a Blender scene

B1 exists to turn `geometry.*` predicates from `UNMEASURABLE` into real
verdicts. Until then, `pil_contract_verdict` **must keep refusing them** — that
refusal is load-bearing and is tested. Do not weaken it as a convenience.

**You already have an acceptance corpus**, and it is better than anything you
would build. `C:\Projects\tms-heim\art\skeleton-crusaders\swordsman\runs\`
contains a real asset's iteration history with per-part topology recorded in
`parts.json` files and matched-view renders on both sides of each operation.
The pairs worth using were surveyed in this session's conversation:

- **Genuine forward polycount decrease:** `SKS_Garment_Tabard_01` 1350 → 787
  polys (−42%) between `rev2-lower-20260818` and `rev3-faceting-20260818`, while
  every other part *increased* and the whole model went 6,643 → 14,033. That
  combination is the discriminating case.
- **A second decrease:** the same part 787 → 564 across the torso-depth rebuild,
  with `pre-rebuild-state.json` / `post-rebuild-state.json` recording both.
- **No-change control:** `SKS_Garment_UnderMailSkirt_01` sits at exactly 362
  polys across all three revisions.
- **Exact round-trip control:** `source/*.blend` vs `staging/roundtrip/*.blend`,
  which the asset's README records as topology-preserving.
- **Forward increases** (must read VIOLATED): MailSleeve 352 → 1132 → 3040.

Note the honest framing: whole-model *decreases* are only available by feeding
the revisions **backwards**. Label them as reverse-ordered in any ledger.

B2 and B3 are only worth starting once B1 can answer a geometry question.

---

## 7. How work is expected to be reviewed here

This is how 0.4.0 was built, and it is why it shipped what it shipped.

1. **Design before code**, with acceptance criteria that are objectively
   gradeable — a number, a command, a test name, a payload field. "Well
   documented" is not a criterion; "every public function's docstring states why
   it exists" is.
2. **An independent critic grades by execution, not by reading.** It runs the
   tool, runs the failure paths, re-runs for determinism, builds adversarial
   fixtures, and reproduces the implementer's numbers rather than accepting
   them. A reproduced number that differs from a reported one is itself a
   finding.
3. **Loop until the critic passes.** `pil_image_info` took three rounds.
4. **For anything visual, a fresh agent must look at the output** — given only
   the image, forbidden from reading the code or the JSON. This is not
   ceremony. Against pre-fix renders, blind readers reported numerals as
   *clipped* on glyphs that were geometrically whole and inside the frame. **No
   assertion in this tree could have caught that.**

Every single tool in 0.4.0 **failed its first critic**, and not one of those
failures was visible in a green test suite: a corrupt EXIF block indistinguish-
able from an empty one; a 16-bit image reduced from 400 distinct values to 3
while its docstring claimed exactness; numerals no reader could transcribe; an
RGBA region measuring differently from identical pixels pre-cropped. Assume your
work has an equivalent defect and go looking for it.

**Grade the case selection, not just the assertions.** The `--region` gate was
rigorous — correct strip list, real binary invoked, full payload compared — and
still missed a bug, because none of its cases passed `--foreground`. A test can
be thorough and still be looking the wrong way.

---

## 8. If you orchestrate multiple agents

Hard-won, from three separate incidents in one build:

- **One writer per file, ever, and you count as a writer.** Give each agent an
  exclusive writable set and check it against every in-flight agent's. Shared
  files mean the items must **serialize**, not fan out. New-file-only work is
  the safe unit of parallelism.
- **Assign ownership once and do not reverse it.** Reversing caused a deadlock
  where both parties disclaimed the same file.
- **Never kill a launcher that looks stalled** without checking both job
  registration and process ancestry. Registration has lagged ~8 minutes; a
  premature relaunch produced a duplicate worker that overwrote another agent's
  files, recovered only by replaying its session transcript.
- **Agents report and stop; the coordinator commits.** One hand on git.
- Launch Puppetmaster jobs via the **CLI**, not the MCP `start_*` tools, which
  silently dropped 2 of 6 launches while `doctor` stayed green. Pass
  `--allow-dirty` on *both* subcommands; omitting it on `codex` fails instantly
  with `dirty_worktree` and the launcher still exits 0.

---

## 9. Definition of done for your work

Reuse §10 of `docs/aaa-build-plan.md` as the template — it is twelve criteria,
each a command, a file or a number. The two that matter most and are easiest to
skip:

- **D12 — publish residuals, do not bury them.** If a known imperfection
  survives, it goes in the bundle **with numbers**. 0.4.0 shipped a numeral that
  two readers flagged as low-confidence; the cause was measured, the proper fix
  was *named and deliberately not implemented* because it was unmeasured
  machinery, and all of that is written down.
- **D3 — grade against truth, not against a threshold.** A test that passes
  because its bound is enormous is vacuous. Where ground truth exists, assert
  against it with absolute literals.

Finally: **the run bundles under `runs/` are evidence, not working files.** Do
not regenerate one to make it agree with new code. If a change would alter a
published artefact, stop and say so — regenerating it makes the evidence
self-fulfilling. That nearly happened during 0.4.0 and was caught and reverted;
the reasoning is recorded in
`runs/2026-08-20-annotate-readback/README.md`.
