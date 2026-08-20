"""Focused contract tests for the file-fact image-info CLI."""

import hashlib
import inspect
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from PIL import ExifTags, Image, ImageCms

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
CALIBRATION = REPO_ROOT / "calibration"
for _path in (SCRIPTS, CALIBRATION):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import pil_image_info  # noqa: E402
import scenes  # noqa: E402
from pil_common import load_rgb_alpha  # noqa: E402


REQUIRED_IMAGE_KEYS = {
    "path",
    "readable",
    "reason",
    "size",
    "width",
    "height",
    "mode",
    "format",
    "bands",
    "bit_depth_per_channel",
    "has_alpha_channel",
    "uses_transparency",
    "alpha_min",
    "alpha_max",
    "transparency_key",
    "palette_size",
    "icc_profile_present",
    "icc_profile_bytes",
    "icc_profile_description",
    "exif_present",
    "exif",
    "dpi",
    "n_frames",
    "is_animated",
    "file_bytes",
    "sha256",
    "flags",
}


def run_info(*paths):
    """Run W5 directly so non-zero batch exits remain assertable facts."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "pil_image_info.py"), *map(str, paths)],
        capture_output=True,
        text=True,
    )


def assert_readable_record_keys(record):
    """Keep the complete readable-record schema contract on every fixture path."""
    assert set(record) == REQUIRED_IMAGE_KEYS


def only_image(*paths):
    """Return one successful record and fail loudly if the CLI contract changed."""
    proc = run_info(*paths)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert len(payload["images"]) == 1
    record = payload["images"][0]
    assert_readable_record_keys(record)
    return payload, proc.stdout, record


def test_rgba_transparency_reports_complete_facts_and_is_byte_deterministic(tmp_path):
    """Catches a report that forgets keys, scans no alpha, or emits unstable JSON."""
    path = tmp_path / "transparent.png"
    image = Image.new("RGBA", (3, 2), (9, 8, 7, 255))
    image.putpixel((1, 1), (9, 8, 7, 0))
    image.save(path)

    payload, first_stdout, record = only_image(path)
    second = run_info(path)
    assert second.returncode == 0
    assert second.stdout == first_stdout
    assert payload["tool"] == "pil_image_info"
    assert payload["parameters"] == {"image_count": 1}
    assert payload["interpretation_limits"]
    assert record["size"] == [3, 2]
    assert record["mode"] == "RGBA"
    assert record["has_alpha_channel"] is True
    assert record["uses_transparency"] is True
    assert record["alpha_min"] == 0
    assert record["alpha_max"] == 255
    assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("name", "image_factory", "save_kwargs", "expected_has_alpha", "expected_transparency"),
    [
        ("rgb", lambda: Image.new("RGB", (2, 2), (1, 2, 3)), {}, False, False),
        ("rgba_real", lambda: Image.new("RGBA", (2, 2), (1, 2, 3, 0)), {}, True, True),
        ("rgba_opaque", scenes.alpha_opaque, {}, True, False),
        ("palette_trns", lambda: Image.new("P", (2, 2), 0), {"transparency": 0}, True, True),
        ("la", lambda: Image.new("LA", (2, 2), (9, 0)), {}, True, True),
    ],
)
def test_alpha_rule_matches_loader_contract_for_all_required_image_kinds(
    tmp_path, name, image_factory, save_kwargs, expected_has_alpha, expected_transparency
):
    """Catches conflating channel presence, palette tRNS, and actual opacity."""
    path = tmp_path / f"{name}.png"
    image_factory().save(path, **save_kwargs)
    _payload, _stdout, record = only_image(path)
    assert record["has_alpha_channel"] is expected_has_alpha
    assert record["uses_transparency"] is expected_transparency
    assert record["uses_transparency"] is (load_rgb_alpha(path)[1] is not None)
    if expected_has_alpha:
        assert record["alpha_min"] is not None
        assert 0 <= record["alpha_min"] <= record["alpha_max"] <= 255
    else:
        assert record["alpha_min"] is None
        assert record["alpha_max"] is None


def test_greyscale_reports_native_mode_without_inventing_alpha(tmp_path):
    """Catches an RGB conversion that hides a file's greyscale mode or adds alpha."""
    path = tmp_path / "grey.png"
    Image.new("L", (4, 5), 128).save(path)
    _payload, _stdout, record = only_image(path)
    assert record["mode"] == "L"
    assert record["bands"] == ["L"]
    assert record["bit_depth_per_channel"] == 8
    assert record["has_alpha_channel"] is False
    assert record["uses_transparency"] is False


