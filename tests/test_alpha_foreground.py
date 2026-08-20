"""RED tests for alpha-weighted foreground measurement.

The defect these document, measured rather than assumed:
``pil_common.load_rgb_alpha`` alpha-composites an RGBA file onto **black**, and
``foreground_mask`` then admits every pixel with ``alpha >= ALPHA_FOREGROUND_MIN``
(8) at **full weight**. So a half-covered edge pixel of a bright blade arrives
darkened by the compositing and is then counted exactly like an opaque one. The
mask knows the pixel is there; nothing downstream knows how much of it is there.

The corpus in ``calibration/scenes.py`` is built to isolate that. Every scene
exists in two forms containing the same object -- RGBA on transparent film, and
the same RGBA alpha-composited onto a flat backdrop -- so the two foreground
mask paths (alpha, and the border-median OKLab estimate) can be compared on
identical content. ``calibration/alpha_truth.py`` supplies the exact
alpha-weighted reference: the true colour of every pixel and the fraction of the
pixel the object covers, both read straight out of the corpus image.

Everything marked ``xfail(strict=True)`` fails against current behaviour on
purpose and carries its measured number in the reason string. When the fix
lands they become xpass, which is a failure under ``strict``, so the suite says
so loudly rather than quietly going green.

The tests that PASS today are not filler. Three of them are what make the
diagnosis specific rather than a hunch:

*   the hard-edged control -- an RGBA object with no partial coverage anywhere
    reads *identically* through both mask paths, so the alpha path itself is
    sound and partial coverage is the cause;
*   the alpha-floor sweep -- raising ALPHA_FOREGROUND_MIN does reduce the bias,
    but only in proportion to the object pixels it deletes, so recalibrating
    that constant is not the fix;
*   the floor-loss measurement -- on a 3px blade the shipped floor of 8 already
    discards a third of the pixels the object covers, before any statistic is
    computed.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
CALIBRATION = REPO_ROOT / "calibration"
for _path in (SCRIPTS, CALIBRATION):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import alpha_truth  # noqa: E402
import scenes  # noqa: E402
from pil_common import (  # noqa: E402
    ALPHA_FOREGROUND_MIN,
    DEFAULT_ACCENT_SAT_MIN,
    DEFAULT_ACCENT_VAL_MIN,
    DEFAULT_BACKGROUND_DELTA,
    accent_mask,
    foreground_mask,
    load_rgb_alpha,
)

# Thresholds come from the shipped calibration bundle, never from a literal
# here: these tests assert that a measured disagreement clears the bar WP2
# actually derived, and a hardcoded copy would silently stop tracking it.
DETECTION_LIMITS = json.loads(
    (SCRIPTS / "detection_limits.json").read_text(encoding="utf-8")
)


def _foreground_threshold(metric):
    return DETECTION_LIMITS[metric]["threshold_foreground"]


SATURATION_LIMIT = _foreground_threshold("saturation_mean_delta_abs")  # 4.8875
LUMINANCE_LIMIT = _foreground_threshold("luminance_mean_delta_abs")  # 34.12895
SIMILARITY_LIMIT = _foreground_threshold("structural_similarity")  # 0.793772

ALPHA_SCENES = {label: (scene, params) for label, scene, params in scenes.ALPHA_CORPUS}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """Every ALPHA_CORPUS scene on disk, in both forms, built once.

    Module-scoped because the anti-aliased scenes are rendered at 4x and
    downsampled, and rebuilding them per test would dominate the suite's
    runtime for no benefit -- the builders are pure functions of their
    parameters, so one build is every build.
    """
    root = tmp_path_factory.mktemp("alpha-corpus")
    paths = {}
    for label, scene, params in scenes.ALPHA_CORPUS:
        rgba = root / f"{label}_rgba.png"
        composited = root / f"{label}_composited.png"
        scenes.build_alpha(scene, **params).save(rgba)
        scenes.build_alpha(scene, composite=True, **params).save(composited)
        paths[label] = {"rgba": rgba, "composited": composited}
    return paths


@pytest.fixture(scope="module")
def truth():
    """Exact alpha-weighted ground truth, keyed by corpus label."""
    return {record["label"]: record for record in alpha_truth.inventory()}


def _palette_deltas(tool, paths):
    """(|saturation delta|, |luminance delta|) between a scene's two forms."""
    result, _ = tool("pil_palette_diff.py", paths["rgba"], paths["composited"], "--foreground")
    diff = result["diff"]
    return (
        abs(diff["saturation_mean_delta"]),
        abs(diff["luminance_mean_delta"]),
        result,
    )


