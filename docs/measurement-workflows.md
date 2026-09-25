# Measurement workflows in 0.9.0

These workflows add explicit input normalization, image-bound selections,
controlled registration, domain calibration, local differences, and tool
discovery. Existing tool commands retain their default pixel processing and
thresholds. A new processing mode does not inherit an old calibration claim.

Use the plugin's Python environment. From the plugin repository:

```powershell
uv sync --extra reconstruction --extra embedding --extra comparison --extra mcp
& .venv/Scripts/python.exe scripts/pil_capabilities.py --tool pil_register
```

Core image operations need only Pillow and NumPy. Registration uses the existing
`reconstruction` extra. Standard SSIM is optional through `comparison`;
the stdio server is optional through `mcp`. Bootstrap also supports
`install/check --comparison --mcp`, alongside existing flags. No model is
downloaded by bootstrap. Tesseract and ONNX models retain their existing setup.

## Normalize displayed appearance

```powershell
& .venv/Scripts/python.exe scripts/pil_normalize.py photo.jpg --mode display --output display.png
```

`display` applies EXIF orientation and converts an embedded ICC profile to sRGB
using LittleCMS relative-colorimetric intent. Alpha is preserved separately.
Untagged RGB/grayscale input is explicitly assumed to be sRGB. Malformed profiles
and untagged unsupported colour modes refuse; they do not silently fall back.
`stored` preserves the decoded orientation/channels while making an RGBA working
image. This is an 8-bit analysis pipeline, not an HDR/RAW conversion tool.

The output JSON records source hash, source/profile facts, applied orientation,
output space, profile assumptions, and the `source_to_output` 3×3 matrix.
Coordinates use pixel edges; centers are `(x+0.5, y+0.5)`. Existing output files
are refused. File metadata about a normalized PNG describes that derived file;
the original file's facts remain in the preparation metadata.

You can feed the resulting PNG to any existing tool, or use `pil_pipeline`
below for preparation and measurement in one command.

Embedding capability eligibility now uses the model hash **and the full
preprocessing specification**. Known CLIP bytes with ImageNet preprocessing
report `model_configuration_not_gated`, no validated capabilities, and
`engine.model_gated: false`. Compare also recalculates eligibility for stored
descriptors. A matching pair of incorrect profiles is not a validated model
configuration. Read `configuration_gate` for structured capability/evidence data.

## Select named parts, including holes

Write `selection-spec.json`, with pixel-center coordinates in the selected
input mode's frame:

```json
{
  "name": "coat",
  "polygons": [[[20, 20], [180, 20], [180, 220], [20, 220]]],
  "subtract_polygons": [[[70, 60], [110, 60], [110, 90], [70, 90]]],
  "rasters": [],
  "subtract_rasters": []
}
```

```powershell
& .venv/Scripts/python.exe scripts/pil_mask.py character.png --spec selection-spec.json --output coat.json --input-mode stored
```

This writes `coat.json` plus `coat.png`. Raster inputs are resolved relative to
the spec file; dimensions must exactly match. Nonzero raster values select a
pixel. Added rasters/polygons are unioned; subtraction happens afterwards.
Empty selections refuse. The manifest binds the mask to image bytes, input mode,
dimensions and raster hash, and records the name and coordinate transform.

A selection mask is **binary membership**, not alpha coverage. Selected
semi-transparent pixels retain their original alpha when measured. Modified
images require a new mask binding, even when their dimensions remain the same.

## Reuse selections across existing tools

```powershell
& .venv/Scripts/python.exe scripts/pil_pipeline.py --tool pil_image_analyze --image-a character.png --mask-a coat.json
& .venv/Scripts/python.exe scripts/pil_pipeline.py --tool pil_palette_diff --image-a character.png --mask-a coat.json -- --colors 12
& .venv/Scripts/python.exe scripts/pil_pipeline.py --tool pil_structure_diff --image-a before.png --image-b after.png --mask-a before-mask.json --mask-b after-mask.json
& .venv/Scripts/python.exe scripts/pil_pipeline.py --tool pil_ocr --image-a sign.png --input-mode display -- --psm 7
& .venv/Scripts/python.exe scripts/pil_pipeline.py --tool pil_embed --image-a character.png --mask-a coat.json -- --model C:\Models\mobilenetv2-12.onnx --preprocessing imagenet
```

