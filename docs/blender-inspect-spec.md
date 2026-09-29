# blender-inspect: spec

Status: approved for the blender-inspect harness loop (2026-09-29).
Harness units: U1-U9, worked in order. IDs are stable; each row is one harness task.

## Why

Agents modeling in Blender cannot see depth or surface imperfections. The renders they get are
orthographic with flat light and carry no depth data, and `pil_blender_mesh` reports only counts,
materials and bounds. The scene already holds exact depth and topology; this work exposes it.

`blender-inspect` is a second plugin in this repository and marketplace, focused on Blender
scenes. `pil-agent-plugin` stays the image-measurement plugin and gains concept-image depth.

## Decisions (binding)

- **Name / location.** Plugin `blender-inspect` at `plugins/blender-inspect/`, listed as a second
  entry in `.claude-plugin/marketplace.json` (`source: "./plugins/blender-inspect"`). Tag format
  `blender-inspect--v<version>`. First version **0.1.0**. `pil-agent-plugin` moves to **0.10.0**.
- **Script naming.** New-plugin scripts are `plugins/blender-inspect/scripts/blender_*.py`. They
  never reuse a `pil_*` basename, so both scripts dirs can be on pytest's path without import
  collisions.
- **Test naming.** Test files under `plugins/blender-inspect/tests/` are named `test_bi_*.py`, and any
  helper modules there are prefixed `bi_`, so no basename collides with `tests/` under pytest's default
  import mode when both directories are collected in one run.
- **Host side is stdlib-only.** Wrapper scripts run under any Python 3.11+ and shell out to
  Blender. Heavy work (bmesh, numpy, EXR decoding) runs inside Blender's bundled Python.
- **Blender launch.** Always `--factory-startup --background` (user add-ons such as Tripo3D must
  not load), `--python-exit-code 1`, sentinel-wrapped JSON on stdout, the same refusal contract as
  `pil_blender_mesh` (exit 2, byte-empty stdout, one-line reason on stderr).
- **Migration: move + deprecated copies.** The canonical Blender tools live in `blender-inspect`.
  `pil-agent-plugin` keeps its `pil_blender_mesh`, `pil_blender_fit`, `pil_blender_render` and
  `pil_multiview_render` **unchanged in behaviour** for this release, marked deprecated in docs
  and in the capability catalog. They are removed in the next minor release (not in this work).
- **Payload compatibility.** `blender_mesh.py` keeps the `scene` object shape that
  `pil_contract_verdict --scene-stats` reads, so the verdict tool accepts output from either.
- **Concept depth.** Depth Anything V2 **Small** (Apache-2.0) exported to ONNX, caller-supplied and
  sha256-pinned exactly like `pil_embed`'s model (never bundled). It lives in `pil-agent-plugin`
  as `scripts/pil_depth.py`, using the existing `embedding` extra (onnxruntime).
- **Tests.** Blender-dependent tests `skipif` Blender is missing (CI has none); model-dependent
  tests `skipif` `PIL_AGENT_DEPTH_MODEL` is unset. Pure host-side logic is tested without either.
- **Evals.** `claude plugin eval` suite at `plugins/blender-inspect/evals/`. Runs use
  `--trust-plugin --no-publish --max-cost-usd 20 --threshold 0.8`, `--output-dir` under the unit's
  run folder (never inside the plugin tree), and `--scaffold --allow-tools Bash` only for the
  suite's own fixture-building scripts.

## Blender 5.2 facts (from the 2026-09-29 spike; do not rediscover)

- Render-engine enum is dynamic: set `scene.render.engine` to `BLENDER_WORKBENCH`,
  `BLENDER_EEVEE` or `CYCLES` directly; do not read the enum to find them.
- Workbench view-layer passes: Combined + **Depth only**. EEVEE: Depth, Normal, AO,
  Cryptomatte (object/material/asset), Position, Mist, Object Index.
- Multilayer EXR: `image_settings.media_type = "MULTI_LAYER_IMAGE"`, then
  `file_format = "OPEN_EXR_MULTILAYER"` (plain `OPEN_EXR_MULTILAYER` on the default media type
  raises). Save with `bpy.data.images["Render Result"].save_render(filepath)`.
- Workbench `shading.light` in {`STUDIO`, `MATCAP`, `FLAT`}; `cavity_type` in {`WORLD`, `SCREEN`,
  `BOTH`}; matcaps are `preferences.studio_lights` with `type == "MATCAP"` (e.g.
  `check_gradient.exr`, `basic_1.exr`...). Confirm they load under `--factory-startup`.
