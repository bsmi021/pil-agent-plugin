# W4 `pil_annotate` read-back bundle — 2026-08-20

Deliverable for **A4.4** and **D8**: the grounding loop verified by a model
reading the overlay, not by a unit test asserting that pixels were drawn.

A4.4 requires an agent **shown the annotated PNG and not the JSON** to
transcribe every box number correctly, on at least three images spanning light,
dark and busy content, with the transcript quoted. This bundle records five
images: the three required, plus the two edge cases from the W4 fix round
(extreme-right-edge placement and overlapping boxes), because those are exactly
the cases a unit test can prove geometrically and still get wrong visually.

**Result: 27 of 27 numerals transcribed correctly across five images.** One
numeral — the `2` in `edge_right.png` — was read correctly but with stated low
confidence by two independent readers. That residual is recorded in full below
rather than dropped, and it is not fixed.

An earlier round of this same procedure, run against the pre-fix renders, is
recorded in "First round" below. It failed in a way no test in the tree covered,
and the fix it forced is the reason the numbers moved.

---

## Procedure

Each read-back was performed by a **fresh `general-purpose` subagent with no
prior context** — not a fork of the annotating session, which would already know
where the boxes were. Each agent received one image path and this prompt
verbatim, with only the path differing:

> Open the image at `<ABSOLUTE PATH>` with the Read tool.
>
> Some numbers have been drawn onto this image. List every number you can see,
> and for each one say where in the image it sits. Then state plainly whether
> any number was unclear, ambiguous, or something you had to guess at.
>
> Do not open, read, list, or search any other file. The image is the only thing
> you may look at. Do not run any commands.

The prompt names no tool, no box count, no expected range, and does not mention
annotation, `pil_annotate`, or a legend. The JSON payloads in `legends/` were
written **after** the renders and were never shown to any reader; each reader's
single tool call was the `Read` of its own PNG. The five agents ran
concurrently and could not see each other's answers.

Environment: Pillow 10.4.0, Python 3.12.10, Windows 11.

## Files

`sources/` are the inputs, `annotated/` the overlays that were read, `legends/`
the JSON that was withheld from the readers and is published here as the ground
truth to grade the transcripts against.
`make_sources.py` regenerates the four synthetic sources.

### Source sha256

| file | sha256 | note |
|---|---|---|
| `sources/light_ui.png` | `3820e54e4367feba424ece4f47b2540478ddef6f95779d0fb27c2a5e10d8fb86` | synthetic, 700x500 |
| `sources/dark_dashboard.png` | `ddc7f3cf53ebdbdc11804333b8390830cce4484a8ea6a638b7b0cc994511f770` | synthetic, 700x500 |
| `runs/2026-08-18-skeleton-warrior-asset-review/_regions/model_full.png` | `6bc7ac68ec7063e91dd09167dbaf46d4e49a96c3779a244aba8466344db46df6` | real render, 900x1395 |
| `sources/edge_right.png` | `b392414063a90967875f9fd33dd65b6ba6d71984ea8c084e550aff4572fe6afc` | synthetic, 400x300 |
| `sources/edge_overlap.png` | `135761391807d40b71e936a636ffb454e99d28e84e8256f8729d6a588a40a7fe` | synthetic, 400x300 |

### Annotated sha256 — these are the exact bytes that were read

| file | sha256 |
|---|---|
| `annotated/light_ui.png` | `7e9275f26e0ce1fa6c53995c30802c38aa973e014204d6e1de246bedac04f545` |
| `annotated/dark_dashboard.png` | `82dacb9feb5ce0e649256bd5a65fe4fc5e335d1d75cb1a84b6f1b6988b4f20cb` |
| `annotated/busy_model.png` | `533fc67691f0dc30b723f7706262dd1426782c58b36248d479c81bc2f055797d` |
| `annotated/edge_right.png` | `b3888e984a6c1b4622cd7e009a5eba7fc17246f6124438674ae60ba73f9c50ac` |
| `annotated/edge_overlap.png` | `1960984346d929c33dabd86fc9a663d11f8e31e219b01b326c7fcd5661b7009b` |

