"""Shared fixtures. All test images are generated synthetically with PIL so the
suite carries no binary fixtures and stays reproducible on any machine."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

# Real-world reference image used for the metric sanity checks. Entirely optional:
# those six tests skip when it is absent, and their strongest assertions are
# duplicated unskipped against synthetic_reference() below, so a clean checkout
# still guards every known regression.
#
# Point PIL_AGENT_REFERENCE_IMAGE at any complex, predominantly dark image with
# small vivid accents to exercise them. The image itself is not distributed.
REFERENCE_IMAGE = Path(
    os.environ.get(
        "PIL_AGENT_REFERENCE_IMAGE", str(Path.home() / "Downloads" / "image (1).png")
    )
)

# Numbers independently derived during the prototyping phase by an agent running
# ad-hoc PIL scripts against REFERENCE_IMAGE. The production tools must land in
# the same neighbourhood; a large divergence means an implementation bug.
REFERENCE_EXPECTED = {
    "size": (1672, 941),
    "luminance_mean": 28.6,
    "luminance_std": 46.94,
    "entropy": 5.945,
    "saturation_mean": 114.0,
}


def run_tool(script_name, *args):
    """Invoke a bundled CLI tool the same way an agent would, and parse its JSON.

    Returns (parsed_json, raw_stdout) so tests can assert on byte-level
    determinism as well as on structure.
    """
    cmd = [sys.executable, str(SCRIPTS / script_name), *[str(a) for a in args]]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(
            f"{script_name} exited {proc.returncode}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout), proc.stdout


@pytest.fixture
def tool():
    return run_tool


@pytest.fixture
def tmp_img(tmp_path):
    """Factory: save a PIL image to a temp PNG and return its path."""
    counter = {"n": 0}

    def _save(img, name=None):
        counter["n"] += 1
        path = tmp_path / (name or f"img{counter['n']}.png")
        img.save(path)
        return path

    return _save


def dark_base_with_accent(accent_rgb, size=(400, 300), accent_frac=0.05, seed=7):
    """A near-black image whose pixels span many dark shades, plus a small patch
    of one vivid accent colour.

    This mirrors the structure of the real reference image (~75% near-black with
    small vivid accents) which is precisely the case where a single global
    quantisation spends its whole colour budget on dark tones and never reports
    the accent.
    """
    img = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Deterministic spread of dark shades across the canvas.
    rnd = _lcg(seed)
    band_h = 4
    for y in range(0, size[1], band_h):
        shade = int(next(rnd) * 40)  # 0..39 -> all "near black"
        draw.rectangle([0, y, size[0], y + band_h], fill=(shade, shade, shade + 3))

    # Accent patch sized to the requested fraction of total area.
    total = size[0] * size[1]
    patch_area = int(total * accent_frac)
    patch_w = size[0]
    patch_h = max(1, patch_area // patch_w)
    draw.rectangle([0, 0, patch_w, patch_h], fill=accent_rgb)
    return img


def _lcg(seed):
    """Tiny deterministic PRNG so fixtures never depend on `random` global state."""
    state = seed
    while True:
        state = (1103515245 * state + 12345) % (2**31)
        yield state / (2**31)


# Hue-bucket representatives, each verified against PIL's HSV conversion so a
# fixture cannot silently drift into a neighbouring family.
#   red (255,0,30)->H=250 exercises the wrap branch of the red family;
#   RED_LOW ->H=0 exercises the low branch.
HUE_SAMPLES = {
    "red": (200, 30, 30),  # H=0
    "red_wrap": (255, 0, 30),  # H=250, upper branch of the wrapped red family
    "orange": (255, 128, 0),  # H=21
    "yellow": (255, 212, 0),  # H=35
    "green": (0, 255, 0),  # H=85
    "cyan": (0, 255, 255),  # H=127
    "blue": (0, 128, 255),  # H=148
    "purple": (128, 0, 255),  # H=191
    "magenta": (255, 0, 200),  # H=221
}


def synthetic_reference(size=(400, 300), include_cyan=True):
    """A stand-in reproducing the structural traits of the real reference image.

    Specifically: a near-black base occupying the bulk of the frame, one dominant
    red accent, and several sub-1%-of-frame secondary hues. That combination is
    what defeats area-weighted palette extraction twice over, so the strongest
    regression tests can run on any machine rather than skipping when the real
    image is absent.
    """
    img = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(img)

    rnd = _lcg(11)
    for y in range(0, size[1], 4):
        shade = int(next(rnd) * 40)
        draw.rectangle([0, y, size[0], y + 4], fill=(shade, shade, shade + 3))

    # Dominant red accent: a few percent of the frame, mirroring the real image.
    draw.rectangle([0, 0, size[0], int(size[1] * 0.06)], fill=HUE_SAMPLES["red"])

    # Secondary hues, each a thin sliver well under 1% of the frame. These are
    # the entries that area-weighted quantisation discards.
    total = size[0] * size[1]
    secondaries = ["orange", "yellow", "green", "blue", "purple", "red_wrap"]
    if include_cyan:
        secondaries.insert(3, "cyan")

    y = int(size[1] * 0.10)
    for name in secondaries:
        # Height chosen so each sliver is ~0.5% of frame area.
        band = max(1, int(total * 0.005 / size[0]))
        draw.rectangle([0, y, size[0], y + band], fill=HUE_SAMPLES[name])
        y += band + 2
    return img


def detailed_vs_flat(size=(400, 300)):
    """An image whose left half is edge-dense (fine stripes) and right half flat."""
    img = Image.new("RGB", size, (20, 20, 20))
    draw = ImageDraw.Draw(img)
    for x in range(0, size[0] // 2, 3):
        draw.line([x, 0, x, size[1]], fill=(230, 230, 230))
    return img