def test_exif_is_file_claim_and_undecodable_bytes_are_described_not_replaced(tmp_path):
    """Catches lossy EXIF decoding or omission of producer-supplied EXIF claims."""
    path = tmp_path / "exif.jpg"
    exif = Image.Exif()
    exif[271] = "Example Camera"
    exif[37510] = b"\xff\xfe\x00"
    Image.new("RGB", (3, 3), (10, 20, 30)).save(path, exif=exif)
    _payload, first_stdout, record = only_image(path)
    second = run_info(path)
    assert second.returncode == 0
    assert second.stdout == first_stdout
    assert record["exif_present"] is True
    assert record["exif"]["Make (271)"] == "Example Camera"
    user_comment = record["exif"]["UserComment (37510)"]
    assert user_comment["type"] == "bytes"
    assert user_comment["length"] == 3
    assert "\ufffd" not in json.dumps(record["exif"], ensure_ascii=False)


def test_icc_reports_profile_presence_size_and_declared_description_without_colour_claim(tmp_path):
    """Catches ICC presence being discarded or substituted with an inferred colour space."""
    path = tmp_path / "profile.png"
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (3, 3), (10, 20, 30)).save(path, icc_profile=profile)
    _payload, _stdout, record = only_image(path)
    assert record["icc_profile_present"] is True
    assert record["icc_profile_bytes"] == len(profile)
    assert record["icc_profile_description"]
    assert "icc_unreadable" not in record["flags"]


def test_malformed_icc_is_flagged_without_aborting_healthy_batch_siblings(tmp_path):
    """Catches ImageCms failures that used to discard every sibling report."""
    good = tmp_path / "good.png"
    bad_icc = tmp_path / "bad-icc.png"
    Image.new("RGB", (4, 4), (10, 20, 30)).save(good)
    Image.new("RGB", (4, 4), (10, 20, 30)).save(bad_icc, icc_profile=b"not an ICC profile")

    proc = run_info(good, bad_icc, good)

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    first, malformed, last = payload["images"]
    assert [first["readable"], malformed["readable"], last["readable"]] == [True, True, True]
    assert_readable_record_keys(first)
    assert_readable_record_keys(malformed)
    assert_readable_record_keys(last)
    assert malformed["icc_profile_present"] is True
    assert malformed["icc_profile_description"] is None
    assert "icc_unreadable" in malformed["flags"]


def test_decompression_bomb_header_does_not_abort_healthy_batch_siblings(tmp_path):
    """Catches Pillow's bomb guard escaping one record and killing the batch."""
    good = tmp_path / "good.png"
    bomb = tmp_path / "bomb-header.png"
    Image.new("RGB", (4, 4), (10, 20, 30)).save(good)
    ihdr = struct.pack(">IIBBBBB", 30_000, 30_000, 8, 2, 0, 0, 0)
    bomb.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(ihdr))
        + b"IHDR"
        + ihdr
        + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
        + struct.pack(">I", 0)
        + b"IEND"
        + struct.pack(">I", zlib.crc32(b"IEND"))
    )

    proc = run_info(good, bomb, good)

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    first, rejected, last = payload["images"]
    assert [first["readable"], rejected["readable"], last["readable"]] == [True, False, True]
    assert_readable_record_keys(first)
    assert_readable_record_keys(last)
    assert "decompression bomb" in rejected["reason"].lower()


