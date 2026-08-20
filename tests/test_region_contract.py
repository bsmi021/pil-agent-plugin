"""Contract checks that keep crop and region measurements on identical pixels."""

import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from pil_region import (  # noqa: E402
    DEFAULT_REGION_SPACE,
    REGION_SPACES,
    RegionError,
    parse_fractional_bbox,
    rect_to_fractional,
    resolve_pixel_rect,
)


def test_module_imports_when_image_dependencies_are_forbidden():
    """Keep region parsing usable before an image dependency is available."""
    script = (
        "import builtins, sys; "
        "original = builtins.__import__; "
        "builtins.__import__ = lambda name, *args, **kwargs: "
        "(_ for _ in ()).throw(ImportError(name)) if name.split('.')[0] in "
        "{'PIL', 'numpy'} else original(name, *args, **kwargs); "
        f"sys.path.insert(0, {str(SCRIPTS)!r}); import pil_region; "
        "print(pil_region.__dict__.get('Image') is None)"
    )

    # Arrange / Act
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

    # Assert
    assert result.returncode == 0, result.stderr
    assert result.stdout == "True\n"


def test_json_and_csv_spellings_are_identical():
    """Make tool-emitted JSON and copied command-line CSV interchangeable."""
    # Arrange
    json_text = "[0.1, 0.4, 0.3, 0.9]"
    csv_text = "0.1, 0.4, 0.3, 0.9"

    # Act
    json_box = parse_fractional_bbox(json_text)
    csv_box = parse_fractional_bbox(csv_text)

    # Assert
    assert json_box == csv_box == [0.1, 0.4, 0.3, 0.9]


@pytest.mark.parametrize(
    ("text", "offending_value", "reason"),
    [
        ("0.1,0.2,0.3", "0.1,0.2,0.3", "not_four_values"),
        ("[0.1,0.2,not-a-number,0.9]", "not-a-number", "non_numeric"),
        ("[0.8,0.2,0.3,0.9]", "0.8", "inverted_horizontal"),
        ("[0.1,0.9,0.3,0.4]", "0.9", "inverted_vertical"),
        ("[-0.1,0.2,0.3,0.9]", "-0.1", "outside_unit_interval"),
    ],
    ids=lambda value: value if isinstance(value, str) and "_" in value else None,
)
def test_parse_rejects_each_invalid_fractional_bbox_class(text, offending_value, reason):
    """Reject inputs that could otherwise make a tool measure an unintended region."""
    # Arrange / Act
    with pytest.raises(RegionError) as error:
        parse_fractional_bbox(text)

    # Assert
    assert offending_value in str(error.value), reason


def test_resolver_rejects_a_valid_fraction_that_rounds_to_no_pixels():
    """Prevent a thin requested region from being silently widened to one pixel."""
    # Arrange
    box = parse_fractional_bbox("[0.500,0.5,0.501,0.9]")

    # Act
    with pytest.raises(RegionError) as error:
        resolve_pixel_rect(box, (100, 100))

    # Assert
    assert "[0.5, 0.5, 0.501, 0.9]" in str(error.value)
    assert "zero width or height" in str(error.value)


@pytest.mark.parametrize(
    ("box", "size", "expected"),
    [
        # floor(0.5 * 101 + 0.5) == floor(51.0) == 51
        ([0.5, 0.0, 1.0, 1.0], (101, 37), (51, 0, 101, 37)),
        # floor(0.5 * 37 + 0.5) == 19; floor(0.333 * 37 + 0.5) == 12
        ([0.333, 0.5, 1.0, 1.0], (37, 37), (12, 19, 37, 37)),
        # floor(0.0 * 3 + 0.5) == 0; floor(0.34 * 3 + 0.5) == 1
        ([0.0, 0.0, 0.34, 1.0], (3, 3), (0, 0, 1, 3)),
    ],
)
def test_resolver_uses_half_up_rounding_at_odd_extents(box, size, expected):
    """Pin edge cases where banker's rounding would make tools disagree by a pixel."""
    # Arrange / Act
    actual = resolve_pixel_rect(box, size)

    # Assert
    assert actual == expected


def test_resolver_offsets_foreground_relative_rects_into_frame_coordinates():
    """Ensure a foreground-space request stays meaningful to frame-space consumers."""
    # Arrange / Act
    actual = resolve_pixel_rect([0.0, 0.0, 1.0, 1.0], (50, 20), origin=(10, 5))

    # Assert
    assert actual == (10, 5, 60, 25)


@pytest.mark.parametrize(
    ("box", "size"),
    [
        ([0.0, 0.0, 1.0, 1.0], (3, 3)),
        ([0.0, 0.0, 0.34, 1.0], (3, 3)),
        ([0.1, 0.1, 0.9, 0.9], (101, 37)),
        ([0.333, 0.25, 0.9, 0.75], (37, 101)),
        ([0.01, 0.02, 0.99, 0.98], (1000, 1000)),
        ([0.25, 0.25, 0.75, 0.75], (101, 37)),
        ([0.0, 0.4, 1.0, 0.9], (73, 19)),
        ([0.12, 0.13, 0.87, 0.88], (17, 29)),
        ([0.2, 0.3, 0.8, 0.7], (47, 53)),
        ([0.125, 0.125, 0.875, 0.875], (256, 256)),
        ([0.4, 0.1, 0.6, 0.9], (31, 97)),
        ([0.01, 0.01, 0.02, 0.99], (101, 103)),
        ([0.49, 0.01, 0.51, 0.99], (201, 41)),
        ([0.0, 0.0, 0.5, 0.5], (37, 101)),
        ([0.5, 0.5, 1.0, 1.0], (37, 101)),
        ([0.2, 0.2, 0.8, 0.8], (11, 13)),
        ([0.09, 0.11, 0.91, 0.89], (89, 97)),
        ([0.333, 0.333, 0.667, 0.667], (300, 300)),
        ([0.123, 0.456, 0.789, 0.987], (97, 89)),
        ([0.001, 0.002, 0.999, 0.998], (999, 997)),
    ],
)
def test_reported_fraction_describes_the_rect_that_was_resolved(box, size):
    """Keep emitted fractions tied to cut pixels instead of the caller's intent."""
    # Arrange
    tolerance = 1.0 / min(size)

    # Act
    rect = resolve_pixel_rect(box, size)
    reported = rect_to_fractional(rect, size)

    # Assert
    assert all(abs(actual - requested) <= tolerance for actual, requested in zip(reported, box))


def test_full_frame_is_a_real_rect_and_constants_remain_frozen():
    """Keep explicit full-frame scoping distinguishable from no region at all."""
    # Arrange / Act
    rect = resolve_pixel_rect([0.0, 0.0, 1.0, 1.0], (50, 20))

    # Assert
    assert rect == (0, 0, 50, 20)
    assert REGION_SPACES == ("frame", "foreground")
    assert DEFAULT_REGION_SPACE == "frame"
    assert issubclass(RegionError, ValueError)