`edge_right.png` re-rendered three times in a row produced
`b3888e...c50ac` each time.

---

## Image 1 — light content, 6 boxes

```
python runs/2026-08-20-annotate-readback/make_sources.py
python scripts/pil_annotate.py runs/2026-08-20-annotate-readback/sources/light_ui.png \
  --out runs/2026-08-20-annotate-readback/annotated/light_ui.png --overwrite \
  --box 0.03,0.05,0.97,0.19 --box 0.03,0.24,0.47,0.60 --box 0.51,0.24,0.97,0.60 \
  --box 0.60,0.30,0.80,0.54 --box 0.03,0.66,0.97,0.94 --box 0.07,0.75,0.17,0.90
```

Ground truth: numerals **1 2 3 4 5 6**, all `#000000`, all `outside_top_left`,
`flags: []`.

Transcript, verbatim:

> **Numbers present: 1 through 6, six in total.**
>
> | Number | Where it sits |
> |---|---|
> | **1** | Top-left corner of the image, just above the full-width pale grey/lavender header band that holds the two grey placeholder bars. |
> | **2** | Left side, roughly mid-upper (just under the header band). Labels the top-left corner of the white card containing the two blue bars and one grey bar. |
> | **3** | Same row as 2, but on the right half. Labels the top-left corner of the cream/pale-orange panel that contains the orange circle. |
> | **4** | Inside panel 3, above and slightly left of the orange circle. Labels the top-left corner of the smaller box drawn tightly around that circle (a nested box inside 3). |
> | **5** | Left side, lower part of the image, just above the full-width white panel holding the row of six green bars. |
> | **6** | Inside panel 5, near its left edge. Labels the top-left corner of the small box drawn around the first (shortest) green bar. |
>
> **On clarity — plainly:** none of the six was unclear, ambiguous, or guessed
> at. All are single-digit black numerals at good contrast against a light
> background, and each read unambiguously.

**Grade: 6/6 correct. No hedge.**

---

## Image 2 — dark content, 5 boxes

```
python scripts/pil_annotate.py runs/2026-08-20-annotate-readback/sources/dark_dashboard.png \
  --out runs/2026-08-20-annotate-readback/annotated/dark_dashboard.png --overwrite \
  --box 0.03,0.04,0.97,0.18 --box 0.03,0.22,0.49,0.58 --box 0.53,0.22,0.97,0.58 \
  --box 0.62,0.28,0.88,0.53 --box 0.03,0.64,0.97,0.94
```

Ground truth: numerals **1 2 3 4 5**, all `#ffffff`, `flags: []`. Placements are
mixed — `inside_top_left`, two `outside_bottom_left`, `outside_top_left`,
`outside_top_right` — which is what makes this image a test of the placement
record and not just of the digit table.

Transcript, verbatim:

> Five numbers are drawn on the image, all in white digits.
>
> **1** — top-left of the image, just inside the upper-left corner of the
> full-width top panel, sitting above and slightly left of the gray horizontal bar.
>
> **2** — bottom-left corner of the bar-chart panel (middle-left), on the seam
> between the middle row of panels and the bottom line-chart panel.
>
> **3** — same horizontal baseline as 2, at the bottom-left corner of the
> right-hand panel (the one containing the orange ellipse).
>
> **4** — inside the right-hand panel, at the top-left corner of the inner orange
> rectangle that frames the red/orange ellipse.
>
> **5** — same horizontal baseline as 2 and 3, at the far right edge of the
> image, at the bottom-right corner of the right-hand panel.
>
> **Clarity:** All five digits were clear and unambiguous. Nothing was guessed.
> The only thing worth noting is placement rather than legibility — the **5**
> sits right up against the right edge of the image, though the glyph itself is
> fully formed and readable.

**Grade: 5/5 correct. No hedge.** The reader's note about `5` is about the
labelled panel reaching the right edge; the glyph itself is at x 664–679 in a
700px frame, 21px clear of the border, and the reader confirms it is fully
formed.

---

## Image 3 — busy content, 12 boxes, two-digit numerals

Real 900x1395 render already committed to this repository.

