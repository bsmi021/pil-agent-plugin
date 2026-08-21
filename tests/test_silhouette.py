"""Contract tests for pil_silhouette.

Cover the four acceptance criteria from docs/phase3-build-plan.md §3.2:

*   Byte-determinism (an actual re-run, not a value compared to itself).
*   Each descriptor's interpretation_limits entry names which mask-quality
    flag suppresses it.
*   fill_ratio on a closed-form fixture (filled circle inscribed in a
    known bbox) matches its analytic expectation within a stated
    tolerance.
*   A background_dominant-flagged input reports every descriptor as null
    with the flag cited.

Plus the tree-wide contract that a rejection path exits 2 with
byte-EMPTY stdout (docs/aaa-build-plan.md D5).
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
TOOL = SCRIPTS / "pil_silhouette.py"


def _run(*args, cwd=None):
    """Invoke the CLI the same way an agent would; return the full CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(TOOL), *[str(a) for a in args]],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def _ok(*args):
    """Run the CLI and assert exit 0; return (payload, raw_stdout)."""
    proc = _run(*args)
    assert proc.returncode == 0, f"exit {proc.returncode}\nSTDERR:\n{proc.stderr}"
    return json.loads(proc.stdout), proc.stdout


# --- Fixture helpers ---------------------------------------------------------
#
# Every fixture sits on a distinct backdrop that is NOT the object colour, so
# the border-median foreground rule (pil_common.foreground_mask, no-alpha
# path) can separate them. Objects are held well clear of the frame edges so
# the border-median estimate reads background rather than the object itself.

BACKDROP = (24, 26, 30)  # matches scenes.PREVIEW_BG


def _circle_on_backdrop(tmp_img, frame=(384, 384), diameter=200):
    """A filled circle inscribed in a known axis-aligned bbox.

    Both frame dimensions are equal and the circle is centred, so the tight
    mask bbox is (frame - diameter) / 2 padded on every side around a
    diameter x diameter square. Fill ratio has the analytic expectation
    pi/4 (~0.7854) in the continuum limit; the raster + LANCZOS working
    resample introduces a small error captured in the test tolerance.
    """
    img = Image.new("RGB", frame, BACKDROP)
    draw = ImageDraw.Draw(img)
    cx, cy = frame[0] // 2, frame[1] // 2
    r = diameter // 2
    draw.ellipse((cx - r, cy - r, cx + r - 1, cy + r - 1), fill=(200, 60, 60))
    return tmp_img(img, "circle.png")


def _square_on_backdrop(tmp_img, frame=(384, 384), side=200):
    """A filled square centred in the frame with backdrop on all sides."""
    img = Image.new("RGB", frame, BACKDROP)
    draw = ImageDraw.Draw(img)
    cx, cy = frame[0] // 2, frame[1] // 2
    half = side // 2
    draw.rectangle(
        (cx - half, cy - half, cx + half - 1, cy + half - 1), fill=(200, 60, 60)
    )
    return tmp_img(img, "square.png")


def _empty_rgba(tmp_img, frame=(384, 384)):
    """A fully-transparent RGBA image: alpha path, empty mask."""
    img = Image.new("RGBA", frame, (0, 0, 0, 0))
    return tmp_img(img, "empty.png")