def test_camera_subifd_exif_claims_are_reported_without_pointer_offsets(tmp_path):
    """Catches real camera EXIF being hidden behind unreported pointer tags."""
    path = tmp_path / "camera-shaped.jpg"
    exif = Image.Exif()
    exif[271] = "Example Camera"
    exif[ExifTags.IFD.Exif] = {
        33434: (1, 125),
        36867: "2026:01:02 03:04:05",
        37510: b"\xff\xfe\x00",
    }
    exif[ExifTags.IFD.GPSInfo] = {1: "N"}
    Image.new("RGB", (3, 3), (10, 20, 30)).save(path, exif=exif)

    _payload, _stdout, record = only_image(path)

    assert record["exif"]["Make (271)"] == "Example Camera"
    assert record["exif"]["EXIF.ExposureTime (33434)"] == [1, 125]
    assert record["exif"]["EXIF.DateTimeOriginal (36867)"] == "2026:01:02 03:04:05"
    assert record["exif"]["EXIF.UserComment (37510)"] == {
        "type": "bytes",
        "length": 3,
        "sha256": hashlib.sha256(b"\xff\xfe\x00").hexdigest(),
    }
    assert record["exif"]["GPS.GPSLatitudeRef (1)"] == "N"
    assert "ExifOffset (34665)" not in record["exif"]
    assert "GPSInfo (34853)" not in record["exif"]


@pytest.mark.parametrize(
    ("name", "format_name"),
    [("jpeg", "JPEG"), ("tiff", "TIFF")],
)
def test_dpi_uses_one_rational_pair_schema_across_container_decoders(tmp_path, name, format_name):
    """Catches content and container dependent DPI JSON value shapes."""
    path = tmp_path / f"dpi.{name}"
    Image.new("RGB", (3, 3), (10, 20, 30)).save(path, format_name, dpi=(300, 300))

    _payload, _stdout, record = only_image(path)

    assert record["dpi"] == [[300, 1], [300, 1]]


def test_animated_gif_reports_real_frame_count(tmp_path):
    """Catches treating a container header as a single image and omitting animation facts."""
    path = tmp_path / "animated.gif"
    first = Image.new("RGB", (2, 2), (255, 0, 0))
    second = Image.new("RGB", (2, 2), (0, 0, 255))
    first.save(path, save_all=True, append_images=[second], duration=20, loop=0)
    _payload, _stdout, record = only_image(path)
    assert record["n_frames"] == 2
    assert record["is_animated"] is True


def test_missing_and_corrupt_files_are_reported_without_aborting_valid_siblings(tmp_path):
    """Catches whole-batch aborts and fabricated successful records for bad files."""
    valid = tmp_path / "valid.png"
    corrupt = tmp_path / "corrupt.png"
    missing = tmp_path / "missing.png"
    Image.new("RGB", (2, 2), (1, 2, 3)).save(valid)
    corrupt.write_bytes(b"not an image")
    proc = run_info(valid, missing, corrupt)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert [image["readable"] for image in payload["images"]] == [True, False, False]
    assert_readable_record_keys(payload["images"][0])
    assert payload["images"][1]["reason"]
    assert payload["images"][2]["reason"]


@pytest.mark.parametrize(
    "raw_exif",
    [
        b"Exif\x00\x00MM\x00*\x00\x00\x00\x00\xff\xff\xff\xff",
        b"Exif\x00\x00MM\x00*\x00\x00\x00\x00\x00\x00\x27\x0f",
        b"Exif\x00\x00MM\x00*\x00\x00\x00\x00\x00\x00\x00\x08\x13\x88\x00\x00\x00\x00",
    ],
)
def test_corrupt_valid_header_exif_is_flagged_as_unreadable_not_silently_empty(tmp_path, raw_exif):
    """Catches malformed IFD offsets/counts that look headed but warn only on parse."""
    path = tmp_path / "corrupt-exif.jpg"
    Image.new("RGB", (2, 2), (1, 2, 3)).save(path, exif=raw_exif)
    _payload, _stdout, record = only_image(path)
    assert record["exif_present"] is True
    assert record["exif"] is None
    assert "exif_unreadable" in record["flags"]


def test_empty_exif_ifd_is_distinct_from_corrupt_exif(tmp_path):
    """Catches treating a valid empty directory as a failed EXIF parse."""
    path = tmp_path / "empty-exif.jpg"
    empty_ifd = b"Exif\x00\x00" + b"MM\x00*\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00"
    Image.new("RGB", (2, 2), (1, 2, 3)).save(path, exif=empty_ifd)
    _payload, _stdout, record = only_image(path)
    assert record["exif_present"] is True
    assert record["exif"] == {}
    assert "exif_unreadable" not in record["flags"]


