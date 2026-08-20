"""Tests for pil_crop.py.

Contracts:
  * determinism        -- byte-identical PNG and payload across runs
  * geometry            -- fractional->pixel resolution matches pil_region's
                            hand-computed rule exactly, including at odd sizes
  * scale honesty        -- --scale invents no colour and drops no colour
  * rejection discipline -- every bad-input path exits 2 with empty stdout
                            and writes nothing
  * region-space parity  -- --region-space foreground names the same box
                            pil_structure_diff --foreground reports
  * alpha fidelity       -- the crop is never composited; alpha survives exactly
"""

import hashlib
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from conftest import SCRIPTS, preview_render_rgba

sys.path.insert(0, str(SCRIPTS))
import pil_crop  # noqa: E402
from pil_region import resolve_pixel_rect  # noqa: E402


def run_reject(*args):
    """Invoke pil_crop.py directly for cases where a non-zero exit is expected.

    conftest.run_tool asserts a zero exit, which is the wrong shape for every
    rejection-path test here -- this is the same subprocess invocation
    without that assertion, so the test itself can check the exit code.
    """
    cmd = [sys.executable, str(SCRIPTS / "pil_crop.py"), *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True)


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _png_chunk_types(path):
    """Read chunk names so the metadata test checks the serialized PNG itself."""
    data = Path(path).read_bytes()
    position = 8
    chunk_types = []
    while position < len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        chunk_types.append(data[position + 4 : position + 8].decode("ascii"))
        position += 12 + length
    return chunk_types


def _swatches(size=(30, 30)):
    """An image with exactly 9 distinct solid colours, for the --scale set-equality test."""
    colours = [
        (255, 0, 0), (0, 255, 0), (0, 0, 255),
        (255, 255, 0), (255, 0, 255), (0, 255, 255),
        (128, 64, 32), (10, 200, 90), (5, 5, 5),
    ]
    img = Image.new("RGB", size, colours[0])
    cell_w, cell_h = size[0] // 3, size[1] // 3
    pixels = img.load()
    for i, colour in enumerate(colours):
        row, col = divmod(i, 3)
        for y in range(row * cell_h, (row + 1) * cell_h):
            for x in range(col * cell_w, (col + 1) * cell_w):
                pixels[x, y] = colour
    return img


def _rgba_gradient(size=(60, 50)):
    """An RGBA image whose alpha varies per-pixel, so a crop of it exercises a
    genuine partial-coverage slice rather than only hard 0/255 edges."""
    xs, ys = np.meshgrid(np.arange(size[0]), np.arange(size[1]))
    r = (xs % 256).astype(np.uint8)
    g = (ys % 256).astype(np.uint8)
    b = np.full(size[::-1], 128, dtype=np.uint8)
    a = ((xs * 7 + ys * 3) % 256).astype(np.uint8)
    arr = np.stack([r, g, b, a], axis=-1)
    return Image.fromarray(arr, "RGBA")


class TestDeterminism:
    def test_repeated_runs_produce_byte_identical_file_and_payload(self, tool, tmp_img):
        # Arrange
        src = tmp_img(_swatches(), "src.png")

        # Act: two independent runs to two different output paths.
        out_1 = src.parent / "out1.png"
        out_2 = src.parent / "out2.png"
        payload_1, _ = tool(
            "pil_crop.py", src, "--out", out_1, "--region", "0.1,0.1,0.8,0.9"
        )
        payload_2, _ = tool(
            "pil_crop.py", src, "--out", out_2, "--region", "0.1,0.1,0.8,0.9"
        )

        # Assert: the files agree byte-for-byte...
        assert _sha256_file(out_1) == _sha256_file(out_2)
        # ...and so do the payloads, once the only field that must legitimately
        # differ (the output path each run was told to use) is masked out.
        assert payload_1["output"]["sha256"] == _sha256_file(out_1)
        assert payload_2["output"]["sha256"] == _sha256_file(out_2)
        assert payload_1["source"]["sha256"] == _sha256_file(src)
        assert payload_2["source"]["sha256"] == _sha256_file(src)
        payload_1["output"]["path"] = None
        payload_2["output"]["path"] = None
        assert payload_1 == payload_2