def _tiny_alpha_object(tmp_img, frame=(400, 400), side=8):
    """A tiny opaque square on transparent film.

    Real alpha path (avoids border-median ambiguity). At side=8 in a
    400x400 frame the mask covers 64 / 160000 = 0.0004 of the frame,
    below FOREGROUND_MIN_FRACTION (~0.0226) -- both foreground_too_small
    and background_dominant must fire.
    """
    img = Image.new("RGBA", frame, (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle(
        (frame[0] // 2, frame[1] // 2, frame[0] // 2 + side - 1, frame[1] // 2 + side - 1),
        fill=(200, 60, 60, 255),
    )
    return tmp_img(img, "tiny.png")


def _midsize_alpha_object(tmp_img, frame=(400, 400), side=64):
    """An opaque square on transparent film covering ~2.6% of the frame.

    64*64 / 400*400 = 0.0256, which sits ABOVE FOREGROUND_MIN_FRACTION
    (~0.0226) but BELOW BACKGROUND_DOMINANT_MAX (0.10). This is the
    narrow band where background_dominant fires alone -- exactly the
    case the advisor's flag-isolation acceptance criterion requires.
    """
    img = Image.new("RGBA", frame, (0, 0, 0, 0))
    left = (frame[0] - side) // 2
    top = (frame[1] - side) // 2
    ImageDraw.Draw(img).rectangle(
        (left, top, left + side - 1, top + side - 1),
        fill=(200, 60, 60, 255),
    )
    return tmp_img(img, "midsize.png")


# --- Payload shape and contract ---------------------------------------------


def test_payload_has_the_tool_contract_fields(tmp_img):
    """Every payload carries tool/version/parameters/interpretation_limits."""
    path = _circle_on_backdrop(tmp_img)
    payload, _stdout = _ok(path)
    assert payload["tool"] == "pil_silhouette"
    assert payload["version"] == "0.5.0"
    assert "parameters" in payload
    assert "image" in payload
    assert "interpretation_limits" in payload
    parameters = payload["parameters"]
    for key in (
        "background_delta",
        "working_long_edge",
        "alpha_foreground_min",
        "foreground_min_fraction",
        "background_dominant_max",
        "orientation_bins",
    ):
        assert key in parameters, key


def test_image_block_has_the_expected_shape(tmp_img):
    """The image block always carries the same schema, populated or nulled."""
    path = _circle_on_backdrop(tmp_img)
    payload, _stdout = _ok(path)
    image = payload["image"]
    assert set(image) == {
        "path",
        "size",
        "working_size",
        "foreground",
        "working_mask",
        "descriptors",
        "flags",
    }
    assert set(image["descriptors"]) == {
        "fill_ratio",
        "perimeter_squared_over_area",
        "orientation_histogram",
    }
    assert set(image["working_mask"]) == {
        "mask_pixels",
        "bbox_pixel_rect",
        "perimeter_pixels",
    }


def test_every_descriptor_names_its_suppressing_flag_in_interpretation_limits(tmp_img):
    """Acceptance criterion: each descriptor entry names the flag that
    suppresses it -- explicit in docs/phase3-build-plan.md §3.2."""
    path = _circle_on_backdrop(tmp_img)
    payload, _stdout = _ok(path)
    joined = " ".join(payload["interpretation_limits"])
    for descriptor in (
        "fill_ratio",
        "perimeter_squared_over_area",
        "orientation_histogram",
    ):
        assert descriptor in joined, (
            f"{descriptor} must appear in interpretation_limits"
        )
    # Each of the three suppression flags must be named somewhere.
    for flag in ("foreground_mask_empty", "foreground_too_small", "background_dominant"):
        assert flag in joined


# --- Byte-determinism -------------------------------------------------------


def test_output_is_byte_identical_across_runs(tmp_img):
    """Actually re-run the tool -- comparing a value to itself is theatre."""
    path = _circle_on_backdrop(tmp_img)
    _payload_a, stdout_a = _ok(path)
    _payload_b, stdout_b = _ok(path)
    assert stdout_a == stdout_b


# --- Closed-form fill ratio -------------------------------------------------


def test_fill_ratio_on_a_filled_circle_matches_pi_over_four(tmp_img):
    """A filled circle inscribed in an NxN bbox has fill_ratio -> pi/4.

    The tolerance covers the LANCZOS-to-working + NEAREST-mask-resize
    combination, which shifts the bbox by up to one pixel on each edge
    at the working resolution and blurs the ellipse rim.
    """
    path = _circle_on_backdrop(tmp_img)
    payload, _stdout = _ok(path)
    ratio = payload["image"]["descriptors"]["fill_ratio"]
    assert ratio is not None
    assert ratio == pytest.approx(math.pi / 4, abs=0.03), (
        f"fill_ratio {ratio} not close to pi/4 (~0.7854) within tolerance"
    )


def test_fill_ratio_on_a_filled_square_is_close_to_one(tmp_img):
    """A solid square inscribed in its own tight bbox has fill_ratio -> 1."""
    path = _square_on_backdrop(tmp_img)
    payload, _stdout = _ok(path)
    ratio = payload["image"]["descriptors"]["fill_ratio"]
    assert ratio is not None
    assert ratio == pytest.approx(1.0, abs=0.03)


def test_perimeter_squared_over_area_on_a_solid_square_is_close_to_sixteen(tmp_img):
    """P^2/A of an NxN pixel square approaches 16 (P=4N, A=N^2).

    The tolerance covers the LANCZOS resample plus the boundary rule's
    small overcounting on corner pixels; the point of the test is to
    catch a wildly wrong measurement (a P^2/A of 500 would say something
    is deeply wrong about the boundary detector), not to pin the value.
    """
    path = _square_on_backdrop(tmp_img)
    payload, _stdout = _ok(path)
    p2a = payload["image"]["descriptors"]["perimeter_squared_over_area"]
    assert p2a is not None
    assert 14.0 <= p2a <= 22.0, f"P^2/A {p2a} outside expected raster-square band"


def test_orientation_histogram_sums_to_one_and_has_correct_length(tmp_img):
    """The histogram is a length-8 probability vector."""
    path = _circle_on_backdrop(tmp_img)
    payload, _stdout = _ok(path)
    hist = payload["image"]["descriptors"]["orientation_histogram"]
    assert isinstance(hist, list)
    assert len(hist) == 8
    assert sum(hist) == pytest.approx(1.0, abs=1e-4)


# --- Flag-suppression acceptance --------------------------------------------


def test_empty_mask_reports_all_descriptors_null_with_flag(tmp_img):
    """A fully transparent RGBA file: alpha path, empty mask."""
    path = _empty_rgba(tmp_img)
    payload, _stdout = _ok(path)
    image = payload["image"]
    assert "foreground_mask_empty" in image["flags"]
    for value in image["descriptors"].values():
        assert value is None


def test_foreground_too_small_reports_all_descriptors_null_with_flag(tmp_img):
    """A tiny alpha-backed object: foreground_too_small must fire."""
    path = _tiny_alpha_object(tmp_img)
    payload, _stdout = _ok(path)
    image = payload["image"]
    assert "foreground_too_small" in image["flags"]
    for value in image["descriptors"].values():
        assert value is None


def test_background_dominant_alone_suppresses_all_descriptors(tmp_img):
    """A 2.6%-of-frame object: background_dominant fires, too_small does NOT.

    This isolates background_dominant as an independent suppression path.
    The build plan's acceptance criterion for §3.2 names background_dominant
    specifically; this is the fixture that exercises it without also firing
    foreground_too_small.
    """
    path = _midsize_alpha_object(tmp_img)
    payload, _stdout = _ok(path)
    image = payload["image"]
    assert "background_dominant" in image["flags"], image["flags"]
    assert "foreground_too_small" not in image["flags"], image["flags"]
    for name, value in image["descriptors"].items():
        assert value is None, f"{name} should be null when background_dominant fires"


# --- Rejection hygiene ------------------------------------------------------


def test_argparse_missing_argument_exits_two_with_empty_stdout():
    """argparse's own error path: exit 2, no JSON on stdout."""
    proc = _run()
    assert proc.returncode == 2
    assert proc.stdout == ""


def test_unreadable_image_exits_two_with_empty_stdout(tmp_path):
    """Point the tool at a file that is not a valid image."""
    bogus = tmp_path / "not-an-image.png"
    bogus.write_bytes(b"this is definitely not a PNG")
    proc = _run(bogus)
    assert proc.returncode == 2
    assert proc.stdout == ""


def test_nonexistent_image_exits_two_with_empty_stdout(tmp_path):
    proc = _run(tmp_path / "missing.png")
    assert proc.returncode == 2
    assert proc.stdout == ""


# --- Sanity: distinct shapes produce distinct descriptors -------------------


def test_circle_and_square_disagree_on_fill_ratio(tmp_img):
    """A crude sanity check that the descriptor discriminates two known shapes.

    Not a calibration claim (that lives in the gate bundle at
    runs/2026-08-20-silhouette-discrimination/) -- just a check that a
    solid square and an inscribed circle report *different* fill ratios,
    with the circle lower, so the number is not a constant.
    """
    circle = _circle_on_backdrop(tmp_img)
    square = _square_on_backdrop(tmp_img)
    circle_payload, _ = _ok(circle)
    square_payload, _ = _ok(square)
    circle_ratio = circle_payload["image"]["descriptors"]["fill_ratio"]
    square_ratio = square_payload["image"]["descriptors"]["fill_ratio"]
    assert circle_ratio < square_ratio - 0.10
