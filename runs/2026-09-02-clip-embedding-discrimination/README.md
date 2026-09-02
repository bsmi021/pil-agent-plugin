# CLIP embedding discrimination gate — 2026-09-02

Re-run of the `pil_embed.py` discrimination gate against a CLIP-family
visual encoder, on the **same corpus and the same pair list** as
[`runs/2026-08-31-embedding-discrimination`](../2026-08-31-embedding-discrimination/README.md),
so the two models are directly comparable. That first gate demoted
same-venue matching because mobilenetv2-12's related and unrelated bands
overlapped, and recorded a stronger embedding model as the supported
upgrade path. This is that upgrade, measured.

## Setup

- Model: `clip-vit-b32-visual.onnx` — the visual tower of OpenAI CLIP
  ViT-B/32, ONNX export published by jina-ai/clip-as-service. sha256
  `06395063c0a5c28b1a8d4bd585261501a878c8f52d1216db6c4cbb651f7c13f1`,
  input `pixel_values` [batch, 3, 224, 224], **512-dimensional** output
  (mobilenetv2-12: 1000-dimensional ImageNet logits).
- Preprocessing profile: `clip` — shortest side to 224 BICUBIC,
  center-crop 224, scale 1/255, CLIP mean/std
  ([0.48145466, 0.4578275, 0.40821073] / [0.26862954, 0.26130258, 0.27577711]),
  NCHW float32. This profile was added to the tool for this run; the
  previous gate's `imagenet` profile is unchanged and still the default.
- Runtime: onnxruntime 1.29.0, CPUExecutionProvider, single-threaded.
- Corpus and pair families: identical to the 2026-08-31 gate — 13 real
  photographs from one photographer's phone, split into **perturbation**
  (n=4, one photo vs its rescale / JPEG q60 / 75% center crop / 5° rotation),
  **same_venue** (n=5, different photographs of one venue) and
  **unrelated** (n=8).

Cosines come from `pil_embed`'s own embed path rather than a private
reimplementation; dhash is recomputed with `pil_image_analyze`'s helpers
for the same contrast column as before. `run-gate.py` is the exact script
that produced these numbers — its corpus paths are session-local (the
photographs are private and not committed), so it is kept for auditability,
not for re-execution.

## Results

| family | cosine min | cosine max | mobilenetv2-12 (2026-08-31) |
|---|---|---|---|
| perturbation (same image) | **0.8762** | 0.9995 | 0.9051 – 1.0000 |
| same_venue (related) | 0.6285 | 0.7535 | 0.4558 – 0.6263 |
| unrelated | 0.3357 | **0.5118** | 0.1101 – 0.4701 |

Every band is fully separated, in order, with no overlap:

```
unrelated  ≤ 0.5118  <  0.6285 ≤ same_venue ≤ 0.7535  <  0.8762 ≤ perturbation
                    margin 0.1167                    margin 0.1227
```

**ADVERTISED — robust same-image identification.** Perturbation minimum
0.8762 against a related-pair maximum of 0.7535. As with the previous
model, this extends identification past the point where perceptual hashes
break: the 75% center crop scores dhash Hamming 18 (inside the unrelated
range, 26–38) while cosine holds 0.8762, and the 5° rotation scores dhash
9 vs cosine 0.9063.

**ADVERTISED (new) — same-venue / related-scene ranking.** This is the
capability the previous gate demoted. The pair that forced that demotion,
two exhibits of one museum (`pirate_flag~pirate_skel`), rose from 0.4558
to **0.7503**; the unrelated pair that outscored it, a photographed
monitor vs a museum flag (`passkey~pirate_flag`), sits at 0.5118 — now
below *every* same-venue pair. Ranking related above unrelated is what was
demonstrated.

**Not a threshold.** The margins (0.1167, 0.1227) are narrow and the
families are small (n=4/5/8, one photographer's corpus). What this gate
supports is *ordering* — rank candidates by cosine — not reading a verdict
off a single value near a band edge. No decision threshold is published,
and none should be inferred.

**Numbers do not transfer between models.** CLIP compresses the whole
scale upward: 0.51 is an unrelated pair here and would have been a top
same-venue score under mobilenetv2-12. This is why `pil_embed` keys its
advertised capabilities on the model's sha256, refuses to compare
fingerprints across model hashes, and reports `model_gated: false` with a
`model_not_gated` flag for any model without a gate of its own.

## Control: what a wrong preprocessing profile does

`wrong-profile-control.json` is the same corpus and pairs through the same
CLIP model under the **wrong** (`imagenet`) profile:

| family | correct (`clip`) | wrong (`imagenet`) |
|---|---|---|
| perturbation | 0.8762 – 0.9995 | 0.9148 – 0.9998 |
| same_venue | 0.6285 – 0.7535 | 0.5730 – 0.7144 |
| unrelated | 0.3357 – 0.5118 | 0.4032 – 0.4989 |
| related-vs-unrelated margin | **0.1167** | 0.0741 |

The mismatch does **not** fail loudly, and that is the finding worth
recording: the wrong profile still produces well-formed vectors that still
separate all three families, so no output inspection reveals the error —
it merely eats 36% of the margin that the advertised capability rests on.
Hence the profile is an explicit named parameter, echoed in every payload
and enforced as part of the comparability key, rather than something the
tool guesses from the model file.

## Limits of this gate

- Same small corpus as the previous gate (4/5/8 pairs, one photographer's
  phone): the numbers bound what was measured, not the model.
- Still no same-OBJECT-different-angle family in the corpus (no true
  re-photograph of one object from a new viewpoint), so that case remains
  unmeasured and unadvertised for this model too.
- The model is a third-party ONNX export pinned by sha256. The gate
  verdict attaches to that exact file; a different export of "the same"
  CLIP is a different model until it has a gate of its own.