class TestFractionalToPixelGeometry:
    # Each rect is hand-computed from pil_region's pinned rule:
    # px = floor(fraction * extent + 0.5), each edge independent, then clamped.
    @pytest.mark.parametrize(
        ("size", "region", "expected_rect"),
        [
            # 101x37: 0.5 -> floor(50.5+0.5)=51 on width, floor(18.5+0.5)=19 on
            # height; the upper edge clamps to the extent.
            ((101, 37), "0.5,0.5,1.0,1.0", (51, 19, 101, 37)),
            # 3x3: 0.34 -> floor(1.02+0.5)=1, the boundary the odd-extent
            # rounding rule is pinned against.
            ((3, 3), "0.0,0.0,0.34,0.34", (0, 0, 1, 1)),
            ((3, 3), "0.34,0.34,1.0,1.0", (1, 1, 3, 3)),
            # 37x101: 0.333 -> floor(12.321+0.5)=12 on width; 0.5 -> 51 on height.
            ((37, 101), "0.0,0.0,0.333,0.5", (0, 0, 12, 51)),
            # A size with no rounding ambiguity, as a plain sanity check.
            ((200, 150), "0.1,0.2,0.9,0.8", (20, 30, 180, 120)),
            # The smallest possible image: the unit region must still resolve.
            ((1, 1), "0.0,0.0,1.0,1.0", (0, 0, 1, 1)),
        ],
    )
    def test_output_size_matches_hand_computed_rect(
        self, tool, tmp_img, size, region, expected_rect
    ):
        # Arrange
        src = tmp_img(Image.new("RGB", size, (10, 20, 30)), "src.png")
        out = src.parent / "out.png"
        left, top, right, bottom = expected_rect

        # Act
        result, _ = tool("pil_crop.py", src, "--out", out, "--region", region)

        # Assert: the payload's own accounting and the file on disk agree.
        assert result["region"]["resolved_pixel_rect"] == list(expected_rect)
        assert result["output"]["size"] == [right - left, bottom - top]
        with Image.open(out) as written:
            assert written.size == (right - left, bottom - top)


class TestScaleIsNearestAndLossless:
    def test_upscaled_output_has_exactly_the_source_colours(self, tool, tmp_img):
        # Arrange: >=5 distinct colours, so a flat crop cannot pass this
        # vacuously -- the equality below must actually hold pixel-for-pixel.
        source = _swatches()
        src = tmp_img(source, "src.png")
        out = src.parent / "out.png"
        source_colours = {px for px in source.getdata()}
        assert len(source_colours) >= 5

        # Act
        tool("pil_crop.py", src, "--out", out, "--region", "0,0,1,1", "--scale", "4")

        # Assert: set equality, not subset -- a resample that invents a single
        # blended colour at a swatch boundary would fail this.
        with Image.open(out) as written:
            assert written.size == (source.width * 4, source.height * 4)
            output_colours = {px for px in written.getdata()}
        assert output_colours == source_colours