```
python scripts/pil_annotate.py runs/2026-08-18-skeleton-warrior-asset-review/_regions/model_full.png \
  --out runs/2026-08-20-annotate-readback/annotated/busy_model.png --overwrite \
  --box 0.417,0.075,0.600,0.208 --box 0.411,0.215,0.606,0.287 \
  --box 0.000,0.251,0.222,0.315 --box 0.778,0.251,1.000,0.315 \
  --box 0.450,0.319,0.556,0.373 --box 0.600,0.323,0.650,0.373 \
  --box 0.483,0.391,0.533,0.416 --box 0.361,0.401,0.456,0.459 \
  --box 0.650,0.573,0.733,0.724 --box 0.311,0.631,0.689,0.681 \
  --box 0.367,0.695,0.622,0.846 --box 0.356,0.860,0.633,0.925
```

Ground truth: numerals **1** through **12** — including the two-digit **10 11
12** — over cluttered low-poly geometry, glyph colours mixed black and white,
`flags: []`.

Transcript, verbatim:

> **Numbers found — 12 total, 1 through 12 with no gaps and no repeats.**
>
> | # | Where it sits |
> |---|---|
> | 1 | Top of the image, just above the box drawn around the skull/head (~x 383, y 95) |
> | 2 | Below the box around the chainmail collar / shoulder cape, sitting on the upper chest of the black surcoat (~x 377, y 410) |
> | 3 | Far left, above the box on the figure's outstretched left arm (viewer's left), near the gauntlet at the image edge (~x 190, y 335) |
> | 4 | Far right, mirror position — above the box on the viewer's-right arm/gauntlet (~x 708, y 335) |
> | 5 | Center chest, above the box framing the red cross emblem (~x 412, y 433) |
> | 6 | Right of 5, above the small box around the gold sword pommel/knob at the shoulder strap (~x 545, y 435) |
> | 7 | Center waist, above the small box around the belt buckle (~x 443, y 533) |
> | 8 | Left hip, above the box around the leather pouch/holster (~x 330, y 545) |
> | 9 | Right side, mid-lower, above the tall narrow box running down the sheathed sword/scabbard (~x 590, y 785) |
> | 10 | Lower left, at the top-left corner of the wide box spanning the red hem plates of the tabard (~x 290, y 868) |
> | 11 | Above the knees, at the top-left of the box around both greaves/shin armor (~x 343, y 982) |
> | 12 | Bottom of the image, *below* the box around both feet (~x 335, y 1302) |
>
> **Was anything unclear, ambiguous, or guessed at?**
>
> No digit was unclear. All twelve are rendered as white text with a dark outline
> against flat backgrounds, at a size where each glyph is unambiguous. I did not
> have to guess at any character, and there was no number I saw partially, cut
> off, or overlapping another mark. Nothing was hidden behind geometry.
>
> Two honest caveats that are about *placement*, not legibility:
>
> 1. **Label-to-box association for 2 and 12 is an inference, not something the
>    image states.** Most labels sit above the top-left/top-right of their box.
>    Labels 2 and 12 sit *below* their boxes instead. I matched 2 to the collar
>    box above it and 12 to the feet box above it because those are the nearest
>    unlabeled boxes, but if the convention is strictly "label above its box,"
>    those two could in principle be intended for something below them. The
>    digits themselves are certain; the pairing is my reading.
> 2. **The coordinates I give are eyeballed, not measured.**

**Grade: 12/12 correct, including all three two-digit numerals. No hedge on any
digit.**

The reader's caveat 1 is accurate and worth keeping: `2` and `12` really were
placed `outside_bottom_left`, because the outside-above candidate collided with
another box's outline in each case. The reader recovered the intended pairing
but recorded that it was an inference. A caller who needs the pairing certain
should read `legend[*].glyph_placement`, which states it exactly. The reader's
description "white text with a dark outline" is a misdescription of a plain
white glyph on dark geometry; no outline is drawn around numerals.

---

## Image 4 — extreme right frame edge (the A4/F2 case)

The box is 12px wide and touches the right frame edge, so the numeral cannot be
drawn left-aligned to the box without leaving the frame.