The pipeline invokes the original CLI, preserving its result under `result`.
Original tool options go after `--`. Masks enable foreground measurement for
palette/structure/analyze; OCR uses white outside the selection; embeddings
see a black-composited selected frame. Selection does not automatically recenter
an embedding: use its `--region` option when necessary.

`inputs` retains original-file provenance. `prepared://a` and `prepared://b`
identify transient working pixels, not downloadable files or original inputs.
Output hashes inside `result` describe those prepared pixels. Use
`pil_normalize` when a persistent prepared image is needed.

`calibration_status: requires_pipeline_specific_profile` is intentional.
Existing metric interpretations remain available, but applying a selection or
colour conversion does not validate historical decision thresholds for it.

## Register comparable frames within declared bounds

```powershell
& .venv/Scripts/python.exe scripts/pil_register.py reference.png candidate.png --max-shift 10 --output-dir aligned
& .venv/Scripts/python.exe scripts/pil_register.py reference.png candidate.png --motion rigid --max-shift 20 --max-rotation 3
& .venv/Scripts/python.exe scripts/pil_register.py reference.png candidate.png --motion affine --max-scale-change 0.03
```

Translation is the default. Selecting rigid or affine explicitly permits those
transform classes. Inputs must have equal prepared dimensions. Bounds apply to
the candidate-to-reference transformation; affine singular values must remain
within `max-scale-change`. Flat/ambiguous inputs, non-convergence, transforms
outside bounds, and insufficient overlap return `UNMEASURABLE` with exit 1.

Successful results report `candidate_to_reference`, response, overlap fraction,
and both `raw` and `aligned` native metrics. The output directory contains
`aligned.png` and `overlap.png`. Aligned statistics exclude interpolation-border
pixels. A low aligned error never excuses an unwanted shift: the raw measurement
and reported transform remain part of the result. These metrics are diagnostic.

## Locate small changes at native resolution

```powershell
& .venv/Scripts/python.exe scripts/pil_diff_regions.py before.png after.png --delta-e-threshold 2 --alpha-threshold 1 --output-dir differences
& .venv/Scripts/python.exe scripts/pil_diff_regions.py before.png after.png --ssim
```

Outputs include native ΔE mean/max/p95, separate alpha error, changed pixels and
area fraction, and 4-connected change regions with half-open pixel boxes.
`--min-pixels` filters region reporting only; the total changed count still
includes smaller regions. `--mask-a/--mask-b` compare the intersection of the
selections. This excludes pixels outside either selection; compare masks
separately when changed selection shape is itself the question.

Artifacts include lossless `delta-e.npy`, a heatmap saturating at ΔE 20, a binary
change mask, and paired crops magnified 4× with nearest-neighbor sampling.
`--max-crops` limits crop artifacts (default 20), not measurements. Defaults
ΔE 2 and alpha 1 are explicit diagnostic thresholds, not perceptual or
domain-calibrated verdicts. RGB describes appearance composited onto black;
alpha is evaluated separately, so black transparency changes are detected.

`--ssim` supplies standard scikit-image SSIM with data range 255, independently
of the existing `structural_similarity` grid-feature metric. It requires
rectangular unmasked frames at least seven pixels in each dimension.

## Calibrate a domain profile and apply it

Profiles support the domain names `screenshot`, `photograph`,
`transparent-render`, and `concept-versus-render`. Supply representative inputs;
the names do not confer accuracy. Do not use generated smoke examples as a
production-domain calibration.

```json
{
  "schema": "calibration-corpus-v1",
  "domain": "screenshot",
  "input_mode": "stored",
  "mask_source": "full_frame",
  "metric": "changed_area_fraction",
  "max_false_alarm_rate": 0.05,
  "max_miss_rate": 0.2,
  "scope": "Describe the actual source collection and supported changes",
  "pairs": [
    {"source": "screen-A", "split": "train", "a": "a.png", "b": "a-reencoded.png", "changed": false},
    {"source": "screen-A", "split": "train", "a": "a.png", "b": "a-edited.png", "changed": true, "perturbation": "button-recolour", "magnitude": 0.02}
  ]
}
```

The excerpt shows the pair format; it is not a complete corpus. Both train and
validation require changed and unchanged pairs from at least two source groups
per class. Each source group belongs to one split only, and identical image
bytes cannot cross splits. Put all variants of one asset/scene in the same
source group. Include realistic no-change nuisances and meaningful defects.
Paths resolve relative to the corpus file. Explicit selections use
`mask_source: explicit_selection` with per-pair `mask_a` and/or `mask_b`.

