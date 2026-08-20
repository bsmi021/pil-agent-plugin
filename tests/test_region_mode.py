"""Tests for --region on pil_structure_diff.py and pil_palette_diff.py (W6).

Contracts, mapped to docs/aaa-build-plan.md #8.3's acceptance table:
  * region-equals-precrop (A6.2) -- the gate. pil_crop.py A --region R --out
    t.png, then measuring t.png, must equal measuring A --region R directly:
    full payload, only the keys that legitimately differ removed, verified
    against the REAL pil_crop binary so the two tools are proven to share one
    parser rather than merely agreeing with each other.
  * identity region (A6.3)        -- --region 0,0,1,1 equals no --region.
  * determinism (A6.4)            -- repeated --region runs are byte-identical.
  * rejection discipline (A6.5)   -- malformed regions exit 2, empty stdout.
  * genuine scoping (A6.6, A6.8)  -- foreground fraction and changed_area_
    fraction actually move under --region, rather than being echoed and
    ignored -- the plan's "single most likely silent failure".
  * resolution independence        -- the region resolves against the full-
    resolution source, not a 256px working copy (plan #8.4, failure mode 1).
  * background re-derivation (A6.7) -- region_background_estimate_diverged.
  * documentation (A6.9)           -- --help and interpretation_limits.
"""

import copy
import subprocess
import sys

import pytest
from PIL import Image

from conftest import SCRIPTS, preview_render, preview_render_rgba, synthetic_reference

TOOLS = ("pil_structure_diff.py", "pil_palette_diff.py")

# The literal key list A6.2/A6.3 require removed: everything expected to
# legitimately differ between a --region run and an equivalent run against a
# file already pre-cropped to that region. interpretation_limits is NOT in
# this list -- it is unconditional in both tools (see the REGION_INTERPRETATION_
# LIMITS comment in each), precisely so it does not need special-casing here.
IMAGE_KEYS_TO_REMOVE = ("region", "source_size", "path")
PARAMETER_KEYS_TO_REMOVE = ("region", "region_space")


def _strip_region_keys(payload):
    """Remove only the keys A6.2/A6.3 name as legitimately differing.

    Everything else -- palettes, hue census, luminance/saturation/entropy,
    cells, hashes, flags, foreground blocks, interpretation_limits -- stays
    in the comparison, so this cannot pass by accident on a handful of
    scalar fields (the vacuous-test failure mode the plan calls out).
    """
    payload = copy.deepcopy(payload)
    for image in payload["images"].values():
        for key in IMAGE_KEYS_TO_REMOVE:
            image.pop(key, None)
    for key in PARAMETER_KEYS_TO_REMOVE:
        payload["parameters"].pop(key, None)
    return payload


def run_reject(script, *args):
    """Invoke a tool directly for cases where a non-zero exit is expected.

    conftest.run_tool asserts a zero exit, which is the wrong shape for
    every rejection-path test here -- this is the same subprocess
    invocation without that assertion, so the test itself can check the
    exit code and stdout emptiness (matching tests/test_crop.py's own
    run_reject).
    """
    cmd = [sys.executable, str(SCRIPTS / script), *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True)


# Four (image, region, region_space) pairs for A6.2, chosen to cover: a plain
# even-sized frame; an odd-sized image (101x37) whose region edges land
# exactly on .5 pixels at BOTH extents (0.5*101=50.5, 0.5*37=18.5); an
# interior region away from any edge; and --region-space foreground on a
# real RGBA object, so the foreground-bbox-origin code path is exercised too.
REGION_PAIRS = [
    pytest.param(
        lambda: synthetic_reference(size=(400, 300)),
        "0.1,0.4,0.3,0.9",
        "frame",
        id="even_frame_space",
    ),
    pytest.param(
        lambda: synthetic_reference(size=(101, 37)),
        "0.5,0.5,1.0,1.0",
        "frame",
        id="odd_size_half_pixel_edges",
    ),
    pytest.param(
        lambda: synthetic_reference(size=(200, 150)),
        "0.05,0.10,0.95,0.90",
        "frame",
        id="interior_region",
    ),
    pytest.param(
        lambda: preview_render_rgba(size=(400, 300)),
        "0,0,1,1",
        "foreground",
        id="foreground_space_rgba",
    ),
]