class TestMaskProvenance:
    """The same object, two mask paths, one answer -- or an explanation.

    An RGBA render and that render composited onto the preview backdrop are the
    same object. A caller comparing one against the other is asking "did the
    asset change?", and the answer must not depend on which file format the two
    renders happened to arrive in.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "measured saturation_mean_delta -11.572 between vivid_blade_w5 as RGBA "
            "and the same object composited onto (24,26,30), against the calibrated "
            "foreground threshold 4.8875 -- a false positive with nothing changed "
            "about the asset. The two masks select the same extent (foreground "
            "fraction 0.011543 against 0.011536), so this is a disagreement about "
            "colour: 59.39% of the foreground is partial coverage, and admitting it "
            "at full weight makes the reading depend on what the fringe was blended "
            "against. Flattened onto black by load_rgb_alpha the fringe keeps its "
            "chroma and reads 254.692; blended toward the near-neutral backdrop its "
            "minimum channel lifts and it reads 243.120. Alpha-weighted, those "
            "pixels count for almost nothing and the object's saturation is 254.665. "
            "Luminance agrees here (4.171, inside 34.12895) only because both paths "
            "err in the same direction -- against truth they read 147.583 and "
            "151.754 for an object at 175.729."
        ),
    )
    def test_thin_vivid_object_reads_the_same_through_both_mask_paths(
        self, tool, corpus
    ):
        # Arrange: a 5px vivid blade, 59% of whose foreground is partial coverage.
        paths = corpus["vivid_blade_w5"]

        # Act
        saturation_delta, luminance_delta, result = _palette_deltas(tool, paths)

        # Assert: the two masks agree about *which* pixels the object occupies,
        # so anything below is a disagreement about colour, not about extent.
        fractions = [
            result["images"][side]["foreground"]["fraction_of_frame"]
            for side in ("a", "b")
        ]
        assert fractions[0] == pytest.approx(fractions[1], abs=1e-4)
        assert saturation_delta <= SATURATION_LIMIT
        assert luminance_delta <= LUMINANCE_LIMIT

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "measured saturation_mean_delta -44.350 between glass_a64 as RGBA and "
            "the same object composited onto (24,26,30), against the calibrated "
            "foreground threshold 4.8875. This scene has no anti-aliased fringe at "
            "all -- its interior is a flat alpha 64 over 82.13% of the foreground -- "
            "so it isolates the weighting defect from the edge case entirely: a "
            "region that is one quarter present is counted as wholly present. "
            "Neither reading is right about the object, whose true saturation is "
            "144.478; they are wrong by different amounts (RGBA 212.467, composited "
            "168.117) and it is the difference that trips the threshold. The same "
            "scene at alpha 128 measures -18.069 and at alpha 192 measures -6.571."
        ),
    )
    def test_interior_transparency_reads_the_same_through_both_mask_paths(
        self, tool, corpus
    ):
        # Arrange: a glass panel, hard-edged, interior at a constant alpha 64.
        paths = corpus["glass_a64"]

        # Act
        saturation_delta, luminance_delta, _ = _palette_deltas(tool, paths)

        # Assert
        assert saturation_delta <= SATURATION_LIMIT
        assert luminance_delta <= LUMINANCE_LIMIT

    @pytest.mark.parametrize("label", ["hard_blade_w12", "hard_blade_w5"])
    def test_hard_edged_rgba_matches_its_composited_twin(self, tool, corpus, label):
        # Arrange: the control. Same geometry and colours as the anti-aliased
        # blades, but alpha is strictly 0 or 255 -- no partial coverage anywhere.
        # If the alpha mask path were itself broken, this would diverge too.
        paths = corpus[label]

        # Act
        saturation_delta, luminance_delta, _ = _palette_deltas(tool, paths)
        structure, _ = tool(
            "pil_structure_diff.py", paths["rgba"], paths["composited"], "--foreground"
        )
        rgb_a, alpha_a = load_rgb_alpha(paths["rgba"])
        rgb_b, alpha_b = load_rgb_alpha(paths["composited"])
        mask_a, source_a, _ = foreground_mask(rgb_a, alpha_a, DEFAULT_BACKGROUND_DELTA)
        mask_b, source_b, _ = foreground_mask(rgb_b, alpha_b, DEFAULT_BACKGROUND_DELTA)

        # Assert: the two paths are genuinely different derivations...
        assert (source_a, source_b) == ("alpha", "background_estimate")
        # ...that select the same pixels and report the same colour, exactly.
        assert mask_a.sum() > 0
        assert np.array_equal(mask_a, mask_b)
        assert saturation_delta == 0.0
        assert luminance_delta == 0.0
        assert structure["diff"]["changed_area_fraction"] == 0.0
        assert structure["diff"]["dhash_distance"] == 0
        assert structure["diff"]["structural_similarity"] >= SIMILARITY_LIMIT


class TestAlphaWeightedTruth:
    """Measured foreground statistics against the exact alpha-weighted reference.

    The reference weights each visible pixel by alpha/255 and uses its true
    un-premultiplied colour -- the convention synty_asset_index's palette.py
    applies to palette extraction, which pil_common's docstrings already claim
    to share. See calibration/alpha_truth.py for what was verified about that
    file and what was not.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "measured foreground luminance bias -4.261 / -12.880 / -27.880 / -42.835 "
            "at blade widths 40 / 12 / 5 / 3 px, against the calibrated foreground "
            "threshold 34.12895. The bias tracks partial-coverage share (9.49% / "
            "28.36% / 59.39% / 58.77%) rather than anything about the object's "
            "colour, which is the same grey at every width: the 3px blade is "
            "reported at luminance 132.896 when the object is 175.731. Note the "
            "40px blade is inside the threshold -- the defect is a "
            "perimeter-to-area effect, so it is the thin assets the foreground "
            "mode exists for that it damages most."
        ),
    )
    def test_foreground_luminance_tracks_alpha_weighted_truth_at_every_blade_width(
        self, tool, corpus, truth
    ):
        # Arrange: one object at four thicknesses.
        labels = [f"aa_blade_w{width}" for width in scenes.ALPHA_BLADE_WIDTHS]

        # Act
        biases = {}
        for label in labels:
            result, _ = tool("pil_palette_diff.py", corpus[label]["rgba"], "--foreground")
            image = result["images"]["a"]
            assert image["foreground"]["source"] == "alpha"
            biases[label] = (
                image["luminance"]["mean"] - truth[label]["truth"]["luminance_mean"]
            )

        # Assert: an object measured through its own alpha channel must agree
        # with what that alpha channel says the object is.
        assert max(abs(bias) for bias in biases.values()) <= LUMINANCE_LIMIT, biases

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "measured foreground luminance 67.696 / 104.654 / 141.612 on glass panels "
            "whose interiors are a flat alpha 64 / 128 / 192, against alpha-weighted "
            "truth 175.749 / 176.883 / 177.431 -- biases -108.053 / -72.229 / -35.818, "
            "every one beyond the calibrated foreground threshold 34.12895. No fringe "
            "is involved: 82.13% of the foreground is one constant alpha, admitted at "
            "full weight. Worth noting against the pairwise tests above, the "
            "RGBA-vs-composited luminance delta on these same three scenes is only "
            "15.605 / 10.677 / 4.928, all inside the threshold -- both mask paths err "
            "in the same direction, so comparing two images hides an error that "
            "comparing against truth exposes."
        ),
    )
    def test_interior_transparency_luminance_tracks_alpha_weighted_truth(
        self, tool, corpus, truth
    ):
        # Arrange: constant-alpha interiors, the case an edge-only scene never
        # reaches.
        labels = ["glass_a64", "glass_a128", "glass_a192"]

        # Act
        biases = {}
        for label in labels:
            result, _ = tool("pil_palette_diff.py", corpus[label]["rgba"], "--foreground")
            image = result["images"]["a"]
            biases[label] = (
                image["luminance"]["mean"] - truth[label]["truth"]["luminance_mean"]
            )

        # Assert: how much of a pixel the object covers is information the file
        # carries and the measurement has to use.
        assert max(abs(bias) for bias in biases.values()) <= LUMINANCE_LIMIT, biases

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "measured on vivid_blade_w5: of 3026 foreground pixels whose true colour "
            "passes the HSV accent gate (S > 100 and V > 60), the tools admit 2805 "
            "-- 221 vivid pixels rejected, an admitted share of 0.926966. Split by "
            "coverage the mechanism is unambiguous: opaque pixels pass at 1.000, "
            "partial-coverage pixels at 0.877017. Compositing onto black scales all "
            "three channels by coverage, which leaves HSV S untouched (it is a "
            "ratio) and scales V down proportionally, so every fringe pixel below "
            "roughly 24% coverage fails a value floor its true colour clears by "
            "195 code values."
        ),
    )
    def test_accent_gate_admits_every_vivid_fringe_pixel(self, corpus):
        # Arrange: read the object through the tools' own loader, and its true
        # colour straight from the same file's RGB channels.
        path = corpus["vivid_blade_w5"]["rgba"]
        flattened, alpha = load_rgb_alpha(path)
        true_rgb = np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)[:, :, :3]

        def gate(rgb):
            return accent_mask(
                Image.fromarray(rgb, "RGB"),
                DEFAULT_ACCENT_SAT_MIN,
                DEFAULT_ACCENT_VAL_MIN,
            )

        # Act
        visible = alpha >= ALPHA_FOREGROUND_MIN
        truly_vivid = visible & gate(true_rgb)
        admitted = gate(np.asarray(flattened, dtype=np.uint8))

        # Assert: a pixel's membership of the accent population is a fact about
        # the object's colour, not about how much of the pixel it covers.
        assert truly_vivid.sum() > 0, "scene must contain vivid pixels to gate"
        rejected = int((truly_vivid & ~admitted).sum())
        assert rejected == 0, f"{rejected} vivid pixels rejected by the accent gate"


