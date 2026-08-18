# Phase 2 research: perceptual colour distance and threshold calibration

**Last updated:** 2026-08-18
**Status:** research input for phase 2. No implementation code here by design.
**Scope:** answers the three questions in
`runs/2026-08-18-pil-agent-plugin-phase1/briefing-phase2-research.md`.

## How to read the verification markers

Every substantive claim below carries one of:

| Marker | Meaning |
| --- | --- |
| **VERIFIED** | Retrieved from a primary source in this session; source URL given. Where a number could be checked by computation, it was. |
| **VERIFIED (computed)** | Derived by running the arithmetic in this session against verified inputs. The computation is reproducible from the formulas given here. |
| **SECONDARY** | Found in a real, citable source that is not the originating authority (a survey, a vendor note, a well-known secondary write-up). |
| **UNVERIFIED / RECALLED** | Stated from memory or inferred. Treat as a hypothesis to check before it becomes a constant in the code. |
| **NOT FOUND** | Searched for and could not locate. Do not fill the gap from memory. |

The single most important thing in this document is
[the Sharma test data](#a3-verification-data-sharma-wu--dalal-2005), which is
**VERIFIED byte-exact from the authors' own server** and was **VERIFIED
(computed)** to reproduce to all four published decimal places. Everything else
is secondary in importance.

---

# A. CIELAB and ΔE2000 without new dependencies

## A0. Assumed illuminant and observer

**Assumption, stated explicitly:** CIE 1931 2° standard observer, **D65**
illuminant, matching sRGB's own definition. All Lab coordinates below are
D65-referenced. This is *not* the same convention as ICC/Photoshop Lab — or
Pillow's own `convert("LAB")` — all of which are D50-referenced. The cost of
mixing them was measured, not assumed: **up to 7.5 ΔE00, averaging 1.75**, i.e. up
to seven times the conventional perceptibility threshold. See
[A4](#a4-does-pillow-have-a-usable-built-in-lab-conversion). The illuminant must
therefore be recorded in the tool's JSON output alongside the numbers.

The Sharma test data is defined purely in Lab coordinates and is therefore
illuminant-agnostic — it tests the ΔE00 formula, not the RGB→Lab chain. The two
must be tested separately.

## A1. sRGB → linear RGB → XYZ → CIELAB

### Step 1: 8-bit to normalised

`V = C / 255.0` for each of R, G, B, giving V in [0, 1].

### Step 2: inverse sRGB companding (the piecewise function, not γ = 2.2)

**VERIFIED** — Bruce Lindbloom, *RGB to XYZ*,
<http://www.brucelindbloom.com/Eqn_RGB_to_XYZ.html>:

```
v = V / 12.92                        if V <= 0.04045
v = ((V + 0.055) / 1.055) ** 2.4     otherwise
```

The same page states explicitly: *"Sometimes the more complicated special case of
sRGB shown above is replaced by a 'simplified' version using a straight gamma
function with γ = 2.2."* Lindbloom's own tables use the proper piecewise
function. Use the piecewise form; a plain 2.2 gamma is wrong, and wrong by the
most in the dark tones — exactly where this project's images live (the phase-1
notes record that a single global quantisation of a dark image spends its whole
budget on near-black tones).

Numpy form: `np.where(V <= 0.04045, V / 12.92, ((V + 0.055) / 1.055) ** 2.4)`.

### Step 3: linear RGB → XYZ (D65)

**VERIFIED** — Lindbloom, *RGB/XYZ Matrices*,
<http://www.brucelindbloom.com/Eqn_RGB_XYZ_Matrix.html>, row "sRGB / D65":

```
        | 0.4124564  0.3575761  0.1804375 |
[M]  =  | 0.2126729  0.7151522  0.0721750 |
        | 0.0193339  0.1191920  0.9503041 |
```

and its published inverse (XYZ → linear sRGB):

```
        |  3.2404542  -1.5371385  -0.4985314 |
[M]^-1= | -0.9692660   1.8760108   0.0415560 |
        |  0.0556434  -0.2040259   1.0572252 |
```

`[X Y Z]^T = [M] · [r g b]^T`, with r, g, b linear in [0, 1] and X, Y, Z in the
nominal range [0, 1] (i.e. Y = 1 for white, not 100).

Two independent self-consistency checks were run — **VERIFIED (computed)**:

1. `[M] · [M]^-1` = identity to 6 decimal places.
2. The row sums of `[M]` are `(0.95047, 1.0000001, 1.08883)` — i.e. the matrix
   maps linear RGB white `(1,1,1)` onto exactly the D65 white point below. That
   is the strongest available confirmation that matrix and white point are the
   same pair and have not been mixed across sources.

### Step 4: the D65 white point

**VERIFIED (computed)** from the matrix row sums above, and consistent with
Lindbloom's site-wide D65 reference white:

```
Xr = 0.95047    Yr = 1.00000    Zr = 1.08883
```

**Implementation note (important):** because the published matrix rounds to 7
decimals, its Y row sums to `1.0000001`, not `1.0`. A perfectly neutral sRGB grey
therefore does **not** land on `a* = b* = 0` exactly. Measured — **VERIFIED
(computed)** — for `#808080`:

```
L* = 53.5850   a* = -9.998e-06   b* = 3.999e-06   C* = 1.077e-05
```

`C*` is ~1e-05 rather than 0, and the resulting hue angle is a completely
meaningless `158.20°`. Every grey from `#404040` to `#ffffff` produces that same
spurious `158.20°`. This is not a rounding curiosity — it is a live hazard for
section B, where hue angle drives family assignment. **Hue must be gated on a
chroma floor before it is ever used.** Alternatively, normalise the matrix so
each row sums exactly to the white point component; but the chroma gate is needed
regardless, because real near-neutrals have small-but-nonzero chroma too.

### Step 5: XYZ → CIELAB

**VERIFIED** — Lindbloom, *XYZ to Lab*,
<http://www.brucelindbloom.com/Eqn_XYZ_to_Lab.html>:

```
xr = X / Xr        yr = Y / Yr        zr = Z / Zr

f(t) = cbrt(t)              if t > eps
f(t) = (kappa * t + 16)/116 otherwise

L* = 116 * f(yr) - 16
a* = 500 * (f(xr) - f(yr))
b* = 200 * (f(yr) - f(zr))
```

The same page gives two variants of the constants and labels them:

| Constant | "Actual CIE standard" | "Intent of the CIE standard" |
| --- | --- | --- |
| `eps` | 0.008856 | 216 / 24389 |
| `kappa` | 903.3 | 24389 / 27 |

**Recommendation:** use the rational forms `eps = 216/24389` (≈ 0.0088564517)
and `kappa = 24389/27` (≈ 903.2962963). Lindbloom's linked *L\* continuity*
discussion is the rationale: the rational pair makes the two branches of `f`
join continuously in both value and first derivative, whereas the rounded
decimals leave a small discontinuity. For this project the practical effect is
tiny, but the rational forms are exact, free, and remove a class of "why does the
dark end wobble" question forever.

Numpy form for `f`: `np.where(t > eps, np.cbrt(t), (kappa * t + 16.0) / 116.0)`.
Use `np.cbrt`, not `t ** (1/3)`, so negative inputs from any upstream numerical
noise do not produce NaN.

### Step 6: CIELAB → LCh(ab), for section B

```
C* = hypot(a*, b*)
h_ab = degrees(atan2(b*, a*)) mod 360
```

`atan2(b, a)` — **that argument order**, b first. Result wrapped into `[0, 360)`.
Hue is undefined when `C* == 0` and meaningless when `C*` is near 0.

### Precision guidance

Do the whole chain in `float64`. `float32` is not enough: the `C**7` terms in
ΔE2000 reach ~6e15 for `C* = 180`, which is representable in float64 but loses
most of its significand in float32, and `25**7 = 6103515625` already exceeds
float32's exact-integer range.

## A2. The complete CIEDE2000 formula

### Source

**VERIFIED** — the authors' own reference MATLAB implementation,
`deltaE2000.m`, downloaded from
<https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/dataNprograms/deltaE2000.m>.
The paper is Sharma, Wu & Dalal, *"The CIEDE2000 Color-Difference Formula:
Implementation Notes, Supplementary Test Data, and Mathematical Observations"*,
Color Research and Application 30(1):21–30, February 2005, PDF at
<https://hajim.rochester.edu/ece/sites/gsharma/papers/CIEDE2000CRNAFeb05.pdf>.
The formulation below is a direct transcription of that MATLAB, restated in
numpy terms and with the radian/degree handling made explicit.

The presentation below follows the reference implementation's *radian-internal*
convention: all hue quantities are held in radians and converted to degrees only
inside the `Δθ` term. This is the formulation that was verified numerically, so
it is the one to implement. (The paper's own tables are in degrees; the two are
algebraically identical.)

### Inputs

`(L1, a1, b1)` is the standard, `(L2, a2, b2)` the sample. Parametric weighting
factors `kL = kC = kH = 1` by default.

### Step 1 — original chroma and the G compression term

```
C1_ab = hypot(a1, b1)
C2_ab = hypot(a2, b2)
C_bar_ab = (C1_ab + C2_ab) / 2

G = 0.5 * (1 - sqrt(C_bar_ab**7 / (C_bar_ab**7 + 25.0**7)))
```

`25.0**7 = 6103515625`.

### Step 2 — a' and C'

```
a1p = (1 + G) * a1
a2p = (1 + G) * a2

C1p = hypot(a1p, b1)
C2p = hypot(a2p, b2)

Cp_prod = C1p * C2p
zero_chroma = (Cp_prod == 0)
```

`b*` is **not** modified. `C'` is recomputed from the *modified* `a'` and the
*original* `b*`.

### Step 3 — h' with explicit quadrant and zero handling

```
h1p = atan2(b1, a1p);  if h1p < 0: h1p += 2*pi
h2p = atan2(b2, a2p);  if h2p < 0: h2p += 2*pi

if abs(a1p) + abs(b1) == 0: h1p = 0
if abs(a2p) + abs(b2) == 0: h2p = 0
```

The reference implementation comments that MATLAB already defines
`atan2(0,0) = 0` but sets it explicitly anyway. Numpy also returns 0.0 for
`np.arctan2(0.0, 0.0)`; set it explicitly regardless, so the code does not depend
on a library convention.

### Step 4 — the three differences

```
dL = L2 - L1
dC = C2p - C1p

dhp = h2p - h1p
if dhp >  pi: dhp -= 2*pi
if dhp < -pi: dhp += 2*pi
if zero_chroma: dhp = 0

dHp = 2 * sqrt(Cp_prod) * sin(dhp / 2)
```

`dC` and `dHp` are **signed**. The reference code's own comment: *"Note that the
defining equations actually need signed Hue and chroma differences which is
different from prior color difference formulae."* The sign matters only through
the `R_T` cross term, but it matters absolutely there.

### Step 5 — the means

```
Lp_bar = (L1 + L2) / 2
Cp_bar = (C1p + C2p) / 2

hp_bar = (h1p + h2p) / 2
if abs(h1p - h2p) > pi: hp_bar -= pi
if hp_bar < 0:          hp_bar += 2*pi
if zero_chroma:         hp_bar = h1p + h2p
```

This compact three-line form is exactly equivalent to the paper's three-case
table for `H̄'` and is what the reference implementation uses (its comment:
*"This is equivalent to that in the paper but simpler programmatically."*). For
cross-checking, the paper's table form is:

| Condition | `H̄'` (degrees) |
| --- | --- |
| `\|h'1 − h'2\| <= 180` | `(h'1 + h'2) / 2` |
| `\|h'1 − h'2\| > 180` and `(h'1 + h'2) < 360` | `(h'1 + h'2 + 360) / 2` |
| `\|h'1 − h'2\| > 180` and `(h'1 + h'2) >= 360` | `(h'1 + h'2 − 360) / 2` |
| `C'1 · C'2 = 0` | `h'1 + h'2` |

Note `L' = L` — CIEDE2000 does not transform lightness, so `L̄'` is the plain
arithmetic mean of `L1` and `L2`.

### Step 6 — weighting functions

```
q  = (Lp_bar - 50)**2
SL = 1 + 0.015 * q / sqrt(20 + q)
SC = 1 + 0.045 * Cp_bar

T  = 1 - 0.17 * cos(hp_bar - pi/6)
      + 0.24 * cos(2 * hp_bar)
      + 0.32 * cos(3 * hp_bar + pi/30)
      - 0.20 * cos(4 * hp_bar - 63*pi/180)

SH = 1 + 0.015 * Cp_bar * T
```

In degrees the four offsets are `−30°`, `0°`, `+6°`, `−63°`, and the four
coefficients are `−0.17, +0.24, +0.32, −0.20`. Signs alternate `− + + −`, which
is easy to get wrong.

### Step 7 — the hue-rotation term R_T

```
d_theta = radians(30) * exp(-(((degrees(hp_bar) - 275) / 25)**2))
RC      = 2 * sqrt(Cp_bar**7 / (Cp_bar**7 + 25.0**7))
RT      = -sin(2 * d_theta) * RC
```

`R_T` is **negative** by construction. `Δθ` is a Gaussian centred on
`H̄' = 275°` with a 25° scale — this is the blue-region correction, and it is the
only place in the formula where a degree value appears inside a non-trigonometric
expression.

### Step 8 — the result

```
dE00 = sqrt( (dL  / (kL * SL))**2
           + (dC  / (kC * SC))**2
           + (dHp / (kH * SH))**2
           + RT * (dC / (kC * SC)) * (dHp / (kH * SH)) )
```

### Where implementations typically go wrong

Each item below is a real, distinct failure mode. Items marked *(caught by test
data)* are exercised by the Sharma set; the rest need their own assertions.

1. **Using `Δh'` directly as the hue difference** instead of
   `ΔH' = 2·√(C'1·C'2)·sin(Δh'/2)`. The most common error. *(caught)*
2. **Not wrapping `Δh'` into (−180°, 180°]**. Produces ~2× errors on pairs that
   straddle the 0°/360° seam. *(caught — rows 1–6 and 21–24 straddle it)*
3. **Not forcing `Δh' = 0` when `C'1·C'2 = 0`.** Without it, a neutral-vs-chromatic
   pair produces garbage or NaN. *(caught — rows 7 and 8 have a pure neutral)*
4. **Getting `H̄'` wrong.** The three-case table exists solely because the naive
   mean of two angles straddling the seam is 180° away from the correct answer.
   `T`, and hence `S_H`, and `Δθ`, and hence `R_T`, all depend on `H̄'`, so this
   error propagates into three separate terms. *(caught — rows 9–16)*
5. **Computing `G` from `C'` instead of from `C*_ab`.** `G` must use the
   arithmetic mean of the *original* chromas. Using `C'` is circular. **Not
   caught by the test data in any obvious way** — write a dedicated assertion.
6. **Applying `(1+G)` to `b*` as well as `a*`.** Only `a*` is stretched.
7. **Recomputing `h'` from `(a*, b*)` instead of `(a', b*)`.** A subtle, small,
   systematic error.
8. **`atan2` argument order.** `atan2(b, a')`, not `atan2(a', b)`. Swapping them
   reflects the hue wheel about 45° and will still produce plausible-looking
   numbers for grey-ish inputs.
9. **Not mapping `h'` into `[0°, 360°)`.** `atan2` returns `(−π, π]`.
10. **Radian/degree confusion in `T` and `Δθ`.** `T`'s offsets are in the same
    unit as `H̄'`; `Δθ`'s Gaussian is in degrees *always*. Mixing these is the
    single most common source of "close but not quite" results.
11. **Sign of `R_T`.** It is `−sin(2Δθ)·R_C`. A dropped minus sign changes only
    blue-region pairs, so a test set without blues will pass. *(caught — rows 1–6
    are deep blues chosen precisely for this)*
12. **`float32` overflow / precision loss in `C**7`.** Use float64.
13. **Assuming the formula is asymmetric.** It is symmetric: swapping standard and
    sample flips the signs of `dL`, `dC` and `dH'` simultaneously, and the `R_T`
    cross term contains `dC·dH'`, so it is invariant. *(caught — rows 7 and 8 are
    the same pair swapped and both give 2.3669. A test asserting symmetry is
    cheap and worth having.)*
14. **`25**7` typed as `25**7` in an integer type that overflows**, or mistyped as
    `25e7`. The value is `6103515625`.
15. **Treating `L'` as transformed.** It is not; `L' = L`.

### The formula's documented discontinuities

Rows 9–15 of the test data are the authors' demonstration that ΔE00 is not
continuous. Rows 11 and 12 differ from rows 9 and 10 only in the sixth
significant figure of `b2` (`0.0009 → 0.0012`), yet the published ΔE00 jumps from
`7.1792` to `7.2195`. Rows 13–15 show the same effect in the other channel
(`4.8045 → 4.7461`). The cause is `H̄'` flipping across the three-case boundary.
This is a property of the formula, not a bug — but it means:

- Do **not** write a test asserting ΔE00 is continuous or Lipschitz.
- Do **not** "smooth" the `H̄'` cases to make the jump go away; that breaks
  conformance.
- These rows are the highest-value tests in the set. Keep them.

## A3. Verification data — Sharma, Wu & Dalal (2005)

**VERIFIED, byte-exact.** Downloaded with `curl` from the first author's own
server:

<https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/dataNprograms/ciede2000testdata.txt>

The file is tab-separated, no header, LF-terminated, **34 data rows**, 1830
bytes. Columns are `L1  a1  b1  L2  a2  b2  expected_dE00`.

**VERIFIED (computed):** the formula transcribed in [A2](#a2-the-complete-ciede2000-formula)
was implemented in numpy in a scratch directory and evaluated against all 34
rows. **Maximum absolute error after rounding to 4 decimal places: 0.0000.**
Every row reproduces the published value exactly at the published precision.
That result cross-validates the transcription of the formula above.

**VERIFIED (computed) — the markdown table below is itself machine-checked.** The
table was re-parsed *out of this document*, the index column stripped, and every
remaining float compared against the downloaded primary file. Result: **34 rows
parsed, 0 float-for-float mismatches, index column 1–34 intact.** This matters
because the artefact that becomes the RED tests is the table in this file, not
the file in the scratch directory — a transcription slip here would have been
invisible to the formula check above. Anyone editing the table should re-run that
comparison against the primary URL before trusting it again.

### The 34 pairs

| # | L1 | a1 | b1 | L2 | a2 | b2 | ΔE00 |
| --: | --- | --- | --- | --- | --- | --- | --- |
| 1 | 50.0000 | 2.6772 | -79.7751 | 50.0000 | 0.0000 | -82.7485 | 2.0425 |
| 2 | 50.0000 | 3.1571 | -77.2803 | 50.0000 | 0.0000 | -82.7485 | 2.8615 |
| 3 | 50.0000 | 2.8361 | -74.0200 | 50.0000 | 0.0000 | -82.7485 | 3.4412 |
| 4 | 50.0000 | -1.3802 | -84.2814 | 50.0000 | 0.0000 | -82.7485 | 1.0000 |
| 5 | 50.0000 | -1.1848 | -84.8006 | 50.0000 | 0.0000 | -82.7485 | 1.0000 |
| 6 | 50.0000 | -0.9009 | -85.5211 | 50.0000 | 0.0000 | -82.7485 | 1.0000 |
| 7 | 50.0000 | 0.0000 | 0.0000 | 50.0000 | -1.0000 | 2.0000 | 2.3669 |
| 8 | 50.0000 | -1.0000 | 2.0000 | 50.0000 | 0.0000 | 0.0000 | 2.3669 |
| 9 | 50.0000 | 2.4900 | -0.0010 | 50.0000 | -2.4900 | 0.0009 | 7.1792 |
| 10 | 50.0000 | 2.4900 | -0.0010 | 50.0000 | -2.4900 | 0.0010 | 7.1792 |
| 11 | 50.0000 | 2.4900 | -0.0010 | 50.0000 | -2.4900 | 0.0011 | 7.2195 |
| 12 | 50.0000 | 2.4900 | -0.0010 | 50.0000 | -2.4900 | 0.0012 | 7.2195 |
| 13 | 50.0000 | -0.0010 | 2.4900 | 50.0000 | 0.0009 | -2.4900 | 4.8045 |
| 14 | 50.0000 | -0.0010 | 2.4900 | 50.0000 | 0.0010 | -2.4900 | 4.8045 |
| 15 | 50.0000 | -0.0010 | 2.4900 | 50.0000 | 0.0011 | -2.4900 | 4.7461 |
| 16 | 50.0000 | 2.5000 | 0.0000 | 50.0000 | 0.0000 | -2.5000 | 4.3065 |
| 17 | 50.0000 | 2.5000 | 0.0000 | 73.0000 | 25.0000 | -18.0000 | 27.1492 |
| 18 | 50.0000 | 2.5000 | 0.0000 | 61.0000 | -5.0000 | 29.0000 | 22.8977 |
| 19 | 50.0000 | 2.5000 | 0.0000 | 56.0000 | -27.0000 | -3.0000 | 31.9030 |
| 20 | 50.0000 | 2.5000 | 0.0000 | 58.0000 | 24.0000 | 15.0000 | 19.4535 |
| 21 | 50.0000 | 2.5000 | 0.0000 | 50.0000 | 3.1736 | 0.5854 | 1.0000 |
| 22 | 50.0000 | 2.5000 | 0.0000 | 50.0000 | 3.2972 | 0.0000 | 1.0000 |
| 23 | 50.0000 | 2.5000 | 0.0000 | 50.0000 | 1.8634 | 0.5757 | 1.0000 |
| 24 | 50.0000 | 2.5000 | 0.0000 | 50.0000 | 3.2592 | 0.3350 | 1.0000 |
| 25 | 60.2574 | -34.0099 | 36.2677 | 60.4626 | -34.1751 | 39.4387 | 1.2644 |
| 26 | 63.0109 | -31.0961 | -5.8663 | 62.8187 | -29.7946 | -4.0864 | 1.2630 |
| 27 | 61.2901 | 3.7196 | -5.3901 | 61.4292 | 2.2480 | -4.9620 | 1.8731 |
| 28 | 35.0831 | -44.1164 | 3.7933 | 35.0232 | -40.0716 | 1.5901 | 1.8645 |
| 29 | 22.7233 | 20.0904 | -46.6940 | 23.0331 | 14.9730 | -42.5619 | 2.0373 |
| 30 | 36.4612 | 47.8580 | 18.3852 | 36.2715 | 50.5065 | 21.2231 | 1.4146 |
| 31 | 90.8027 | -2.0831 | 1.4410 | 91.1528 | -1.6435 | 0.0447 | 1.4441 |
| 32 | 90.9257 | -0.5406 | -0.9208 | 88.6381 | -0.8985 | -0.7239 | 1.5381 |
| 33 | 6.7747 | -0.2908 | -2.4247 | 5.8714 | -0.0985 | -2.2286 | 0.6377 |
| 34 | 2.0776 | 0.0795 | -1.1350 | 0.9033 | -0.0636 | -0.5514 | 0.9082 |

### What each group exercises

Grouping inferred from the structure of the data and the paper's stated purpose —
**UNVERIFIED** as to the authors' exact intent per row, but the geometry is
unambiguous:

- **1–6** — deep blues (`b* ≈ −80`), the `R_T` region. Rows 4–6 are constructed
  to give exactly `1.0000`.
- **7–8** — one member is the pure neutral `(50, 0, 0)`; and the pair is the same
  comparison in both orders, testing the `C'1·C'2 = 0` branch and symmetry.
- **9–16** — near-zero `a*`/`b*` with sixth-figure perturbations. The
  discontinuity set. Highest diagnostic value of the whole table.
- **17–20** — large differences across all three axes; catches gross errors.
- **21–24** — four different pairs all constructed to give exactly `1.0000`, i.e.
  points on a unit ΔE00 iso-surface around `(50, 2.5, 0)`.
- **25–30** — realistic mid-chroma pairs at real lightnesses; these are the rows
  that resemble actual image colours.
- **31–32** — near-neutral light colours (`L* ≈ 90`).
- **33–34** — very dark colours (`L* = 6.8` and `L* = 2.1`). Directly relevant to
  this project, whose images are dark.

### Also available from the same source

- `deltaE2000.m` — the authors' reference MATLAB implementation
  (<https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/dataNprograms/deltaE2000.m>).
  **VERIFIED**, downloaded and used as the transcription source for A2.
- `deltaE2000test.m` — their test harness. **NOT** downloaded.
- `CIEDE2000.xls` — spreadsheet implementation including the test data.
  **NOT** downloaded. Per the paper it also carries the per-pair *intermediate*
  quantities (`a'`, `C'`, `h'`, `H̄'`, `G`, `T`, `S_L`, `S_C`, `S_H`, `R_T`). If a
  future implementation fails a row and it is not obvious why, fetch this file —
  intermediates localise the bug in one step. Worth doing only if needed.

### Suggested test shape

The 34 rows are the RED tests. Assert to 4 decimal places
(`abs(got - expected) < 5e-5`, or `round(got, 4) == expected`), because that is
the precision at which the reference values are published. Do not assert tighter;
do not assert looser. Add the three uncaught-error assertions separately
(G-from-`C*_ab`, `b*` unmodified, `h'` from `a'`) and a symmetry property test.

## A4. Does Pillow have a usable built-in LAB conversion?

**Answer: yes, it works — and it is D50-referenced and 8-bit quantised, so do not
use it for ΔE2000. Hand-roll the chain in numpy.**

> **Correction, and a note on method.** Web research strongly suggested
> `Image.convert("LAB")` was unsupported and raised
> `ValueError: conversion from RGB to LAB not supported`, citing
> <https://github.com/python-pillow/Pillow/issues/6643> (filed against 9.2.0),
> <https://github.com/python-pillow/Pillow/issues/8357>, and the absence of any
> LAB routine from `src/libImaging/Convert.c` on `main`
> (<https://github.com/python-pillow/Pillow/blob/main/src/libImaging/Convert.c>).
> **That is wrong for the version this project actually runs.** Running the
> conversion in the project's own environment shows it succeeding. The
> documentary sources were believed and then falsified by a one-line experiment;
> the paragraphs below report the experiment, not the sources.
>
> **Also note a version discrepancy with the briefing.** The briefing states
> Pillow 12.0.0; the project environment reports **Pillow 12.3.0**
> (`uv run python -c "import PIL; print(PIL.__version__)"`). Every measurement in
> this section is against 12.3.0. Whether 12.0.0 behaves identically was **not**
> tested — if phase 2 pins 12.0.0, re-run the checks below.

### What actually happens (all VERIFIED (computed) against Pillow 12.3.0)

`Image.new('RGB',(1,1),(255,0,0)).convert('LAB')` succeeds and yields mode `LAB`
with pixel bytes `(138, 81, 70)`.

Decoding it, established by fitting nine diverse colours against both a D65 and a
D50 float reference chain:

```
L* = byte0 * 100.0 / 255.0                       # step 0.392
a* = byte1 - 256 if byte1 > 127 else byte1       # two's-complement signed int8
b* = byte2 - 256 if byte2 > 127 else byte2       # step 1.0
```

Two corrections to what the secondary sources implied:

1. **`a*`/`b*` are two's-complement signed bytes, not offset by +128.** For pure
   green Pillow returns `byte1 = 177`; `177 − 256 = −79`, which matches the D50
   reference `a* = −79.29`. An offset-by-128 reading would give `+49`, which
   matches nothing. The nominal range is `−128 … +127` in integer steps.
2. **The illuminant is D50**, and this is decisive rather than marginal. Comparing
   the decoded values against exact float Lab under both illuminants:

| sRGB | Pillow LAB decoded | exact D50 Lab | exact D65 Lab |
| --- | --- | --- | --- |
| `#ff0000` | 54.12, 81, 70 | 54.29, 80.81, 69.89 | 53.24, 80.09, 67.20 |
| `#00ff00` | 87.84, −79, 81 | 87.82, −79.29, 80.99 | 87.73, **−86.18**, 83.18 |
| `#0000ff` | 29.41, 68, −112 | 29.57, 68.30, −112.03 | 32.30, **79.19**, **−107.86** |
| `#ffff00` | 97.65, −16, 93 | 97.61, −15.75, 93.39 | 97.14, **−21.55**, 94.48 |
| `#808080` | 53.73, 0, 0 | 53.59, 0.00, 0.00 | 53.59, −0.00, 0.00 |

Green, blue and yellow settle it: the D65 and D50 values differ there by 7, 11 and
6 units respectively — far above the ±0.5 rounding floor — and Pillow tracks D50
every time. This is the expected ICC behaviour (the profile connection space is
always D50-adapted), and it is corroborated by `src/PIL/ImageCms.py`'s
`createProfile` docstring — **VERIFIED** — which states the default *"is for D50
illuminant if omitted (5000k)"* and that `colorTemp` *"is ONLY applied to LAB
profiles"*.

The D50 reference chain used above is Lindbloom's Bradford-adapted sRGB/D50
matrix — **VERIFIED** from
<http://www.brucelindbloom.com/Eqn_RGB_XYZ_Matrix.html> — whose row sums are
**VERIFIED (computed)** as exactly the D50 white point `(0.96422, 1.00000,
0.82521)`.

### Measured cost, in ΔE00

**VERIFIED (computed)** over a 4096-colour uniform sample of the sRGB cube
(every 17th level per channel), comparing Pillow's decoded LAB against exact
float Lab:

| Comparison | mean | median | p95 | max |
| --- | --: | --: | --: | --: |
| Pillow LAB vs exact **D50** float Lab — *pure 8-bit quantisation cost* | 0.200 | 0.184 | 0.374 | **0.822** |
| Pillow LAB read as if it were **D65** Lab — *quantisation + illuminant error* | 1.773 | 1.326 | 5.172 | **7.516** |
| exact D50 vs exact D65 float Lab — *illuminant mismatch alone* | 1.755 | 1.283 | 5.190 | **7.490** |

Also **VERIFIED (computed)**: no sRGB colour in that sample falls outside the
int8 range (`a*` spans `[−79.29, 93.55]`, `b*` spans `[−112.03, 93.39]` under
D50), so there is **no clipping** — the loss is purely rounding.

Read the third row against the first. The quantisation penalty is **0.2 ΔE00 on
average and 0.82 at worst** — real, but arguably tolerable. The **illuminant
mismatch is nearly nine times larger**, averaging 1.75 ΔE00 and reaching 7.5.
Since ~1.0 is the conventional perceptibility threshold, treating Pillow's LAB as
D65 Lab produces errors of up to **seven JNDs** — errors that are smooth,
hue-dependent, and entirely plausible-looking. That is the exact failure mode the
briefing was most worried about, and it is a trap you fall into by doing the
natural thing: calling `convert("LAB")` on an sRGB image and feeding the result
to a ΔE2000 routine written against the D65 literature.

### Verdict

Hand-roll `sRGB → linear → XYZ(D65) → Lab(D65)` in numpy `float64`. Reasons, in
order of weight:

1. **Illuminant, worth up to 7.5 ΔE00.** Pillow's LAB is D50. The ΔE2000
   literature, the Sharma-style reasoning about hue regions, and sRGB itself are
   D65. Carrying two illuminants in one codebase with only one of them named in
   the output is the highest-value bug this project can avoid for free.
2. **Quantisation, worth up to 0.82 ΔE00.** Comparable to a JND, and it is a
   *floor* — it cannot be reduced by measuring more carefully. A float64 pipeline
   has no such floor.
3. **The encoding is undocumented and surprising.** Two's-complement signed bytes
   for `a*`/`b*` is not stated in Pillow's docs, was contradicted by every
   secondary source consulted, and had to be established by experiment. Code
   depending on it is code depending on an unversioned implementation detail —
   and the `convert("LAB")` path's very existence appears to be version-sensitive.
4. **lcms may apply gamut clipping or black-point compensation** depending on
   rendering intent — a nonlinearity absent from the direct linear-algebra chain.
   Not measured here, and no clipping was observed, but it is an uncontrolled
   variable.
5. **The hand-rolled chain is ~15 lines, adds no dependency, and is testable.**
   The project already depends on numpy.

The one genuine cost is that the conversion must now be tested, which is why
[A1](#a1-srgb--linear-rgb--xyz--cielab) specifies self-consistency checks (matrix
inverse, white-point row sums) and why the illuminant must be echoed into the
JSON output.

**Useful corollary:** the measured D50-vs-D65 table above is itself a ready-made
test. If a future implementation's Lab output for `#0000ff` comes out near
`(29.6, 68.3, −112.0)` rather than `(32.3, 79.2, −107.9)`, it has silently
acquired a D50 chain — and that single assertion catches it.

## A5. Practical ΔE00 interpretation thresholds

This is the weakest-sourced part of section A. Read the markers carefully.

### The "1.0 = JND" claim

**SECONDARY, and contested.** John Seymour's analysis
(<http://johnthemathguy.blogspot.com/2017/07/is-10-delta-e-just-noticeable-difference.html>)
points out that CIE 142-2001, which defines ΔE00, does not use MacAdam/JND
terminology, and that JND (from MacAdam's 1942 ellipses) and ΔE (Munsell-derived,
CIELAB-based) come from different datasets and "will differ numerically" — though
ΔE00's own derivation did absorb large JND-type perceptual datasets. The
practical industry convention of "ΔE00 ≈ 1.0 is the edge of perceptibility" is
widespread but is a convention, not a standard.

Separately, the frequently quoted "**2.3** is the JND" figure applies to
**ΔE\*ab (CIE76)**, not ΔE00. Conflating the two is common and wrong.
**UNVERIFIED / RECALLED** as to the origin of the 2.3 figure.

### The popular 0–1 / 1–2 / 2–10 / 11–49 / 100 banding table

**Do not use this. Its own author disclaims it.** **VERIFIED** — zschuessler's
*Delta E 101* (<http://zschuessler.github.io/DeltaE/learn/>), which is the page
almost every blog restatement of that table traces back to, says verbatim: *"The
table above is built from the author's own tests and does not come from an
authority source"* and *"Do your own testing to determine threshold for your use
case."* The briefing's suspicion was correct.

### A citable alternative banding scale

**SECONDARY** — Mokrzycki & Tatol, *"Colour difference ΔE — A survey"*
(widely cited; located via
<https://www.researchgate.net/publication/286061341_Colour_difference_dE_-_A_survey>)
proposes:

| ΔE | Interpretation |
| --- | --- |
| 0–1 | not noticeable |
| 1–2 | only an experienced observer notices |
| 2–3.5 | an inexperienced observer notices |
| 3.5–5 | a clear difference is noticed |
| > 5 | perceived as two different colours |

**UNVERIFIED at primary-source level** — found via a secondary summary, not the
original paper text. But unlike the popular table it is a named, traceable
academic source. If phase 2 wants a banding scale in the tool's output, cite this
one and fetch the paper first.

### Measured perceptibility / acceptability thresholds

**SECONDARY, and from an unrelated domain.** A peer-reviewed dental-materials
study reports 50:50% perceptibility thresholds of ΔE00 ≈ 2.29 (dentists) / 2.27
(patients) and acceptability thresholds ΔE00 ≈ 2.41 / 2.83. Not independently
fetched — **UNVERIFIED**. The useful signal is directional: in an applied domain
with real observers, both thresholds cluster around **ΔE00 ≈ 2–3**, materially
higher than the "1.0" convention.

The foundational formula paper is Luo, Cui & Rigg (2001), *"The development of
the CIE 2000 colour-difference formula: CIEDE2000"*, Color Research &
Application 26:340–350, which combined perceptibility and acceptability datasets
(BFD-P, Leeds, RIT-DuPont, Witt). **SECONDARY** (citation located, text not
fetched).

**NOT FOUND:** a Sharma or Huang paper specifically on ΔE00
perceptibility/acceptability thresholds. Do not cite one.

### Industry standards

- **SECONDARY** — ISO 12647-2 solid-colour tolerances are commonly cited as
  ΔE ≤ 5 for process-colour solids and ≤ 3 for CMY midtone grey / paper white.
- **SECONDARY** — both ISO and Idealliance adopted ΔE2000 as the industry
  standard for colour difference around 2013; ISO/PAS 15339 (= CGATS21) defines
  the Characterized Reference Print Conditions used in Idealliance certification.
- **NOT FOUND / paywalled** — the exact numeric ΔE00 tolerance tables in
  ISO/PAS 15339 and Idealliance G7. Do not quote numbers from these.

### Recommendation for this project

Treat all of the above as **prior information, not as thresholds**. Section C
exists precisely because thresholds should come from measurement on this
project's own image distribution. Concretely:

1. Report raw ΔE00 in the JSON, always, unrounded beyond 4 dp.
2. If the tool emits a verbal band, cite Mokrzycki & Tatol explicitly in the
   output and label it as a literature heuristic, not a measurement.
3. Anchor the useful scale endpoints. **VERIFIED (computed):** black vs white
   (`L*=0` vs `L*=100`, both neutral) gives `ΔE00 = 100.000000` exactly at
   `kL = 1`, because `L̄' = 50` makes `S_L = 1` and every chroma and hue term
   vanishes. But **ΔE00 is not bounded at 100** — also **VERIFIED (computed)**:
   the maximum over the eight sRGB cube corners is **111.41** (`#00ff00` vs
   `#ff00ff`), and over a 216-colour sRGB grid it reaches **119.22** (`#000066`
   vs `#99ff00`). **Never present ΔE00 as a percentage or normalise it by 100.**
   Black-vs-white is a convenient landmark, not the maximum.
4. Derive the actual decision threshold from the no-change noise floor
   (section C), and expect it to land somewhere in the 1–3 range — which the
   literature above makes plausible but does not establish.

---

# B. Should hue bucketing move from HSV to LCh(ab)?

**Recommendation: yes for the accent mask (a clear, large win). Yes for hue
family assignment too, but the bucket boundaries must be re-derived from
measurement — porting the existing 0–255 bounds into degrees would be worse than
doing nothing.**

All measurements in this section are **VERIFIED (computed)** in this session
using the D65 chain from [A1](#a1-srgb--linear-rgb--xyz--cielab) and Pillow's own
`convert("HSV")` for the HSV side, so the HSV numbers are exactly what
`pil_common.py` sees today.

## B1a. The accent mask: concrete HSV failure modes

Current rule (`pil_common.py:147`): `HSV_S > 100 and HSV_V > 60`.

| Colour | HSV S | HSV V | accent today? | L\* | C\* | h_ab |
| --- | --: | --: | --- | --: | --: | --: |
| dim maroon `#401010` | 191 | 64 | **yes** | 12.60 | 26.37 | 27.99 |
| dark slate `#1e2430` | 95 | 48 | no | 14.12 | 8.81 | 275.93 |
| pale pink `#ffd0d0` | 47 | 255 | **no** | 87.45 | 17.65 | 20.74 |
| pale mint `#d0ffe4` | 47 | 255 | **no** | 96.22 | 21.60 | 158.31 |
| tinted near-black `#050508` | 95 | 8 | no | 1.43 | 1.21 | 290.17 |
| vivid yellow `#ffff00` | 255 | 255 | yes | 97.14 | 96.91 | 102.85 |
| vivid blue `#0000ff` | 255 | 255 | yes | 32.30 | 133.81 | 306.28 |
| UI cyan `#22b8cf` | 213 | 207 | yes | 68.84 | 36.39 | 217.52 |
| brown `#6b4a2f` | 142 | 107 | yes | 34.46 | 24.10 | 63.82 |
| mid grey `#808080` | 0 | 128 | no | 53.59 | 0.00 | *158.20 (spurious)* |

Four distinct failure modes fall out of that table:

1. **False positive on dark colours.** `#401010` is admitted as a "vivid accent"
   at `L* = 12.6` — a near-black maroon that carries essentially no perceptual
   identity. HSV `S` is a *relative* measure, `max−min` over `max`, so it stays
   high as a colour goes to black. This is the direct cause of the phase-1
   observation that a dark image's accent census is unreliable.
2. **False negative on light, low-chroma-but-clearly-coloured pixels.** Pale pink
   (`C* = 17.65`) and pale mint (`C* = 21.60`) are rejected, while dark slate
   (`C* = 8.81` — genuinely near-neutral) sits only just below the same bar. HSV
   ranks these *backwards* relative to perceived colourfulness.
3. **Ordering inversion.** HSV admits `#401010` (`L* = 12.6`) and rejects
   `#d0ffe4` (`L* = 96.2`) despite comparable chroma. There is no monotone
   relationship between `HSV_S` and `C*` at all, so no choice of `sat_min` fixes
   this; the two quantities measure different things.
4. **`V` is not lightness.** Vivid yellow and vivid blue both have `V = 255`, but
   `L* = 97.14` and `L* = 32.30`. A `val_min` threshold treats "as bright as
   possible" and "quite dark" identically. This is why `DEFAULT_ACCENT_VAL_MIN`
   could never be tuned to work across hues.

**Diagnosis:** `HSV_S` and `HSV_V` are cheap algebraic functions of `max`/`min`
over the *companded* RGB channels. They carry no perceptual calibration and no
gamma-correctness. `C*` and `L*` are, by construction, approximately perceptually
scaled and computed on linearised light. The replacement is not a tuning change,
it is a change of measurand.

**Bonus:** `C*` and `L*` are *interpretable*. A reader of the JSON can be told
"chroma above 20, lightness above 20" and reason about it against a colour
picker. "HSV saturation above 100 of 255" is uninterpretable without knowing the
formula.

## B1b. Hue family assignment: is LCh better?

**Yes, and by a large measured margin.** The test: how much perceptual difference
does a fixed angular step buy, at different points on the wheel?

**VERIFIED (computed)** — ΔE00 for a **10° step in HSV hue** at `S = V = 1`:

| HSV H | ΔE00 for +10° |
| --- | --: |
| 0° → 10° | 2.60 |
| 30° → 40° | 14.53 |
| 60° → 70° | 7.94 |
| 90° → 100° | 2.98 |
| 120° → 130° | **1.22** |
| 150° → 160° | 6.42 |
| 180° → 190° | **16.21** |
| 210° → 220° | 14.65 |
| 240° → 250° | **1.12** |
| 270° → 280° | 6.70 |
| 300° → 310° | 5.84 |
| 330° → 340° | 10.03 |

Range **1.12 to 16.21 — a 14.4× spread.**

**VERIFIED (computed)** — ΔE00 for a **10° step in `h_ab`** at fixed `L* = 60`,
`C* = 45`:

| h_ab | ΔE00 for +10° |
| --- | --: |
| 0° → 10° | 4.27 |
| 30° → 40° | 5.20 |
| 60° → 70° | 5.60 |
| 90° → 100° | 5.45 |
| 120° → 130° | 4.36 |
| 150° → 160° | 4.13 |
| 180° → 190° | 4.78 |
| 210° → 220° | 4.15 |
| 240° → 250° | **3.94** |
| 270° → 280° | **5.67** |
| 300° → 310° | 5.70 |
| 330° → 340° | 4.08 |

Range **3.94 to 5.70 — a 1.4× spread.**

**Answer to B3's "is LCh better or just differently uneven":** it is better by an
order of magnitude, and it is still uneven. `h_ab` is not perfectly perceptually
uniform (CIELAB's known hue-uniformity deficiency is exactly why ΔE00 has a `T`
term at all), but a 1.4× spread is a rounding concern where a 14.4× spread is a
correctness concern. Notably HSV's worst regions are `120°–130°` and
`240°–250°` — the pure green and pure blue plateaus, where an entire 10° of HSV
hue is perceptually almost nothing, and `180°–190°` where 10° is 16 ΔE. Bucket
boundaries placed by eye on the HSV wheel are therefore placed almost at random
in perceptual terms, which is a sufficient explanation for why the current
bounds needed hand-tuning.

## B2. Recommended thresholds and hue ranges

### Vivid-accent gate

```
accent  <=>  C* >= C_MIN  and  L* >= L_MIN
```

**Starting values, to be calibrated per section C — these are reasoned defaults,
not measured thresholds:**

- `C_MIN = 20.0`
- `L_MIN = 20.0`
- no upper `L*` bound

Rationale from the measured table above: genuine UI neutrals sit at
`C* ≈ 9–13` (`#1e2430` → 8.81, `#3c4658` → 12.04, `#8892a8` → 12.81), so a floor
of 20 clears them with margin. UI accents sit at `C* ≈ 24–78` (`#6b4a2f` → 24.10,
`#22b8cf` → 36.39, `#d64545` → 65.17, `#f0a30a` → 77.75), comfortably above. An
`L*` floor of 20 excludes `#401010` (`L* = 12.6`) and `#0a0a0c` (`L* = 2.8`),
which is the phase-1 false positive. No *upper* `L*` bound is needed and one
would be actively harmful: vivid yellow is `L* = 97.14` with `C* = 96.91`, so a
cap at 95 would discard the most saturated colour in sRGB. `C*` already excludes
white, which has `C* = 0`.

Consider also emitting a second, looser tier — `C* >= 10` — as "chromatic at
all", so the JSON distinguishes "no colour here" from "colour present but muted".
That is cheap and directly addresses the phase-1 complaint that a dark image's
accent census collapses.

### Hue-family ranges

**There is no authoritative set of CIELAB hue-angle boundaries for basic colour
names. NOT FOUND.** Searching returned only scattered application-specific
ranges (one patent-derived claim of "red = 355°–75°", another of
"blue = 225°–310°" — mutually inconsistent and unusable). Anything presenting
itself as *the* standard boundary table should be distrusted.

**SECONDARY** — for orientation, the four Hering unique hues sit at roughly
`h_ab`: unique red ≈ 25°, unique yellow ≈ 90°, unique green ≈ 165°, unique blue
≈ 247°–257° (MacEvoy's CIELAB a\*b\* plot; Kuehni's work on unique-hue
variability). Sources located only as secondary summaries — **UNVERIFIED**.
The important structural fact, which *is* solid, is that these four are **not**
at 0/90/180/270: CIELAB's hue circle is not aligned to perceptual opponent axes.

Given no standard exists, the defensible approach is to derive boundaries from
the sRGB colours users actually name, so that the label the tool emits matches
the word a human would use. **VERIFIED (computed)** anchor angles for the eight
fully-saturated sRGB hues currently in `HUE_FAMILIES`:

| Family | HSV deg | HSV 0–255 (current bounds' units) | h_ab | C\* | L\* |
| --- | --: | --: | --: | --: | --: |
| red | 0 | 0.0 | **40.00** | 104.55 | 53.24 |
| orange | 30 | 21.2 | **59.78** | 85.59 | 66.96 |
| yellow | 60 | 42.5 | **102.85** | 96.91 | 97.14 |
| green | 120 | 85.0 | **136.02** | 119.78 | 87.73 |
| cyan | 180 | 127.5 | **196.38** | 50.12 | 91.11 |
| blue | 240 | 170.0 | **306.28** | 133.81 | 32.30 |
| purple | 275 | 194.8 | **313.74** | 122.77 | 43.63 |
| magenta | 300 | 212.5 | **328.23** | 115.54 | 60.32 |

Midpoint boundaries between consecutive anchors — **VERIFIED (computed)**:

| Transition | angular span | boundary `h_ab` |
| --- | --: | --: |
| red → orange | 19.79° | **49.89°** |
| orange → yellow | 43.07° | **81.32°** |
| yellow → green | 33.16° | **119.43°** |
| green → cyan | 60.36° | **166.20°** |
| cyan → blue | 109.91° | **251.33°** |
| blue → purple | 7.45° | **310.01°** |
| purple → magenta | 14.50° | **320.99°** |
| magenta → red | 71.76° | **4.12°** |

Which yields these **bucket widths** — the numbers that actually get implemented,
so read them before adopting anything (**VERIFIED (computed)**, and they sum to
360.00):

| Family | bucket `h_ab` | width |
| --- | --- | --: |
| red | 4.12° – 49.89° | 45.77° |
| orange | 49.89° – 81.32° | 31.43° |
| yellow | 81.32° – 119.43° | 38.11° |
| green | 119.43° – 166.20° | 46.77° |
| cyan | 166.20° – 251.33° | **85.13°** |
| blue | 251.33° – 310.01° | 58.68° |
| purple | 310.01° – 320.99° | **10.98°** |
| magenta | 320.99° – 4.12° (wraps) | 43.13° |

**Purple gets an 11° sliver while cyan gets 85° — a 7.8× spread in bucket width,
in a space where a degree costs roughly the same everywhere (1.4×, per B1b).**
That is the fact to weigh Option 1 against Option 2, and it is a defect of the
inherited *names*, not of LCh.

Two consequences, both concrete:

1. **The `cyan → blue` boundary at 251.33° falls inside the unique-blue locus**
   (≈247°–257°, SECONDARY). So Option 1 splits perceptually-blue pixels across
   the `cyan` and `blue` families — a pure blue near 248° is labelled *cyan*.
   Since the whole point of the family census is to notice an accent hue
   disappearing, a boundary sitting on top of a perceptual anchor is the worst
   possible placement, and a small render change could flip pixels between the
   two buckets without any visible colour change.
2. **`blue` and `purple` are perceptually near-degenerate.** The sRGB blue primary
   lands at `h_ab = 306.28°`, which by any perceptual account is **violet**, and
   it is only 7.45° from the sRGB "purple" anchor at 313.74°. Meanwhile real
   bluish UI colours live well below both — `#22b8cf` at 217.52° (bucketed
   *cyan*) and `#1e2430` at 275.93° (bucketed *blue*).

**Two options, and a recommendation.**

- **Option 1 — keep the eight sRGB-anchored names, use the midpoint boundaries
  above.** Preserves label continuity with phase 1, and every boundary is now
  placed at a perceptually-metrized midpoint rather than by eye. Costs, per the
  two consequences above: the `blue`/`purple` distinction remains
  near-meaningless (11° bucket), the `cyan` bucket stays 85° wide, and the
  `cyan/blue` boundary sits on the unique-blue anchor. **Cheap partial mitigation
  worth taking even under Option 1:** nudge the `cyan → blue` boundary off the
  anchor — to ~235°, below the unique-blue locus — so that perceptually-blue
  pixels land in `blue` rather than straddling. That single change costs nothing,
  keeps all eight names, and removes the flip-flop risk; it makes `cyan` 68.8°
  and `blue` 75.0°, which is also more even than the midpoint version.
- **Option 2 — re-anchor to perceptual hue.** Drop to six or seven families
  spaced more evenly in `h_ab` (e.g. red ~30°, orange/yellow ~75°, yellow-green
  ~120°, green ~165°, cyan/teal ~210°, blue ~265°, magenta/purple ~320°), which
  puts an anchor in the genuinely-blue region and merges the redundant
  blue/purple pair. Cost: family names change, so phase-1 outputs are no longer
  directly comparable.

**Recommendation: Option 1 for phase 2, with the 235° boundary nudge**, because it
is a pure improvement over the status quo with no output-schema churn, and because
the real win in this section is the accent gate, not the family names. Record
Option 2 as a deferred item with a TODO, and state the `blue`/`purple` degeneracy
(an 11° bucket) explicitly in the tool's interpretation-limits output so a reader
is not misled into treating those two families as independent signals. Revisit
once section C's corpus can measure whether family reassignment ever actually
changes a verdict.

## B3. Wraparound and other hazards

1. **Hue is undefined at zero chroma, and the sRGB matrix makes "zero" not zero.**
   **VERIFIED (computed):** every neutral grey from `#404040` to `#ffffff`
   produces `C* ≈ 1e-05` and a spurious `h_ab = 158.20°` — which falls in the
   `green → cyan` bucket. Without a chroma gate, a greyscale image would be
   reported as uniformly green-cyan. **The chroma gate is not an optimisation; it
   is a correctness requirement.** Compute hue only where `C* >= C_MIN`, and emit
   an explicit `achromatic` count for the rest rather than silently bucketing it.
2. **The red family wraps.** `magenta → red` boundary is at `4.12°` and
   `red → orange` at `49.89°`, so red is `[4.12, 49.89)` — but the *general* case
   of a wrapping bucket must be handled, because Option 2 or any recalibration
   could move a boundary across 0°. Implement bucket membership as
   `(h - lo) mod 360 < (hi - lo) mod 360`, which handles wrapping uniformly and
   removes the need for the current two-range special case for red.
3. **Never average hue angles arithmetically.** The mean of 350° and 10° is 180°,
   not 0°. If any aggregate hue is ever reported, use the circular mean
   (`atan2(mean(sin h), mean(cos h))`), and weight it by chroma — an unweighted
   circular mean over near-neutrals is dominated by noise.
4. **Never take a plain difference of hue angles.** Use the signed wrapped
   difference `((h2 - h1 + 180) mod 360) - 180`. This is the same hazard as
   ΔE2000 failure mode #2 in [A2](#where-implementations-typically-go-wrong).
5. **A hue *rotation* of a fixed number of degrees in `h_ab` is not a fixed
   number of degrees in HSV H, or vice versa.** Relevant to section C: the
   synthetic corpus's "known hue rotation of known degrees" must state *which*
   space the rotation happened in, and the two are related by the 14.4× nonlinear
   warp measured in B1b. A ground-truth label of "rotated 15°" is meaningless
   without that qualifier. This is a trap that would silently corrupt the entire
   calibration.
6. **`h_ab` at very low `L*` is unstable.** Near black, small absolute changes in
   XYZ produce large swings in `a*`/`b*` direction. The `L_MIN` floor mitigates
   this; the surviving instability should be acknowledged rather than hidden.

---

# C. Threshold calibration methodology

## C1. Is synthetic-ground-truth calibration sound, and what will it get wrong?

**Sound as a floor-finding exercise; unsound as a substitute for real data.** It
will reliably tell you *how little a metric can detect* and *how much noise a
no-op produces*. It will not reliably tell you *what threshold to ship*, because
the perturbations you can generate are not the perturbations you will encounter.

The strongest external evidence for that caution comes from the image-quality
literature, which has run exactly this experiment at scale.

- **SECONDARY** — **KonIQ-10k** (Hosu et al., IEEE TIP 2020) was built
  specifically as an "ecologically valid" IQA database using *authentic*
  real-world distortions, on the explicit premise that models trained on
  synthetically applied distortions do not generalise to real images.
  <https://darus.uni-stuttgart.de/dataset.xhtml?persistentId=doi%3A10.18419%2Fdarus-2435>
- **SECONDARY** — **SPAQ** (smartphone photography, 11,125 images, 66 cameras)
  makes the same argument for camera-pipeline distortions. Not fetched at primary
  level.
- **SECONDARY** — the synthetic-to-real transfer gap is an active research topic;
  a 2026 arXiv paper, *"Towards Syn-to-Real IQA: A Novel Perspective on Reshaping
  Synthetic Data Distributions"* (<https://arxiv.org/html/2601.00225>),
  frames it as a known failure requiring pretrain-then-finetune approaches.

### Specific things synthetic calibration will get wrong here

1. **Perturbations will be *global and uniform*; real changes are *local and
   structured*.** A synthetic hue rotation applies a smooth function to every
   pixel. A real regression is "one button turned grey" — a small-area, high-
   contrast, semantically-loaded change. Metrics that pool over the whole frame
   (mean luminance, entropy, saturation mean) will look far more sensitive on
   synthetic data than they are in practice, because the synthetic perturbation
   moves every pixel in the same direction and the pooled statistic accumulates
   it coherently. **This is the single largest expected bias, and it biases
   toward over-optimism.**
2. **The real noise floor is environmental, not algorithmic.** Real screenshot
   pairs differ by font hinting, subpixel antialiasing, GPU/driver rasterisation
   differences, cursor position, animation phase, and scrollbar presence. None of
   those are reproducible by `Image.filter` or `Image.resize`. A noise floor
   measured from re-encode and rescale round-trips will be **too low**, so the
   shipped threshold will be too tight and will produce false alarms on the first
   real cross-machine comparison. This is precisely why visual-regression tools
   ship an antialiasing-tolerance switch — see C5.
3. **Perturbation magnitude is not monotone in "does a human care".** A 5° hue
   rotation over 100% of the frame and a 180° rotation over 2% of the frame can
   produce the same metric value while meaning entirely different things.
   Calibrating against a scalar "known magnitude" collapses a two-dimensional
   ground truth (extent × intensity) into one number. **Mitigation: make the
   corpus a 2-D grid over extent and intensity, and report the metric's response
   surface, not a curve.**
4. **Synthetic perturbations are exactly invertible and noise-free**, so they
   never test interaction effects — e.g. blur applied *after* a hue shift, or
   compression applied to an already-noisy render. Real pipelines compose.
5. **The corpus inherits the content of its source images.** With one source
   image and two variants (the phase-1 situation), a "threshold" is a property of
   that image. Even with a synthetic corpus, if all sources are dark UI
   screenshots, the thresholds will be dark-UI thresholds. State that as a
   documented limitation rather than pretending generality.
6. **Metric-specific pathologies get hidden.** dHash/aHash are 64-bit; their
   Hamming distance is quantised in steps of 1/64 and saturates. A synthetic
   sweep will show a clean sigmoid; it will not reveal that two structurally
   different layouts can collide. Quantised metrics need adversarial tests, not
   sweeps.

### What to do about it

Do the synthetic calibration — it is cheap, reproducible, and it is the only way
to get a *lower bound* on detectability. But:

- Frame the deliverable as **"detection limit and noise floor per metric"**, not
  "the threshold". That is honest, and it is more useful to a calling agent: an
  agent told "this metric cannot resolve hue rotations below 8° affecting under
  5% of pixels" can decide whether to trust a null result.
- Reserve a **held-out set of real image pairs**, however small, and treat any
  threshold that fails on them as refuted. Even three or four genuine
  before/after pairs from real work will catch the two largest biases above.
- Record every threshold's provenance in the repo — which corpus, which metric,
  which criterion, which date — so the next person can tell a measured constant
  from a guess. The phase-1 problem was not that the constants were wrong, it was
  that nothing recorded how they were chosen.

## C2. Picking a threshold from a response curve

### The standard options

- **ROC + Youden's J.** `J = sensitivity + specificity − 1`, maximised over the
  threshold; geometrically the point of greatest vertical distance from the chance
  diagonal. **VERIFIED** original citation: Youden, W.J. (1950), *"Index for
  rating diagnostic tests"*, Cancer 3(1):32–35, doi
  `10.1002/1097-0142(1950)3:1<32::AID-CNCR2820030106>3.0.CO;2-3`
  (<https://acsjournals.onlinelibrary.wiley.com/doi/10.1002/1097-0142(1950)3:1%3C32::AID-CNCR2820030106%3E3.0.CO;2-3>).
  **VERIFIED** caution: Perkins & Schisterman (2006), *"The Inconsistency of
  'Optimal' Cutpoints Obtained using Two Criteria based on the Receiver Operating
  Characteristic Curve"*, Am J Epidemiol 163(7):670–675
  (<https://academic.oup.com/aje/article/163/7/670/77813>) — the Youden cutpoint
  and the "closest to (0,1)" cutpoint generally *differ*, so "optimal" is
  criterion-dependent. Directly relevant: whichever criterion you pick, say so.
- **Equal error rate (EER / crossover error rate).** The threshold where FAR =
  FRR. **VERIFIED** as standard practice in biometrics
  (<https://www.innovatrics.com/glossary/equal-error-rate-eer/>). **NOT FOUND:**
  a canonical originating citation. EER implicitly asserts the two error types
  cost the same, which is false for this project.
- **Neyman–Pearson / fixed false-positive budget.** Fix a tolerable
  false-positive rate α; choose the threshold that achieves it; report the
  resulting power. **VERIFIED** conceptually
  (<https://www.mathworks.com/help/phased/ug/neyman-pearson-hypothesis-testing.html>,
  <https://nowak.ece.wisc.edu/ece830/ece830_fall11_lecture6.pdf>). The original
  Neyman & Pearson (1933) *Phil. Trans. R. Soc. A* 231:289–337 citation is
  **UNVERIFIED / RECALLED** — not fetched.
- **F-beta.** `F_β = (1+β²)·P·R / (β²·P + R)`; `β > 1` weights recall (i.e.
  penalises false negatives) more. **VERIFIED**
  (<https://machinelearningmastery.com/fbeta-measure-for-machine-learning/>).
- **Cost-sensitive Bayes threshold.** `τ* = C_FP / (C_FP + C_FN)` on the
  posterior probability; reduces to 0.5 only when costs are equal. **SECONDARY**
  (general ML sources; also Sheng & Ling, *"Thresholding for Making Classifiers
  Cost-Sensitive"*, AAAI 2006, <https://www.csd.uwo.ca/~xling/papers/AAAI06a.pdf>
  — located, not deep-read).
- **Cost curves.** **VERIFIED** — Drummond, C. & Holte, R.C. (2006), *"Cost
  curves: An improved method for visualizing classifier performance"*, Machine
  Learning 65(1):95–130
  (<https://link.springer.com/article/10.1007/s10994-006-8199-5>). Plots expected
  cost against a probability-cost function; the point–line dual of ROC. Useful
  precisely when the cost ratio is *unknown but bounded*, which is this project's
  situation.

### Small-sample hazards

**SECONDARY, but well corroborated.** A threshold chosen by maximising any
criterion on the same data used to estimate it is **optimistically biased**; the
optimism-correction literature (Steyerberg; see
<https://www.fharrell.com/post/bootcal/> and
<https://thestatsgeek.com/2014/10/04/adjusting-for-optimismoverfitting-in-measures-of-predictive-ability-using-bootstrapping/>)
estimates the gap by bootstrap and subtracts it. Reported further: bootstrap CIs
on optimism-corrected metrics can show marked undercoverage at small sample
sizes, i.e. small-sample calibration is *overconfident about its own
uncertainty*. A specific Steyerberg AUC figure (0.74 apparent → ~0.65 validated)
appeared in a secondary summary and is **UNVERIFIED**.

Consequence for this project: with a synthetic corpus of tens-to-hundreds of
pairs, the threshold estimate has real variance, and reusing the calibration set
to report expected performance will overstate it.

### Recommendation for this project

**Use a Neyman–Pearson / fixed-false-positive-budget rule on the no-change
control distribution, then report the achieved detection limit. Do not use
Youden's J or EER.**

Concretely, per metric:

1. Build the no-change control set (C4). Measure the metric on every control
   pair. This distribution is cheap to sample densely and is the only
   distribution you can characterise near-exhaustively.
2. Set `threshold = Q(1 − α)` of that distribution, **taking the upper bound of a
   bootstrap confidence interval on that quantile** rather than the point
   estimate. Use ≥ 1000 bootstrap resamples; it costs nothing here.

   **Choose α against the control-set size, not by taste.** An extreme quantile is
   not estimable from a small sample: the bootstrap resamples *with replacement
   from the observed values*, so it can never extrapolate past the observed
   maximum. With `n = 20` controls, `Q(0.99)` degenerates to `max`, its
   "confidence interval" collapses to a point, and the reported uncertainty is a
   fiction — which directly contradicts the small-sample warning above. A rough
   working rule is `n >= 3/α`, i.e. enough observations that several sit beyond
   the quantile:

   | α (false-alarm budget) | controls needed (`≈ 3/α`) |
   | --- | --- |
   | 0.01 | ~300 |
   | 0.05 | ~60 |
   | 0.10 | ~30 |

   So **α = 0.01 implies a control set of roughly 300 pairs.** That is achievable
   for the synthetic controls (they are generated), but not for the
   captured-twice control in C4 item 6, which is the one that matters most. If the
   union control set cannot reach ~300, **use α = 0.05 and say so in the run
   ledger** rather than quoting a 99th percentile that is really just the maximum.
   Record `n`, `α`, the point estimate and the CI upper bound together; a
   threshold without its `n` is not interpretable.
3. Then *measure*, don't assume, the sensitivity: sweep the synthetic corpus and
   report the smallest ground-truth perturbation (per perturbation type, over the
   extent × intensity grid) that exceeds the threshold. **That number — the
   detection limit — is the thing to publish in the tool's output**, because it
   is what tells a calling agent how to interpret a null result.
4. If a metric's detection limit is worse than the smallest change anyone cares
   about, the honest outcome is to demote the metric, exactly as phase 1 did with
   palette distance. The
   `runs/2026-08-18-pil-agent-plugin-phase1/10-metric-discrimination-matrix.md`
   result is already this analysis done informally; C2 just makes it rigorous.

**Justification, since the briefing asked for one:**

- **Why not Youden's J:** J requires a well-sampled *positive* class whose
  distribution matches deployment. Here the positive class is "a real change",
  whose distribution over magnitudes is unknown and is precisely what the
  synthetic corpus is fabricating. Maximising J against a fabricated positive
  distribution optimises for the fabrication. The Perkins & Schisterman result
  compounds this: the answer would also depend on an arbitrary choice between J
  and the (0,1)-distance criterion.
- **Why not EER:** EER asserts symmetric costs. The briefing states false
  negatives cost more. EER is the wrong shape of answer.
- **Why N-P fits:** the briefing's cost asymmetry (missing a regression is worse
  than a spurious warning) argues for the *lowest* threshold whose false-alarm
  rate is tolerable. That is exactly the N-P construction: fix α, maximise power.
  It also has the practical virtue of only needing the negative class to be
  well-characterised — which is the class you can actually generate.
- **On the cost ratio:** the Bayes rule `τ* = C_FP/(C_FP + C_FN)` is the
  principled alternative, but it needs numeric costs nobody can supply for
  "agent gets a spurious warning" vs "agent ships a regression". Rather than
  invent them, fix α and report the resulting power — and if the cost ratio ever
  becomes estimable, Drummond & Holte cost curves are the right way to show
  robustness across a *range* of assumed ratios.
- **On the value of α:** this is a choice, not a measurement, and it is
  constrained by the control-set size per the table in step 2. Justify it in the
  run ledger and make it a documented, overridable parameter. A tool whose
  threshold parameter is visible and named `false_alarm_budget=0.05` alongside the
  `n` it was estimated from is honest in a way that `CHANGE_THRESHOLD = 10` is not.

## C3. Perturbation types the synthetic corpus should cover

The IQA community has already built the taxonomies. Steal them rather than
inventing one.

**VERIFIED** — **TID2013**: 25 reference images × **24 distortion types** × 5
levels = 3000 images. Ponomarenko et al., *"Image database TID2013:
Peculiarities, results and perspectives"*, Signal Processing: Image
Communication, 2015 (<https://www.ponomarenko.info/papers/tid2013.pdf>;
<https://qualinet.github.io/databases/image/tampere_image_database_tid2013/>).
The 24 types:

1. Additive Gaussian noise
2. Additive noise more intense in colour components than in luminance
3. Spatially correlated noise
4. Masked noise
5. High-frequency noise
6. Impulse noise
7. Quantisation noise
8. Gaussian blur
9. Image denoising (over-smoothing)
10. JPEG compression
11. JPEG2000 compression
12. JPEG transmission errors
13. JPEG2000 transmission errors
14. Non-eccentricity pattern noise
15. Local block-wise distortions of different intensity
16. Mean shift (intensity shift)
17. Contrast change
18. Change of colour saturation
19. Multiplicative Gaussian noise
20. Comfort noise
21. Lossy compression of noisy images
22. Image colour quantisation with dither
23. Chromatic aberrations
24. Sparse sampling and reconstruction

**VERIFIED (count and provenance)** — **KADID-10k**: 81 pristine images × **25
distortions** × 5 levels. Lin, Hosu & Saupe, QoMEX 2019
(<https://ieeexplore.ieee.org/document/8743252>). Type list per secondary
sources — **exact names/order UNVERIFIED**, check the paper table before
hard-coding: Gaussian blur, lens blur, motion blur, colour diffusion, colour
shift, colour quantisation, colour saturation (1), colour saturation (2),
JPEG2000, JPEG, white noise, white noise in colour component, impulse noise,
multiplicative noise, denoise, brighten, darken, mean shift, jitter,
non-eccentricity patch, pixelate, quantisation, colour block, high sharpen,
contrast change.

**VERIFIED** — **LIVE**: JPEG, JPEG2000, white Gaussian noise, Gaussian blur,
fast-fading (JPEG2000 over a simulated Rayleigh channel)
(<https://qualinet.github.io/databases/image/live_image_quality_assessment_database/>).
**VERIFIED** — **CSIQ**: JPEG, JPEG2000, global contrast decrement, additive
pink Gaussian noise, Gaussian blur; 866 distorted images
(<https://qualinet.github.io/databases/image/categorical_image_quality_csiq_database/>).

### Recommended corpus for this project

The IQA lists are aimed at photographic quality, and several entries
(transmission errors, comfort noise, chromatic aberration) have no analogue in
"did this render change". Prune to what maps onto rendered-UI comparison, and add
what the IQA lists omit:

**Colour axis** — the metrics this project most needs to calibrate:
- Hue rotation, **stating the space** (see B3 hazard 5). Recommend rotating in
  `h_ab` at fixed `L*`, `C*`, so the ground-truth magnitude is perceptually
  meaningful, and separately in HSV H so the current metric's own space is
  covered. Levels: ±2, 5, 10, 20, 45, 90, 180°.
- Chroma scaling (`C* × k`, k ∈ {0.5, 0.8, 0.9, 1.1, 1.25, 2.0}) — the perceptual
  analogue of TID2013 #18.
- Lightness / exposure shift (`L* + δ`, δ ∈ {±1, ±2, ±5, ±10, ±20}) — TID2013 #16.
- Contrast change — TID2013 #17.
- Single-accent recolour: change *one* palette entry, leave the rest. **This is
  the perturbation that most resembles a real regression and is absent from every
  IQA database.** Parameterise by the ΔE00 of the swap and by the fraction of the
  frame that entry covers.
- Colour quantisation / palette reduction — TID2013 #22.

**Structural axis:**
- Gaussian blur (detail loss) — TID2013 #8.
- Sharpening — KADID "high sharpen".
- Additive Gaussian noise and impulse noise — TID2013 #1, #6.
- Geometric translation by 1, 2, 4, 8, 16 px, and scale by 0.9/1.1. The
  scale-invariance claim in `pil_common.py` needs a test that measures it.
- Local block-wise edit — TID2013 #15. Parameterise by patch area fraction:
  0.1%, 0.5%, 1%, 5%, 25%, 100%. **This is the extent axis of the 2-D grid from
  C1 item 3, and it is the most important single addition.**
- Pixelate / downsample-upsample round-trip.

**Encoding axis:**
- JPEG at quality 95, 85, 70, 50 — TID2013 #10.
- PNG re-encode (lossless — belongs in the control set, C4).
- Bit-depth reduction / dithering.

**Explicitly out of scope** (record as deferred, with reasons): transmission
errors, comfort noise, chromatic aberration, lens/motion blur, denoising
artefacts. These are camera- and channel-specific and do not occur in
render-vs-reference comparison.

**Structure the corpus as a grid, not a list.** Every perturbation gets
(intensity level) × (spatial extent). A flat list of single-perturbation images
cannot expose the extent/intensity confound that C1 item 3 identifies, and that
confound is the one most likely to produce a wrong threshold.

## C4. Constructing the no-change control

The control set defines the noise floor and therefore *directly* sets every
threshold under the C2 recommendation. It deserves more care than the
perturbation set.

Ranked by how much each control matters:

1. **Rescale round-trip — essential.** Resize down and back up, or render at 2×
   and downsample. `to_working()` resamples every input to a 256px long edge with
   LANCZOS, so *every* comparison this tool makes already includes a resampling
   step. Any threshold that does not clear resampling noise is unusable. This is
   the control the phase-1 constants most obviously needed and, per the briefing,
   the one that produced the palette-distance embarrassment — a genuinely
   recoloured image scoring as *more* similar than an unchanged rescale is
   exactly a noise floor exceeding the signal.
2. **Lossy re-encode — essential.** JPEG at the quality the real pipeline uses.
   Screenshots that pass through any lossy stage acquire ringing at every text
   edge, which is the dominant real-world noise source for edge-density and
   per-pixel-change metrics.
3. **Sub-threshold perturbation — essential, and it is the definition of the
   floor.** A perturbation deliberately below the smallest magnitude anyone cares
   about (e.g. a 0.5° hue rotation, or a ΔE00 = 0.5 recolour). This is the only
   control that encodes a *decision* about what "no change" means, rather than an
   artefact of the file pipeline. Without it, the noise floor is a property of
   PNG encoders rather than of the task.
4. **Identical file — necessary but nearly useless as a floor.** It should return
   exactly zero for every deterministic metric, so its real job is a **determinism
   regression test** (the first design constraint in `pil_common.py`), not noise
   estimation. Keep it, but do not let it set any threshold — a floor derived from
   it is zero, and a threshold of zero fires on everything.
5. **Lossless re-encode (PNG round-trip, metadata strip) — cheap, include it.**
   Should also be exactly zero; if it is not, something in the load path is
   non-deterministic and that is worth knowing immediately.
6. **The control you cannot synthesise, and must therefore obtain: the same
   render captured twice.** On the same machine, and if at all possible on a
   different machine or a different browser/GPU. This is the only control that
   captures font hinting, subpixel antialiasing and rasterisation variance — the
   dominant real noise source per C1 item 2. **Even two or three such pairs will
   tell you more about the true floor than a thousand synthetic controls.** If
   they cannot be obtained, say so explicitly in the run ledger and mark every
   threshold as provisional.

**Combine, do not choose.** Take the union of controls 1, 2, 3, 5 and 6 as the
negative class, and compute the α-quantile over the union. A per-control floor is
also worth reporting — knowing that JPEG re-encode dominates the floor for edge
density but rescaling dominates it for palette distance is directly actionable.

## C5. Published work on calibrating image-similarity thresholds

### Render-vs-reference specifically

**FLIP** — the closest thing to prior art for this exact problem. **VERIFIED** —
Andersson et al., *"FLIP: A Difference Evaluator for Alternating Images"*,
Proc. ACM Comput. Graph. Interact. Tech. (HPG 2020) 3(2)
(<https://dl.acm.org/doi/abs/10.1145/3406183>;
<https://portal.research.lu.se/en/publications/flip-a-difference-evaluator-for-alternating-images/>).
Purpose-built to compare a *rendered* image against a reference by modelling what
a human notices when alternating between the two. Combines a colour-difference
component (a CIELAB-like perceptual space plus a contrast-sensitivity model) with
an edge/feature-detection component, and outputs a per-pixel error map in [0,1]
plus a weighted error histogram.

**On thresholds: no canonical pass/fail scalar was found.** FLIP is presented as
a diagnostic and visualisation tool; any cutoff is an application-level
convention. **UNVERIFIED at full-text level** — abstracts and indexing pages
only, PDF not fetched. Worth fetching in phase 2 for two reasons: its
colour-difference component is a validated design for exactly this task and may
be a better target than raw ΔE00 pooling, and its "weighted histogram instead of
a single number" output design is a direct precedent for reporting a response
*distribution* rather than a scalar verdict.

### Perceptual metric validation generally

- **LPIPS** — **VERIFIED** paper (Zhang et al., CVPR 2018, arXiv:1801.03924,
  <https://arxiv.org/abs/1801.03924>; code and the BAPPS dataset at
  <https://github.com/richzhang/PerceptualSimilarity>). BAPPS has two human
  judgment types: **2AFC** (which of two distorted patches is closer to the
  reference) and **JND** (brief presentation, "same" or "different"). The JND
  subtask is the relevant one here: it evaluates a metric against human
  *same/different* decisions near threshold. Evaluation is reported to be
  **mAP**-based — rank pairs by predicted distance and compute average precision
  against the human "different" labels. **UNVERIFIED** as to exact wording; the
  LPIPS paper's JND section was not fetched.

  **Why this matters for this project:** mAP-style ranking evaluation sidesteps
  the threshold question entirely. "Does this metric order changes correctly by
  severity" is a strictly easier claim to establish than "here is the cutoff",
  it is well-defined on a small synthetic corpus, and for a tool advising an
  agent it may be the more useful claim. Recommend reporting **both**: a ranking
  score (does the metric order the extent × intensity grid monotonically) and an
  N-P threshold with its measured detection limit. A metric that fails the
  ranking test should not be given a threshold at all.
- **SSIM / MS-SSIM** — **VERIFIED** — Wang, Bovik, Sheikh & Simoncelli (2004),
  *"Image Quality Assessment: From Error Visibility to Structural Similarity"*,
  IEEE TIP (<https://ece.uwaterloo.ca/~z70wang/research/ssim/>). Defines **no
  intrinsic pass/fail threshold**; validated purely by correlation with
  MOS/DMOS on databases such as LIVE. Every "SSIM > 0.95 means unchanged"
  convention in tooling is invented downstream, not from the paper. Useful
  precedent: the most-cited metric in the field declines to ship a threshold.
- **DISTS** — **VERIFIED** paper exists (Ding, Ma, Wang & Simoncelli, 2020,
  arXiv:2004.07728, <https://arxiv.org/abs/2004.07728>). Also a continuous score
  validated by MOS correlation. That it likewise defines no threshold is
  **UNVERIFIED** — inferred by analogy, not confirmed against its text.
- **Correlation measures and VQEG logistic fitting.** SROCC, PLCC and KROCC are
  the standard triple. Before computing PLCC, VQEG recommends fitting a monotonic
  logistic function mapping objective scores onto the subjective scale:
  `p(Q) = β1·[0.5 − 1/(1+exp(β2·(Q−β3)))] + β4·Q + β5`. Rank-based SROCC/KROCC
  need no such fit, being invariant to monotone transforms. **SECONDARY /
  UNVERIFIED against VQEG primary text**; the convention is consistent across
  many IQA papers (e.g. <https://par.nsf.gov/servlets/purl/10334027>).

  Relevance: the logistic-fit step is a reminder that a metric's raw scale is
  arbitrary. If phase 2 ever wants to map ΔE00 or a change fraction onto a
  "severity" word, fit a monotone function to something — don't assert bands.

### Software-practice precedent

**VERIFIED** — **BackstopJS** (built on Resemble.js) gates on a configurable
`misMatchThreshold`, expressed as a percentage of differing pixels
(<https://github.com/garris/BackstopJS/blob/master/README.md>, and issues
<https://github.com/garris/BackstopJS/issues/477>, `/310`, `/313`). Two details
are directly instructive:

1. Mismatches below roughly 0.01% may not register reliably unless "precise
   matching" (`usePreciseMatching`) is enabled — i.e. the tool has a documented
   quantisation floor, and it exposes it rather than hiding it.
2. It ships an explicit `ignoreAntialiasing` / `resembleOutputOptions.ignoreAntialiasing`
   switch to suppress false positives from subpixel antialiasing, with documented
   accuracy caveats and known cross-environment variation in headless Chrome
   (<https://github.com/rsmbl/Resemble.js/issues/151>,
   <https://github.com/garris/BackstopJS/issues/504>).

This is the most widely deployed answer to this exact problem, and the answer is:
*one tunable percentage threshold per project, plus a special case for
antialiasing.* It is a low bar, but it corroborates C1 item 2 — the practitioners
who hit this at scale found antialiasing noise dominant enough to warrant its own
flag. Any threshold this project derives without an antialiasing-bearing control
should be treated as untested.

**NOT FOUND:** academic literature on calibrating screenshot-diff or
visual-regression thresholds in software engineering. If it exists, this search
did not surface it. Do not cite a paper here.

---

# Summary of what could NOT be verified

Listed so it is not lost in the body. Nothing here should become a constant in
the code without further work.

**Section A**
- **Pillow's LAB byte encoding is documented nowhere I could find.** It was
  established by experiment (two's-complement signed `a*`/`b*`, D50), and every
  secondary source consulted got it wrong or claimed the conversion did not exist
  at all. Treat it as an unversioned implementation detail — which is itself one
  of the reasons A4 recommends against using it.
- **Pillow 12.0.0 was not tested.** The briefing names 12.0.0; the environment
  runs 12.3.0. All A4 measurements are against 12.3.0. Whether `convert("LAB")`
  exists or behaves identically in 12.0.0 is unknown.
- Whether lcms applies gamut clipping or black-point compensation on this path was
  not measured. No clipping was observed within the sRGB gamut, but rendering
  intent was not varied.
- The origin of "ΔE\*ab 2.3 = JND".
- Mokrzycki & Tatol's banding table at primary-source level.
- The dental-materials perceptibility/acceptability figures (2.27–2.83) at
  primary-source level.
- Exact ΔE00 tolerance tables in ISO/PAS 15339 and Idealliance G7 (paywalled).
- **NOT FOUND:** any Sharma or Huang paper on ΔE00
  perceptibility/acceptability thresholds. Do not cite one.
- The per-row *intent* of the Sharma test-data groupings (the geometry is
  unambiguous; the authors' stated rationale was not read).

**Section B**
- **NOT FOUND:** any authoritative CIELAB hue-angle boundary set for basic colour
  names. The two application-specific ranges found are mutually inconsistent.
- Unique-hue angles (≈25/90/165/247–257°) are secondary summaries of MacEvoy and
  Kuehni, not primary.
- The recommended `C_MIN = 20`, `L_MIN = 20` are *reasoned from measured sRGB
  values*, not calibrated. They are section C's job.

**Section C**
- Neyman & Pearson (1933) citation details (recalled, not fetched).
- A canonical originating citation for EER/CER.
- Specific Steyerberg optimism figures.
- Exact KADID-10k distortion names and order — verify against the paper table
  before hard-coding.
- LPIPS's exact JND/mAP methodology wording.
- DISTS's own stance on thresholds.
- FLIP's stance on thresholds (inferred from abstracts; PDF not fetched).
- VQEG's logistic-fitting recommendation at primary-source level.
- **NOT FOUND:** academic work on visual-regression / screenshot-diff threshold
  calibration.

# Sources

Primary, fetched and used directly:

- [Sharma, Wu & Dalal CIEDE2000 page](https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/)
- [ciede2000testdata.txt (the 34 test pairs)](https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/dataNprograms/ciede2000testdata.txt)
- [deltaE2000.m (reference implementation)](https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/dataNprograms/deltaE2000.m)
- [Sharma, Wu & Dalal 2005, paper PDF](https://hajim.rochester.edu/ece/sites/gsharma/papers/CIEDE2000CRNAFeb05.pdf)
- [Sharma et al. 2005, Color Research & Application (publisher)](https://onlinelibrary.wiley.com/doi/10.1002/col.20070)
- [Lindbloom — RGB to XYZ](http://www.brucelindbloom.com/Eqn_RGB_to_XYZ.html)
- [Lindbloom — RGB/XYZ Matrices](http://www.brucelindbloom.com/Eqn_RGB_XYZ_Matrix.html)
- [Lindbloom — XYZ to Lab](http://www.brucelindbloom.com/Eqn_XYZ_to_Lab.html)
- [Lindbloom — Working Space Information](http://www.brucelindbloom.com/WorkingSpaceInfo.html)
- [Pillow src/PIL/ImageCms.py](https://github.com/python-pillow/Pillow/blob/main/src/PIL/ImageCms.py) — the D50 default, confirmed
- [Pillow docs/handbook/concepts.rst](https://github.com/python-pillow/Pillow/blob/main/docs/handbook/concepts.rst) — `LAB` listed as a 3×8-bit mode
- [Pillow issue #6643](https://github.com/python-pillow/Pillow/issues/6643) — **falsified for 12.3.0**; claims `convert("LAB")` raises. Retained to record what was checked and found wrong.
- [Pillow issue #8357](https://github.com/python-pillow/Pillow/issues/8357) — same, for CMYK
- [Pillow src/libImaging/Convert.c](https://github.com/python-pillow/Pillow/blob/main/src/libImaging/Convert.c) — no LAB routine on `main`; the working conversion evidently does not live here
- [Youden 1950, Cancer 3(1):32–35](https://acsjournals.onlinelibrary.wiley.com/doi/10.1002/1097-0142(1950)3:1%3C32::AID-CNCR2820030106%3E3.0.CO;2-3)
- [Perkins & Schisterman 2006, Am J Epidemiol](https://academic.oup.com/aje/article/163/7/670/77813)
- [Drummond & Holte 2006, Machine Learning 65(1)](https://link.springer.com/article/10.1007/s10994-006-8199-5)
- [Sheng & Ling, AAAI 2006](https://www.csd.uwo.ca/~xling/papers/AAAI06a.pdf)
- [LPIPS — arXiv:1801.03924](https://arxiv.org/abs/1801.03924)
- [LPIPS / BAPPS code](https://github.com/richzhang/PerceptualSimilarity)
- [SSIM — Wang et al. 2004](https://ece.uwaterloo.ca/~z70wang/research/ssim/)
- [DISTS — arXiv:2004.07728](https://arxiv.org/abs/2004.07728)
- [FLIP — ACM](https://dl.acm.org/doi/abs/10.1145/3406183)
- [TID2013 paper PDF](https://www.ponomarenko.info/papers/tid2013.pdf)
- [TID2013 — QUALINET](https://qualinet.github.io/databases/image/tampere_image_database_tid2013/)
- [KADID-10k — IEEE](https://ieeexplore.ieee.org/document/8743252)
- [LIVE — QUALINET](https://qualinet.github.io/databases/image/live_image_quality_assessment_database/)
- [CSIQ — QUALINET](https://qualinet.github.io/databases/image/categorical_image_quality_csiq_database/)
- [KonIQ-10k dataset](https://darus.uni-stuttgart.de/dataset.xhtml?persistentId=doi%3A10.18419%2Fdarus-2435)
- [Towards Syn-to-Real IQA — arXiv](https://arxiv.org/html/2601.00225)
- [BackstopJS README](https://github.com/garris/BackstopJS/blob/master/README.md)
- [Resemble.js issue #151 — antialiasing](https://github.com/rsmbl/Resemble.js/issues/151)

Secondary, used with markers:

- [Delta E 101 — author's own disclaimer of the popular banding table](http://zschuessler.github.io/DeltaE/learn/)
- [Seymour — "Is 1.0 delta E a just noticeable difference?"](http://johnthemathguy.blogspot.com/2017/07/is-10-delta-e-just-noticeable-difference.html)
- [Mokrzycki & Tatol — "Colour difference ΔE: a survey"](https://www.researchgate.net/publication/286061341_Colour_difference_dE_-_A_survey)
- [Neyman–Pearson testing — MathWorks](https://www.mathworks.com/help/phased/ug/neyman-pearson-hypothesis-testing.html)
- [Equal Error Rate — Innovatrics glossary](https://www.innovatrics.com/glossary/equal-error-rate-eer/)
- [F-beta measure](https://machinelearningmastery.com/fbeta-measure-for-machine-learning/)
- [Harrell — bootstrap optimism correction](https://www.fharrell.com/post/bootcal/)
- [FLIP — Lund University research portal](https://portal.research.lu.se/en/publications/flip-a-difference-evaluator-for-alternating-images/)