class TestRegionEqualsPreCrop:
    """A6.2, the gate: --region and a pil_crop.py pre-crop resolve to
    identical pixels, because both share pil_region's rect arithmetic."""

    @pytest.mark.parametrize("script", TOOLS)
    @pytest.mark.parametrize(("image_factory", "region", "region_space"), REGION_PAIRS)
    def test_region_run_equals_precropped_file_run(
        self, tool, tmp_img, script, image_factory, region, region_space
    ):
        # Arrange: build the pre-cropped file with the REAL pil_crop binary.
        src = tmp_img(image_factory(), "src.png")
        cropped = src.parent / "cropped.png"
        crop_result, _ = tool(
            "pil_crop.py",
            src,
            "--out",
            cropped,
            "--region",
            region,
            "--region-space",
            region_space,
        )

        # Act
        region_result, _ = tool(
            script, src, "--region", region, "--region-space", region_space
        )
        precrop_result, _ = tool(script, cropped)

        # Assert: full payload equality with only the named keys removed.
        assert _strip_region_keys(region_result) == _strip_region_keys(precrop_result)
        # ...and the crop pil_crop actually wrote on disk is the same size
        # and pixel rect the diff tool itself reports measuring -- ties the
        # equality above to real pixels rather than two payloads that merely
        # agree with each other.
        assert region_result["images"]["a"]["size"] == crop_result["output"]["size"]
        assert (
            region_result["images"]["a"]["region"]["resolved_pixel_rect"]
            == crop_result["region"]["resolved_pixel_rect"]
        )


class TestRegionFullFrameEqualsNoRegion:
    """A6.3: --region 0,0,1,1 must be indistinguishable from omitting
    --region, once the keys that legitimately differ are removed."""

    @pytest.mark.parametrize("script", TOOLS)
    def test_unit_region_equals_no_region(self, tool, tmp_img, script):
        # Arrange
        src = tmp_img(synthetic_reference(), "src.png")

        # Act
        no_region, _ = tool(script, src)
        unit_region, _ = tool(script, src, "--region", "0,0,1,1")

        # Assert
        assert _strip_region_keys(no_region) == _strip_region_keys(unit_region)
        # The unit region is a REAL rect, echoed as one -- never a None
        # shortcut (pil_region's own A0.6 rejection, re-verified here at the
        # tool boundary so a caller can tell "scoped to the whole frame"
        # from "never asked for a region" if it ever matters to them).
        assert unit_region["images"]["a"]["region"] is not None
        assert no_region["images"]["a"]["region"] is None


class TestDeterminismWithRegion:
    """A6.4: byte-determinism holds with --region, same as without."""

    @pytest.mark.parametrize("script", TOOLS)
    def test_repeated_region_runs_are_byte_identical(self, tool, tmp_img, script):
        # Arrange
        src = tmp_img(preview_render(), "src.png")

        # Act
        _, raw1 = tool(script, src, "--region", "0.1,0.4,0.3,0.9", "--foreground")
        _, raw2 = tool(script, src, "--region", "0.1,0.4,0.3,0.9", "--foreground")

        # Assert
        assert raw1 == raw2

    @pytest.mark.parametrize("script", TOOLS)
    def test_repeated_two_image_region_runs_are_byte_identical(
        self, tool, tmp_img, script
    ):
        # Arrange
        a = tmp_img(preview_render(), "a.png")
        b = tmp_img(preview_render(mirrored=True), "b.png")

        # Act
        _, raw1 = tool(script, a, b, "--region", "0.0,0.0,0.6,1.0")
        _, raw2 = tool(script, a, b, "--region", "0.0,0.0,0.6,1.0")

        # Assert
        assert raw1 == raw2