def test_webp_bare_tiff_exif_round_trips_without_an_unreadable_flag(tmp_path):
    """Catches rejecting WebP's valid bare-TIFF EXIF payload as non-APP1 data."""
    path = tmp_path / "exif.webp"
    exif = Image.Exif()
    exif[271] = "Example Camera"
    exif[272] = "Model X"
    exif[274] = 1
    Image.new("RGB", (2, 2), (1, 2, 3)).save(path, "WEBP", exif=exif)
    _payload, _stdout, record = only_image(path)
    assert record["exif"] == {
        "Make (271)": "Example Camera",
        "Model (272)": "Model X",
        "Orientation (274)": 1,
    }
    assert "exif_unreadable" not in record["flags"]


@pytest.mark.parametrize("keep_percent", [30, 40, 50, 60, 70, 80])
def test_header_only_rgb_read_does_not_fabricate_an_exif_failure(tmp_path, keep_percent):
    """Catches attributing a later whole-file read error to absent EXIF metadata."""
    path = tmp_path / f"truncated-rgb-{keep_percent}.png"
    Image.new("RGB", (20, 20), (1, 2, 3)).save(path)
    original = path.read_bytes()
    path.write_bytes(original[: len(original) * keep_percent // 100])
    proc = run_info(path)
    payload = json.loads(proc.stdout)
    record = payload["images"][0]
    assert "exif_unreadable" not in record.get("flags", [])
    if record["readable"]:
        assert proc.returncode == 0
        assert_readable_record_keys(record)
        assert record["exif_present"] is False
        assert record["exif"] is None
        assert any(
            "header" in limit and "pixel integrity" in limit
            for limit in payload["interpretation_limits"]
        )
    else:
        assert proc.returncode == 1


@pytest.mark.parametrize(
    ("mode", "colour", "transparency"),
    [("RGB", (10, 20, 30), (10, 20, 30)), ("L", 10, 10)],
)
def test_colour_key_transparency_is_not_claimed_as_an_alpha_channel(
    tmp_path, mode, colour, transparency
):
    """Catches widening alpha-channel claims for RGB/L colour-key transparency."""
    path = tmp_path / f"{mode}-colour-key.png"
    Image.new(mode, (8, 8), colour).save(path, transparency=transparency)
    _payload, _stdout, record = only_image(path)
    assert record["has_alpha_channel"] is False
    assert record["uses_transparency"] is False
    assert record["alpha_min"] is None
    assert record["alpha_max"] is None
    expected_key = list(transparency) if isinstance(transparency, tuple) else transparency
    assert record["transparency_key"] == expected_key


@pytest.mark.parametrize("table", [bytes([65, 66, 67]), bytes([65, 66, 67, 255])])
def test_palette_transparency_tables_are_always_lists_of_alpha_bytes(tmp_path, table):
    """Catches content-dependent text or bytes-descriptor serialisation of tRNS tables."""
    path = tmp_path / f"palette-{len(table)}.png"
    image = Image.new("P", (len(table), 1))
    image.putdata(list(range(len(table))))
    image.putpalette(list(range(256)) * 3)
    image.save(path, transparency=table, bits=8)
    _payload, _stdout, record = only_image(path)
    assert record["transparency_key"] == list(table)


def test_invalid_cli_invocation_keeps_stdout_empty(tmp_path):
    """Catches argparse rejection paths that accidentally emit a partial JSON document."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "pil_image_info.py")], capture_output=True, text=True
    )
    assert proc.returncode == 2
    assert proc.stdout == ""


def test_every_public_function_documents_why_it_exists():
    """Catches future public helpers that expose behaviour without an honesty boundary."""
    functions = [
        obj
        for name, obj in vars(pil_image_info).items()
        if inspect.isfunction(obj)
        and obj.__module__ == pil_image_info.__name__
        and not name.startswith("_")
    ]
    assert functions
    for function in functions:
        docstring = inspect.getdoc(function)
        assert docstring and len(docstring.strip()) > 20, function.__name__