```
python scripts/pil_annotate.py runs/2026-08-20-annotate-readback/sources/edge_right.png \
  --out runs/2026-08-20-annotate-readback/annotated/edge_right.png --overwrite \
  --box 0.97,0.30,1.00,0.60 --box 0.10,0.13,0.50,0.67
```

Ground truth: numerals **1** (`outside_top_left`) and **2**
(`outside_top_shifted`, glyph_rect `[382, 68, 397, 89]`), `flags: []`.

Transcript, verbatim:

> **Two numbers total** in the image (400×300, pale background with grey
> gridlines).
>
> **Number "1"** — top-left of the image, at roughly x≈45, y≈25 [...] Black glyph
> on the pale background, fully inside the frame, crisp and unobstructed.
>
> **Number "2"** — far right edge of the image, at roughly x≈385–400, y≈65–85. It
> sits directly on top of the tall narrow red/orange bar that hugs the right
> border [...] The glyph is dark, drawn right at the top of that bar, and is
> jammed against the image's right boundary.
>
> **Plainly, on certainty:**
>
> - The **"1" was not a guess** — it is clearly and unambiguously a 1.
> - The **"2" was partly a guess.** Its best reading is "2", but I am not fully
>   confident. It is clipped by the right edge of the image, so the right-hand
>   side of the glyph appears cut off, and it overlaps the dark top of the red
>   bar, which kills the contrast. Under those conditions it could plausibly be
>   another rounded digit. Treat that second value as a low-confidence reading,
>   not a verified one.

**Grade: 2/2 correct — but the `2` is a recorded residual, not a clean pass.**

This is the honest weak point of the bundle and it is not fixed. Both this
reader and the first-round reader transcribed `2` correctly and both hedged.
Facts against the reader's stated reasons:

* The glyph is **not** clipped. It occupies x 382–396 in a 400px frame, three
  pixels clear of the border, and every one of its 21 rows is present. The
  perception of clipping at 3px of clearance is itself the finding: a numeral
  near the frame edge reads as truncated even when it is whole.
* The contrast complaint is real. The footprint spans y 68–88 while the red bar
  begins at y 80, so the lower 40% of a black numeral sits on `#c82828`
  (luminance 74) and the upper 60% on `#f5f5f0` (luminance 245). No single
  glyph colour is good across that footprint; black is the better of the two
  (worst-case separation 74, against 10 for white), and black is what was
  chosen.

Fixing this properly needs content-aware placement — scoring candidate
footprints by background uniformity, not only by geometry. That is not
implemented, and no claim is made here that this case is comfortably legible.
What is claimed is narrower and is what A4.4 asks for: the numeral was
transcribed correctly, twice, by readers who had never seen the box list.

---

## Image 5 — overlapping boxes (the A4/F3 case)

Two boxes with equal tops whose left edges are 8px apart, well inside one glyph
width — the configuration that used to stack both numerals on the same pixels.

```
python scripts/pil_annotate.py runs/2026-08-20-annotate-readback/sources/edge_overlap.png \
  --out runs/2026-08-20-annotate-readback/annotated/edge_overlap.png --overwrite \
  --grid 1x1 --box 0.10,0.0,0.30,0.40 --box 0.12,0.0,0.40,0.50
```

Ground truth: numeral **1** at `[105, 121, 120, 142]` (`outside_bottom_right`)
and **2** at `[48, 151, 63, 172]` (`outside_bottom_left`), `flags: []`.

Transcript, verbatim:

> **"1"** — lower-middle-left of the image, roughly x≈110, y≈130 in a ~400×300
> frame. It sits *inside* the blue filled block, near its bottom edge,
> immediately to the left of the vertical orange line at x≈125 [...] Black glyph
> on the blue fill; its right side nearly touches that orange border.
>
> **"2"** — just below and left of the "1", roughly x≈52, y≈162. It sits
> *outside* the blue block entirely, on the cream background, a little below the
> blue rectangle's bottom-left corner [...] Black glyph on cream.
>
> That is the complete list: **two numbers, 1 and 2**.
>
> - **Neither digit was ambiguous to read.** Both "1" and "2" are single black
>   glyphs with clean contrast (black on blue, black on cream) and I read them
>   directly, not by inference. I did not guess either value.
> - **But I am not confident the list is complete.** The file is named
>   `edge_overlap`, and I count what look like **three** orange rectangle
>   outlines [...] with two of them running off the **top edge** of the image —
>   their top borders are clipped at y=0. If each rectangle carries a label, a
>   third number would be missing from my list [...]