```powershell
& .venv/Scripts/python.exe scripts/pil_calibrate.py build corpus.json --output screenshot-profile.json
& .venv/Scripts/python.exe scripts/pil_calibrate.py evaluate screenshot-profile.json before.png after.png --domain screenshot
```

The builder chooses the higher empirical `(1 − max_false_alarm_rate)` quantile
of training controls. Validation never tunes that threshold. The profile records
held-out false alarms/misses, source-cluster bootstrap intervals, exact input
hashes, measured detection points, identity and a content hash. `accepted` means
observed held-out rates meet the supplied budgets. Zero observed errors does not
establish zero population risk. Magnitudes are caller-defined and must carry a
meaningful perturbation name; points are not extrapolated detection limits.

Evaluation checks the profile hash, domain, mode, mask source and metric/pipeline
version. Rejected profiles refuse evaluation. Successful evaluations return
`CHANGE_DETECTED` or `NO_CHANGE_DETECTED`, measurement evidence, and a companion
comparison from the original structure tool. Supported calibrated metrics are
changed area, mean/p95 ΔE and mean absolute alpha difference. Profiles are not
interchangeable with legacy `pil_contract_verdict --thresholds` bundles.
Registration is explicitly `none` in this profile version.

## Discover tools and run batches

```powershell
& .venv/Scripts/python.exe scripts/pil_capabilities.py
& .venv/Scripts/python.exe scripts/pil_capabilities.py --tool pil_embed
& .venv/Scripts/python.exe scripts/pil_capabilities.py --batch jobs.json --output results.json --summary
```

Discovery captures actual argparse contracts, including subcommands, types,
choices, defaults and repeatable options. It also returns input/output envelope
schemas, dependencies, mutation hints, cost classes and interpretation status.
Readiness checks discover dependencies; they do not replace the engines'
diagnostics or evaluate model accuracy. Some tools have both read-only and
writing modes and are conservatively marked as mutating.

```json
[
  {"id": "reference", "tool": "pil_image_info", "argv": ["reference.png"]},
  {"id": "comparison", "tool": "pil_diff_regions", "argv": ["reference.png", "candidate.png"], "timeout": 300}
]
```

Batches run sequentially, retain every item and continue after individual
failures. `--summary` requires a new `--output` receipt, so compact output never
discards the full evidence. Nonzero tool status is retained under `exit_code`;
the batch returns 1 if any item fails. Dispatcher invocation uses an allowlist,
an argument array, no shell, and closed child stdin. Limits are 100 jobs and
1–1800 seconds per job. Use the tool-specific schemas for mode-level behavior.

## Optional MCP server

```powershell
& .venv/Scripts/python.exe scripts/pil_mcp.py
```

This is a stdio protocol process. Configure it in the calling host rather than
running it as an ordinary interactive command. Example host configuration:

```json
{
  "mcpServers": {
    "pil-agent-plugin": {
      "command": "C:/Projects/pil-agent-plugin/pil-agent-plugin/.venv/Scripts/python.exe",
      "args": ["C:/Projects/pil-agent-plugin/pil-agent-plugin/scripts/pil_mcp.py"]
    }
  }
}
```

Replace paths with the active plugin copy's paths. The repository deliberately
does not auto-enable this optional server for core-only installs. Tools are
listed individually with their CLI contract and mutation hints. Call with
`{"argv": ["image.png"]}`; optional `timeout` is in seconds. Responses contain
both text and structured output with `ok`, `exit_code`, `result`, and `error`.
MCP `isError` follows the original command result. The CLI and MCP share the
same dispatcher; no measurement logic is duplicated in the server.

The adapter uses the maintained MCP Python SDK 1.x API with an explicit `<2`
dependency bound. Install `--extra mcp` before activating it. Engines, model
weights, paths and host authorization remain local to the active plugin copy.
MCP calls use local stdio and the dispatcher invokes only tools from its fixed
public catalog. It passes arguments as an argv array with shell execution off,
and gives tool subprocesses an allowlisted environment that excludes host
tokens, API keys, passwords, and registry credentials. The adapter does not
read `.npmrc` or call a remote image-processing service.
Bootstrap installs from the public package index without reading local package
manager configuration or credential stores. No credential is needed for setup.
