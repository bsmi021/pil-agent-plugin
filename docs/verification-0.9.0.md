# 0.9.0 verification

Scope: the first six recommendations in the September 5 capability assessment.
The versioned source plugin includes the implementations, usage documentation,
agent routing updates, optional dependencies and synchronized package manifests.
This record describes local execution; it does not claim remote publication or
an update to a separate installed plugin cache.

## Evidence location

The persistent run is
[`runs/2026-09-05-six-capabilities/`](../runs/2026-09-05-six-capabilities/).
Its scripts create their own test assets and retain JSON outputs. Model weights
and generated images are local artifacts, not packaged model dependencies.

- [Public-tool execution matrix](../runs/2026-09-05-six-capabilities/proof-v2/matrix.json)
  — **50 successful invocations across all 30 public CLI tools**.
- [Protocol receipt](../runs/2026-09-05-six-capabilities/proof-v2/mcp-protocol.json)
  — actual MCP stdio initialization, discovery and a successful structured call.
- [Refinement matrix](../runs/2026-09-05-six-capabilities/refined-proof/matrix.json)
  — **10 additional successful invocations**, including refined alignment and
  rebuilt/applied calibration profiles.
- [Reproduction harness](../runs/2026-09-05-six-capabilities/verify_tools.py)
  and [refinement harness](../runs/2026-09-05-six-capabilities/refine_evidence.py).
- [Model receipt](../runs/2026-09-05-six-capabilities/model-receipt.json)
  — supported MobileNet ONNX bytes checked against the existing documented hash.
- [Plugin validation](../runs/2026-09-05-six-capabilities/plugin-validation.txt)
  and [focused lint results](../runs/2026-09-05-six-capabilities/ruff.txt).

The first evidence attempt stopped because the harness reused a mask-manifest
filename for its command receipt. That damaged only a newly generated fixture;
the plugin correctly refused its invalid binding. The failed attempt remains in
`proof/`; the corrected complete run is `proof-v2/`.

## Demonstrated improvements

| Capability | Input/control | Observed result |
|---|---|---|
| EXIF normalization | Rotated tagged input versus display-oriented reference | Original analyzer measured zero changed area after normalization; all eight orientation matrices also pass pixel-coordinate tests |
| ICC handling | Tagged Lab TIFF and malformed-profile control | Display normalization converts through LittleCMS; malformed ICC refuses rather than treating tagged bytes as sRGB |
| Embedding claims | Known CLIP hash paired with incorrect preprocessing | No validated capability is advertised; the comparison output recalculates configuration eligibility |
| Explicit masks | Named selections with a subtracted hole and semi-transparent pixels | Binding/hole checks pass; selected colour is isolated and original alpha-weighted luminance is preserved |
| Cross-tool mask use | Same selection supplied to palette, structure, analyzer, embedding and OCR | All five existing tools execute through the composition pipeline; masked OCR retains the expected text |
| Translation | Known 5-pixel horizontal / −3-pixel vertical shift | Refined mean ΔE **38.03101028 → 0.0** over valid overlap; raw displacement evidence remains in the result |
| Rigid/affine registration | Known rotation and small scale change | Both explicit model tests reduce error; flat inputs and out-of-bounds transforms refuse |
| Native local differences | Two edits of 9 and 16 pixels in a 600×600 frame | **25 changed pixels**, separate exact boxes `[10,10,13,13]` and `[500,500,504,504]`; alpha-only change also detected |
| Complement to original structure metrics | Same tiny-edit pair | Original analyzer reports structural similarity **0.99928** and one encompassing region; the new tool retains separate native edit locations |
| Domain calibration | Four named synthetic corpora with disjoint train/validation source groups | Each build/apply succeeds with zero observed false alarms and misses on these smoke inputs; leakage and mismatched-profile use refuse |
| Tool access | Direct CLI, batch and real MCP process | Dispatcher result equals direct CLI JSON; batches preserve partial failures and MCP preserves `isError` / exit codes |

Registration receipts include interpolation-valid overlap; no claim covers
excluded border pixels. Calibration corpora here are **synthetic execution
checks**, including the corpus labeled `photograph`. They are not evidence that
thresholds transfer to real photographs, screenshots or concept-art workflows.
Representative source-disjoint domain data is required for that claim.

## Original-tool execution

The complete matrix contains a successful invocation of each original public
tool. Results and tests distinguish execution from any broader accuracy claim.

| Original tools | Proof exercised |
|---|---|
| `pil_image_info`, `pil_image_analyze`, `pil_palette_diff`, `pil_structure_diff` | Synthetic RGBA input, identical comparisons, normalization and localized-edit comparisons |
| `pil_alignment`, `pil_components`, `pil_silhouette` | Diagnostic shape/alignment output plus black/white WCAG contrast; demoted capabilities remain demoted |
| `pil_crop`, `pil_annotate` | Actual native crops and numbered-box images written and inspected |
| `pil_contract_verdict` | Declared invariants evaluated on an identical image pair |
| `pil_ocr` | Real Tesseract reads **PIL QUALITY 123** exactly and emits usable text claims |
| `pil_semantic_record` | OCR claims sealed, verified against image bytes and compared |
| `pil_embed` | Real supported-model inference; identical-image cosine **1.0** and stored-descriptor comparison |
| `pil_bootstrap` | Real install/check workflow with OCR, model, reconstruction, comparison and MCP selected |
| `pil_blender_mesh`, `pil_blender_fit` | Actual Blender scene data and bounded fit; sampled clearance changes from approximately **0.005 to 0.020** scene units |
| `pil_blender_render`, `pil_character_sheet_review` | Actual render and reference-based character-sheet review |
| `pil_multiview_render`, `pil_multiview_prepare`, `pil_multiview_review` | Seven locked-framing views, contour preparation and complete matched-view review |
| `pil_multiview_solve`, `pil_reconstruct` | Known supplied 3D landmarks recovered within 0.001; orchestration reaches `COMPLETED` |

Blender clearance is vertex-sampled, as before. The after-fit value is
0.0199999809 due to floating-point representation; the original tool's strict
violation counter still counts those four samples while its fit tolerance
permits `FITTED`. This evidence demonstrates the bounded improvement, not a
surface-wide clearance guarantee or a change to that existing convention.

## Regression checks

**Final full suite: 799 passed, 6 skipped, 3 deprecation warnings.** All six
skips require the unavailable private historical reference image. Real OCR,
embedding and Blender checks ran. A final focused run after tightening malformed
mask/corpus input handling passed **22 tests** (input pipeline, calibration,
discovery and actual MCP transport). No tests or coverage settings were weakened.

The final regression totals and environment are recorded in
[`full-suite-validated.txt`](../runs/2026-09-05-six-capabilities/full-suite-validated.txt)
and [`suite-validated-receipt.json`](../runs/2026-09-05-six-capabilities/suite-validated-receipt.json).
The command is the unfiltered repository suite: `python -m pytest -q`.
The test process receives the checked model path and Tesseract's directory on
`PATH`. The unavailable private historical reference image is not substituted
with a generated fixture.

The earlier full run passed **781 tests, with 13 skipped**. Seven legacy OCR
tests used PATH-only discovery; the final environment correction enables those.
Six historical reference-image tests remain conditional on the unavailable
private image. Focused red/green and refinement logs remain alongside the full
suite, including the failed precision regression that led to ECC refinement.

The MCP test uses actual subprocess pipes, checks tool discovery, compares a
successful structured result, and verifies an original command's refusal.
During development, inherited protocol stdin caused a Windows child-process
hang; the shared dispatcher now explicitly uses `DEVNULL`, and the real
transport regression passes with a bounded test timeout.