class TestRejectionPaths:
    """A6.5: every malformed region exits 2 with empty stdout, on both
    tools -- never a silently clamped measurement of the wrong pixels."""

    @pytest.mark.parametrize("script", TOOLS)
    @pytest.mark.parametrize(
        "region",
        [
            "0,0,1.2,1",  # out of [0.0, 1.0]
            "0.9,0,0.1,1",  # inverted (left >= right)
            "0.5,0,0.5,1",  # degenerate (left == right)
        ],
    )
    def test_bad_region_exits_2_with_empty_stdout(self, tmp_img, script, region):
        # Arrange
        src = tmp_img(synthetic_reference(), "src.png")

        # Act
        proc = run_reject(script, str(src), "--region", region)

        # Assert
        assert proc.returncode == 2
        assert proc.stdout == ""

    @pytest.mark.parametrize("script", TOOLS)
    def test_empty_foreground_mask_under_region_space_foreground_exits_2(
        self, tmp_img, script
    ):
        # Arrange: a uniform frame has no border-median-distinguishable
        # foreground bounding box to resolve --region against.
        src = tmp_img(Image.new("RGB", (80, 80), (50, 50, 50)), "flat.png")

        # Act
        proc = run_reject(
            script, str(src), "--region", "0,0,1,1", "--region-space", "foreground"
        )

        # Assert
        assert proc.returncode == 2
        assert proc.stdout == ""

    @pytest.mark.parametrize("script", TOOLS)
    def test_malformed_region_on_the_second_image_still_writes_nothing(
        self, tmp_img, script
    ):
        # A rejection while resolving image B's region must not have already
        # flushed a partial payload built from image A.
        # Arrange
        a = tmp_img(synthetic_reference(), "a.png")
        b = tmp_img(synthetic_reference(), "b.png")

        # Act
        proc = run_reject(script, str(a), str(b), "--region", "0.9,0,0.1,1")

        # Assert
        assert proc.returncode == 2
        assert proc.stdout == ""


class TestRegionScopesForeground:
    """A6.6 and the direct check for it: the region must actually cut the
    measured pixels, not merely be echoed into the payload -- the plan
    names this "the single most likely silent failure"."""

    @pytest.mark.parametrize("script", TOOLS)
    def test_region_around_the_hilt_reports_far_higher_foreground_fraction(
        self, tool, tmp_img, script
    ):
        # Arrange: preview_render's grip+gem sit at roughly x:20-56,
        # y:250-285 on the 400x300 frame -- a tight region around them.
        src = tmp_img(preview_render(), "src.png")

        # Act
        full, _ = tool(script, src, "--foreground")
        region, _ = tool(
            script, src, "--foreground", "--region", "0.05,0.833,0.14,0.95"
        )

        # Assert
        full_fraction = full["images"]["a"]["foreground"]["fraction_of_frame"]
        region_fraction = region["images"]["a"]["foreground"]["fraction_of_frame"]
        assert region_fraction >= 3 * full_fraction

    @pytest.mark.parametrize("script", TOOLS)
    def test_region_actually_shrinks_the_measured_pixels(self, tool, tmp_img, script):
        # Arrange
        src = tmp_img(preview_render(), "src.png")

        # Act
        full, _ = tool(script, src)
        region, _ = tool(script, src, "--region", "0.1,0.1,0.5,0.5")

        # Assert: size must reflect the crop, not the source frame -- an
        # implementation that echoes the region without applying it would
        # still report the full frame's size here.
        assert region["images"]["a"]["size"] != full["images"]["a"]["size"]
        assert region["images"]["a"]["source_size"] == full["images"]["a"]["size"]


class TestRegionResolutionIndependence:
    """Guards against cropping the working-resolution copy instead of the
    full-resolution source (plan #8.4, failure mode 1): the same fractional
    region on two resolutions of identical content must read the same,
    regardless of source size."""

    def test_same_region_reads_close_across_two_resolutions(self, tool, tmp_img):
        # Arrange: the same rendered content at two different pixel sizes.
        base = preview_render()
        small = tmp_img(base, "small.png")
        big = tmp_img(base.resize((800, 600), Image.LANCZOS), "big.png")
        region = "0.05,0.833,0.14,0.95"

        # Act
        small_result, _ = tool(
            "pil_structure_diff.py", small, "--region", region, "--foreground"
        )
        big_result, _ = tool(
            "pil_structure_diff.py", big, "--region", region, "--foreground"
        )

        # Assert: if the region resolved against a fixed-size working copy
        # instead of the full-resolution source, this would diverge sharply
        # between a 400x300 and an 800x600 render of the same content --
        # scale invariance is the whole point of a fractional region.
        small_lum = small_result["images"]["a"]["luminance"]["mean"]
        big_lum = big_result["images"]["a"]["luminance"]["mean"]
        assert small_lum == pytest.approx(big_lum, abs=1.0)


