---
name: image-measurement
description: Measure one image or compare two with reproducible pixel, colour, structure, region, OCR, embedding, and metadata tools. Use when a request needs numeric image facts or localized changes, not mesh geometry.
---

# Image measurement

Use native visual inspection for meaning, objects, layout, and style. Use this
skill when the answer needs reproducible numbers, coordinates, file facts, or a
declared comparison verdict.

For calibrated template fitting, Blender clearance, or an image-plus-mesh
review, route through [`image-analysis`](../image-analysis/SKILL.md) or
[`multiview-reconstruction`](../multiview-reconstruction/SKILL.md). Pixel
complexity is not polygon count, topology, collision, or metric depth.

## Start with the narrowest useful tool

Resolve the active plugin root from this file rather than assuming the working
directory. Discover exact arguments, dependencies, and interpretation status
before an unfamiliar operation:

```powershell
uv run --project '<plugin-root>' python '<plugin-root>/scripts/pil_capabilities.py' --tool NAME
```

Use `pil_image_analyze.py` for a broad one-image profile or complete two-image
comparison. It combines file, colour, structure, fingerprints, tonal, channel,
and detail results. Choose an individual tool when the request needs only one
layer or a specialized mode.

| Need | Tool or route |
|---|---|
| Broad profile or pair comparison | `pil_image_analyze.py` |
| Exact colours or perceptual palette change | `pil_palette_diff.py` |
| Layout, structure, hashes, or changed area | `pil_structure_diff.py` |
| Native local change boxes and crops | `pil_diff_regions.py` |
| Declared intended change plus invariants | `pil_contract_verdict.py` |
| Normalize orientation/colour or apply a named selection | `pil_normalize.py`, `pil_mask.py`, `pil_pipeline.py` |
| Bounded frame alignment | `pil_register.py` |
| Inspect or mark a region | `pil_crop.py`, `pil_annotate.py` |
| Dimensions, alpha, EXIF, ICC, frames | `pil_image_info.py` |
| Machine-read text with boxes | `pil_ocr.py` |
| Copy identification or related-image ranking | `pil_embed.py` |
| Bind visual claims to exact source bytes | `pil_semantic_record.py` |
| WCAG contrast arithmetic | `pil_alignment.py contrast` |

Read the task-specific section of
[measurement workflows](../../docs/measurement-workflows.md) for normalization,
selections, registration, local diffs, calibration, batching, or MCP. Use the
[metric guide](../../README.md#choosing-a-metric) when selecting among colour,
structure, silhouette, and contract signals. Do not load either document when
the tool and interpretation are already clear.

## Interpretation invariants

- Inspect `flags` and `interpretation_limits` before trusting a score.
- For object renders on a shared backdrop, use foreground mode when the payload
  reports background dominance. A tiny or empty foreground remains weak or
  unmeasurable evidence.
- A match question needs both colour and structure evidence. Luminance hashes
  and structural similarity can miss a pure recolour.
- Keep raw and aligned evidence. Registration may enable comparison but must not
  erase a real displacement finding.
- Do not transfer thresholds to normalized, selected, or otherwise changed
  pipelines unless a matching calibration profile establishes that use.
- `UNMEASURABLE` is an outcome, not an invitation to substitute a different
  metric. Multi-pair contracts use worst-case aggregation.
- Embedding claims are model- and preprocessing-specific. Ungated models may
  rank results but do not inherit another model's bands or verdicts.
- OCR confidence belongs to the OCR engine. Verify important or stylized text
  visually from a native-resolution crop.
- A sealed semantic record proves source binding and attribution, not the truth
  of the visual claim.
- Annotated images are for communication; measure the unannotated source.
- Fixed image-pair measurements are deterministic. OCR and Blender render
  reproducibility is scoped to the recorded engine/install and machine.

## Report and finish

Record the input identity, normalization and selection state, region or
foreground mode, deciding fields, flags, calibration/profile identity, and
detection limits. Use exact tool status terms rather than smoothing a refusal
or unreadable input into a numeric answer.

When the user asks what changed, include both magnitude and location. When the
user asks whether an intended edit succeeded, evaluate the requested change and
the stated invariants. Finish when each requested predicate has a supported
verdict or a named evidence gap; do not run unrelated measurements merely
because the plugin exposes them.
