## Changes

Release 0.9.0 implements the first six recommendations from the September 5
capability assessment and synchronizes all release metadata.

Input handling gains EXIF orientation and ICC/display normalization with
preserved alpha and coordinate provenance; a malformed profile refuses rather
than being treated as sRGB. Embedding capability labels now validate the model
*and* its preprocessing configuration, so no unvalidated capability is
advertised. Explicit named masks support subtracted holes and semi-transparent
pixels, and a composition pipeline feeds prepared inputs to the existing
palette, structure, analyzer, embedding and OCR tools without changing their
historical defaults or thresholds.

Registration adds bounded translation/rigid/affine models that retain raw
displacement evidence alongside the aligned result and refuse flat or
out-of-bounds inputs. A native local-difference tool reports exact per-edit
bounding boxes where the original structural metric returns one encompassing
region. Domain calibration profiles carry a versioned metric/pipeline identity
with source-group holdouts, and refuse on leakage or mismatched profile use.
Tool discovery is exposed through a catalog, a batch dispatcher and an optional
MCP stdio adapter.

New public tools: `pil_normalize`, `pil_mask`, `pil_pipeline`, `pil_register`,
`pil_diff_regions`, `pil_calibrate`, `pil_capabilities`, optional `pil_mcp`,
plus shared `pil_io`. Bootstrap gains comparison/MCP extras. Legacy CLI
defaults and measurement implementations are unchanged; new measurements stay
diagnostic unless a compatible evaluated profile is supplied.

All four manifests, the marketplace listing, every `TOOL_VERSION` (30 tools)
and the lockfile are aligned at 0.9.0. The README Status entry supplies the
automatic release notes.

## Validation

Evidence run: `runs/2026-09-05-six-capabilities/`, summarized in
[`docs/verification-0.9.0.md`](docs/verification-0.9.0.md).

- Public-tool execution matrix: **50 successful invocations across all 30 public
  CLI tools**, plus a real MCP stdio session (initialize, discovery, structured
  call, `isError` and exit-code preservation).
- Refinement matrix: 10 further successful invocations, including refined
  alignment and rebuilt/applied calibration profiles.
- Known 5px/−3px translation control: mean ΔE **38.03101028 → 0.0** over the
  valid overlap.
- Two 9- and 16-pixel edits in a 600×600 frame: **25 changed pixels**, separate
  exact boxes `[10,10,13,13]` and `[500,500,504,504]`.
- Behavioral red/green tests per feature; refusal paths covered (malformed ICC,
  invalid mask binding, corpus leakage, mismatched profile, out-of-bounds
  transform).
- Full suite run locally on this commit, the way `ci.yml` runs it
  (`uv run --extra reconstruction --extra embedding pytest -q`, Tesseract on
  PATH): **795 passed, 11 skipped, 3 deprecation warnings in 297.85s**, exit 0.
  All 11 skips are environmental: 5 embedding tests need
  `PIL_AGENT_EMBED_MODEL`, which CI also leaves unset because model weights are
  caller-supplied and never bundled; 6 need the private historical reference
  image. The Sep 5 evidence run reported 799 passed / 6 skipped because it had
  downloaded the pinned MobileNet into that run.
- Release pre-flight: `check_version_bump.py` passes, and `release_notes.py`
  extracts the 0.9.0 `## Status` entry cleanly, so the post-merge tagging job
  has notes and a title to work with.

Scope limits stated in the verification record: the four calibration corpora
are **synthetic execution checks**, including the one labeled `photograph`.
They demonstrate that build/apply works, not that thresholds transfer to real
photographs, screenshots or concept-art workflows. Registration receipts cover
interpolation-valid overlap only; no claim covers excluded border pixels. Model
weights remain caller-supplied and are not bundled.
