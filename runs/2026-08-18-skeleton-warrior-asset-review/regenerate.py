"""Regenerate the measurement artifacts in this evidence bundle.

The two source images are NOT distributed (see the bundle README). Point this at
your own concept sheet and render to reproduce the method:

    uv run python runs/2026-08-18-skeleton-warrior-asset-review/regenerate.py \
        --concept "<sheet.png>" --render "<render.png>"

What is a plugin output and what is harness code is kept explicit, because this
bundle is capability evidence and must not credit the plugin with measurements it
does not make:

  * `01-region-palette-diffs.json`  -- pil_palette_diff, one run per region pair
  * `02-structure-diff-rejected.json` -- pil_structure_diff, kept because it
    correctly refuses this comparison via its own flags
  * `03-harness-proportions.txt`    -- silhouette geometry computed HERE, in numpy.
    The plugin has no silhouette, proportion or region-cutting capability; see
    the bundle README's "What this exercise wants from phase 2".

Region cutting is the part worth promoting to a tool: regions are cut at identical
fractions of each figure's silhouette bounding box, which makes two images with
different framing, pose and resolution directly comparable part-by-part.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

BUNDLE = Path(__file__).resolve().parent
REPO = BUNDLE.parents[1]
SCRIPTS = REPO / "scripts"

# Fractions of each figure's silhouette bounding box: (y0, y1, x0, x1).
# x is a fraction of the *core column* -- the horizontal extent of the skirt band --
# so outstretched T-pose arms do not shift the torso regions off-centre.
REGIONS = {
    "skull":    (0.000, 0.155, 0.22, 0.78),
    "gorget":   (0.160, 0.245, 0.05, 0.95),
    "torso":    (0.250, 0.400, 0.18, 0.82),
    "belt":     (0.400, 0.480, 0.05, 0.95),
    "skirtmid": (0.480, 0.640, 0.10, 0.90),
    "hemred":   (0.645, 0.740, 0.05, 0.95),
    "greaves":  (0.760, 0.900, 0.12, 0.88),
    "feet":     (0.900, 1.000, 0.08, 0.92),
}

# Default sub-crop isolating the front view from a multi-view concept sheet,
# as fractions of the sheet. Override with --concept-front for your own sheet.
CONCEPT_FRONT = (0.105, 0.0, 0.385, 0.47)


def backdrop(arr: np.ndarray) -> np.ndarray:
    """Median of the four corner patches -- both sources use a flat backdrop."""
    corners = [arr[0:10, 0:10], arr[0:10, -10:], arr[-10:, 0:10], arr[-10:, -10:]]
    return np.median(np.concatenate([c.reshape(-1, 3) for c in corners]), axis=0)


def silhouette(im: Image.Image, tol: int = 18):
    """Figure mask, bounding box, and the core column x-extent."""
    arr = np.asarray(im.convert("RGB")).astype(np.int16)
    mask = np.abs(arr - backdrop(arr)).max(axis=2) > tol
    rows = np.where(mask.any(axis=1))[0]
    top, bottom = int(rows[0]), int(rows[-1])
    height = bottom - top + 1
    band = mask[top + int(0.55 * height): top + int(0.70 * height)]
    cols = np.where(band.any(axis=0))[0]
    return mask, top, bottom, height, int(cols[0]), int(cols[-1])


def cut_regions(im: Image.Image, out_dir: Path, tag: str) -> None:
    _, top, _, height, left, right = silhouette(im)
    width = right - left + 1
    for name, (y0, y1, x0, x1) in REGIONS.items():
        box = (left + int(x0 * width), top + int(y0 * height),
               left + int(x1 * width), top + int(y1 * height))
        im.convert("RGB").crop(box).save(out_dir / f"{tag}_{name}.png")


def run_tool(script: str, *args: object) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *[str(a) for a in args]],
        capture_output=True, text=True,
    )
    if proc.returncode:
        raise SystemExit(f"{script} exited {proc.returncode}\n{proc.stderr}")
    return json.loads(proc.stdout)


def harness_proportions(im: Image.Image, label: str, lines: list[str]) -> None:
    """Silhouette geometry. NOT a plugin capability -- computed here in numpy."""
    mask, top, bottom, height, _, _ = silhouette(im)
    widths = mask.sum(axis=1)
    lo, hi = top + int(0.06 * height), top + int(0.22 * height)
    neck_y = lo + int(np.argmin(widths[lo:hi]))
    head_h = neck_y - top + 1
    skull_w = int(widths[top:neck_y].max())
    lines += [
        f"{label}",
        f"  figure height          : {height} px",
        f"  head height (top->neck): {head_h} px",
        f"  HEAD COUNT             : {height / head_h:.2f} heads tall",
        f"  skull width            : {skull_w} px",
        f"  skull width / height   : {skull_w / head_h:.3f}",
        f"  neck width / head h    : {int(widths[neck_y]) / head_h:.3f}",
        "",
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--concept", required=True, help="multi-view concept sheet")
    ap.add_argument("--render", required=True, help="single-view render to review")
    ap.add_argument("--concept-front", default=",".join(map(str, CONCEPT_FRONT)),
                    help="l,t,r,b fractions isolating the front view on the sheet")
    ap.add_argument("--out", default=str(BUNDLE), help="output directory")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    work = out / "_regions"
    work.mkdir(exist_ok=True)

    sheet = Image.open(args.concept).convert("RGB")
    l, t, r, b = (float(x) for x in args.concept_front.split(","))
    concept = sheet.crop((int(l * sheet.width), int(t * sheet.height),
                          int(r * sheet.width), int(b * sheet.height)))
    render = Image.open(args.render).convert("RGB")

    cut_regions(concept, work, "concept")
    cut_regions(render, work, "model")

    # --- plugin output 1: per-region colour diffs ---------------------------
    diffs = {}
    for part in REGIONS:
        payload = run_tool("pil_palette_diff.py",
                           work / f"concept_{part}.png", work / f"model_{part}.png")
        # Source images are private and their paths embed a username;
        # phase 1 scrubbed its JSON evidence the same way.
        payload["images"]["a"]["path"] = "<concept:%s>" % part
        payload["images"]["b"]["path"] = "<render:%s>" % part
        diffs[part] = payload
    (out / "01-region-palette-diffs.json").write_text(
        json.dumps(diffs, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # --- plugin output 2: the structure tool declining the comparison -------
    concept.save(work / "concept_full.png")
    render.save(work / "model_full.png")
    structure = run_tool("pil_structure_diff.py",
                         work / "concept_full.png", work / "model_full.png", "--grid", "4x6")
    structure["images"]["a"]["path"] = "<concept:front-view>"
    structure["images"]["b"]["path"] = "<render:full>"
    (out / "02-structure-diff-rejected.json").write_text(
        json.dumps(structure, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # --- harness measurements (NOT plugin capabilities) ---------------------
    lines = ["Silhouette geometry -- computed by this harness in numpy, not by the plugin.",
             "The plugin has no silhouette, proportion or region-cutting capability.",
             ""]
    harness_proportions(concept, "CONCEPT front view", lines)
    harness_proportions(render, "RENDER under review", lines)
    (out / "03-harness-proportions.txt").write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote 01-region-palette-diffs.json ({len(diffs)} region pairs)")
    print(f"wrote 02-structure-diff-rejected.json "
          f"(flags: {structure['diff'].get('flags')})")
    print("wrote 03-harness-proportions.txt")
    print(f"intermediate crops left in {work} -- gitignored, not distributed")


if __name__ == "__main__":
    main()