**Grade: 2/2 correct, no hedge on either digit.** The reader's completeness
doubt is about counting boxes, not reading numerals: the two boxes genuinely
begin at y=0 because the caller asked for `top=0.0`, and their overlap makes the
four vertical edges look like three rectangles. Both numerals are whole,
separated by 42px, and neither is crossed by an outline.

Note the reader inferred the filename hint `edge_overlap` from the path it was
given. The path had to be supplied for the read to happen at all; no other
information about the case was.

---

## First round — the failure this bundle caught

The same five images were rendered and read once before, against
`pil_annotate.py` as it stood at the start of the fix round. Recorded because a
bundle that shows only the passing run is worth less than no bundle.

Numerals were transcribed correctly in that round too, but three of the five
readers reported the numerals as **clipped by the frame**, and two downgraded
their reading to a guess:

* `edge_right.png`, glyph flush at x 385–400 (frame width 400):
  > "It straddles the right border of the image and its right-hand portion is cut
  > off by the image boundary. It **reads as `2`**, but only the left part of the
  > glyph is present. [...] it could plausibly be another digit with a similar
  > left profile (e.g. `3`), and I cannot rule that out."
* `edge_overlap.png`, both glyphs at y=2:
  > "Both digits are **clipped by the top border of the image**. [...] **\"1\"** —
  > I read it as a 1, but this is the weaker of the two. With the top clipped, a
  > \"1\" and a \"7\" [...] are hard to separate [...] This one I am partly
  > guessing at."
* `busy_model.png`, glyph flush at x 0–15:
  > "**Number 3 is the one I am least sure of.** Its box is flush against the left
  > border of the image and the label glyph is clipped by that border. [...] I am
  > partly inferring it from position in the sequence rather than seeing the whole
  > character."

In all three the glyph was geometrically whole and inside the frame. No test in
the tree could have found this, and neither the tool's own legend nor a pixel
assertion would have reported anything wrong. It took a reader.

Two changes followed, both in `scripts/pil_annotate.py`:

1. **The frame border became a placement hazard** (`frame_edge`, flagged as
   `glyph_touches_frame_edge` when unavoidable), with a margin of one glyph cell
   — `label_scale` pixels, 3 at the default. Busy `3` moved from x 0 to x 185;
   `edge_right`'s `2` moved from x 385 to x 382 via a new
   `outside_top_shifted` placement that stays outside the box while clearing the
   border.
2. **Every outside placement is now tried before any inside one**, per §6.1's
   occlusion rule. `edge_overlap`'s numerals moved off the top edge entirely, to
   open background below their boxes.

Second-round results are the five sections above: no reader reported a clipped
glyph on `busy_model.png` or `edge_overlap.png`, and both remaining hedges on
`edge_right.png` are recorded rather than resolved.

---

## What this bundle does and does not establish

**Establishes.** On these five images, agents shown only the PNG transcribed
every numeral correctly — 27 of 27, including the two-digit 10, 11 and 12 —
across light, dark, busy, right-edge and overlapping-box content. The digit
table is legible to a reader at `--label-scale 3` without any font.

**Does not establish.** That every numeral is *comfortably* legible: the
`edge_right` `2` was correct but low-confidence for both readers who saw it, and
that is unresolved. That box-to-numeral *association* is unambiguous where boxes
overlap or a numeral is placed below its box — two readers correctly noted they
were inferring the pairing. That any of this generalises past these five images
and this Pillow version; it is five images, read once each per round.

**Not measured here.** Byte determinism, shuffle invariance, source
immutability, rejection paths and the geometric placement invariants are
asserted in `tests/test_annotate.py`, not in this bundle. The read-back exists
to cover exactly the thing those tests cannot: whether a model can read the
result.
