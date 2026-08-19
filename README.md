# pil-agent-plugin

An agent plugin that gives coding agents
**quantitative** image measurement — exact colour palettes, per-hue census, layout
statistics, perceptual hashes, and changed-region localisation.

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
| `skills/image-measurement/SKILL.md` | Portable skill, per the [Agent Skills spec](https://agentskills.io/specification) | Any Agent Plugins client |
| `.claude-plugin/plugin.json` | Claude Code's native manifest | Claude Code |
| `agents/` | Claude Code subagent — no portable equivalent in 1.0.0 | Claude Code |
| `.claude-plugin/marketplace.json` | Single-plugin marketplace, so the CLI can install this repo; declares Claude Code's own `$schema` | Claude Code |

All three manifests describe the same package. `agents/` and `.claude-plugin/` are
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
- **Pillow** and **numpy** — no other runtime dependencies
- **[uv](https://docs.astral.sh/uv/)** recommended, for a pinned environment

## Installation

```bash
git clone https://github.com/bsmi021/pil-agent-plugin.git
cd pil-agent-plugin
uv sync                        # installs Pillow + numpy into a local venv
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
uv run python scripts/pil_palette_diff.py   "reference.png"
uv run python scripts/pil_palette_diff.py   "reference.png" "render.png"
uv run python scripts/pil_structure_diff.py "reference.png" "render.png" --grid 4x3
uv run python scripts/pil_structure_diff.py "view_a.png" "view_b.png" --foreground
```

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
| Is this the same image? | structure | `dhash_distance`, `changed_area_fraction` |
| Same layout / composition? | structure | `structural_similarity` |
| What changed, and where? | structure | `changed_region_bbox_fractional`, `most_divergent_cells` |
| Did the colour scheme change? | palette | `accent_hue_shift_detected`, `hue_family_fraction_deltas` |
| What exact colours are used? | palette | `base_palette`, `accent_palette`, `hue_families` |
| More / less detailed? | structure | per-cell `edge_mean` (see caveat below) |
| Same **object**, ignoring the backdrop? | both, `--foreground` | same fields, foreground-masked |

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

**86 tests.** Fixtures are generated synthetically in-process, so no binary test
assets are committed.

Six tests additionally confirm results against a real reference image and **skip
when it is absent** — so a fresh clone reports `80 passed, 6 skipped`, which is
expected. Their strongest assertions are duplicated unskipped against a synthetic
stand-in, so a clean checkout still guards every known regression.

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

Known limitations, tracked in [`docs/index.md`](docs/index.md#open-items):

- Thresholds are calibrated against a small sample and need broader validation.
- Palette distance is Euclidean RGB, which is not perceptually uniform; it is
  deliberately demoted to supporting detail until ΔE2000 replaces it.
- There is no notion of *intended* versus *unintended* change yet.

## License

MIT — see [LICENSE](LICENSE).
