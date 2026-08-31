# pil-agent-plugin

An agent plugin that gives coding agents **quantitative** image measurement —
exact colour palettes, per-hue census, layout statistics, perceptual hashes, and
changed-region localisation — plus an optional constrained multi-view layer for
template-mesh fitting, Blender BVH clearance, and arbitrary matched renders.

It is designed to *complement* an agent's native multimodal vision, not replace it.

## Why this exists

An agent with multimodal vision already reads images well: it transcribes text,
understands layout semantically, identifies objects, and recognises visual style.
What it cannot do is produce a **number** — an exact hex value, a reproducible
similarity score, or coordinates of what changed between two renders.

This was validated rather than assumed. Two subagents analysed the same complex
image; one was restricted to native vision, the other was permitted Pillow
scripting. The result was that **neither approach is a superset of the other**:

- Native vision, unassisted, produced a full text transcription, semantic layout,
  object inventory, correct style identification, *and* a correct reading of the
  image's colour-coded semantics.
- Naive measurement got one of those things **wrong**: a global 8-colour
  quantisation of the image returned *zero vivid colours*, because ~75% of its
  pixels were near-black and area-weighted extraction spent its whole budget on
  dark tones. Vision's qualitative read was right where the measurement was wrong.

So the tools here are shaped around that failure. Full write-up:
[`docs/design-rationale.md`](docs/design-rationale.md).

## Standards conformance