- bmesh checks that detected planted defects: `edge.is_manifold`/`is_boundary`/`is_wire`,
  loose verts, `bmesh.ops.find_doubles`, face-flip count via `recalc_face_normals` on a **copy**
  (count `min(flips, faces - flips)`), `BVHTree.FromBMesh(bm).overlap(self)` with pairs sharing a
  vertex filtered out.
- Spike scripts (local only): `runs/2026-09-29-blender-plugin-harness/spike/`.

---

## U1 - Plugin scaffold, packaging and CI for two plugins

| ID | Requirement |
|---|---|
| FR-PKG-01 | Create `plugins/blender-inspect/` with the same four-manifest set as the root plugin (portable `plugin.json`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json` with `interface` metadata, and an entry in the root `.claude-plugin/marketplace.json`), plus `README.md` (with a `## Status` section), `LICENSE`, `PRIVACY.md`, `assets/icon.svg`, `skills/`, `agents/`, `scripts/`, `tests/`, `evals/`. Version 0.1.0. |
| FR-PKG-02 | `plugins/blender-inspect/scripts/blender_common.py`: Blender executable discovery (`--blender-executable`, env `BLENDER_INSPECT_BLENDER`, the Windows 5.2 install path, then `blender` on PATH), headless launch with the flags in Decisions, sentinel parsing, and the exit-2 refusal contract. Every later `blender_*.py` uses it. `TOOL_VERSION` lives in each script, as in the root plugin. |
| FR-PKG-03 | Update the marketplace description (no longer "single-plugin"). Root `pyproject.toml` `testpaths` and `pythonpath` include `plugins/blender-inspect/tests` and `plugins/blender-inspect/scripts`. |
| FR-PKG-04 | Conformance tests cover both plugins: each plugin's four manifests agree on name/version, every shipped script's `TOOL_VERSION` equals its own plugin's version, marketplace entries match each plugin's manifest, skill names are valid. |
| FR-PKG-05 | `.github/scripts/check_version_bump.py` checks each plugin independently: a PR that changes a plugin's shipped paths must bump that plugin's version (root plugin shipped paths exclude `plugins/`). |
| FR-PKG-06 | `.github/workflows/release.yml` tags and releases every plugin in the marketplace whose `<name>--v<version>` tag does not exist yet, with notes/title from that plugin's README `## Status` entry. `release_notes.py` takes the README path. CI (`ci.yml`) runs both plugins' tests. |
| FR-PKG-07 | `.github/scripts/preflight.py [--base <ref>]` runs, in order: `check_version_bump.py <base> HEAD`, `uv lock --check`, `release_notes.py` for every marketplace plugin's current version (forcing UTF-8 output), and the conformance tests; exits non-zero on the first failure with its output. The harness finalize step and U9 both call it. |
| AC-PKG-01 | `claude plugin validate` (or `claude plugin details`) accepts `plugins/blender-inspect`, and the marketplace lists both plugins. | command output saved in the run folder |
| AC-PKG-02 | `check_version_bump.py` passes/fails correctly on four synthetic diffs (root-only change without bump -> fail; new-plugin-only change without bump -> fail; each with bump -> pass). | pytest test + output |
| AC-PKG-03 | `release_notes.py` extracts notes for both plugins' current versions under `PYTHONIOENCODING=utf-8`, and a dry simulation of release.yml's tag loop (script or test) yields the two expected tags. | output saved in the run folder |

## U2 - Move the existing Blender tools; hand-off inputs

| ID | Requirement |
|---|---|
| FR-MIG-01 | Canonical copies in the new plugin: `blender_mesh.py`, `blender_fit.py`, `blender_render.py`, `blender_multiview_render.py` (from `pil_blender_mesh`, `pil_blender_fit`, `pil_blender_render`, `pil_multiview_render`), on `blender_common.py`, same CLIs and payload shapes, `tool` fields renamed to the new names. Their tests move with them (adapted), keeping the Blender-missing skips. |
| FR-MIG-02 | The four `pil_*` originals stay behaviourally unchanged, gain a one-line deprecation notice on stderr and in `--help`, and `pil_capabilities` marks them `deprecated: true` with `replacement: "blender-inspect/<script>"`. |
| FR-MIG-03 | `pil_character_sheet_review.py` accepts `--renders <manifest.json>` (pre-rendered view images from `blender_render.py`/`blender_multiview_render.py`) as an alternative to rendering itself. |
| FR-MIG-04 | `pil_reconstruct.py` accepts a job manifest that points the fit/render stages at externally produced payloads (from `blender_fit.py` / `blender_multiview_render.py`) instead of invoking the deprecated copies; `pil_multiview_review.py` needs no change beyond accepting those render manifests. |
| FR-MIG-05 | `pil_contract_verdict --scene-stats` accepts `blender_mesh.py` payloads (test proves it). |
| AC-MIG-01 | On `runs/2026-09-05-six-capabilities/proof-v2/scene.blend`, old and new mesh/render/fit tools produce equivalent payloads (identical after normalising `tool`/`version`), and the renders are pixel-identical. | diff output saved in the run folder |
| AC-MIG-02 | A character-sheet review and a reconstruction job each run end-to-end through the hand-off path using only `blender-inspect` for Blender work. | payloads saved in the run folder |

## U3 - Mesh defect audit

| ID | Requirement |
|---|---|
| FR-AUD-01 | `blender_mesh_audit.py <scene.blend> [--objects ...] [--merge-distance 1e-5] [--max-locations 50]`: per render-visible mesh object (evaluated, world space), report counts for non-manifold edges, boundary (hole) edges, wire edges, loose verts, duplicate verts (within merge distance), degenerate faces (area below a tolerance), n-gons, poles (valence >= 6), inconsistent normals (faces that would flip on recalculation), and non-adjacent self-intersecting face pairs. |
| FR-AUD-02 | Each defect class carries up to `--max-locations` examples with element indices and world-space coordinates, plus a per-object `clean: true/false` and a scene summary. Payload schema `mesh-audit-v1` in `plugins/blender-inspect/schemas/`. |
| FR-AUD-03 | `--pairs A:B` (repeatable) reports inter-object interpenetration (face-pair overlap count and example locations) between named objects. |
| FR-AUD-04 | Test fixtures are built by a script (`tests/fixtures/build_defect_scene.py`, run inside Blender) that plants a known count of each defect class; no binary `.blend` is committed. |
| AC-AUD-01 | On the planted fixture every defect class is reported with exactly the planted count, and a clean cube reports all zeros. | pytest (Blender-gated) + payload saved |
| AC-AUD-02 | On the proof-v2 `scene.blend` the audit completes, and the Garment's 4 boundary edges are reported. | payload saved |

## U4 - Depth-revealing render modes

| ID | Requirement |
|---|---|
| FR-REN-01 | `blender_inspect_render.py <scene.blend> --views <manifest> --modes ...` renders each view with a **perspective** camera option (`--projection` (`perspective` or `orthographic`), `--lens`), including named 3/4 orbit presets (`front-left-high`, `front-right-high`, `back-left-high`, `back-right-high`) plus the existing arbitrary direction/up manifest. Locked framing across modes. |
| FR-REN-02 | Modes: `matcap` (Workbench MATCAP, cavity BOTH with ridge/valley emphasis), `depth` (EEVEE Depth pass -> colourised heatmap PNG with a numeric legend of near/far distances in scene units, plus raw float32 `.npy` and multilayer `.exr`), `normal` (EEVEE Normal pass -> RGB-encoded PNG + `.npy`), `ao` (EEVEE AO pass PNG), `object-id` (Cryptomatte or object-index mask PNG + JSON colour->object map). |
| FR-REN-03 | Payload lists every file per view and mode, the camera matrix, projection, lens, near/far depth, and background mask coverage. Missing geometry in a view -> `RENDER_BLOCKED` for that view without dropping it. |
| AC-REN-01 | On the defect fixture, the worker inspects the matcap and normal renders and records in `EVIDENCE.md` that the flipped face and the self-intersecting quad are visible (with the image paths). | EVIDENCE.md + PNGs |
| AC-REN-02 | Depth `.npy` values at the fixture's known object centres match the camera distance within 1% (test, Blender-gated). | pytest |

## U5 - Depth ordering report

| ID | Requirement |
|---|---|
| FR-DOR-01 | `blender_depth_order.py <scene.blend> --views <manifest or presets>`: for each view, each render-visible object's nearest/farthest distance along the view direction, its screen-space bounding box, and for every overlapping pair the front object, the depth gap, and the overlap fraction, computed from geometry (per-pixel from the object-ID + depth passes, with a vertex-projection fallback). |
| FR-DOR-02 | A plain-language `summary` line per overlapping pair (e.g. "Belt is 0.021 in front of Tunic in view front (overlap 34% of Belt)"). Schema `depth-order-v1`. |
| AC-DOR-01 | On a fixture of three stacked boxes at known depths, ordering and gaps are exact for front, side and a 3/4 view (test, Blender-gated). | pytest + payload |

## U6 - Concept-image depth (pil-agent-plugin)

| ID | Requirement |
|---|---|
| FR-MDE-01 | `scripts/pil_depth.py estimate <image> --model <onnx> [--mask]`: runs Depth Anything V2 Small (ONNX, caller-supplied via `--model` or `PIL_AGENT_DEPTH_MODEL`), records the model sha256 and preprocessing profile, and writes relative inverse depth (`.npy`) and a heatmap PNG. Payload says plainly that the output is model-inferred **relative** depth, not metric. |
| FR-MDE-02 | `pil_depth.py compare <concept-depth.npy> <render-depth.npy> [--mask]`: aligns scale and shift (least squares in inverse-depth space inside the mask), reports Spearman rank correlation, AbsRel after alignment, and a disagreement heatmap PNG with the worst regions as fractional bboxes. Refuses on shape mismatch after documented resampling rules. |
| FR-MDE-03 | `pil_bootstrap` gains a `--depth` check for the model (like `--embedding`); `pil_capabilities` lists the tool; README documents obtaining the Small model and its license. |
| AC-MDE-01 | Unit tests with a stubbed ONNX session cover preprocessing, payload, alignment maths and refusals (run in CI). | pytest |
| AC-MDE-02 | Real smoke run: the worker raises a `device` Needs item asking Brian for the model file path; after it's answered, `estimate` runs on a real concept image and `compare` runs against a U4 depth render. | payloads + PNGs in the run folder |

## U7 - Skills, agent and routing

| ID | Requirement |
|---|---|
| FR-SKL-01 | `blender-inspect` skill `model-inspection`: the modeling loop (audit -> diagnostic renders -> depth order -> fix -> re-audit), when to use each tool, how to read each payload, and what each layer does not establish. |
| FR-SKL-02 | `blender-inspect` skill `bootstrap` (Blender discovery/version check) and an agent `blender-model-inspector` that runs the loop read-only and reports defects with locations and evidence images. |
| FR-SKL-03 | `pil-agent-plugin` skills (`image-analysis`, `multiview-reconstruction`, `image-measurement`) and the `image-comparison-analyst` agent route Blender work to `blender-inspect` (when installed) and concept depth to `pil_depth`; deprecated tool mentions updated. |
| AC-SKL-01 | Skill descriptions pass the conformance tests, and `claude plugin details` shows the new plugin's skills and agent. | output saved |

## U8 - Evals

| ID | Requirement |
|---|---|
| FR-EVL-01 | Spike: prove a headless worker can run `claude plugin eval` against `plugins/blender-inspect` with one trivial case (flags per Decisions) and record cost and time. |
| FR-EVL-02 | Cases (each with scaffold that builds its fixture via Blender): (a) "find the imperfections" on the planted-defect scene; (b) "which part is in front" on the stacked-box scene; (c) "I can't see the dent" -> uses matcap/cavity/normal renders and locates it; (d) a negative case: clean mesh -> reports no defects without inventing any. Graders: deterministic checks on named defect counts/order where possible, `tool_used: Skill` (with-only), plus an LLM grader for the explanation. |
| AC-EVL-01 | Full suite run: every case scores >= 0.8 over 3 runs, and the with-plugin arm beats the no-plugin baseline on (a)-(c). Run cost <= $20. | aggregate-result.json + local HTML report under the run folder |

## U9 - Docs, versions and PR body

| ID | Requirement |
|---|---|
| FR-REL-01 | Versions: `pil-agent-plugin` 0.10.0 everywhere (four manifests, marketplace, every `scripts/pil_*.py` TOOL_VERSION, `uv.lock`), `blender-inspect` 0.1.0 everywhere. README `## Status` entries for both versions. |
| FR-REL-02 | Root README and `docs/` describe the two-plugin layout, the deprecations and the new depth tool; `docs/index.md` links this spec. |
| FR-REL-03 | `PR-BODY.md` in the unit's run folder: summary per unit, test counts, what was verified only locally (Blender-gated tests and evals do not run in CI), eval scores, and the deprecation/removal plan. Ends with the Claude Code attribution line. |
| AC-REL-01 | `python .github/scripts/preflight.py --base origin/main` passes on the final commit. | outputs saved in the run folder |