class TestRegionAppliedToBothImages:
    """A6.8: --region crops A and B identically, so a region covering only
    the changed area reports a far higher changed_area_fraction than the
    same diff full-frame -- catches "region applied to image A only"."""

    def test_region_over_the_changed_area_reports_much_higher_fraction(
        self, tool, tmp_img
    ):
        # Arrange: only the accent gem differs between a and b.
        cyan = (30, 200, 200)
        red = (220, 30, 30)
        a = tmp_img(preview_render(accent=cyan), "a.png")
        b = tmp_img(preview_render(accent=red), "b.png")

        # Act
        full, _ = tool("pil_structure_diff.py", a, b)
        region, _ = tool(
            "pil_structure_diff.py", a, b, "--region", "0.09,0.80,0.16,0.89"
        )

        # Assert
        full_fraction = full["diff"]["changed_area_fraction"]
        region_fraction = region["diff"]["changed_area_fraction"]
        assert region_fraction > full_fraction * 10


class TestRegionBackgroundDivergence:
    """A6.7: region_background_estimate_diverged fires when --region
    composed with --foreground re-derives a border-median estimate far from
    the whole frame's -- and only under exactly that composition."""

    @pytest.mark.parametrize("script", TOOLS)
    def test_fires_on_a_region_wholly_inside_the_object(self, tool, tmp_img, script):
        # Arrange: a region strictly inside the solid sword grip -- every
        # one of its own border samples is grip colour, nothing like the
        # PREVIEW_BG backdrop.
        src = tmp_img(preview_render(), "src.png")

        # Act
        result, _ = tool(
            script, src, "--foreground", "--region", "0.06,0.887,0.11,0.937"
        )

        # Assert
        assert "region_background_estimate_diverged" in result["images"]["a"]["flags"]

    @pytest.mark.parametrize("script", TOOLS)
    def test_does_not_fire_on_a_region_with_a_normal_backdrop_share(
        self, tool, tmp_img, script
    ):
        # Arrange: a region whose own border samples still land on the
        # backdrop (top 90% of the frame, all 8 border-sample points miss
        # the thin diagonal blade).
        src = tmp_img(preview_render(), "src.png")

        # Act
        result, _ = tool(script, src, "--foreground", "--region", "0.0,0.0,1.0,0.9")

        # Assert
        assert (
            "region_background_estimate_diverged" not in result["images"]["a"]["flags"]
        )

    @pytest.mark.parametrize("script", TOOLS)
    def test_never_fires_without_foreground_mode(self, tool, tmp_img, script):
        # Arrange: the same region that fires the flag under --foreground.
        src = tmp_img(preview_render(), "src.png")

        # Act
        result, _ = tool(script, src, "--region", "0.06,0.887,0.11,0.937")

        # Assert: the signal is meaningless without a background estimate
        # actually deciding a mask, so it must not appear at all here.
        assert (
            "region_background_estimate_diverged" not in result["images"]["a"]["flags"]
        )

    @pytest.mark.parametrize("script", TOOLS)
    def test_never_fires_on_the_alpha_path(self, tool, tmp_img, script):
        # Arrange: real alpha means the mask never used a background
        # estimate in the first place, so there is nothing to have diverged
        # from -- gated on alpha is None in both tools.
        src = tmp_img(preview_render_rgba(), "src.png")

        # Act
        result, _ = tool(
            script, src, "--foreground", "--region", "0.05,0.62,0.14,0.95"
        )

        # Assert
        assert (
            "region_background_estimate_diverged" not in result["images"]["a"]["flags"]
        )


class TestRegionForegroundCompositionDocumented:
    """A6.9: the --region x --foreground order of operations is documented
    in both tools' --help and in a new interpretation_limits entry."""

    @pytest.mark.parametrize("script", TOOLS)
    def test_help_documents_the_composition_order(self, script):
        # Act
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / script), "--help"],
            capture_output=True,
            text=True,
        )
        help_text = proc.stdout.lower()

        # Assert
        assert proc.returncode == 0
        assert "--foreground" in help_text
        assert "crops first" in help_text
        assert "region_background_estimate_diverged" in help_text

    @pytest.mark.parametrize("script", TOOLS)
    def test_interpretation_limits_documents_region_and_foreground_order(
        self, tool, tmp_img, script
    ):
        # Arrange: even a run that never passes --region must carry the
        # entry (it is unconditional -- see each tool's own comment on why
        # gating it would break A6.2/A6.3's equality).
        src = tmp_img(preview_render(), "src.png")

        # Act
        result, _ = tool(script, src)

        # Assert
        limits = " ".join(result["interpretation_limits"]).lower()
        assert "region_background_estimate_diverged" in limits
        assert "crop" in limits and "foreground mask" in limits