This package conforms to **[Agent Plugins 1.0.0](https://agent-plugins.org/specification)**,
the vendor-neutral standard for packaging reusable agent components, and ships the
portable and Claude Code-native layouts side by side:

| Path | Role | Read by |
|---|---|---|
| `plugin.json` | Portable manifest, `$schema` pinned to [`schemas/1.0.0/plugin.schema.json`](https://agent-plugins.org/schemas/1.0.0/plugin.schema.json) | Any Agent Plugins client |
| `.codex-plugin/plugin.json` | Codex-native manifest and interface metadata | Codex |
| `skills/image-measurement/SKILL.md` | Portable skill, per the [Agent Skills spec](https://agentskills.io/specification) | Any Agent Plugins client |
| `.claude-plugin/plugin.json` | Claude Code's native manifest | Claude Code |
| `agents/` | Claude Code subagent — no portable equivalent in 1.0.0 | Claude Code |
| `.claude-plugin/marketplace.json` | Single-plugin marketplace, so the CLI can install this repo; declares Claude Code's own `$schema` | Claude Code |

All four manifests describe the same package. `agents/`, `.codex-plugin/`, and `.claude-plugin/` are
undefined top-level directories under Agent Plugins, which the specification
requires clients to ignore rather than reject, so their presence does not affect
portability. No `mcp.json` is shipped — this plugin exposes CLI tools and a skill,
not an MCP server.

Two honest limits on that claim:

- **Claude Code does not itself parse the portable manifest.** It installs from
  `.claude-plugin/plugin.json`. The root `plugin.json` exists so that Agent Plugins
  clients and tooling can consume the same checkout; it is additive, and both
  `claude plugin validate --strict` and schema validation of `plugin.json` pass.
- **`${CLAUDE_PLUGIN_ROOT}` in the skill is client-specific.** Agent Plugins names
  the equivalent variable `${PLUGIN_ROOT}`; `SKILL.md` states both.

Conformance is enforced by [`tests/test_packaging_conformance.py`](tests/test_packaging_conformance.py),
which checks the closed manifest schema, the name patterns, skill discovery at the
fixed location, the Agent Skills frontmatter constraints, and that the three
manifests have not drifted apart — version now lives in all of them, and
`claude plugin tag` refuses a release when they disagree. To re-check the manifest against the published
schema directly:

```bash
uv run --with jsonschema python -c "
import json, urllib.request
from jsonschema import Draft202012Validator
schema = json.load(urllib.request.urlopen('https://agent-plugins.org/schemas/1.0.0/plugin.schema.json'))
Draft202012Validator(schema).validate(json.load(open('plugin.json')))
print('valid')"
```

## Requirements

- **Claude Code** (for plugin installation; the scripts also run standalone)
- **Python 3.11+**
- **Pillow** and **numpy** for the core image tools
- Optional reconstruction extra: **OpenCV** and **SciPy**
- Optional Blender tools: a local Blender executable (tested with Blender 5.2)
- **[uv](https://docs.astral.sh/uv/)** recommended, for a pinned environment

## Installation

```bash
git clone https://github.com/bsmi021/pil-agent-plugin.git
cd pil-agent-plugin
uv sync                        # installs Pillow + numpy into a local venv
uv sync --extra reconstruction # also installs OpenCV + SciPy
```

Then install it as a plugin. The Claude Code CLI installs from *marketplaces*, not
from a bare directory path, so the repository ships a single-plugin marketplace
manifest and registers itself:

```bash
claude plugin marketplace add ./                                 # register this repo
claude plugin install pil-agent-plugin@pil-agent-plugin -s user  # all your projects
# or: ... -s project   for this project only
```

Verify with `claude plugin list` (or `/plugin` inside Claude Code) — you should see
`pil-agent-plugin` at scope `user`, enabled, with one skill and one agent.
`claude plugin details pil-agent-plugin` prints the component inventory.

Two things worth knowing:

- **Installing takes a snapshot copy**, versioned under
  `~/.claude/plugins/cache/`. Edits to your clone do not reach an installed
  plugin until you run `claude plugin update pil-agent-plugin`. If `uv sync` has
  already created `.venv/`, that is copied too — budget ~65 MB.
- **`claude plugin validate .` now validates the *marketplace* manifest**, which
  takes precedence at the repository root. To validate the plugin manifest, point
  at it directly: `claude plugin validate .claude-plugin/plugin.json --strict`.
- **Editing `marketplace.json` needs a refresh**: `claude plugin marketplace update
  pil-agent-plugin`. Editing the plugin itself needs `claude plugin update
  pil-agent-plugin`. They are separate caches.

<details>
<summary>Installing without <code>uv</code></summary>

The scripts only need Pillow and numpy, so any environment with those works:

```bash
pip install "pillow>=10.0" "numpy>=1.26"
claude plugin marketplace add ./
claude plugin install pil-agent-plugin@pil-agent-plugin -s user
```

The bundled skill prefers `uv` and falls back to a plain `python` invocation.
Skipping `uv sync` also keeps `.venv/` out of the installed snapshot.
</details>

## Usage

There are three ways in, from most to least automatic.

### 1. Just ask

Once installed, the `image-measurement` skill is invoked automatically when your
request calls for it. Its description triggers on questions like:

- *"Do these two screenshots match?"*
- *"Did this render keep the reference's colour scheme?"*
- *"What exactly changed between these two versions?"*
- *"What are the exact colours in this logo?"*

For work that crosses from images into calibrated multi-view constraints or
Blender geometry, the `image-analysis` umbrella skill composes
`image-measurement` with `multiview-reconstruction`. It keeps visual
interpretation, pixel measurement, reconstruction residuals, and scene geometry
as separate evidence layers, then combines them in one report.

### 2. The comparison agent

For a rigorous two-image comparison, invoke the bundled
`image-comparison-analyst` agent. It is deliberately structured to look **first**
and measure **second**:

1. Reads both images and commits to a visual description, so the numbers cannot
   anchor its perception.
2. Runs both measurement tools on the pair.
3. Reports where vision and measurement **disagree** — rather than silently
   deferring to whichever spoke last.

It reports findings only; it never edits files.

### 3. Direct CLI

Both tools emit JSON on stdout, are deterministic (repeated runs are
byte-identical, so output can be committed and diffed), and accept one image
(analyse) or two (analyse and diff).

```bash
uv run python scripts/pil_image_analyze.py  "reference.png"                # maximal one-call profile
uv run python scripts/pil_image_analyze.py  "reference.png" "render.png"   # full comparison, one call
uv run python scripts/pil_palette_diff.py   "reference.png"
uv run python scripts/pil_palette_diff.py   "reference.png" "render.png"
uv run python scripts/pil_structure_diff.py "reference.png" "render.png" --grid 4x3
uv run python scripts/pil_structure_diff.py "view_a.png" "view_b.png" --foreground
```

`pil_image_analyze.py` is the one-call entry point: it composes the file-fact,
palette and structure tools (their blocks are content-identical to standalone
runs) and adds what none of them emit for a single image — persistable
dhash/ahash **fingerprints** (hex strings comparable across runs by Hamming
distance, so an image profiled today can be identified against a payload
stored last week), exact tonal percentiles and clipping fractions, per-channel
statistics, an exact distinct-colour count and greyscale test, and
edge/sharpness diagnostics. With two images it also contains both tools'
pairwise diffs verbatim plus all four fingerprint distances.

Always quote paths — image filenames routinely contain spaces and parentheses.

**Use `--foreground` (both tools) when comparing object renders** — a model on a
preview backdrop, a product shot, a sprite. Full-frame metrics include the
background, and a shared backdrop can be ~98% of both frames: two *different*
swords measured 0.991 full-frame structural similarity because the background was
doing the scoring. Foreground mode masks the background out (alpha when the file
carries real transparency, border-median OKLab colour otherwise — the same
visible-pixel definition as the Synty asset index), crops to the object's
bounding box so position in frame stops mattering, and scores only grid cells
with real foreground support. When you *don't* pass it, the tools estimate
foreground coverage anyway and flag `background_dominant` on mostly-background
frames — treat that flag as "these scores describe the backdrop".

## Worked example

Two images, identical in layout and lightness. The only difference is that one
small accent stripe changes from cyan to orange.

```bash
uv run python scripts/pil_palette_diff.py before.png after.png
```

```json
{
  "accent_hue_shift_detected": true,
  "hue_families_lost":      ["cyan"],
  "hue_families_gained":    ["orange"],
  "hue_families_diminished":["cyan"],
  "base_palette_distance":  1.6432,
  "accent_palette_distance": 67.6892
}
```

Now the same pair through the structure tool:

```json
{
  "structural_similarity": 0.999097,
  "dhash_distance": 0,
  "changed_area_fraction": 0.007241,
  "changed_region_bbox_fractional": [0.0625, 0.1871, 0.3164, 0.2222]
}
```

**This is the whole argument in one example.** `structural_similarity` says 0.999
and `dhash_distance` says 0 — both are *blind* to the change, because they are
luminance-based and the edit preserved luminance. Only the hue census catches it.
Meanwhile `changed_region_bbox_fractional` pinpoints *where* it happened.

A caller reading only a similarity score or a hash would have concluded "identical".

## Choosing a metric

| Question | Tool | Field |
|---|---|---|
| Everything measurable about an image, one call | `pil_image_analyze` | full profile: `file` + `colour` + `structure` + `fingerprints` + `tonal` + `channels` + `detail` |
| Fingerprint an image for later identification | `pil_image_analyze` | `fingerprints.full_frame.dhash`/`ahash` — hex, cross-run comparable |
| Exposure / clipping / dynamic range | `pil_image_analyze` | `tonal.percentiles`, `clipped_black_fraction`, `clipped_white_fraction` |
| Exact distinct-colour count, true-greyscale test | `pil_image_analyze` | `channels.unique_colours`, `channels.all_channels_equal` |
| What **text** does the image contain, machine-read? | `pil_ocr` | Tesseract lines/words with engine confidence and frame-mapped boxes; `--claims-out` feeds the semantic layer |
| Record/verify/compare **vision claims** about an image | `pil_semantic_record` | sealed `vision_claim` records — sha256-bound, content-addressed, never a measurement |
| Let me **see** a region at full resolution | `pil_crop` | native-resolution crop, integer upscale only |
| Let me **point** at something a model will understand | `pil_annotate` | numbered boxes on a copy |
| What does the **file** say (alpha, EXIF, ICC, true size)? | `pil_image_info` | file facts vision never receives |
| Measure only part of the frame | both diffs, `--region` | every field, scoped |
| Is this the same image? | structure | `dhash_distance`, `changed_area_fraction` |
| Same layout / composition? | structure | `structural_similarity` |
| What changed, and where? | structure | `changed_region_bbox_fractional`, `most_divergent_cells` |
| Did the colour scheme change? | palette | `accent_hue_shift_detected`, `hue_family_fraction_deltas` |
| What exact colours are used? | palette | `base_palette`, `accent_palette`, `hue_families` |
| More / less detailed? | structure | per-cell `edge_mean` (see caveat below) |
| Same **object**, ignoring the backdrop? | both, `--foreground` | same fields, foreground-masked |
| Did the colour change *perceptually*? | palette | `base_palette_distance_de2000`, `accent_palette_distance_de2000` |
| Did my *intended* change land, and nothing else? | contract | per-predicate `verdict` + `detection_limit` |
| Is polygon/vertex count, material or bounding-box geometry different? | `pil_blender_mesh` + `pil_contract_verdict`'s `geometry.*` | real scene stats, never inferred from pixels |
| Does this colour pair meet WCAG contrast? | `pil_alignment contrast` | `contrast_ratio`, verified against the standard's own worked examples |
| Render a Blender scene's front/side/back and check it registers against a reference | `pil_blender_render` | matched-view render + `pil_structure_diff --foreground` comparison, refuses rather than warps |
| Review a whole character sheet (multiple matched views) as one contract | `pil_character_sheet_review` | `pil_contract_verdict --pairs` aggregate over B2's rendered views; a single diverging view forces the aggregate |
| Extract ordered contours from several reference views | `pil_multiview_prepare` | normalized contour, bbox, foreground provenance, per-view refusal |
| Fit template vertices to calibrated multi-view constraints | `pil_multiview_solve` | `SOLVED`, `UNDERDETERMINED`, or `VIEW_CONFLICT`, rank and per-view residuals |
| Measure or correct garment/body clearance | `pil_blender_fit` | Blender BVH signed clearance; read-only probe or bounded new-file fit |
| Render a locked-framing seven-view or arbitrary-view set | `pil_multiview_render` | one render record per requested view |
| Review arbitrary named views without averaging away a failure | `pil_multiview_review` | existing worst-case contract aggregate |

**Consult both tools for any "do these match?" question.** They are blind to
different things. And **read `flags` before any score**: `background_dominant`,
`accent_support_low`, `foreground_too_small` and friends each name a specific
way the numbers beside them are weakened. `accent_hue_shift_detected` is
support-gated — a hue family needs both a minimum pixel count and a minimum
frame fraction before its appearance or disappearance can flip the verdict, so
a stray anti-aliased pixel cannot — but with `accent_support_low` flagged, even
the gated verdict should not influence a decision.

### Colour is reported three ways, deliberately

| Field | Answers |
|---|---|
| `base_palette` | Which colours dominate **by area** |
| `accent_palette` | Which colours dominate among **vivid pixels only** |
| `hue_families` | Which hues are **present at all**, regardless of area |

**Read `hue_families` when a colour matters semantically.** Area-weighted palettes
systematically miss small vivid accents. In the validation image, a cyan occupying
**0.485% of the frame** encoded an entire UI state — and it appeared in *neither*
palette, only in the hue census. Semantic importance and pixel area are
uncorrelated.

Do not rely on `base_palette_distance` for colour-scheme questions: measured
against a real recolour, it scored the changed image as *more* similar than an
unchanged rescale.

### Direction of comparison

`hue_families_lost` / `_gained` / `_diminished` / `_amplified` and
`hue_family_fraction_deltas` describe what the **second** image did relative to the
first. `accent_hue_shift_detected` and both palette distances are symmetric under
swapping the two.

## Scope limit: these tools do not measure geometry

`edge_mean` and `entropy` are 2D image-complexity proxies. They are **not** polygon
counts, mesh density, or topology, and must not be used as a proxy for them.
Shading, normal maps, lighting and camera angle all move these numbers
independently of the underlying model — a smooth-shaded low-poly render can score
as *more* complex than a flat-shaded high-poly one.

For polygon, mesh or topology questions, query the 3D scene's own statistics (for
example via a Blender MCP server) instead of analysing a render. Inferring geometry
from pixels produces confident, wrong answers.

This limit is emitted in every `pil_structure_diff` payload under
`interpretation_limits`, and enforced by a test, so it travels with the data rather
than living only in this README.

## Development

```bash
uv sync
uv run pytest -v
```

**663 tests.** Fixtures are generated synthetically in-process, so no binary test
assets are committed.

Six tests confirm results against a real reference image and **skip when it is
absent** — so a fresh clone reports `657 passed, 6 skipped`, which is expected.
Their strongest assertions are duplicated unskipped against a synthetic
stand-in, so a clean checkout still guards every known regression. A further
nineteen tests across `tests/test_blender_mesh.py`, `tests/test_blender_render.py`
and `tests/test_character_sheet_review.py` run only when an external corpus
and a Blender install are both present — the corpus path defaults to a known
location and can be overridden per test file, mirroring
`PIL_AGENT_REFERENCE_IMAGE` (see
[`runs/2026-08-20-blender-mesh-validation/`](runs/2026-08-20-blender-mesh-validation/README.md),
[`runs/2026-08-20-blender-render-validation/`](runs/2026-08-20-blender-render-validation/README.md),
[`runs/2026-08-20-character-sheet-loop/`](runs/2026-08-20-character-sheet-loop/README.md));
they are already counted as passing above on a machine that has both, and skip
cleanly otherwise.

To run those six, point the env var at any complex, predominantly dark image with
small vivid accents:

```bash
PIL_AGENT_REFERENCE_IMAGE="/path/to/image.png" uv run pytest -v
```

The original validation image is not distributed.

## How this was validated

Design decisions here came from measurement, not anticipation. The evidence bundle
in [`runs/2026-08-18-pil-agent-plugin-phase1/`](runs/2026-08-18-pil-agent-plugin-phase1/README.md)
contains a 13-step RED→GREEN ledger, the raw JSON outputs, and a
[metric discrimination matrix](runs/2026-08-18-pil-agent-plugin-phase1/10-metric-discrimination-matrix.md)
showing that **only 4 of 11 metrics** separate a genuine colour change from a
no-op rescale — and that one metric answers the question backwards.

The tools were then taken to a real production task — reviewing a Blender game
character against its concept sheet — in
[`runs/2026-08-18-skeleton-warrior-asset-review/`](runs/2026-08-18-skeleton-warrior-asset-review/README.md).
That trial is the strongest evidence for the vision-first method in this repository:
**four of the reviewing agent's own confident visual conclusions were wrong**, and
measurement caught all four before they reached the defect list. Two would have sent
an artist to fix work that was already correct. It also records `pil_structure_diff`
correctly *refusing* the comparison via its own `aspect_ratio_mismatch` flag, and
draws a hard line between what the plugin measured and what harness code around it
measured.

## Documentation

- [`docs/index.md`](docs/index.md) — documentation index and open items
- [`docs/design-rationale.md`](docs/design-rationale.md) — why each metric exists,
  and what failed along the way
- [`docs/phase2-scope.md`](docs/phase2-scope.md) — planned work: perceptual ΔE2000
  colour distance, threshold calibration, contract-driven verdicts

## Status

**0.6.0 — constrained multi-view reconstruction.** The optional
`multiview-reconstruction` skill adds OpenCV contour preparation, a SciPy
least-squares template solver with full-rank and conflict refusal states,
Blender BVH clearance probing/bounded copy fitting, arbitrary locked-framing
Workbench renders, and worst-case review over every named view. The core
Pillow/NumPy install and existing `image-measurement` behavior are unchanged.
See [`docs/phase4-scope.md`](docs/phase4-scope.md).

**0.5.0 — Phase 3 complete.** All of Track A5 and Track B1/B2/B3 now ships
alongside Track A1-A4 from 0.4.0. The release also closes the first PR #7
critic round: hard-failed views make every contract predicate UNMEASURABLE,
character-sheet JSON contains stable logical render identifiers rather than
deleted temporary paths, support-insufficient comparisons refuse, and Blender
renders are staged atomically so failure preserves any caller-owned output.

**Track B2 + B3 — the character-sheet loop ships.** `pil_blender_render.py`
renders front/side/back from a `.blend` scene (headless Blender Workbench,
auto-framed from the mesh bounding box) and registers each against a
caller-supplied reference via `pil_structure_diff --foreground`;
`pil_character_sheet_review.py` composes those matched views into one
`pil_contract_verdict --pairs` call, so a whole character-sheet review is a
single contract evaluated over N registered pairs — worst-case aggregated,
proven end to end on real renders (a deliberately swapped view is not
averaged away by two good ones; a hard-failed view is represented, not
dropped). Verified against a real production character
(`C:\Projects\tms-heim\art\skeleton-crusaders\brute\`): the camera-axis
convention was verified two independent ways rather than assumed, a real
render-determinism defect (Blender's per-render PNG metadata) was found and
fixed, and matched real renders VIOLATE Phase 2's calibrated similarity
thresholds — an honest content-difference finding, not a defect; both new
tools accept `--thresholds` for callers who need a different bar. Cross-machine
render determinism is not claimed, only same-machine/same-install; comparison
metrics on a fixed image pair remain fully deterministic everywhere. Full
numbers: [`docs/index.md`](docs/index.md#status).

Track A5's three new-metric candidates each ran a real discrimination gate,
and it did its job: connected-component counting, silhouette shape descriptors
and projection-profile alignment were all **demoted** — each tool still exists,
is tested, and degrades honestly (a demoted candidate reports real diagnostic
numbers with an explicit low-confidence flag, never a silent overclaim), but
none is advertised as a validated discrimination capability. The one A5
candidate that ships is `pil_alignment`'s **WCAG contrast** half, which needed
no gate — verified against the standard's own published worked examples.

Track B1 ships: `pil_blender_mesh.py` reads real polygon/vertex/material
counts and bounding dimensions from a Blender scene headlessly, and
`pil_contract_verdict.py`'s `geometry.*` predicates now resolve real verdicts
when scene stats are supplied (refusing exactly as before when they are not).
Verified against a real production asset's revision history — including
resolving a genuine discrepancy between two sources of "ground truth" and
finding a claimed "topology-preserving" pair was actually scene-level
violated. Full numbers: [`docs/index.md`](docs/index.md#status),
[`docs/phase3-scope.md`](docs/phase3-scope.md#sequencing-and-gates).

**0.4.0 — coverage-weighted foreground, three new tools, region scoping.**

The headline fix: on RGBA input in `--foreground` mode, statistics are now
coverage-weighted. Previously a partially-covered edge pixel was composited onto
black and then counted like a fully-opaque one, which read a 5px-wide blade
**27.9 code values too dark** and — worse — made the reading depend on *where*
the object landed on the pixel grid, so a quarter-pixel re-render of an
unchanged asset could cross a calibrated threshold and report a change that
never happened. Measured after the fix: error against alpha-weighted truth is
**±0.0004** across all 19 corpus scenes, and the placement excursion is
**±0.0006** against a bound of 0.5. Full-frame behaviour is byte-identical.

Three new tools close the loop between measurement and vision:
[`pil_crop`](scripts/pil_crop.py) hands vision a native-resolution view of a
region the tools located, [`pil_annotate`](scripts/pil_annotate.py) draws
numbered boxes so a model can point precisely, and
[`pil_image_info`](scripts/pil_image_info.py) reports the file facts an image
never carries into a vision encoder. `--region` scopes every metric on both
diff tools, byte-equal to pre-cropping with `pil_crop`.

Foreground thresholds are now [split by mask source](runs/2026-08-20-foreground-recalibration/README.md)
and the alpha path is calibrated for the first time (luminance **0.997** against
the estimate path's 34.166, n=380, α=0.01). The annotation tool's legibility is
[verified by read-back](runs/2026-08-20-annotate-readback/README.md) — fresh
agents shown only the image transcribed 27 of 27 numerals correctly, and the
bundle records an earlier round that *failed*.

Phase 1 complete: tools built and validated, plugin packaged,
`claude plugin validate --strict` passing, and the package audited against and
conforming to [Agent Plugins 1.0.0](#standards-conformance).

0.2.0 adds foreground separation, driven by a production failure: run over two
asset renders sharing a preview background, full-frame similarity scored two
different objects 0.991. Both tools now estimate foreground coverage and flag
`background_dominant`, accept `--foreground` for masked, bbox-registered
measurement (alpha-derived or border-median OKLab, matching the Synty asset
index's visible-pixel definition), and support-gate the hue-shift verdict so a
handful of anti-aliased pixels cannot flip it.

0.3.0 implements [phase 2](docs/phase2-scope.md) in full: CIEDE2000 colour
distance hand-rolled in numpy and verified against all 34 published Sharma
reference values (`base/accent_palette_distance_de2000` are now the primary
colour signal); Neyman–Pearson threshold calibration over synthetic ground
truth with published per-metric detection limits (the
[calibration bundle](runs/2026-08-19-phase2-calibration/README.md) records
every derived constant with its n, α and CI — including one derivation that was
*rejected*, with the reason in `scripts/pil_common.py`); an opt-in LCh accent
gate (`--accent-space lch`); and `pil_contract_verdict.py` — declared-intent
verdicts where every null result carries its detection limit, `geometry.*` and
`style.*` refuse rather than approximate, and multi-pair aggregation is
worst-case so one broken view cannot be averaged away.

Those thresholds were then
[validated against a real production corpus](runs/2026-08-20-phase2-real-validation/README.md)
— a game asset's actual revision history — rather than only the synthetic data
they were derived from: **zero false alarms** across 160 real no-change controls
in full-frame mode, 21 of 24 real revision pairs detected in both modes, and the
three undetected ones below the published 2%-of-frame detection limit exactly as
that limit predicts. The published detection limits transfer to production
input, which is what makes a `SATISFIED` invariant's limit worth reading.

Known limitations, tracked in [`docs/index.md`](docs/index.md#open-items):

- Thresholds are calibrated against a small sample and need broader validation.
- Palette distance is Euclidean RGB, which is not perceptually uniform; it is
  deliberately demoted to supporting detail until ΔE2000 replaces it.
- There is no notion of *intended* versus *unintended* change yet.

## License

MIT — see [LICENSE](LICENSE).
