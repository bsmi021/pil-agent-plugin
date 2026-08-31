# Embedding fingerprint discrimination gate — 2026-08-31

Gate for `pil_embed.py`'s cosine similarity, run before the capability was
advertised, per this repository's rule that a new metric must demonstrate
discrimination on real input or ship demoted.

## Setup

- Model: `mobilenetv2-12.onnx` (ONNX Model Zoo, validated/vision/classification),
  sha256 `c0c3f76d93fa3fd6580652a45618618a220fced18babf65774ed169de0432ad5`,
  1000-dimensional output.
- Runtime: onnxruntime 1.29.0, CPUExecutionProvider, single-threaded.
- Corpus: 13 real photographs (one photographer's phone: casino interiors,
  retail displays, outdoor landmarks, a pet, photographed screens, a night
  drone show) — real production-style input, not synthetic renders.
- Three pair families:
  - **perturbation** (n=4): one photo vs its 50% rescale, JPEG q60
    re-encode, 75% center crop, 5° rotation. Ground truth: same image.
  - **same_venue** (n=5): different photographs taken at the same venue
    (two rooms of one store, two rooms of one museum, two outdoor views of
    one town). Ground truth: related but distinct images.
  - **unrelated** (n=8): pairs sharing no subject or venue.

`gate-results.json` records every pair's embedding cosine and, for
contrast, its dhash Hamming distance.

## Results

| family | cosine min | cosine max |
|---|---|---|
| perturbation (same image) | **0.9051** | 1.0000 |
| same_venue (related) | 0.4558 | 0.6263 |
| unrelated | 0.1101 | **0.4701** |

**ADVERTISED — robust same-image identification.** Perturbation minimum
0.9051 against unrelated maximum 0.4701: full separation with a 0.435
margin on this corpus. This extends identification beyond the perceptual
hashes where they measurably break: the 75% center crop scores dhash
Hamming distance 18 (indistinguishable from the unrelated range, 26–38)
while embedding cosine holds 0.9051; the 5° rotation scores dhash 9 vs
cosine 0.9682. Rescale and re-encode are detected by both (dhash 0,
cosine ≥ 0.9997).

**DEMOTED — same-venue / same-place matching across different photos.**
The same_venue range [0.4558, 0.6263] OVERLAPS the unrelated maximum
0.4701: a photographed monitor vs a museum flag (unrelated, 0.4701)
scored higher than two exhibits of the same museum (0.4558). Cosine in
the 0.45–0.63 band cannot distinguish "same place" from "similar-looking
scene" with this model on this corpus. The tool still reports the raw
cosine — it is a real retrieval signal for *ranking* candidates — but no
"same venue" verdict is advertised, and none should be inferred from a
mid-band value.

## Limits of this gate

- n is small (4/5/8 pairs) and the corpus is one photographer's phone;
  the numbers bound what was measured, not the model.
- No same-OBJECT-different-angle family existed in the corpus (no true
  re-photograph of one object from a new viewpoint), so that case is
  unmeasured — treat it as demoted alongside same-venue.
- The default model's descriptor is an ImageNet classification output
  space; a stronger embedding model (e.g. a CLIP-family visual encoder in
  ONNX) may pass the venue family. The tool is model-agnostic and pins the
  model by sha256, so re-running this gate against a new model is the
  supported upgrade path.
