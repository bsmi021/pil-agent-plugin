"""Contract tests for the Tesseract OCR tool.

Engine-dependent tests skip cleanly when tesseract is not installed,
mirroring how the Blender-dependent tests skip without a Blender install.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pil_ocr  # noqa: E402
import pil_semantic_record  # noqa: E402

HAS_TESSERACT = shutil.which("tesseract") is not None

needs_tesseract = pytest.mark.skipif(
    not HAS_TESSERACT, reason="tesseract binary not installed"
)


def _run(capsys, *argv):
    code = pil_ocr.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out) if out else None


def _font():
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, 72)
    try:
        return ImageFont.load_default(size=72)
    except TypeError:
        pytest.skip("no scalable font available for rendering test text")


@pytest.fixture
def text_image(tmp_path):
    img = Image.new("RGB", (900, 220), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((40, 70), "HELLO WORLD 42", fill=(0, 0, 0), font=_font())
    path = tmp_path / "text.png"
    img.save(path)
    return path


def test_missing_binary_exits_2_with_empty_stdout(capsys, text_image):
    code, payload = _run(
        capsys, str(text_image), "--tesseract-executable", "/nonexistent/tesseract"
    )
    assert code == 2
    assert payload is None


@needs_tesseract
def test_reads_rendered_text_with_boxes(capsys, text_image):
    code, payload = _run(capsys, str(text_image))
    assert code == 0
    texts = [w["text"] for w in payload["words"]]
    assert texts == ["HELLO", "WORLD", "42"]
    assert payload["full_text"] == "HELLO WORLD 42"
    assert payload["engine"].startswith("tesseract")
    for word in payload["words"]:
        left, top, right, bottom = word["bbox_pixels"]
        assert 0 <= left < right <= 900 and 0 <= top < bottom <= 220
        assert all(0.0 <= v <= 1.0 for v in word["bbox_fractional"])
        assert word["confidence"] > 50


@needs_tesseract
def test_output_is_deterministic(capsys, text_image):
    code_1, payload_1 = _run(capsys, str(text_image))
    code_2, payload_2 = _run(capsys, str(text_image))
    assert code_1 == code_2 == 0
    assert payload_1 == payload_2


@needs_tesseract
def test_region_boxes_map_back_to_frame(capsys, tmp_path):
    img = Image.new("RGB", (1000, 1000), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((520, 720), "CORNER", fill=(0, 0, 0), font=_font())
    path = tmp_path / "corner.png"
    img.save(path)

    code, payload = _run(capsys, str(path), "--region", "0.5,0.7,1.0,0.9")
    assert code == 0
    assert payload["region"]["resolved_pixel_rect"] == [500, 700, 1000, 900]
    words = {w["text"]: w for w in payload["words"]}
    assert "CORNER" in words
    left, top, right, bottom = words["CORNER"]["bbox_pixels"]
    # Frame coordinates, not crop coordinates: the word sits where it was drawn.
    assert 500 <= left < right <= 1000
    assert 700 <= top < bottom <= 900


@needs_tesseract
def test_blank_image_flags_no_text(capsys, tmp_path):
    path = tmp_path / "blank.png"
    Image.new("RGB", (400, 300), (250, 250, 250)).save(path)
    code, payload = _run(capsys, str(path))
    assert code == 0
    assert payload["words"] == []
    assert payload["full_text"] == ""
    assert "no_text_found" in payload["flags"]


@needs_tesseract
def test_claims_out_seals_with_semantic_record(capsys, tmp_path, text_image):
    claims_path = tmp_path / "claims.json"
    code, payload = _run(capsys, str(text_image), "--claims-out", str(claims_path))
    assert code == 0
    assert payload["claims_emitted"] == 1
    claims = json.loads(claims_path.read_text())
    claim = claims["claims"][0]
    assert claim["kind"] == "text_transcription"
    assert claim["value"] == "HELLO WORLD 42"
    assert claim["confidence"] in ("high", "medium", "low")
    assert "tesseract" in claim["evidence"]

    seal_code = pil_semantic_record.main(
        ["seal", str(text_image), "--claims", str(claims_path)]
    )
    sealed = json.loads(capsys.readouterr().out)
    assert seal_code == 0
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(sealed))
    verify_code = pil_semantic_record.main(
        ["verify", str(text_image), "--record", str(record_path)]
    )
    verification = json.loads(capsys.readouterr().out)["verification"]
    assert verify_code == 0
    assert verification["verified"] is True


@needs_tesseract
def test_claims_min_confidence_filters_only_claims(capsys, tmp_path, text_image):
    claims_path = tmp_path / "claims.json"
    code, payload = _run(
        capsys,
        str(text_image),
        "--claims-out",
        str(claims_path),
        "--claims-min-confidence",
        "101",
    )
    assert code == 0
    # The OCR payload still reports every word; only the claims file is gated.
    assert payload["words"]
    assert payload["claims_emitted"] == 0
    assert "claims_out_skipped_no_accepted_lines" in payload["flags"]
    assert not claims_path.exists()


@needs_tesseract
def test_interpretation_limits_present(capsys, text_image):
    _code, payload = _run(capsys, str(text_image))
    limits = payload["interpretation_limits"]
    assert any("ENGINE ESTIMATE" in entry for entry in limits)
    assert any("NOT calibrated" in entry for entry in limits)