class TestAlphaFloorSweep:
    """Whether ALPHA_FOREGROUND_MIN could be recalibrated out of the problem.

    It is the obvious first move -- the fringe pixels are the ones with low
    alpha, so raise the floor until they are gone -- and it must be answered
    with numbers before anyone spends a calibration run on it.
    """

    def test_raising_the_alpha_floor_only_buys_accuracy_by_deleting_the_object(
        self, truth
    ):
        # Arrange: the 5px blade, where 59.39% of the foreground is partial
        # coverage, and the alpha ladder, whose alpha histogram is known by
        # construction rather than measured.
        for label, retention_ceiling in (("aa_blade_w5", 0.82), ("alpha_ladder", 0.36)):
            sweep = truth[label]["alpha_floor_sweep"]
            shipped = next(row for row in sweep if row["alpha_floor"] == ALPHA_FOREGROUND_MIN)
            baseline_pixels = shipped["foreground_pixels"]

            # Act
            biases = [abs(row["luminance_bias"]) for row in sweep]
            retentions = [row["foreground_pixels"] / baseline_pixels for row in sweep]
            accurate = [row for row in sweep if abs(row["luminance_bias"]) < 5.0]

            # Assert: at the shipped floor the bias is large...
            assert abs(shipped["luminance_bias"]) > 25.0, label
            # ...raising the floor does reduce it, but never for free -- both
            # curves fall together, so every code value of accuracy is paid for
            # in object pixels...
            assert biases == sorted(biases, reverse=True), (label, biases)
            assert retentions == sorted(retentions, reverse=True), (label, retentions)
            # ...and the price of getting under 5 luminance code values is a
            # large share of the object, which is what a recalibration of this
            # constant would actually be buying.
            assert accurate, f"{label}: sweep must reach the accurate regime"
            for row in accurate:
                retention = row["foreground_pixels"] / baseline_pixels
                assert retention <= retention_ceiling, (label, row["alpha_floor"], retention)


