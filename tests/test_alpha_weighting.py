"""Tight, absolute-literal grading for the alpha/coverage fix (W1).

Everything in tests/test_alpha_foreground.py's TestAlphaWeightedTruth passes
after the fix with an enormous margin: the tool and alpha_truth now compute
the same thing, so the bias is close to zero against a bound of 34.129 --
that bound is the calibrated foreground threshold, set almost entirely by one
placement-perturbation control family (see that file's module docstring), and
a bound that loose grades almost nothing (docs/aaa-build-plan.md #3.7). This
file adds the tight assertions alongside it.

Deliberately does NOT import the generated foreground-threshold JSON W2
regenerates (docs/aaa-build-plan.md #3.11's "grading against a generated
threshold file instead of the exact reference" failure mode): every bound
here is an absolute literal, either a fixed tolerance stated in the
acceptance criteria (A1.2, A1.7, A1.9) or derived directly from
calibration/alpha_truth.py's own exact arithmetic, so a later recalibration
of the foreground thresholds cannot loosen what this file grades.

Reuses the `corpus` and `truth` fixtures from test_alpha_foreground.py rather
than rebuilding the corpus a third time; `tool` comes from conftest.py.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
CALIBRATION = REPO_ROOT / "calibration"
for _path in (SCRIPTS, CALIBRATION):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import alpha_truth  # noqa: E402
import scenes  # noqa: E402
from pil_color import rgb_ciede2000  # noqa: E402
from pil_common import (  # noqa: E402
    ALPHA_FOREGROUND_MIN,
    resize_coverage,
    resize_mask,
)

from test_alpha_foreground import corpus, truth  # noqa: E402,F401  (fixtures)

# Scenes with a real, non-empty alpha-derived foreground -- excludes
# degenerate_empty (mask is empty by construction) and degenerate_opaque
# (alpha resolves to None; it exercises the border-median path on purpose,
# per test_an_all_opaque_alpha_channel_provides_no_foreground).
ALPHA_TRUTH_LABELS = [
    label
    for label, _scene, _params in scenes.ALPHA_CORPUS
    if label not in ("degenerate_empty", "degenerate_opaque")
]

# A1.9's floor sweep, and the full-frame no-change floor A1.7/A1.9 grade
# against. Both are fixed literals from the acceptance table, not read from
# any generated calibration artefact.
ALPHA_FLOOR_SWEEP = (8, 16, 32, 64)
FULL_FRAME_NO_CHANGE_FLOOR = 1.243
SUB_PIXEL_EXCURSION_BOUND = 0.5


@pytest.mark.parametrize("label", ALPHA_TRUTH_LABELS)
def test_foreground_statistics_match_alpha_weighted_truth(tool, corpus, truth, label):
    """A1.2: tight, absolute-literal grading of the alpha path against
    calibration/alpha_truth.py's exact coverage-weighted reference.

    Runs the real CLI (a subprocess), not a library call, so it also catches
    the wiring errors docs/aaa-build-plan.md #3.9/#3.11 name explicitly: a
    weights array that is never applied, an accent gate reading composited
    colour, or a floor that lets a stray argument silently do nothing.
    """
    # Arrange/Act
    result, _ = tool("pil_palette_diff.py", corpus[label]["rgba"], "--foreground")
    image = result["images"]["a"]
    reference = truth[label]["truth"]

    # Assert: the tool must agree with the exact reference to within a few
    # thousandths of a code value -- not "inside a calibrated threshold".
    assert image["foreground"]["source"] == "alpha", label
    assert image["luminance"]["mean"] == pytest.approx(
        reference["luminance_mean"], abs=0.001
    ), label
    assert image["saturation"]["mean"] == pytest.approx(
        reference["saturation_mean"], abs=0.001
    ), label
    assert image["accent_pixel_fraction"] == pytest.approx(
        reference["accent_fraction"], abs=1e-4
    ), label


@pytest.mark.parametrize("size", [(256, 256), (256, 192), (37, 101)])
@pytest.mark.parametrize(
    "label,scene,params",
    scenes.ALPHA_CORPUS,
    ids=[lbl for lbl, _scene, _params in scenes.ALPHA_CORPUS],
)
def test_working_resolution_membership_matches_resize_mask(label, scene, params, size):
    """A1.6: resize_coverage's NEAREST membership is provably the same boolean
    array as resize_mask's, at every corpus scene and every target size --
    this is what keeps the alpha and border-median paths selecting the same
    pixels on a hard-edged object (see resize_coverage's docstring).
    """
    # Arrange
    rgba = scenes.build_alpha(scene, **params)
    alpha = np.asarray(rgba.convert("RGBA"), dtype=np.uint8)[:, :, 3]

    # Act
    via_mask = resize_mask(alpha >= ALPHA_FOREGROUND_MIN, size)
    via_coverage = resize_coverage(alpha, size) >= ALPHA_FOREGROUND_MIN

    # Assert
    assert np.array_equal(via_mask, via_coverage), (label, size)


def test_sub_pixel_placement_excursion_is_within_half_a_code_value(tool, tmp_path_factory):
    """A1.7: the tighter, separate bound alongside test_alpha_foreground.py's
    TestSubPixelPlacement test, which keeps its existing 1.243 (full-frame
    no-change floor) assertion unchanged. Pre-fix reference (from that test's
    docstring): excursions of 17.74 and 15.91 code values at the two
    grid-commensurate angles (0, 45 degrees).
    """
    root = tmp_path_factory.mktemp("alpha-weighting-phase")

    excursions = {}
    for angle in scenes.ALPHA_PHASE_ANGLES:
        readings, truths = [], []
        for offset in scenes.ALPHA_PHASE_OFFSETS:
            image = scenes.alpha_blade(
                angle_deg=angle, phase_px=offset, **scenes.ALPHA_PHASE_BLADE
            )
            path = root / f"a{angle}_p{offset}.png"
            image.save(path)
            result, _ = tool("pil_palette_diff.py", path, "--foreground")
            assert result["images"]["a"]["foreground"]["source"] == "alpha"
            readings.append(result["images"]["a"]["luminance"]["mean"])
            rgb, alpha = alpha_truth.truth_arrays(image)
            truths.append(alpha_truth.weighted_stats(rgb, alpha)["luminance_mean"])
        excursions[angle] = (max(readings) - min(readings)) - (max(truths) - min(truths))

    assert max(excursions.values()) <= SUB_PIXEL_EXCURSION_BOUND, excursions


def test_partial_coverage_pixels_carry_their_true_colour(tool, corpus):
    """A1.8: on glass_a64, the interior's true colour (#00ffff) must survive
    into base_palette, and its coverage-darkened form under the old,
    unweighted composite-then-average path (#004040) must not appear. This is
    the assertion that fails if un-premultiply / the straight-RGB read is
    omitted -- see docs/aaa-build-plan.md #3.11, "weighting without
    un-premultiplying".
    """
    result, _ = tool("pil_palette_diff.py", corpus["glass_a64"]["rgba"], "--foreground")
    palette = result["images"]["a"]["base_palette"]

    def nearest_de2000(hex_target):
        target_rgb = np.array(
            [int(hex_target[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.float64
        )
        distances = [
            float(rgb_ciede2000(np.array(entry["rgb"], dtype=np.float64), target_rgb))
            for entry in palette
        ]
        return min(distances) if distances else float("inf")

    assert nearest_de2000("#00ffff") <= 3.0, palette
    assert nearest_de2000("#004040") > 3.0, palette


@pytest.mark.parametrize("label", ALPHA_TRUTH_LABELS)
def test_weighted_statistics_are_insensitive_to_the_alpha_floor(truth, label):
    """A1.9: sweeping ALPHA_FOREGROUND_MIN over 8/16/32/64 and recomputing the
    WEIGHTED statistics (library-level, via alpha_truth.weighted_stats --
    there is no CLI knob for this floor) moves luminance.mean by no more than
    the full-frame no-change floor. Pre-fix, the same sweep moved it by tens
    of code values (TestAlphaFloorSweep, which sweeps the UNWEIGHTED reading).
    If this fails, the floor is still doing bias control and the fix is
    incomplete (docs/aaa-build-plan.md #11.2).
    """
    scene, params = next(
        (scene, params) for lbl, scene, params in scenes.ALPHA_CORPUS if lbl == label
    )
    rgba = scenes.build_alpha(scene, **params)
    rgb, alpha = alpha_truth.truth_arrays(rgba)

    means = []
    for floor in ALPHA_FLOOR_SWEEP:
        stats = alpha_truth.weighted_stats(rgb, alpha, alpha_min=floor)
        if stats["luminance_mean"] is not None:
            means.append(stats["luminance_mean"])

    if len(means) < 2:
        pytest.skip(f"{label}: fewer than 2 non-empty floor steps to compare")

    assert max(means) - min(means) <= FULL_FRAME_NO_CHANGE_FLOOR, (label, means)


def test_foreground_source_mismatch_flags_rgba_vs_composited_pairs(tool, corpus):
    """A1.10: foreground_source_mismatch appears in diff.flags for every
    RGBA-vs-composited pair in the corpus with a real alpha channel, and in
    no same-format pair -- checked on one representative scene each way
    rather than the full corpus, to keep this test's runtime proportionate.
    """
    # A genuinely mixed-provenance pair: real alpha vs border-median.
    mixed, _ = tool(
        "pil_palette_diff.py",
        corpus["aa_blade_w12"]["rgba"],
        corpus["aa_blade_w12"]["composited"],
        "--foreground",
    )
    assert "foreground_source_mismatch" in mixed["diff"]["flags"]

    # Same-format pairs: both alpha, or both border-median -- source always
    # agrees, so the flag must never fire.
    same_alpha, _ = tool(
        "pil_palette_diff.py",
        corpus["aa_blade_w12"]["rgba"],
        corpus["aa_blade_w5"]["rgba"],
        "--foreground",
    )
    assert "foreground_source_mismatch" not in same_alpha["diff"]["flags"]

    same_composited, _ = tool(
        "pil_palette_diff.py",
        corpus["aa_blade_w12"]["composited"],
        corpus["aa_blade_w5"]["composited"],
        "--foreground",
    )
    assert "foreground_source_mismatch" not in same_composited["diff"]["flags"]

    # And structure_diff carries the same flag.
    mixed_structure, _ = tool(
        "pil_structure_diff.py",
        corpus["aa_blade_w12"]["rgba"],
        corpus["aa_blade_w12"]["composited"],
        "--foreground",
    )
    assert "foreground_source_mismatch" in mixed_structure["diff"]["flags"]


@pytest.mark.parametrize("tool_name", ["pil_palette_diff.py", "pil_structure_diff.py"])
def test_alpha_path_is_byte_deterministic(tool, corpus, tool_name):
    """A1.11: two consecutive --foreground runs on the same alpha-bearing
    input produce byte-identical stdout."""
    _, stdout_1 = tool(tool_name, corpus["aa_blade_w3"]["rgba"], "--foreground")
    _, stdout_2 = tool(tool_name, corpus["aa_blade_w3"]["rgba"], "--foreground")
    assert stdout_1 == stdout_2