class TestRejectionPaths:
    @pytest.mark.parametrize(
        "region",
        [
            "0,0,1.2,1",  # out of [0.0, 1.0]
            "0.9,0,0.1,1",  # inverted (left >= right)
            "0.5,0,0.5,1",  # degenerate (left == right)
        ],
    )
    def test_bad_region_exits_2_with_empty_stdout_and_writes_nothing(
        self, tmp_img, region
    ):
        # Arrange
        src = tmp_img(Image.new("RGB", (100, 100), (1, 2, 3)), "src.png")
        out = src.parent / "out.png"

        # Act
        proc = run_reject(str(src), "--out", str(out), "--region", region)

        # Assert
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert not out.exists()

    def test_unreadable_source_exits_2_with_empty_stdout(self, tmp_path):
        # Arrange
        missing = tmp_path / "does-not-exist.png"
        out = tmp_path / "out.png"

        # Act
        proc = run_reject(str(missing), "--out", str(out), "--region", "0,0,1,1")

        # Assert
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert not out.exists()

    def test_missing_output_parent_exits_2_with_empty_stdout(self, tmp_img):
        # Arrange
        src = tmp_img(Image.new("RGB", (20, 20), (1, 2, 3)), "src.png")
        out = src.parent / "missing-parent" / "out.png"

        # Act
        proc = run_reject(str(src), "--out", str(out), "--region", "0,0,1,1")

        # Assert
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert not out.exists()

    def test_non_png_output_suffix_exits_2_with_empty_stdout(self, tmp_img):
        # Arrange
        src = tmp_img(Image.new("RGB", (20, 20), (1, 2, 3)), "src.png")
        out = src.parent / "out.jpg"

        # Act
        proc = run_reject(str(src), "--out", str(out), "--region", "0,0,1,1")

        # Assert
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert not out.exists()

    def test_high_bit_depth_source_is_rejected_without_clipping(self, tmp_path):
        # Arrange: values span a 16-bit range, so converting to RGB would clip.
        src = tmp_path / "high16.png"
        values = np.arange(400, dtype=np.uint16).reshape(20, 20) * 160
        Image.fromarray(values, mode="I;16").save(src)
        out = tmp_path / "out.png"

        # Act
        proc = run_reject(str(src), "--out", str(out), "--region", "0,0,1,1")

        # Assert: rejection is the only faithful behaviour without a scaling policy.
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert "high-bit-depth" in proc.stderr
        assert not out.exists()


class TestOverwriteRefusal:
    def test_second_run_without_overwrite_leaves_the_file_untouched(
        self, tool, tmp_img
    ):
        # Arrange: a first, successful run establishes the file.
        src = tmp_img(Image.new("RGB", (50, 50), (9, 9, 9)), "src.png")
        out = src.parent / "out.png"
        tool("pil_crop.py", src, "--out", out, "--region", "0,0,1,1")
        original_hash = _sha256_file(out)

        # Act: a second run at the same --out, without --overwrite.
        proc = run_reject(str(src), "--out", str(out), "--region", "0,0,0.5,0.5")

        # Assert: rejected, and the original file was never touched.
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert _sha256_file(out) == original_hash

    def test_overwrite_flag_permits_replacement(self, tool, tmp_img):
        # Arrange
        src = tmp_img(Image.new("RGB", (50, 50), (9, 9, 9)), "src.png")
        out = src.parent / "out.png"
        tool("pil_crop.py", src, "--out", out, "--region", "0,0,1,1")
        first_size = Image.open(out).size

        # Act
        tool(
            "pil_crop.py", src, "--out", out, "--region", "0,0,0.5,0.5", "--overwrite"
        )

        # Assert: the file changed to the new region's dimensions.
        assert Image.open(out).size != first_size