class TestDegenerateAlpha:
    """The two cases where an alpha channel carries no foreground at all."""

    def test_fully_transparent_film_falls_back_with_foreground_mask_empty(
        self, tool, corpus
    ):
        # Arrange
        path = corpus["degenerate_empty"]["rgba"]

        # Act
        result, _ = tool("pil_palette_diff.py", path, "--foreground")
        image = result["images"]["a"]

        # Assert: no foreground exists, so the tool must say so and fall back
        # rather than computing statistics over an empty pixel set.
        assert image["foreground"]["source"] == "alpha"
        assert image["foreground"]["fraction_of_frame"] == 0.0
        assert "foreground_mask_empty" in image["flags"]
        assert image["foreground"]["applied"] is False
        assert image["base_palette"], "fallback must still analyse the frame"

    def test_an_all_opaque_alpha_channel_provides_no_foreground(self, corpus):
        # Arrange: an RGBA file whose alpha channel is entirely 255. It has a
        # channel but no information; a mask of "every pixel" is not a
        # foreground, so the loader must decline to supply one.
        path = corpus["degenerate_opaque"]["rgba"]
        assert Image.open(path).mode == "RGBA"

        # Act
        rgb, alpha = load_rgb_alpha(path)
        mask, source, background_hex = foreground_mask(rgb, alpha, DEFAULT_BACKGROUND_DELTA)

        # Assert
        assert alpha is None
        assert source == "background_estimate"
        assert background_hex == "#181a1e"
        assert 0.0 < mask.mean() < 0.10