class TestForegroundRegionSpace:
    def test_matches_pil_structure_diffs_foreground_bbox(self, tool, tmp_img):
        # Arrange: an object on a transparent background, exactly what
        # --region-space foreground exists to cut against.
        src = tmp_img(preview_render_rgba(), "src.png")
        out = src.parent / "out.png"

        # Act
        diff_result, _ = tool("pil_structure_diff.py", src, "--foreground")
        bbox_fractional = diff_result["images"]["a"]["foreground"]["bbox_fractional"]
        size = tuple(diff_result["images"]["a"]["size"])
        expected_rect = resolve_pixel_rect(bbox_fractional, size)

        crop_result, _ = tool(
            "pil_crop.py",
            src,
            "--out",
            out,
            "--region",
            "0,0,1,1",
            "--region-space",
            "foreground",
        )

        # Assert: the whole-foreground crop lands on the same box the diff
        # tool independently reports, because both resolve through the same
        # pil_common mask rule and the same pil_region arithmetic.
        assert crop_result["region"]["resolved_pixel_rect"] == list(expected_rect)
        assert crop_result["output"]["size"] == [
            expected_rect[2] - expected_rect[0],
            expected_rect[3] - expected_rect[1],
        ]

    def test_json_bracket_region_spelling_works_through_cli(self, tool, tmp_img):
        # Arrange
        src = tmp_img(Image.new("RGB", (30, 40), (1, 2, 3)), "src.png")
        out = src.parent / "out.png"

        # Act
        result, _ = tool(
            "pil_crop.py",
            src,
            "--out",
            out,
            "--region",
            "[0.1, 0.2, 0.8, 0.9]",
        )

        # Assert
        assert result["region"]["requested_fractional"] == [0.1, 0.2, 0.8, 0.9]

    def test_metadata_is_stripped_to_png_structure(self, tool, tmp_path):
        # Arrange
        src = tmp_path / "metadata.png"
        metadata = PngInfo()
        metadata.add_text("Comment", "must not be copied")
        Image.new("RGB", (20, 20), (1, 2, 3)).save(
            src,
            pnginfo=metadata,
            icc_profile=b"fake-icc-profile",
            dpi=(72, 72),
        )
        out = tmp_path / "out.png"

        # Act
        tool("pil_crop.py", src, "--out", out, "--region", "0,0,1,1")

        # Assert
        assert _png_chunk_types(out) == ["IHDR", "IDAT", "IEND"]

    def test_payload_carries_all_interpretation_limits(self, tool, tmp_img):
        # Arrange
        src = tmp_img(Image.new("RGB", (20, 20), (1, 2, 3)), "src.png")
        out = src.parent / "out.png"

        # Act
        result, _ = tool("pil_crop.py", src, "--out", out, "--region", "0,0,1,1")

        # Assert
        limits = result["interpretation_limits"]
        assert len(limits) == 5
        assert any("recovers no detail the source never had" in limit for limit in limits)
        assert any("resolved_fractional is always frame-relative" in limit for limit in limits)
        assert any("No metadata is carried across" in limit for limit in limits)

    def test_empty_foreground_mask_is_rejected(self, tmp_img):
        # Arrange: a uniform frame has no border-median-distinguishable
        # foreground at all.
        src = tmp_img(Image.new("RGB", (80, 80), (50, 50, 50)), "src.png")
        out = src.parent / "out.png"

        # Act
        proc = run_reject(
            str(src), "--out", str(out), "--region", "0,0,1,1",
            "--region-space", "foreground",
        )

        # Assert
        assert proc.returncode == 2
        assert proc.stdout == ""
        assert not out.exists()


class TestAlphaPreservation:
    def test_cropped_alpha_equals_the_source_slice_exactly(self, tool, tmp_img):
        # Arrange: alpha that genuinely varies pixel-to-pixel, not just a
        # hard 0/255 silhouette edge.
        source = _rgba_gradient((60, 50))
        src = tmp_img(source, "src.png")
        out = src.parent / "out.png"
        source_alpha = np.asarray(source)[:, :, 3]

        # Act
        result, _ = tool(
            "pil_crop.py", src, "--out", out, "--region", "0.2,0.2,0.7,0.8"
        )
        left, top, right, bottom = result["region"]["resolved_pixel_rect"]

        # Assert: no un-premultiply, no compositing -- the stored alpha
        # survives the crop untouched.
        with Image.open(out) as written:
            assert written.mode == "RGBA"
            written_alpha = np.asarray(written)[:, :, 3]
        expected_slice = source_alpha[top:bottom, left:right]
        assert np.array_equal(written_alpha, expected_slice)


class TestDocstrings:
    def test_every_public_function_documents_why_it_exists(self):
        import inspect

        functions = [
            obj
            for name, obj in vars(pil_crop).items()
            if inspect.isfunction(obj)
            and obj.__module__ == pil_crop.__name__
            and not name.startswith("_")
        ]
        assert functions, "expected at least one public function to check"
        for func in functions:
            doc = inspect.getdoc(func)
            assert doc and len(doc.strip()) > 20, (
                f"{func.__name__} is missing a docstring explaining why it exists"
            )