class TestCorpusIntegrity:
    """The corpus is the instrument; these guard it against silent drift."""

    def test_the_corpus_spans_the_perimeter_to_area_axis(self, truth):
        # Arrange/Act: perimeter-to-area is the governing variable, so the share
        # of the foreground that is partially covered has to move by most of its
        # range across the four widths. A future edit that flattened this axis
        # would leave the xfails above passing for the wrong reason.
        shares = [
            truth[f"aa_blade_w{width}"]["partial_share_of_foreground"]
            for width in scenes.ALPHA_BLADE_WIDTHS
        ]

        # Assert: measured 0.094927 / 0.283604 / 0.593853 / 0.587650 at widths
        # 40 / 12 / 5 / 3 px. The rise stops at 5px rather than continuing --
        # see the floor-loss test below for why -- so the monotone claim is
        # made only where it holds.
        assert shares[0] < shares[1] < shares[2], shares
        assert shares[0] < 0.15
        assert shares[2] > 0.55 and shares[3] > 0.55
        assert truth["degenerate_hairline"]["partial_share_of_foreground"] > 0.95
        assert truth["hard_blade_w12"]["partial_share_of_foreground"] == 0.0

    def test_the_alpha_floor_discards_a_growing_share_of_a_thinning_object(self, truth):
        # Arrange/Act: pixels with 0 < alpha < ALPHA_FOREGROUND_MIN are dropped
        # before any statistic is computed. Their count is set by the object's
        # perimeter, which barely moves as the blade thins, so they become a
        # larger and larger share of the object that was actually drawn.
        lost = []
        for width in scenes.ALPHA_BLADE_WIDTHS:
            pixels = truth[f"aa_blade_w{width}"]["pixels"]
            drawn = pixels["foreground"] + pixels["below_floor"]
            lost.append(pixels["below_floor"] / drawn)

        # Assert: measured 0.0458 / 0.1252 / 0.2355 / 0.3252 at 40 / 12 / 5 / 3
        # px, from an almost constant 987 / 959 / 932 / 921 discarded pixels.
        # This is the second reason raising the floor cannot be the fix: on a
        # thin asset the shipped floor is already deleting a third of the object.
        assert lost == sorted(lost), lost
        assert lost[0] < 0.05
        assert lost[-1] > 0.30

    def test_every_corpus_scene_builds_byte_identical_pngs(self):
        # Arrange/Act/Assert: the corpus is only usable as a calibration
        # instrument if two runs produce the same bytes. Nothing here is seeded
        # because nothing here is random, so this is a check on that claim
        # rather than on a PRNG.
        import hashlib
        import io

        for label, scene, params in scenes.ALPHA_CORPUS:
            digests = []
            for _ in range(2):
                for composite in (False, True):
                    buffer = io.BytesIO()
                    scenes.build_alpha(scene, composite=composite, **params).save(
                        buffer, format="PNG"
                    )
                    digests.append(hashlib.sha256(buffer.getvalue()).hexdigest())
            assert digests[:2] == digests[2:], label
